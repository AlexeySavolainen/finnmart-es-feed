#!/usr/bin/env python3
"""Synchronize a localized Merchant feed from one Shopify Market catalog.

The script is intentionally read-only against Shopify. It resolves the exact
MarketCatalog publication, requires reviewed translations for the selected
target, exports the catalog through Shopify Bulk GraphQL, validates the complete
result and only then atomically replaces the public XML file.
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
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path


SHOP = "b38c32.myshopify.com"
CLIENT_ID = "0fc0f57284e5eeae25fc823de9e0da2f"
API_VERSION = "2026-07"
CATALOG_ID = "gid://shopify/MarketCatalog/166288720196"
CATALOG_TITLE = "Finnmart EU – NovaEngel + Royal Textile"
MARKET_ID = "106799857988"
MARKET_GID = f"gid://shopify/Market/{MARKET_ID}"
PUBLICATION_ID = "gid://shopify/Publication/327947911492"
STORE = "https://finnmart.eu"
CURRENCY = "EUR"
G = "http://base.google.com/ns/1.0"
SHIPPING_MAX_GRAMS = 20_000
REQUIRED_TRANSLATIONS = {"title", "body_html", "product_type"}
ET.register_namespace("g", G)

TARGETS = {
    "ES": {
        "locale": "es", "country": "ES", "name": "Spain",
        "handle_locale": "es",
        "path_prefix": "/es",
        "feed_title": "Finnmart Spain Market feed",
        "feed_description": "Shopify Market synchronized feed for Spain",
        "shipping_service": "Entrega estándar (3–5 días laborables)",
        "shipping_min_days": 3, "shipping_max_days": 5,
        "shipping_bands": ((10_000, "4.90 EUR"), (20_000, "6.90 EUR")),
        "default_out": "public/finnmart-es.xml",
        "default_summary": "public/finnmart-es-summary.json",
    },
    "IE": {
        "locale": "en", "country": "IE", "name": "Ireland",
        "handle_locale": "en",
        # English is the default language of finnmart.eu, so Shopify serves it
        # at the root rather than below a non-existent /en/ prefix.
        "path_prefix": "",
        "feed_title": "Finnmart Ireland Market feed",
        "feed_description": "Shopify Market synchronized feed for Ireland",
        "shipping_service": "Standard delivery (3–5 business days)",
        "shipping_min_days": 3, "shipping_max_days": 5,
        # Exact Shopify General profile rates for Ireland on 13 September 2026.
        "shipping_bands": (
            (500, "11.50 EUR"), (2_000, "11.50 EUR"),
            (5_000, "16.65 EUR"), (10_000, "24.15 EUR"),
            (15_000, "30.70 EUR"), (20_000, "32.15 EUR"),
        ),
        "default_out": "public/finnmart-ie.xml",
        "default_summary": "public/finnmart-ie-summary.json",
    },
    "FR": {
        "locale": "fr", "country": "FR", "name": "France",
        # French content is published under the existing English market URLs.
        # Shopify therefore resolves /fr/ products with the English handle.
        "handle_locale": "en",
        "path_prefix": "/fr",
        "feed_title": "Finnmart France Market feed",
        "feed_description": "Shopify Market synchronized feed for France",
        "shipping_service": "Livraison standard (3–5 jours ouvrables)",
        "shipping_min_days": 3, "shipping_max_days": 5,
        # Exact Shopify General profile rates for France on 14 September 2026.
        "shipping_bands": (
            (500, "7.95 EUR"), (2_000, "7.95 EUR"),
            (5_000, "9.05 EUR"), (10_000, "13.80 EUR"),
            (15_000, "14.95 EUR"), (20_000, "21.90 EUR"),
        ),
        "default_out": "public/finnmart-fr.xml",
        "default_summary": "public/finnmart-fr-summary.json",
    },
    "PT": {
        "locale": "pt-PT", "country": "PT", "name": "Portugal",
        # European Portuguese is published at /pt-pt/ while product handles
        # remain aligned with the English market URLs.
        "handle_locale": "en",
        "path_prefix": "/pt-pt",
        "feed_title": "Finnmart Portugal Market feed",
        "feed_description": "Shopify Market synchronized feed for Portugal",
        "shipping_service": "Entrega standard (5–8 dias úteis)",
        "shipping_min_days": 5, "shipping_max_days": 8,
        # Exact Shopify General profile rates for Portugal on 15 September 2026.
        "shipping_bands": ((10_000, "4.90 EUR"), (20_000, "6.90 EUR")),
        "default_out": "public/finnmart-pt.xml",
        "default_summary": "public/finnmart-pt-summary.json",
    },
}


def translated_catalog_query(locale: str, handle_locale: str) -> str:
    return '''query CatalogProducts($id: ID!, $after: String) {
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
          translations(locale: "__LOCALE__") { key value }
          handleTranslations: translations(locale: "__HANDLE_LOCALE__") { key value }
        }
      }
    }
  }
}'''.replace("__LOCALE__", locale).replace("__HANDLE_LOCALE__", handle_locale)


def translated_bulk_fields(locale: str, handle_locale: str, country: str) -> str:
    return '''
  id
  __typename
  status
  tags
  vendor
  translations(locale: "__LOCALE__") { key value }
  handleTranslations: translations(locale: "__HANDLE_LOCALE__") { key value }
  media {
    edges { node {
      __typename
      ... on MediaImage { id image { url } }
    } }
  }
  variants {
    edges { node {
      id
      __typename
      legacyResourceId
      availableForSale
      inventoryQuantity
      barcode
      sku
      selectedOptions { name value }
      contextualPricing(context: {country: __COUNTRY__}) {
        price { amount currencyCode }
      }
      inventoryItem {
        measurement { weight { value unit } }
      }
    } }
  }
'''.replace("__LOCALE__", locale).replace("__HANDLE_LOCALE__", handle_locale).replace("__COUNTRY__", country)


def configure_target(country: str) -> None:
    global LOCALE, HANDLE_LOCALE, COUNTRY, TARGET_NAME, PATH_PREFIX, FEED_TITLE, FEED_DESCRIPTION
    global SHIPPING_SERVICE, SHIPPING_MIN_DAYS, SHIPPING_MAX_DAYS, SHIPPING_BANDS
    global DEFAULT_OUT, DEFAULT_SUMMARY, USER_AGENT
    global CATALOG_QUERY, BULK_PRODUCT_FIELDS, BULK_PRODUCT_PROBE_FIELDS
    global BULK_PRODUCT_PROBE_QUERY
    config = TARGETS[country]
    LOCALE = config["locale"]
    HANDLE_LOCALE = config["handle_locale"]
    COUNTRY = config["country"]
    TARGET_NAME = config["name"]
    PATH_PREFIX = config["path_prefix"]
    FEED_TITLE = config["feed_title"]
    FEED_DESCRIPTION = config["feed_description"]
    SHIPPING_SERVICE = config["shipping_service"]
    SHIPPING_MIN_DAYS = config["shipping_min_days"]
    SHIPPING_MAX_DAYS = config["shipping_max_days"]
    SHIPPING_BANDS = config["shipping_bands"]
    DEFAULT_OUT = Path(config["default_out"])
    DEFAULT_SUMMARY = Path(config["default_summary"])
    USER_AGENT = f"Finnmart{TARGET_NAME}Feed/2.0"
    CATALOG_QUERY = translated_catalog_query(LOCALE, HANDLE_LOCALE)
    BULK_PRODUCT_FIELDS = translated_bulk_fields(LOCALE, HANDLE_LOCALE, COUNTRY)
    BULK_PRODUCT_PROBE_FIELDS = BULK_PRODUCT_FIELDS.replace(
        "  media {", "  media(first: 10) {"
    ).replace("  variants {", "  variants(first: 100) {")
    BULK_PRODUCT_PROBE_QUERY = """query BulkProductProbe($publicationId: ID!) {
  publication(id: $publicationId) {
    includedProducts(first: 1) {
      nodes {
""" + BULK_PRODUCT_PROBE_FIELDS + """
      }
    }
  }
}"""


configure_target("ES")

CATALOG_METADATA_QUERY = """query CatalogMetadata($id: ID!) {
  catalog(id: $id) {
    __typename
    id
    title
    status
    publication {
      id
      includedProductsCount(limit: null) { count precision }
    }
  }
}"""

BULK_RUN_MUTATION = """mutation RunFeedBulkQuery($query: String!) {
  bulkOperationRunQuery(query: $query) {
    bulkOperation { id status }
    userErrors { field message }
  }
}"""

BULK_STATUS_QUERY = """query FeedBulkStatus($id: ID!) {
  bulkOperation(id: $id) {
    id
    status
    errorCode
    objectCount
    rootObjectCount
    fileSize
    url
    partialDataUrl
  }
}"""

RECENT_BULK_QUERY = """query RecentFeedBulkOperations {
  bulkOperations(first: 5, sortKey: CREATED_AT, reverse: true) {
    nodes {
      id
      status
      errorCode
      createdAt
      completedAt
      objectCount
      rootObjectCount
      fileSize
      url
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
        raise ValueError(f"Variant exceeds {TARGET_NAME} shipping limit: {grams} g")
    return grams


def shipping_price(grams: int) -> str:
    for maximum, price in SHIPPING_BANDS:
        if grams <= maximum:
            return price
    raise ValueError(f"Variant exceeds {TARGET_NAME} shipping limit: {grams} g")


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


def catalog_metadata(api: Shopify) -> dict:
    data = api.call(CATALOG_METADATA_QUERY, {"id": CATALOG_ID})
    catalog = data.get("catalog")
    if not catalog:
        raise RuntimeError("Shopify Market catalog not found")
    if (
        catalog.get("__typename") != "MarketCatalog"
        or catalog.get("id") != CATALOG_ID
        or catalog.get("title") != CATALOG_TITLE
        or catalog.get("status") != "ACTIVE"
    ):
        raise RuntimeError("Catalog identity/title/status guard failed")
    publication = catalog.get("publication")
    if not publication or publication.get("id") != PUBLICATION_ID:
        raise RuntimeError("Unexpected Market publication")
    count = publication.get("includedProductsCount", {})
    if count.get("precision") != "EXACT":
        raise RuntimeError("Shopify did not return an exact Market product count")
    return {
        "catalog_id": CATALOG_ID,
        "catalog_title": CATALOG_TITLE,
        "publication_id": PUBLICATION_ID,
        "included_products": int(count["count"]),
    }


def bulk_query_document() -> str:
    return (
        "{\n"
        f'  publication(id: "{PUBLICATION_ID}") {{\n'
        "    includedProducts {\n"
        "      edges { node {\n"
        + BULK_PRODUCT_FIELDS
        + "      } }\n"
        "    }\n"
        "  }\n"
        "}"
    )


def run_bulk_query(api: Shopify) -> dict:
    payload = api.call(BULK_RUN_MUTATION, {"query": bulk_query_document()})
    result = payload["bulkOperationRunQuery"]
    if result.get("userErrors"):
        raise RuntimeError("Shopify rejected bulk query: " + json.dumps(result["userErrors"]))
    operation = result.get("bulkOperation")
    if not operation:
        raise RuntimeError("Shopify did not create a bulk query")
    operation_id = operation["id"]
    started = time.monotonic()
    last_count = None
    while True:
        operation = api.call(BULK_STATUS_QUERY, {"id": operation_id}).get("bulkOperation")
        if not operation:
            raise RuntimeError("Shopify bulk operation disappeared")
        status = operation["status"]
        count = int(operation.get("objectCount") or 0)
        if count != last_count:
            print(f"Bulk operation {status}: objects={count}", flush=True)
            last_count = count
        if status == "COMPLETED":
            if not operation.get("url"):
                raise RuntimeError("Completed Shopify bulk operation has no result URL")
            return operation
        if status in {"FAILED", "CANCELED", "EXPIRED"}:
            raise RuntimeError(
                f"Shopify bulk operation {status}: {operation.get('errorCode') or 'unknown error'}"
            )
        if time.monotonic() - started > 90 * 60:
            raise RuntimeError("Shopify bulk operation exceeded 90 minutes")
        time.sleep(10)


def money_to_cents(value: object) -> int:
    try:
        cents = (Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError("Invalid contextual price") from None
    amount = int(cents)
    if amount <= 0:
        raise ValueError("Non-positive contextual price")
    return amount


def bulk_variant(record: dict) -> dict:
    price = record.get("contextualPricing", {}).get("price", {})
    if price.get("currencyCode") != CURRENCY:
        raise ValueError("Variant contextual price is not EUR")
    weight = record.get("inventoryItem", {}).get("measurement", {}).get("weight")
    if not weight:
        raise ValueError("Missing variant weight")
    unit_map = {
        "GRAMS": "g",
        "KILOGRAMS": "kg",
        "OUNCES": "oz",
        "POUNDS": "lb",
    }
    unit = unit_map.get(weight.get("unit"))
    if not unit:
        raise ValueError("Unsupported Shopify weight unit")
    return {
        "id": int(record["legacyResourceId"]),
        "price": money_to_cents(price.get("amount")),
        "available": bool(record.get("availableForSale")),
        "inventory_quantity": record.get("inventoryQuantity"),
        "barcode": record.get("barcode"),
        "sku": record.get("sku"),
        "options": record.get("selectedOptions", []),
        "weight": weight.get("value"),
        "weight_unit": unit,
    }


def load_bulk_products(url: str, expected_products: int) -> tuple[list[tuple[Candidate, dict]], dict]:
    products: dict[str, dict] = {}
    order: list[str] = []
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        response = urllib.request.urlopen(request, timeout=120)
    except urllib.error.URLError:
        raise RuntimeError("Unable to download Shopify bulk result") from None
    with response:
        for line_number, raw_line in enumerate(response, 1):
            if len(raw_line) > 16 * 1024 * 1024:
                raise RuntimeError("Oversized Shopify bulk JSONL record")
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError:
                raise RuntimeError(f"Invalid Shopify bulk JSONL at line {line_number}") from None
            kind = record.get("__typename")
            if kind == "Product" or (
                not record.get("__parentId")
                and str(record.get("id", "")).startswith("gid://shopify/Product/")
            ):
                product_id = record["id"]
                record["_images"] = []
                record["_variants"] = []
                products[product_id] = record
                order.append(product_id)
                continue
            parent_id = record.get("__parentId")
            parent = products.get(parent_id)
            if not parent:
                raise RuntimeError("Shopify bulk child appeared without its product")
            if kind == "MediaImage":
                image_url = record.get("image", {}).get("url")
                if image_url and len(parent["_images"]) < 11:
                    parent["_images"].append(image_url)
            elif kind == "ProductVariant":
                parent["_variants"].append(record)
    if len(products) != expected_products:
        raise RuntimeError(
            f"Shopify bulk root count mismatch: expected {expected_products}, received {len(products)}"
        )

    rows: list[tuple[Candidate, dict]] = []
    skipped = {
        "inactive": 0,
        "unapproved_supplier": 0,
        f"missing_{LOCALE}": 0,
        "invalid_product": 0,
    }
    invalid: list[dict] = []
    missing_translations: list[dict] = []
    for product_id in order:
        record = products[product_id]
        if record.get("status") != "ACTIVE":
            skipped["inactive"] += 1
            continue
        supplier = supplier_from_tags(record.get("tags", []))
        if not supplier:
            skipped["unapproved_supplier"] += 1
            continue
        translations = {row["key"]: row.get("value") for row in record.get("translations", [])}
        handle_translations = {
            row["key"]: row.get("value") for row in record.get("handleTranslations", [])
        }
        present = {key for key, value in translations.items() if clean_text(value)}
        if not REQUIRED_TRANSLATIONS.issubset(present) or not clean_text(handle_translations.get("handle")):
            skipped[f"missing_{LOCALE}"] += 1
            missing_translations.append({
                "product_id": product_id,
                "missing": sorted(
                    (REQUIRED_TRANSLATIONS - present)
                    | ({f"handle:{HANDLE_LOCALE}"} if not clean_text(handle_translations.get("handle")) else set())
                ),
            })
            continue
        handle = clean_text(handle_translations["handle"])
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", handle):
            skipped["invalid_product"] += 1
            invalid.append({"product_id": product_id, "error": f"unsafe {LOCALE} handle"})
            continue
        try:
            variants = [bulk_variant(row) for row in record["_variants"]]
        except (KeyError, TypeError, ValueError) as exc:
            skipped["invalid_product"] += 1
            invalid.append({"product_id": product_id, "handle": handle, "error": str(exc)})
            continue
        if not variants:
            skipped["invalid_product"] += 1
            invalid.append({"product_id": product_id, "handle": handle, "error": "no variants"})
            continue
        candidate = Candidate(numeric_gid(product_id), handle, supplier)
        rows.append((candidate, {
            "id": candidate.product_id,
            "handle": handle,
            "title": translations["title"],
            "description": translations["body_html"],
            "type": translations["product_type"],
            "vendor": record.get("vendor"),
            "images": record["_images"],
            "variants": variants,
        }))
    return rows, {
        "skipped": skipped,
        "invalid_products": invalid,
        "missing_translations": missing_translations,
    }


def latest_completed_bulk(api: Shopify) -> dict:
    operations = api.call(RECENT_BULK_QUERY, {})["bulkOperations"]["nodes"]
    for operation in operations:
        if operation.get("status") == "COMPLETED" and operation.get("url"):
            print(f"Reusing completed bulk operation {operation['id']}", flush=True)
            return operation
    raise RuntimeError("No reusable completed Shopify bulk operation found")


def bulk_catalog_products(api: Shopify, reuse_latest: bool = False) -> tuple[list[tuple[Candidate, dict]], dict]:
    catalog = catalog_metadata(api)
    operation = latest_completed_bulk(api) if reuse_latest else run_bulk_query(api)
    rows, report = load_bulk_products(operation["url"], catalog["included_products"])
    catalog.update(report)
    catalog["bulk_operation"] = {
        key: operation.get(key)
        for key in ("id", "status", "objectCount", "rootObjectCount", "fileSize")
    }
    return rows, catalog


def catalog_candidates(api: Shopify) -> tuple[list[Candidate], dict]:
    after = None
    candidates: list[Candidate] = []
    skipped = {"inactive": 0, "unapproved_supplier": 0, f"missing_{LOCALE}": 0}
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
            handle_translations = {
                row["key"]: clean_text(row.get("value"))
                for row in product.get("handleTranslations", [])
            }
            if (
                not REQUIRED_TRANSLATIONS.issubset({key for key, value in translations.items() if value})
                or not handle_translations.get("handle")
            ):
                skipped[f"missing_{LOCALE}"] += 1
                continue
            handle = handle_translations["handle"]
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", handle):
                raise RuntimeError(f"Unsafe {LOCALE} handle for {product['id']}")
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
    url = f"{STORE}{PATH_PREFIX}/products/{quoted}.js"
    request = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    })
    last_error = "unknown"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                product = json.load(response)
            if int(product.get("id", 0)) != candidate.product_id:
                raise ValueError(f"{TARGET_NAME} handle resolved to another product")
            if product.get("handle") != candidate.handle or not product.get("published_at"):
                raise ValueError(f"{TARGET_NAME} market publication mismatch")
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


def add_product(
    channel: ET.Element,
    candidate: Candidate,
    product: dict,
    report: dict | None = None,
) -> int:
    title = clean_text(product.get("title"), 150)
    description = clean_text(product.get("description"), 5000)
    product_type = clean_text(product.get("type"))
    images = [https_url(value) for value in product.get("images", []) if value]
    if not title or not description or not product_type or not images:
        missing = [
            name
            for name, value in (
                ("title", title),
                ("description", description),
                ("product_type", product_type),
                ("images", images),
            )
            if not value
        ]
        raise ValueError("Missing required feed content: " + ",".join(missing))
    option_names = [
        clean_text(value.get("name") if isinstance(value, dict) else value).casefold()
        for value in product.get("options", [])
    ]
    count = 0
    for variant in product["variants"]:
        quantity = variant.get("inventory_quantity")
        # Never treat missing inventory data (including old bulk exports) as
        # zero: stop publication rather than silently emptying a live feed.
        if type(quantity) is not int:
            raise ValueError("Missing or invalid variant inventory quantity")
        if quantity <= 0 or not variant.get("available"):
            if report is not None:
                report["excluded_unavailable_items"] = report.get("excluded_unavailable_items", 0) + 1
            continue
        variant_id = int(variant["id"])
        price = int(variant["price"])
        if price <= 0:
            raise ValueError("Non-positive price")
        try:
            grams = variant_weight_grams(variant)
        except ValueError:
            grams = 1000
            if report is not None:
                report["weight_fallback_items"] = report.get("weight_fallback_items", 0) + 1
                by_supplier = report.setdefault("weight_fallback_by_supplier", {})
                by_supplier[candidate.supplier] = by_supplier.get(candidate.supplier, 0) + 1
        item = ET.SubElement(channel, "item")
        child(item, "id", f"shopify_{COUNTRY}_{candidate.product_id}_{variant_id}")
        child(item, "title", title)
        child(item, "description", description)
        child(item, "link", f"{STORE}{PATH_PREFIX}/products/{candidate.handle}?variant={variant_id}")
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


def build(
    secret: str,
    out: Path,
    summary_path: Path,
    workers: int,
    minimum_products: int,
    reuse_latest_bulk: bool = False,
) -> dict:
    started = time.time()
    api = Shopify(secret)
    successes, catalog = bulk_catalog_products(api, reuse_latest=reuse_latest_bulk)
    if len(successes) < minimum_products:
        raise RuntimeError(f"Safety stop: only {len(successes)} eligible Market products")
    previous = previous_product_count(summary_path)
    if previous and len(successes) < previous * 0.90:
        raise RuntimeError("Safety stop: Market product count fell by more than 10%")
    successes.sort(key=lambda row: row[0].product_id)

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = FEED_TITLE
    ET.SubElement(channel, "link").text = f"{STORE}{PATH_PREFIX}/"
    ET.SubElement(channel, "description").text = FEED_DESCRIPTION
    item_count = 0
    suppliers = {"NovaEngel": 0, "Royal Textile": 0}
    built_products = 0
    build_failures: list[dict] = []
    fallback_report = {
        "excluded_unavailable_items": 0,
        "excluded_unavailable_products": 0,
        "weight_fallback_items": 0,
        "weight_fallback_by_supplier": {"NovaEngel": 0, "Royal Textile": 0},
    }
    for candidate, product in successes:
        previous_children = len(channel)
        try:
            added = add_product(channel, candidate, product, fallback_report)
            item_count += added
        except (KeyError, TypeError, ValueError) as exc:
            del channel[previous_children:]
            build_failures.append({
                "product_id": candidate.product_id,
                "handle": candidate.handle,
                "supplier": candidate.supplier,
                "error": str(exc),
            })
            continue
        if added == 0:
            fallback_report["excluded_unavailable_products"] += 1
            continue
        suppliers[candidate.supplier] += 1
        built_products += 1
    missing_image_failures = [
        row for row in build_failures
        if row["error"] == "Missing required feed content: images"
    ]
    unexpected_failures = [
        row for row in build_failures
        if row["error"] != "Missing required feed content: images"
    ]
    if unexpected_failures or len(missing_image_failures) > round(len(successes) * 0.02):
        print(json.dumps({
            "build_failure_counts": Counter(row["error"] for row in build_failures),
            "build_failure_samples": build_failures[:20],
        }, ensure_ascii=False, indent=2), flush=True)
        raise RuntimeError(f"Safety stop: {len(build_failures)} product build failures")
    if not all(suppliers.values()):
        raise RuntimeError("Both approved suppliers must be present")

    xml_bytes = ET.tostring(rss, encoding="utf-8", xml_declaration=True)
    parsed = ET.fromstring(xml_bytes)
    items = parsed.findall("./channel/item")
    ids = [item.find(f"{{{G}}}id").text for item in items]
    links = [item.find(f"{{{G}}}link").text for item in items]
    if len(items) != item_count or len(ids) != len(set(ids)):
        raise RuntimeError("Final item validation failed")
    if any(not value.startswith(f"shopify_{COUNTRY}_") for value in ids):
        raise RuntimeError("Unexpected offer ID prefix")
    if any(not value.startswith(f"{STORE}{PATH_PREFIX}/products/") for value in links):
        raise RuntimeError("Unexpected landing page locale")
    if len(parsed.findall(f"./channel/item/{{{G}}}shipping")) != item_count:
        raise RuntimeError("Shipping is missing from one or more offers")
    if any(item.findtext(f"{{{G}}}availability") != "in_stock" for item in items):
        raise RuntimeError("Unavailable variant leaked into Finnmart feed")

    summary = {
        "status": "validated",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_seconds": round(time.time() - started, 1),
        "shop": SHOP,
        "market_id": MARKET_ID,
        **catalog,
        "eligible_products": len(successes),
        "products": built_products,
        "items": item_count,
        "suppliers": suppliers,
        "build_failures": build_failures,
        "excluded_missing_images": len(missing_image_failures),
        **fallback_report,
        "locale": LOCALE,
        "country": COUNTRY,
        "inventory_filter": "inventoryQuantity > 0 and availableForSale",
        "shipping": {
            "service": SHIPPING_SERVICE,
            "delivery_days": f"{SHIPPING_MIN_DAYS}-{SHIPPING_MAX_DAYS}",
            "weight_bands": [
                {"maximum_grams": maximum, "price": price}
                for maximum, price in SHIPPING_BANDS
            ],
        },
        "xml_sha256": hashlib.sha256(xml_bytes).hexdigest(),
    }
    atomic_write(out, xml_bytes)
    atomic_write(summary_path, json.dumps(summary, ensure_ascii=False, indent=2).encode())
    return summary


def diagnose(secret: str, workers: int, limit: int) -> dict:
    """Inspect a bounded, evenly distributed storefront sample without writing a feed."""
    api = Shopify(secret)
    candidates, catalog = catalog_candidates(api)
    if not candidates:
        raise RuntimeError(f"Catalog has no eligible {LOCALE} products")
    sample_size = min(limit, len(candidates))
    if sample_size == 1:
        sample = [candidates[0]]
    else:
        sample = [
            candidates[round(index * (len(candidates) - 1) / (sample_size - 1))]
            for index in range(sample_size)
        ]
    successes = 0
    failures: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {pool.submit(fetch_product, candidate): candidate for candidate in sample}
        for future in concurrent.futures.as_completed(future_map):
            candidate = future_map[future]
            try:
                future.result()
                successes += 1
            except Exception as exc:
                failures.append({
                    "product_id": candidate.product_id,
                    "handle": candidate.handle,
                    "supplier": candidate.supplier,
                    "error": str(exc),
                })
    result = {
        "catalog": catalog,
        "eligible_products": len(candidates),
        "sampled": sample_size,
        "successes": successes,
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def bulk_probe(secret: str) -> dict:
    api = Shopify(secret)
    data = api.call(BULK_PRODUCT_PROBE_QUERY, {
        "publicationId": "gid://shopify/Publication/327947911492",
    })
    products = data.get("publication", {}).get("includedProducts", {}).get("nodes", [])
    if len(products) != 1:
        raise RuntimeError("Bulk field probe did not return exactly one product")
    product = products[0]
    translations = {row["key"]: bool(clean_text(row.get("value"))) for row in product["translations"]}
    result = {
        "product_id": product["id"],
        "status": product["status"],
        "translations": translations,
        "media_count": len(product["media"]["edges"]),
        "variant_count_in_probe": len(product["variants"]["edges"]),
        "variant_sample": [row["node"] for row in product["variants"]["edges"][:1]],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def recent_bulk_status(secret: str) -> list[dict]:
    api = Shopify(secret)
    operations = api.call(RECENT_BULK_QUERY, {})["bulkOperations"]["nodes"]
    print(json.dumps(operations, ensure_ascii=False, indent=2))
    return operations


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=sorted(TARGETS), default="ES")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--minimum-products", type=int, default=10_000)
    parser.add_argument("--diagnose-limit", type=int, default=0)
    parser.add_argument("--bulk-probe", action="store_true")
    parser.add_argument("--bulk-status", action="store_true")
    parser.add_argument("--reuse-latest-bulk", action="store_true")
    args = parser.parse_args()
    configure_target(args.target)
    out = args.out or DEFAULT_OUT
    summary_path = args.summary or DEFAULT_SUMMARY
    if not 1 <= args.workers <= 32:
        raise SystemExit("workers must be between 1 and 32")
    secret = os.environ.get("SHOPIFY_CLIENT_SECRET", "")
    if args.bulk_probe:
        bulk_probe(secret)
        return
    if args.bulk_status:
        recent_bulk_status(secret)
        return
    if args.diagnose_limit:
        if args.diagnose_limit < 1:
            raise SystemExit("diagnose limit must be positive")
        diagnose(secret, args.workers, args.diagnose_limit)
        return
    result = build(
        secret,
        out,
        summary_path,
        args.workers,
        args.minimum_products,
        reuse_latest_bulk=args.reuse_latest_bulk,
    )
    print(json.dumps({
        key: result[key]
        for key in ("status", "products", "items", "suppliers", "xml_sha256", "duration_seconds")
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
