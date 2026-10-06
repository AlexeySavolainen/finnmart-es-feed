import csv
import gzip
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import build_openai_ads_feed as feed
from sync_feed import G, child


class OpenAIAdsFeedTests(unittest.TestCase):
    def item(self, availability="in_stock"):
        item = ET.Element("item")
        values = {
            "id": "shopify_FI_123_456",
            "title": "Peitto, 140 x 200",
            "description": "<p>Lämmin &amp; pehmeä peitto.</p>",
            "link": "https://vuodevaatteet.fi/products/peitto?variant=456",
            "image_link": "https://cdn.example.test/peitto.jpg",
            "availability": availability,
            "price": "81.00 EUR",
            "sale_price": "30.85 EUR",
            "brand": "Sleeptime",
            "item_group_id": "shopify_FI_123",
            "gtin": "4006381333931",
            "mpn": "SKU-1",
            "identifier_exists": "yes",
            "condition": "new",
            "product_type": "Peitot",
            "size": "140 x 200",
            "custom_label_0": "New Royal Textile",
            "custom_label_3": "ROYAL-PROFIT",
        }
        for key, value in values.items():
            child(item, key, value)
        return item

    def test_item_mapping_is_plain_text_and_ads_eligible(self):
        row = feed.item_to_row(self.item())
        self.assertEqual(row["item_id"], "shopify_FI_123_456")
        self.assertEqual(row["description"], "Lämmin & pehmeä peitto.")
        self.assertEqual(row["is_ads_eligible"], "true")
        self.assertEqual(row["target_countries"], '["FI"]')
        self.assertEqual(row["is_eligible_checkout"], "false")
        self.assertEqual(
            json.loads(row["ads_metadata"]),
            {"custom_label_0": "New Royal Textile", "custom_label_3": "ROYAL-PROFIT"},
        )

    def test_out_of_stock_item_is_not_ads_eligible(self):
        row = feed.item_to_row(self.item("out_of_stock"))
        self.assertEqual(row["is_ads_eligible"], "false")

    def test_build_publishes_exactly_100_in_stock_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = ET.Element("rss")
            channel = ET.SubElement(root, "channel")
            for index in range(105):
                item = self.item("out_of_stock" if index < 3 else "in_stock")
                item.find(f"{{{G}}}id").text = f"shopify_FI_123_{index}"
                channel.append(item)
            xml_path = Path(directory) / "source.xml"
            ET.ElementTree(root).write(xml_path, encoding="utf-8", xml_declaration=True)
            out = Path(directory) / "pilot.csv.gz"
            summary = Path(directory) / "summary.json"
            result = feed.build(xml_path, out, summary, 100)

            self.assertEqual(result["validated_rows"], 100)
            self.assertEqual(result["out_of_stock_skipped"], 3)
            with gzip.open(out, "rt", encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 100)
            self.assertTrue(all(row["availability"] == "in_stock" for row in rows))
            self.assertEqual(json.loads(summary.read_text())["status"], "validated-openai-ads-pilot")


if __name__ == "__main__":
    unittest.main()
