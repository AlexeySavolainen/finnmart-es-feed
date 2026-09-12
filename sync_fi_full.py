#!/usr/bin/env python3
"""Build the complete Vuodevaatteet.fi Finland production feed candidate.

Shopify is read-only. The generated candidate is not connected to Merchant
Center automatically. Products are streamed from a Shopify Bulk GraphQL JSONL
result and the XML is written atomically so a failed run cannot publish a
partial feed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sync_feed import (
    G,
    BULK_RUN_MUTATION,
    BULK_STATUS_QUERY,
    Shopify,
    atomic_write,
    child,
    clean_text,
    https_url,
    numeric_gid,
    valid_gtin,
)
from sync_fi_pilot import (
    GOOGLE_NAMESPACE,
    LABEL_KEYS,
    OPTION_FIELDS,
    PASSTHROUGH_FIELDS,
    PRODUCT_TYPE_CATEGORY_FALLBACKS,
    decimal_money,
    money,
    shipping_weight,
    variant_google_labels,
    variant_title,
)


STORE = "https://vuodevaatteet.fi"
COUNTRY = "FI"
CURRENCY = "EUR"
LANGUAGE = "fi"
FEED_LABEL = "FI"
PRODUCT_SEARCH = "status:active AND published_status:published"
MINIMUM_PRODUCTS = 100_000
MAX_FAILURE_RATE = Decimal("0.02")
USER_AGENT = "VuodevaatteetFinlandFeed/1.0"

PRODUCT_META_ALIASES = {
    "googleProductCategory": "google_product_category",
    "conditionMeta": "condition",
    "ageGroupMeta": "age_group",
    "genderMeta": "gender",
    "sizeSystemMeta": "size_system",
    "sizeTypeMeta": "size_type",
    "adultMeta": "adult",
    "colorMeta": "color",
    "sizeMeta": "size",
    "materialMeta": "material",
    "patternMeta": "pattern",
    "shippingLabelMeta": "shipping_label",
    "returnPolicyLabelMeta": "return_policy_label",
    "unitPricingMeasureMeta": "unit_pricing_measure",
    "unitPricingBaseMeasureMeta": "unit_pricing_base_measure",
}


def metafield_selection(alias: str, key: str, indent: str = "      ") -> str:
    return (
        f'{indent}{alias}: metafield(namespace: "{GOOGLE_NAMESPACE}", '
        f'key: "{key}") {{ value }}\n'
    )


def bulk_query_document() -> str:
    product_metafields = "".join(
        metafield_selection(alias, key) for alias, key in PRODUCT_META_ALIASES.items()
    )
    variant_labels = "".join(
        metafield_selection(f"customLabel{index}", key, "          ")
        for index, key in enumerate(LABEL_KEYS)
    )
    return (
        "{\n"
        f'  products(query: "{PRODUCT_SEARCH}") {{\n'
        "    edges { node {\n"
        "      id\n"
        "      __typename\n"
        "      legacyResourceId\n"
        "      title\n"
        "      handle\n"
        "      descriptionHtml\n"
        "      status\n"
        "      vendor\n"
        "      productType\n"
        "      tags\n"
        "      category { id name fullName }\n"
        + product_metafields
        + "      media { edges { node {\n"
        "        __typename\n"
        "        ... on MediaImage { image { url altText } }\n"
        "      } } }\n"
        "      variants { edges { node {\n"
        "          id\n"
        "          __typename\n"
        "          legacyResourceId\n"
        "          title\n"
        "          sku\n"
        "          barcode\n"
        "          price\n"
        "          compareAtPrice\n"
        "          availableForSale\n"
        "          selectedOptions { name value }\n"
        + variant_labels
        + "          inventoryItem {\n"
        "            unitCost { amount currencyCode }\n"
        "            measurement { weight { value unit } }\n"
        "          }\n"
        "      } } }\n"
        "    } }\n"
        "  }\n"
        "}"
    )


def run_bulk_query(api: Shopify) -> dict:
    payload = api.call(BULK_RUN_MUTATION, {"query": bulk_query_document()})
    result = payload["bulkOperationRunQuery"]
    if result.get("userErrors"):
        raise RuntimeError("Shopify rejected FI bulk query: " + json.dumps(result["userErrors"]))
    operation = result.get("bulkOperation")
    if not operation:
        raise RuntimeError("Shopify did not create the FI bulk query")
    operation_id = operation["id"]
    started = time.monotonic()
    last_count = None
    while True:
        operation = api.call(BULK_STATUS_QUERY, {"id": operation_id}).get("bulkOperation")
        if not operation:
            raise RuntimeError("Shopify FI bulk operation disappeared")
        status = operation["status"]
        count = int(operation.get("objectCount") or 0)
        if count != last_count:
            print(f"Finland bulk operation {status}: objects={count}", flush=True)
            last_count = count
        if status == "COMPLETED":
            if not operation.get("url"):
                raise RuntimeError("Completed FI bulk operation has no result URL")
            return operation
        if status in {"FAILED", "CANCELED", "EXPIRED"}:
            raise RuntimeError(
                f"Shopify FI bulk operation {status}: {operation.get('errorCode') or 'unknown error'}"
            )
        if time.monotonic() - started > 120 * 60:
            raise RuntimeError("Shopify FI bulk operation exceeded 120 minutes")
        time.sleep(10)


def product_google_metafields(product: dict) -> dict[str, str]:
    values: dict[str, str] = {}
    for alias, key in PRODUCT_META_ALIASES.items():
        value = clean_text((product.get(alias) or {}).get("value"))
        if value:
            values[key] = value
    return values


def supplier_from_tags(tags: list[str]) -> str:
    values = {clean_text(value).casefold() for value in tags}
    if "nova engel" in values:
        return "NovaEngel"
    if values & {"royal textile", "new royal textile", "old royal textile", "royal"}:
        return "Royal Textile"
    if values & {"vida-xl", "vidaxl", "vida xl"}:
        return "VidaXL"
    return "Other"


def product_images(product: dict) -> list[str]:
    images: list[str] = []
    for value in product.get("_images", []):
        try:
            images.append(https_url(value))
        except ValueError:
            continue
    return images[:11]


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


def build_item(product: dict, variant: dict, report: dict) -> ET.Element:
    product_id = numeric_gid(product["id"])
    variant_id = numeric_gid(variant["id"])
    title = clean_text(product.get("title"), 150)
    handle = clean_text(product.get("handle"))
    description_html = product.get("descriptionHtml") or ""
    description_text = clean_text(description_html)
    images = product_images(product)
    if not title or not handle or not images:
        raise ValueError("missing title, handle, or image")
    if not description_text:
        description_html = f"<p>Osta {title} verkkokaupasta Vuodevaatteet.fi.</p>"
        report["description_fallback_items"] += 1

    current = decimal_money(variant.get("price"))
    if not current:
        raise ValueError("variant has no positive price")
    compare_at = decimal_money(variant.get("compareAtPrice"))
    metafields = product_google_metafields(product)
    category = clean_text((product.get("category") or {}).get("fullName"))
    product_type = clean_text(product.get("productType"))
    category = category or metafields.get("google_product_category", "")
    category = category or PRODUCT_TYPE_CATEGORY_FALLBACKS.get(product_type.casefold(), "")

    item = ET.Element("item")
    child(item, "id", f"shopify_FI_{product_id}_{variant_id}")
    child(item, "title", variant_title(title, variant))
    child(item, "description", description_html)
    encoded_handle = urllib.parse.quote(handle, safe="-._~")
    canonical = f"{STORE}/products/{encoded_handle}"
    child(item, "link", f"{canonical}?variant={variant_id}")
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
    child(item, "brand", clean_text(product.get("vendor")) or "Vuodevaatteet")
    child(item, "item_group_id", f"shopify_FI_{product_id}")
    if product_type:
        child(item, "product_type", product_type)
    if category:
        child(item, "google_product_category", category)
        report["category_items"] += 1

    sku = clean_text(variant.get("sku"))
    barcode = clean_text(variant.get("barcode"))
    if valid_gtin(barcode):
        child(item, "gtin", barcode)
        report["gtin_items"] += 1
    elif barcode:
        report["invalid_gtin_items"] += 1
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
    labels = variant_google_labels(variant)
    for key in LABEL_KEYS:
        value = labels.get(key)
        if value:
            child(item, key, value)
            report["labels"][key][value] += 1
        else:
            report["missing_labels"][key] += 1
    for key in PASSTHROUGH_FIELDS:
        if key not in OPTION_FIELDS and key != "condition" and metafields.get(key):
            child(item, key, metafields[key])
    for key in ("unit_pricing_measure", "unit_pricing_base_measure"):
        if metafields.get(key):
            child(item, key, metafields[key])
    return item


def empty_report() -> dict:
    return {
        "products_seen": 0,
        "products": 0,
        "items": 0,
        "sale_price_items": 0,
        "category_items": 0,
        "gtin_items": 0,
        "invalid_gtin_items": 0,
        "mpn_items": 0,
        "cogs_items": 0,
        "weight_fallback_items": 0,
        "description_fallback_items": 0,
        "labels": {key: Counter() for key in LABEL_KEYS},
        "missing_labels": Counter(),
        "supplier_products": Counter(),
        "supplier_items": Counter(),
        "failure_counts": Counter(),
        "failure_samples": [],
    }


def record_failure(report: dict, product: dict, error: str) -> None:
    report["failure_counts"][error] += 1
    if len(report["failure_samples"]) < 100:
        report["failure_samples"].append({
            "product_id": product.get("id"),
            "handle": product.get("handle"),
            "error": error,
        })


def emit_product(out, product: dict, report: dict) -> None:
    report["products_seen"] += 1
    if product.get("status") != "ACTIVE":
        record_failure(report, product, "bulk search returned non-active product")
        return
    variants = product.get("_variants", [])
    if not variants:
        record_failure(report, product, "product has no variants")
        return
    supplier = supplier_from_tags(product.get("tags", []))
    fragments: list[bytes] = []
    for variant in variants:
        try:
            item = build_item(product, variant, report)
        except (KeyError, TypeError, ValueError) as exc:
            record_failure(report, product, str(exc))
            continue
        fragments.append(ET.tostring(item, encoding="utf-8"))
    if not fragments:
        return
    for fragment in fragments:
        out.write(fragment)
    report["products"] += 1
    report["items"] += len(fragments)
    report["supplier_products"][supplier] += 1
    report["supplier_items"][supplier] += len(fragments)


def stream_bulk_to_xml(url: str, xml_path: Path, report: dict) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        response = urllib.request.urlopen(request, timeout=180)
    except urllib.error.URLError:
        raise RuntimeError("Unable to download Shopify FI bulk result") from None
    with xml_path.open("wb") as out, response:
        out.write(b'<?xml version="1.0" encoding="utf-8"?>')
        out.write(f'<rss xmlns:g="{G}" version="2.0"><channel>'.encode())
        out.write(b"<title>Vuodevaatteet.fi Finland production candidate</title>")
        out.write(b"<link>https://vuodevaatteet.fi</link>")
        out.write(b"<description>Complete Shopify Finland feed candidate</description>")
        current: dict | None = None
        for line_number, raw_line in enumerate(response, 1):
            if len(raw_line) > 16 * 1024 * 1024:
                raise RuntimeError("Oversized Shopify FI bulk JSONL record")
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError:
                raise RuntimeError(f"Invalid Shopify FI bulk JSONL at line {line_number}") from None
            is_product = record.get("__typename") == "Product" or (
                not record.get("__parentId")
                and str(record.get("id", "")).startswith("gid://shopify/Product/")
            )
            if is_product:
                if current is not None:
                    emit_product(out, current, report)
                current = record
                current["_images"] = []
                current["_variants"] = []
                continue
            if current is None or record.get("__parentId") != current.get("id"):
                raise RuntimeError("Unexpected Shopify FI bulk JSONL parent ordering")
            if record.get("__typename") == "MediaImage":
                image_url = (record.get("image") or {}).get("url")
                if image_url and len(current["_images"]) < 11:
                    current["_images"].append(image_url)
            elif record.get("__typename") == "ProductVariant":
                current["_variants"].append(record)
        if current is not None:
            emit_product(out, current, report)
        out.write(b"</channel></rss>")


def validate_xml(path: Path, expected_items: int) -> dict:
    ids: set[str] = set()
    count = 0
    required = {"id", "title", "description", "link", "image_link", "availability", "price"}
    for _, element in ET.iterparse(path, events=("end",)):
        if element.tag != "item":
            continue
        fields = {child_node.tag.rsplit("}", 1)[-1]: child_node.text for child_node in element}
        missing = required - {key for key, value in fields.items() if value}
        if missing:
            raise RuntimeError("Final FI item misses required fields: " + ",".join(sorted(missing)))
        offer_id = fields["id"]
        if not offer_id.startswith("shopify_FI_") or offer_id in ids:
            raise RuntimeError("Duplicate or invalid final FI offer ID")
        if not fields["link"].startswith(f"{STORE}/products/"):
            raise RuntimeError("Unexpected final FI landing page")
        ids.add(offer_id)
        count += 1
        element.clear()
    if count != expected_items:
        raise RuntimeError(f"Final FI XML count mismatch: expected {expected_items}, parsed {count}")
    return {"validated_items": count, "unique_ids": len(ids)}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def previous_product_count(summary_path: Path) -> int | None:
    try:
        return int(json.loads(summary_path.read_text())["products"])
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
        return None


def build(secret: str, out: Path, summary_path: Path, minimum_products: int) -> dict:
    started = time.time()
    api = Shopify(secret)
    operation = run_bulk_query(api)
    root_count = int(operation.get("rootObjectCount") or 0)
    if root_count < minimum_products:
        raise RuntimeError(f"Safety stop: FI bulk returned only {root_count} products")
    previous = previous_product_count(summary_path)
    if previous and root_count < previous * 0.90:
        raise RuntimeError("Safety stop: FI product count fell by more than 10%")

    out.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=out.name, dir=out.parent)
    os.close(handle)
    temporary_path = Path(temporary_name)
    report = empty_report()
    try:
        stream_bulk_to_xml(operation["url"], temporary_path, report)
        if report["products_seen"] != root_count:
            raise RuntimeError(
                f"FI root count mismatch: operation {root_count}, JSONL {report['products_seen']}"
            )
        if report["products"] < minimum_products:
            raise RuntimeError(f"Safety stop: only {report['products']} FI products built")
        failures = sum(report["failure_counts"].values())
        if Decimal(failures) / Decimal(root_count) > MAX_FAILURE_RATE:
            raise RuntimeError(f"Safety stop: {failures} FI product/variant failures")
        validation = validate_xml(temporary_path, report["items"])
        digest = file_sha256(temporary_path)
        temporary_path.replace(out)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()

    labels = {
        key: {value: count for value, count in counts.most_common()}
        for key, counts in report["labels"].items()
    }
    summary = {
        "status": "validated-production-candidate",
        "connected_to_gmc": False,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_seconds": round(time.time() - started, 1),
        "shop": "b38c32.myshopify.com",
        "country": COUNTRY,
        "language": LANGUAGE,
        "feed_label": FEED_LABEL,
        "product_search": PRODUCT_SEARCH,
        "bulk_operation": {
            key: operation.get(key)
            for key in ("id", "status", "objectCount", "rootObjectCount", "fileSize")
        },
        **{key: value for key, value in report.items() if key not in {
            "labels", "missing_labels", "supplier_products", "supplier_items",
            "failure_counts",
        }},
        "labels": labels,
        "missing_labels": dict(report["missing_labels"]),
        "supplier_products": dict(report["supplier_products"]),
        "supplier_items": dict(report["supplier_items"]),
        "failure_counts": dict(report["failure_counts"]),
        **validation,
        "xml_bytes": out.stat().st_size,
        "xml_sha256": digest,
    }
    atomic_write(summary_path, json.dumps(summary, ensure_ascii=False, indent=2).encode())
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out", type=Path, default=Path("public/vuodevaatteet-fi-production-candidate.xml")
    )
    parser.add_argument(
        "--summary", type=Path,
        default=Path("public/vuodevaatteet-fi-production-candidate-summary.json"),
    )
    parser.add_argument("--minimum-products", type=int, default=MINIMUM_PRODUCTS)
    args = parser.parse_args()
    if args.minimum_products < 1:
        raise SystemExit("minimum products must be positive")
    result = build(
        os.environ.get("SHOPIFY_CLIENT_SECRET", ""),
        args.out,
        args.summary,
        args.minimum_products,
    )
    print(json.dumps({
        key: result[key]
        for key in (
            "status", "products", "items", "supplier_products", "supplier_items",
            "weight_fallback_items", "xml_bytes", "xml_sha256", "duration_seconds",
        )
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
