#!/usr/bin/env python3
"""Build an isolated 100-product Finland feed pilot from Shopify.

The pilot mirrors the field set currently used by Simprosys but uses distinct
offer IDs and excludes every offer from ads and free listings. It is safe to
import beside the existing live API source for diagnostics.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sync_feed import G, Shopify, atomic_write, child, clean_text, https_url, valid_gtin


STORE = "https://vuodevaatteet.fi"
COUNTRY = "FI"
CURRENCY = "EUR"
PILOT_PRODUCTS = 100
SAMPLE_QUOTAS = {"NovaEngel": 34, "Royal Textile": 33, "VidaXL": 33}
PRODUCT_QUERIES = {
    "NovaEngel": 'status:active AND published_status:published AND tag:"Nova Engel"',
    "Royal Textile": 'status:active AND published_status:published AND tag:"Royal Textile"',
    "VidaXL": 'status:active AND published_status:published AND tag:"vida-xl"',
}
ROYAL_SAMPLE_QUERIES = (
    ('status:active AND published_status:published AND tag:"New Royal Textile"', 17),
    ('status:active AND published_status:published AND tag:"Old Royal Textile"', 16),
)
EXCLUDED_DESTINATIONS = ("Shopping_ads", "Display_ads", "Free_listings")
GOOGLE_NAMESPACE = "mm-google-shopping"
LABEL_KEYS = tuple(f"custom_label_{index}" for index in range(5))
OPTION_FIELDS = {
    "color": {"color", "colour", "väri"},
    "size": {"size", "koko"},
    "material": {"material", "materiaali"},
    "pattern": {"pattern", "kuvio"},
}
PRODUCT_TYPE_CATEGORY_FALLBACKS = {
    "parfyymit ja partavedet": "479",
}
PASSTHROUGH_FIELDS = (
    "age_group",
    "gender",
    "size_system",
    "size_type",
    "shipping_label",
    "return_policy_label",
    "condition",
    "adult",
    "color",
    "size",
    "material",
    "pattern",
)


PRODUCT_QUERY = """query FinlandPilotProducts($first: Int!, $query: String!) {
  products(first: $first, query: $query, sortKey: ID) {
    nodes {
      id
      legacyResourceId
      title
      handle
      descriptionHtml
      status
      vendor
      productType
      tags
      category { id name fullName }
      media(first: 11) {
        nodes {
          __typename
          ... on MediaImage { image { url altText } }
        }
      }
      metafields(first: 100) {
        nodes { namespace key type value }
      }
      variants(first: 100) {
        pageInfo { hasNextPage }
        nodes {
          id
          legacyResourceId
          title
          sku
          barcode
          price
          compareAtPrice
          availableForSale
          selectedOptions { name value }
          inventoryItem {
            unitCost { amount currencyCode }
            measurement { weight { value unit } }
          }
        }
      }
    }
  }
}
"""


def decimal_money(value: object) -> Decimal | None:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return amount if amount > 0 else None


def money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'))} {CURRENCY}"


def numeric_gid(value: str) -> int:
    number = value.rsplit("/", 1)[-1]
    if not number.isdigit():
        raise ValueError("Invalid Shopify GID")
    return int(number)


def google_metafields(product: dict) -> dict[str, str]:
    values: dict[str, str] = {}
    for row in product.get("metafields", {}).get("nodes", []):
        if row.get("namespace") != GOOGLE_NAMESPACE:
            continue
        value = clean_text(row.get("value"))
        if value:
            values[clean_text(row.get("key"))] = value
    return values


def product_images(product: dict) -> list[str]:
    images = []
    for row in product.get("media", {}).get("nodes", []):
        value = row.get("image", {}).get("url") if row.get("__typename") == "MediaImage" else None
        if value:
            images.append(https_url(value))
    return images


def shipping_weight(variant: dict) -> tuple[str, bool]:
    weight = variant.get("inventoryItem", {}).get("measurement", {}).get("weight")
    units = {"GRAMS": "g", "KILOGRAMS": "kg", "OUNCES": "oz", "POUNDS": "lb"}
    if weight:
        amount = decimal_money(weight.get("value"))
        unit = units.get(weight.get("unit"))
        if amount and unit:
            return f"{format(amount.normalize(), 'f')} {unit}", False
    return "1 kg", True


def variant_title(product_title: str, variant: dict) -> str:
    values = [
        clean_text(row.get("value"))
        for row in variant.get("selectedOptions", [])
        if clean_text(row.get("value")).casefold() != "default title"
    ]
    suffix = ", ".join(value for value in values if value)
    return clean_text(f"{product_title}, {suffix}" if suffix else product_title, 150)


def add_option_fields(item: ET.Element, variant: dict, metafields: dict[str, str]) -> None:
    emitted: set[str] = set()
    for option in variant.get("selectedOptions", []):
        name = clean_text(option.get("name")).casefold()
        value = clean_text(option.get("value"))
        if not value or value.casefold() == "default title":
            continue
        for field, names in OPTION_FIELDS.items():
            if name in names and field not in emitted:
                child(item, field, value)
                emitted.add(field)
    for field in OPTION_FIELDS:
        if field not in emitted and metafields.get(field):
            child(item, field, metafields[field])


def add_product(channel: ET.Element, supplier: str, product: dict, report: dict) -> int:
    product_id = numeric_gid(product["id"])
    title = clean_text(product.get("title"), 150)
    description = product.get("descriptionHtml") or ""
    description_text = clean_text(description)
    product_type = clean_text(product.get("productType"))
    handle = clean_text(product.get("handle"))
    vendor = clean_text(product.get("vendor")) or supplier
    images = product_images(product)
    if not all((title, description_text, product_type, handle, images)):
        raise ValueError("missing title, description, type, handle, or image")
    variants = product.get("variants", {})
    if variants.get("pageInfo", {}).get("hasNextPage"):
        raise ValueError("product has more than 100 variants")
    if not variants.get("nodes"):
        raise ValueError("product has no variants")

    encoded_handle = urllib.parse.quote(handle, safe="-._~")
    canonical = f"{STORE}/products/{encoded_handle}"
    metafields = google_metafields(product)
    category = clean_text((product.get("category") or {}).get("fullName"))
    category = category or metafields.get("google_product_category", "")
    category = category or PRODUCT_TYPE_CATEGORY_FALLBACKS.get(product_type.casefold(), "")
    item_count = 0
    for variant in variants["nodes"]:
        variant_id = numeric_gid(variant["id"])
        current = decimal_money(variant.get("price"))
        if not current:
            raise ValueError("variant has no positive price")
        compare_at = decimal_money(variant.get("compareAtPrice"))
        item = ET.SubElement(channel, "item")
        child(item, "id", f"pilot_FI_{product_id}_{variant_id}")
        child(item, "title", variant_title(title, variant))
        child(item, "description", description)
        query = urllib.parse.urlencode({
            "currency": CURRENCY,
            "country": COUNTRY,
            "variant": str(variant_id),
            "utm_source": "google",
            "utm_medium": "cpc",
            "utm_campaign": "FI Own Feed Pilot",
        })
        child(item, "link", f"{canonical}?{query}")
        child(item, "canonical_link", canonical)
        child(item, "image_link", images[0])
        for image in images[1:11]:
            child(item, "additional_image_link", image)
        child(item, "availability", "in_stock" if variant.get("availableForSale") else "out_of_stock")
        if compare_at and compare_at > current:
            child(item, "price", money(compare_at))
            child(item, "sale_price", money(current))
            report["sale_price_items"] += 1
        else:
            child(item, "price", money(current))
        child(item, "condition", metafields.get("condition", "new"))
        child(item, "brand", vendor)
        child(item, "item_group_id", f"pilot_FI_{product_id}")
        child(item, "product_type", product_type)
        if category:
            child(item, "google_product_category", category)
            report["category_items"] += 1

        sku = clean_text(variant.get("sku"))
        barcode = clean_text(variant.get("barcode"))
        if valid_gtin(barcode):
            child(item, "gtin", barcode)
            report["gtin_items"] += 1
        if sku:
            child(item, "mpn", sku)
            report["mpn_items"] += 1
        child(item, "identifier_exists", "yes" if valid_gtin(barcode) or sku else "no")

        weight, fallback = shipping_weight(variant)
        child(item, "shipping_weight", weight)
        if fallback:
            report["weight_fallback_items"] += 1
        unit_cost = variant.get("inventoryItem", {}).get("unitCost") or {}
        cost = decimal_money(unit_cost.get("amount"))
        if cost and unit_cost.get("currencyCode") == CURRENCY:
            child(item, "cost_of_goods_sold", money(cost))
            report["cogs_items"] += 1

        add_option_fields(item, variant, metafields)
        for key in LABEL_KEYS:
            if metafields.get(key):
                child(item, key, metafields[key])
                report["labels"][key][metafields[key]] += 1
        for key in PASSTHROUGH_FIELDS:
            if key not in OPTION_FIELDS and key != "condition" and metafields.get(key):
                child(item, key, metafields[key])
        for destination in EXCLUDED_DESTINATIONS:
            child(item, "excluded_destination", destination)
        item_count += 1
    return item_count


def fetch_query_products(api: Shopify, query: str, quota: int) -> tuple[list[dict], list[dict]]:
    data = api.call(PRODUCT_QUERY, {"first": min(250, quota + 40), "query": query})
    selected: list[dict] = []
    skipped: list[dict] = []
    for product in data["products"]["nodes"]:
        try:
            if product.get("status") != "ACTIVE":
                raise ValueError("not active")
            if not product_images(product):
                raise ValueError("no image")
            if not clean_text(product.get("descriptionHtml")):
                raise ValueError("no description")
            if product.get("variants", {}).get("pageInfo", {}).get("hasNextPage"):
                raise ValueError("more than 100 variants")
        except ValueError as exc:
            skipped.append({"product_id": product.get("id"), "error": str(exc)})
            continue
        selected.append(product)
        if len(selected) == quota:
            break
    if len(selected) != quota:
        raise RuntimeError(f"Unable to select {quota} valid products for query: {query}")
    return selected, skipped


def fetch_supplier_products(api: Shopify, supplier: str, quota: int) -> tuple[list[dict], list[dict]]:
    if supplier != "Royal Textile":
        return fetch_query_products(api, PRODUCT_QUERIES[supplier], quota)
    selected: list[dict] = []
    skipped: list[dict] = []
    for query, subquota in ROYAL_SAMPLE_QUERIES:
        products, query_skipped = fetch_query_products(api, query, subquota)
        selected.extend(products)
        skipped.extend(query_skipped)
    if len(selected) != quota or len({row["id"] for row in selected}) != quota:
        raise RuntimeError("Royal pilot sample is incomplete or duplicated")
    return selected, skipped


def build(secret: str, out: Path, summary_path: Path, product_limit: int = PILOT_PRODUCTS) -> dict:
    if product_limit != PILOT_PRODUCTS:
        raise ValueError("The isolated pilot is fixed at exactly 100 products")
    started = time.time()
    api = Shopify(secret)
    selected: list[tuple[str, dict]] = []
    skipped: dict[str, list[dict]] = {}
    for supplier, quota in SAMPLE_QUOTAS.items():
        products, supplier_skipped = fetch_supplier_products(api, supplier, quota)
        selected.extend((supplier, product) for product in products)
        skipped[supplier] = supplier_skipped
    selected.sort(key=lambda row: numeric_gid(row[1]["id"]))
    if len(selected) != PILOT_PRODUCTS:
        raise RuntimeError("Pilot selection is not exactly 100 products")

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "Vuodevaatteet.fi Finland own-feed pilot"
    ET.SubElement(channel, "link").text = STORE
    ET.SubElement(channel, "description").text = "Isolated 100-product Finland Merchant feed test"
    report = {
        "sale_price_items": 0,
        "category_items": 0,
        "gtin_items": 0,
        "mpn_items": 0,
        "cogs_items": 0,
        "weight_fallback_items": 0,
        "labels": {key: Counter() for key in LABEL_KEYS},
    }
    items = 0
    suppliers = Counter()
    for supplier, product in selected:
        before = len(channel)
        try:
            count = add_product(channel, supplier, product, report)
        except (KeyError, TypeError, ValueError) as exc:
            del channel[before:]
            raise RuntimeError(f"Pilot build failed for {product.get('id')}: {exc}") from None
        suppliers[supplier] += 1
        items += count

    xml_bytes = ET.tostring(rss, encoding="utf-8", xml_declaration=True)
    parsed = ET.fromstring(xml_bytes)
    parsed_items = parsed.findall("./channel/item")
    ids = [node.find(f"{{{G}}}id").text for node in parsed_items]
    if len(parsed_items) != items or len(ids) != len(set(ids)):
        raise RuntimeError("Duplicate or missing pilot items")
    if any(not value.startswith("pilot_FI_") for value in ids):
        raise RuntimeError("Pilot ID isolation failed")
    for destination in EXCLUDED_DESTINATIONS:
        if len(parsed.findall(f"./channel/item/{{{G}}}excluded_destination[.='{destination}']")) != items:
            raise RuntimeError(f"Pilot destination exclusion failed: {destination}")

    summary = {
        "status": "validated",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_seconds": round(time.time() - started, 1),
        "shop": "b38c32.myshopify.com",
        "country": COUNTRY,
        "language": "fi",
        "feed_label": "FI-PILOT",
        "products": len(selected),
        "items": items,
        "sample_quotas": dict(SAMPLE_QUOTAS),
        "suppliers": dict(suppliers),
        "excluded_destinations": list(EXCLUDED_DESTINATIONS),
        "skipped_candidates": skipped,
        **{key: value for key, value in report.items() if key != "labels"},
        "labels": {
            key: {value: count for value, count in counts.most_common()}
            for key, counts in report["labels"].items()
        },
        "xml_sha256": hashlib.sha256(xml_bytes).hexdigest(),
    }
    atomic_write(out, xml_bytes)
    atomic_write(summary_path, json.dumps(summary, ensure_ascii=False, indent=2).encode())
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("public/vuodevaatteet-fi-pilot.xml"))
    parser.add_argument("--summary", type=Path, default=Path("public/vuodevaatteet-fi-pilot-summary.json"))
    args = parser.parse_args()
    result = build(os.environ.get("SHOPIFY_CLIENT_SECRET", ""), args.out, args.summary)
    print(json.dumps({key: result[key] for key in ("status", "products", "items", "suppliers", "xml_sha256")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
