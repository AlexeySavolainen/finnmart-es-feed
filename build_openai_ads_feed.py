#!/usr/bin/env python3
"""Convert the validated Finland GMC XML into an OpenAI Ads CSV.gz pilot.

The source XML remains the Merchant Center source of truth.  This converter
creates a separate, plain-text OpenAI product feed and never modifies Shopify
or the GMC XML.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import tempfile
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

from sync_feed import G, atomic_write, clean_text, valid_gtin


STORE = "https://vuodevaatteet.fi"
SELLER_NAME = "Vuodevaatteet.fi"
COUNTRY = "FI"
DEFAULT_LIMIT = 100
REQUIRED_FIELDS = (
    "item_id", "title", "description", "url", "brand", "image_url",
    "price", "availability", "seller_name", "is_ads_eligible",
)
FIELDNAMES = (
    *REQUIRED_FIELDS,
    "seller_url", "group_id", "gtin", "mpn", "condition",
    "product_category", "color", "size", "material", "sale_price",
    "target_countries", "store_country", "is_eligible_checkout",
    "ads_metadata",
)


def field(element: ET.Element, name: str) -> str:
    return clean_text(element.findtext(f"{{{G}}}{name}"))


def valid_public_url(value: str, expected_host: str | None = None) -> bool:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        return False
    return expected_host is None or parsed.hostname == expected_host


def item_to_row(item: ET.Element) -> dict[str, str]:
    availability = field(item, "availability")
    if availability not in {"in_stock", "out_of_stock", "pre_order", "backorder"}:
        raise ValueError("unsupported availability")
    item_id = field(item, "id")
    title = clean_text(field(item, "title"), 150)
    description = clean_text(item.findtext(f"{{{G}}}description"), 5000)
    url = field(item, "link")
    image_url = field(item, "image_link")
    brand = field(item, "brand")
    price = field(item, "price")
    if not all((item_id, title, description, url, brand, image_url, price)):
        raise ValueError("missing required source field")
    if not valid_public_url(url, "vuodevaatteet.fi"):
        raise ValueError("invalid product URL")
    if not valid_public_url(image_url):
        raise ValueError("invalid image URL")

    gtin = field(item, "gtin")
    mpn = field(item, "mpn")
    identifier_exists = field(item, "identifier_exists")
    if gtin and not valid_gtin(gtin):
        raise ValueError("invalid GTIN")
    if identifier_exists != "no" and not (gtin or mpn):
        raise ValueError("missing product identifier")

    labels = {
        f"custom_label_{index}": value
        for index in range(5)
        if (value := field(item, f"custom_label_{index}"))
    }
    return {
        "item_id": item_id,
        "title": title,
        "description": description,
        "url": url,
        "brand": brand,
        "image_url": image_url,
        "price": price,
        "availability": availability,
        "seller_name": SELLER_NAME,
        "is_ads_eligible": "true" if availability == "in_stock" else "false",
        "seller_url": STORE,
        "group_id": field(item, "item_group_id"),
        "gtin": gtin,
        "mpn": mpn,
        "condition": field(item, "condition") or "new",
        "product_category": field(item, "product_type") or field(item, "google_product_category"),
        "color": field(item, "color"),
        "size": field(item, "size"),
        "material": field(item, "material"),
        "sale_price": field(item, "sale_price"),
        "target_countries": json.dumps([COUNTRY], separators=(",", ":")),
        "store_country": COUNTRY,
        "is_eligible_checkout": "false",
        "ads_metadata": json.dumps(labels, ensure_ascii=False, separators=(",", ":")),
    }


def iter_rows(xml_path: Path, limit: int) -> tuple[list[dict[str, str]], dict]:
    rows: list[dict[str, str]] = []
    report = {"items_scanned": 0, "out_of_stock_skipped": 0, "invalid_items": 0}
    for _, element in ET.iterparse(xml_path, events=("end",)):
        if element.tag != "item":
            continue
        report["items_scanned"] += 1
        try:
            row = item_to_row(element)
        except ValueError:
            report["invalid_items"] += 1
            element.clear()
            continue
        element.clear()
        if row["availability"] != "in_stock":
            report["out_of_stock_skipped"] += 1
            continue
        rows.append(row)
        if limit and len(rows) >= limit:
            break
    return rows, report


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate(path: Path, expected_rows: int) -> dict:
    seen: set[str] = set()
    count = 0
    with gzip.open(path, "rt", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != FIELDNAMES:
            raise RuntimeError("OpenAI Ads CSV header mismatch")
        for row in reader:
            missing = [key for key in REQUIRED_FIELDS if not row.get(key)]
            if missing:
                raise RuntimeError("OpenAI Ads row misses: " + ",".join(missing))
            if row["item_id"] in seen:
                raise RuntimeError("Duplicate OpenAI Ads item_id")
            if row["availability"] != "in_stock" or row["is_ads_eligible"] != "true":
                raise RuntimeError("Pilot contains an ineligible item")
            if not valid_public_url(row["url"], "vuodevaatteet.fi"):
                raise RuntimeError("Pilot contains an invalid landing page")
            seen.add(row["item_id"])
            count += 1
    if count != expected_rows:
        raise RuntimeError(f"OpenAI Ads row mismatch: expected {expected_rows}, got {count}")
    return {"validated_rows": count, "unique_item_ids": len(seen)}


def build(xml_path: Path, out: Path, summary_path: Path, limit: int) -> dict:
    started = time.time()
    if limit < 1:
        raise ValueError("Pilot limit must be positive")
    rows, scan_report = iter_rows(xml_path, limit)
    if len(rows) != limit:
        raise RuntimeError(f"Only {len(rows)} eligible rows found; expected {limit}")

    out.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=out.name, dir=out.parent)
    os.close(handle)
    temporary_path = Path(temporary_name)
    try:
        with gzip.open(temporary_path, "wt", encoding="utf-8", newline="", compresslevel=9) as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDNAMES, extrasaction="raise")
            writer.writeheader()
            writer.writerows(rows)
        validation = validate(temporary_path, len(rows))
        digest = sha256(temporary_path)
        temporary_path.replace(out)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()

    summary = {
        "status": "validated-openai-ads-pilot",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": str(xml_path),
        "country": COUNTRY,
        "currency": "EUR",
        "format": "openai-native-csv-gzip",
        "pilot_limit": limit,
        **scan_report,
        **validation,
        "gzip_bytes": out.stat().st_size,
        "gzip_sha256": digest,
        "duration_seconds": round(time.time() - started, 1),
    }
    atomic_write(summary_path, json.dumps(summary, ensure_ascii=False, indent=2).encode())
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input", type=Path,
        default=Path("public/vuodevaatteet-fi-production-candidate.xml"),
    )
    parser.add_argument(
        "--out", type=Path,
        default=Path("public/vuodevaatteet-fi-openai-ads-test.csv.gz"),
    )
    parser.add_argument(
        "--summary", type=Path,
        default=Path("public/vuodevaatteet-fi-openai-ads-test-summary.json"),
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args()
    result = build(args.input, args.out, args.summary, args.limit)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
