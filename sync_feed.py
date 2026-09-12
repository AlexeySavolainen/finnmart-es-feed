#!/usr/bin/env python3
"""Synchronize the Spain Merchant feed from one Shopify Market catalog.

The script is intentionally read-only against Shopify. It resolves the exact
MarketCatalog publication, requires reviewed Spanish translations, fetches the
live Spanish storefront representation, validates the complete result and only
then atomically replaces the public XML file.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import html
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


SHOP = "b38c32.myshopify.com"
CLIENT_ID = "0fc0f57284e5eeae25fc823de9e0da2f"
API_VERSION = "2026-07"
CATALOG_ID = "gid://shopify/MarketCatalog/166288720196"
CATALOG_TITLE = "Finnmart EU – NovaEngel + Royal Textile"
MARKET_ID = "106799857988"
STORE = "https://finnmart.eu"
LOCALE = "es"
COUNTRY = "ES"
CURRENCY = "EUR"
G = "http://base.google.com/ns/1.0"
SHIPPING_SERVICE = "Entrega estándar (3–5 días laborables)"
SHIPPING_MIN_DAYS = 3
SHIPPING_MAX_DAYS = 5
SHIPPING_LIGHT_MAX_GRAMS = 10_000
SHIPPING_MAX_GRAMS = 20_000
SHIPPING_LIGHT_PRICE = "11.50 EUR"
SHIPPING_HEAVY_PRICE = "13.10 EUR"
REQUIRED_TRANSLATIONS = {"title", "body_html", "handle", "product_type"}
ET.register_namespace("g", G)


CATALOG_QUERY = """query CatalogProducts($id: ID!, $after: String) {
  catalog(id: $id) {
    __typename
    id
    title
    status
    publication {
      id
      includedProductsCount(limit: null) { count precision }
      includedProducts(first: 250, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          handle
          status
          tags
          translations(locale: \"es\") { key value }
        }
      }
    }
  }
}"""


@dataclass(frozen=True)
class Candidate:
    product_id: int
    handle: str
    supplier: str


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name, delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def clean_text(value: object, limit: int | None = None) -> str:
    text = str(value or "")
    text = re.sub(r"<\s*br\s*/?\s*>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def child(parent: ET.Element, name: str, value: object) -> ET.Element:
    node = ET.SubElement(parent, f"{{{G}}}{name}")
    node.text = str(value)
    return node


def valid_gtin(value: str) -> bool:
    if not value or not value.isdigit() or len(value) not in (8, 12, 13, 14):
        return False
    digits = [int(x) for x in value]
    check = digits.pop()
    total = sum(d * (3 if (len(digits) - i) % 2 else 1) for i, d in enumerate(digits))
    return (10 - total % 10) % 10 == check


def https_url(value: str) -> str:
    if value.startswith("//"):
        return "https:" + value
    if value.startswith("https://"):
        return value
    raise ValueError("Expected HTTPS Shopify asset URL")


def supplier_from_tags(tags: list[str]) -> str | None:
    values = {clean_text(tag).casefold() for tag in tags}
    if values & {"royal textile", "royal", "old royal textile"}:
        return "Royal Textile"
    if "nova engel" in values:
        return "NovaEngel"
    return None


def numeric_gid(value: str) -> int:
    number = value.rsplit("/", 1)[-1]
    if not number.isdigit():
        raise ValueError("Invalid Shopify product ID")
    return int(number)


def variant_weight_grams(variant: dict) -> int:
    raw = variant.get("grams")
    if raw is None:
        raw = variant.get("weight")
    if raw is None:
        raise ValueError("Missing variant weight")
    unit = clean_text(variant.get("weight_unit")).casefold() or "g"
    factors = {"g": 1, "kg": 1000, "oz": 28.349523125, "lb": 453.59237}
    if unit not in factors:
        raise ValueError(f"Unsupported weight unit: {unit}")
    grams = round(float(raw) * factors[unit])
    if grams <= 0:
        raise ValueError("Non-positive variant weight")
    if grams > SHIPPING_MAX_GRAMS:
        raise ValueError(f"Variant exceeds Spain shipping limit: {grams} g")
    return grams


def shipping_price(grams: int) -> str:
    return SHIPPING_LIGHT_PRICE if grams <= SHIPPING_LIGHT_MAX_GRAMS else SHIPPING_HEAVY_PRICE


class Shopify:
    def __init__(self, secret: str):
        if not secret.startswith("shpss_") or len(secret) < 30:
            raise RuntimeError("SHOPIFY_CLIENT_SECRET is missing or invalid")
        self.token = self._exchange(secret)

    @staticmethod
    def _exchange(secret: str) -> str:
        request = urllib.request.Request(
            f"https://{SHOP}/admin/oauth/access_token",
            data=urllib.parse.urlencode({
                "grant_type": "client_credentials",
                "client_id": CLIENT_ID,
                "client_secret": secret,
            }).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Shopify token exchange failed with HTTP {exc.code}") from None
        scopes = set(payload.get("scope", "").split(","))
        effective = scopes | ({"read_translations"} if "write_translations" in scopes else set())
        required = {"read_products", "read_publications", "read_translations"}
        if not required.issubset(effective):
            raise RuntimeError("Shopify app lacks read_products/read_publications/read_translations")
        return payload["access_token"]

    def call(self, query: str, variables: dict) -> dict:
        endpoint = f"https://{SHOP}/admin/api/{API_VERSION}/graphql.json"
        for attempt in range(6):
            request = urllib.request.Request(
                endpoint,
                data=json.dumps({"query": query, "variables": variables}).encode(),
                headers={
                    "Content-Type": "application/json",
                    "X-Shopify-Access-Token": self.token,
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    payload = json.load(response)
            except urllib.error.HTTPError as exc:
                if exc.code in {429, 500, 502, 503, 504} and attempt < 5:
                    time.sleep(min(2**attempt, 16))
                    continue
                raise RuntimeError(f"Shopify GraphQL HTTP {exc.code}") from None
            except (urllib.error.URLError, TimeoutError):
                if attempt < 5:
                    time.sleep(min(2**attempt, 16))
                    continue
                raise RuntimeError("Shopify GraphQL network retry limit reached") from None
            errors = payload.get("errors", [])
            if errors:
                if all(e.get("extensions", {}).get("code") == "THROTTLED" for e in errors) and attempt < 5:
                    time.sleep(min(2**attempt, 16))
                    continue
                raise RuntimeError("Shopify GraphQL error: " + json.dumps(errors))
            throttle = payload.get("extensions", {}).get("cost", {}).get("throttleStatus", {})
            if throttle.get("currentlyAvailable", 1000) < 100:
                time.sleep(2)
            return payload["data"]
        raise RuntimeError("Shopify GraphQL retry limit reached")


def catalog_candidates(api: Shopify) -> tuple[list[Candidate], dict]:
    after = None
    candidates: list[Candidate] = []
    skipped = {"inactive": 0, "unapproved_supplier": 0, "missing_spanish": 0}
    expected = None
    publication_id = None
    page_number = 0
    while True:
        page_number += 1
        data = api.call(CATALOG_QUERY, {"id": CATALOG_ID, "after": after})
        catalog = data.get("catalog")
        if not catalog:
            raise RuntimeError("Shopify Market catalog not found")
        if catalog.get("__typename") != "MarketCatalog" or catalog.get("id") != CATALOG_ID:
            raise RuntimeError("Unexpected catalog identity")
        if catalog.get("title") != CATALOG_TITLE or catalog.get("status") != "ACTIVE":
            raise RuntimeError("Catalog title/status guard failed")
        publication = catalog.get("publication")
        if not publication:
            raise RuntimeError("Market catalog has no publication")
        publication_id = publication["id"]
        count = publication["includedProductsCount"]
        expected = int(count["count"])
        page = publication["includedProducts"]
        for product in page["nodes"]:
            if product.get("status") != "ACTIVE":
                skipped["inactive"] += 1
                continue
            supplier = supplier_from_tags(product.get("tags", []))
            if not supplier:
                skipped["unapproved_supplier"] += 1
                continue
            translations = {row["key"]: clean_text(row.get("value")) for row in product.get("translations", [])}
            if not REQUIRED_TRANSLATIONS.issubset({key for key, value in translations.items() if value}):
                skipped["missing_spanish"] += 1
                continue
            handle = translations["handle"]
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", handle):
                raise RuntimeError(f"Unsafe Spanish handle for {product['id']}")
            candidates.append(Candidate(numeric_gid(product["id"]), handle, supplier))
        print(
            f"Catalog page {page_number}: eligible={len(candidates)} "
            f"included={expected}",
            flush=True,
        )
        if not page["pageInfo"]["hasNextPage"]:
            break
        following = page["pageInfo"]["endCursor"]
        if not following or following == after:
            raise RuntimeError("Catalog pagination did not advance")
        after = following
    if expected is None or publication_id is None:
        raise RuntimeError("Catalog returned no metadata")
    if len({row.product_id for row in candidates}) != len(candidates):
        raise RuntimeError("Duplicate Shopify products in catalog")
    return candidates, {
        "catalog_id": CATALOG_ID,
        "catalog_title": CATALOG_TITLE,
        "publication_id": publication_id,
        "included_products": expected,
        "skipped": skipped,
    }


def fetch_product(candidate: Candidate) -> tuple[Candidate, dict]:
    quoted = urllib.parse.quote(candidate.handle, safe="-._~")
    url = f"{STORE}/{LOCALE}/products/{quoted}.js"
    request = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "FinnmartSpainFeed/1.0",
    })
    last_error = "unknown"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                product = json.load(response)
            if int(product.get("id", 0)) != candidate.product_id:
                raise ValueError("Spanish handle resolved to another product")
            if product.get("handle") != candidate.handle or not product.get("published_at"):
                raise ValueError("Spanish market publication mismatch")
            if not product.get("variants"):
                raise ValueError("Product has no variants")
            return candidate, product
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            last_error = str(exc)
            if attempt < 3:
                time.sleep(0.5 * (2**attempt))
    raise RuntimeError(last_error)


def add_shipping(item: ET.Element, grams: int) -> None:
    child(item, "shipping_weight", f"{grams} g")
    shipping = child(item, "shipping", "")
    child(shipping, "country", COUNTRY)
    child(shipping, "service", SHIPPING_SERVICE)
    child(shipping, "price", shipping_price(grams))
    child(shipping, "min_handling_time", 0)
    child(shipping, "max_handling_time", 0)
    child(shipping, "min_transit_time", SHIPPING_MIN_DAYS)
    child(shipping, "max_transit_time", SHIPPING_MAX_DAYS)


def add_product(channel: ET.Element, candidate: Candidate, product: dict) -> int:
    title = clean_text(product.get("title"), 150)
    description = clean_text(product.get("description"), 5000)
    product_type = clean_text(product.get("type"))
    images = [https_url(value) for value in product.get("images", []) if value]
    if not title or not description or not product_type or not images:
        raise ValueError("Missing required Spanish content")
    option_names = [
        clean_text(value.get("name") if isinstance(value, dict) else value).casefold()
        for value in product.get("options", [])
    ]
    count = 0
    for variant in product["variants"]:
        variant_id = int(variant["id"])
        price = int(variant["price"])
        if price <= 0:
            raise ValueError("Non-positive price")
        grams = variant_weight_grams(variant)
        item = ET.SubElement(channel, "item")
        child(item, "id", f"shopify_{COUNTRY}_{candidate.product_id}_{variant_id}")
        child(item, "title", title)
        child(item, "description", description)
        child(item, "link", f"{STORE}/{LOCALE}/products/{candidate.handle}?variant={variant_id}")
        child(item, "image_link", images[0])
        for image in images[1:11]:
            child(item, "additional_image_link", image)
        child(item, "availability", "in_stock" if variant.get("available") else "out_of_stock")
        child(item, "price", f"{price / 100:.2f} {CURRENCY}")
        add_shipping(item, grams)
        child(item, "condition", "new")
        child(item, "brand", clean_text(product.get("vendor")) or candidate.supplier)
        child(item, "item_group_id", f"shopify_{COUNTRY}_{candidate.product_id}")
        child(item, "product_type", product_type)
        sku = clean_text(variant.get("sku"))
        barcode = clean_text(variant.get("barcode"))
        if valid_gtin(barcode):
            child(item, "gtin", barcode)
        if sku:
            child(item, "mpn", sku)
        child(item, "identifier_exists", "yes" if valid_gtin(barcode) or sku else "no")
        for index, raw_option in enumerate(variant.get("options", [])):
            if isinstance(raw_option, dict):
                name = clean_text(raw_option.get("name")).casefold()
                value = clean_text(raw_option.get("value"))
            else:
                name = option_names[index] if index < len(option_names) else ""
                value = clean_text(raw_option)
            if not value or value.casefold() == "default title":
                continue
            if name in {"color", "colour", "väri", "color de producto"}:
                child(item, "color", value)
            elif name in {"size", "koko", "talla"}:
                child(item, "size", value)
        child(item, "custom_label_0", candidate.supplier)
        count += 1
    return count


def previous_product_count(summary_path: Path) -> int | None:
    try:
        return int(json.loads(summary_path.read_text())["products"])
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
        return None


def build(secret: str, out: Path, summary_path: Path, workers: int, minimum_products: int) -> dict:
    started = time.time()
    api = Shopify(secret)
    candidates, catalog = catalog_candidates(api)
    if len(candidates) < minimum_products:
        raise RuntimeError(f"Safety stop: only {len(candidates)} eligible Market products")
    previous = previous_product_count(summary_path)
    if previous and len(candidates) < previous * 0.90:
        raise RuntimeError("Safety stop: Market product count fell by more than 10%")

    successes: list[tuple[Candidate, dict]] = []
    failures: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {pool.submit(fetch_product, candidate): candidate for candidate in candidates}
        for completed, future in enumerate(concurrent.futures.as_completed(future_map), 1):
            candidate = future_map[future]
            try:
                successes.append(future.result())
            except Exception as exc:
                failures.append({
                    "product_id": candidate.product_id,
                    "handle": candidate.handle,
                    "supplier": candidate.supplier,
                    "error": str(exc),
                })
            if completed % 500 == 0 or completed == len(candidates):
                print(
                    f"Storefront validation {completed}/{len(candidates)}: "
                    f"ok={len(successes)} failed={len(failures)}",
                    flush=True,
                )
    successes.sort(key=lambda row: row[0].product_id)
    if len(successes) < minimum_products or len(failures) > max(25, round(len(candidates) * 0.01)):
        raise RuntimeError(f"Safety stop: {len(failures)} storefront fetch failures")

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "Finnmart Spain Market feed"
    ET.SubElement(channel, "link").text = f"{STORE}/{LOCALE}/"
    ET.SubElement(channel, "description").text = "Shopify Market synchronized feed for Spain"
    item_count = 0
    suppliers = {"NovaEngel": 0, "Royal Textile": 0}
    for candidate, product in successes:
        item_count += add_product(channel, candidate, product)
        suppliers[candidate.supplier] += 1
    if not all(suppliers.values()):
        raise RuntimeError("Both approved suppliers must be present")

    xml_bytes = ET.tostring(rss, encoding="utf-8", xml_declaration=True)
    parsed = ET.fromstring(xml_bytes)
    items = parsed.findall("./channel/item")
    ids = [item.find(f"{{{G}}}id").text for item in items]
    links = [item.find(f"{{{G}}}link").text for item in items]
    if len(items) != item_count or len(ids) != len(set(ids)):
        raise RuntimeError("Final item validation failed")
    if any(not value.startswith("shopify_ES_") for value in ids):
        raise RuntimeError("Unexpected offer ID prefix")
    if any(not value.startswith(f"{STORE}/{LOCALE}/products/") for value in links):
        raise RuntimeError("Unexpected landing page locale")
    if len(parsed.findall(f"./channel/item/{{{G}}}shipping")) != item_count:
        raise RuntimeError("Shipping is missing from one or more offers")

    summary = {
        "status": "validated",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_seconds": round(time.time() - started, 1),
        "shop": SHOP,
        "market_id": MARKET_ID,
        **catalog,
        "eligible_products": len(candidates),
        "products": len(successes),
        "items": item_count,
        "suppliers": suppliers,
        "fetch_failures": failures,
        "shipping": {
            "service": SHIPPING_SERVICE,
            "delivery_days": "3-5",
            "up_to_10kg": SHIPPING_LIGHT_PRICE,
            "over_10kg_to_20kg": SHIPPING_HEAVY_PRICE,
        },
        "xml_sha256": hashlib.sha256(xml_bytes).hexdigest(),
    }
    atomic_write(out, xml_bytes)
    atomic_write(summary_path, json.dumps(summary, ensure_ascii=False, indent=2).encode())
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("public/finnmart-es.xml"))
    parser.add_argument("--summary", type=Path, default=Path("public/finnmart-es-summary.json"))
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--minimum-products", type=int, default=10_000)
    args = parser.parse_args()
    if not 1 <= args.workers <= 32:
        raise SystemExit("workers must be between 1 and 32")
    secret = os.environ.get("SHOPIFY_CLIENT_SECRET", "")
    result = build(secret, args.out, args.summary, args.workers, args.minimum_products)
    print(json.dumps({
        key: result[key]
        for key in ("status", "products", "items", "suppliers", "xml_sha256", "duration_seconds")
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
