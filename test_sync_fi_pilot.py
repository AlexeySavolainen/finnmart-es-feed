import unittest
import xml.etree.ElementTree as ET

import sync_fi_pilot as feed


class FinlandPilotTests(unittest.TestCase):
    def sample_product(self):
        return {
            "id": "gid://shopify/Product/123",
            "title": "Testilakana",
            "handle": "testilakana",
            "descriptionHtml": "<p>Hyvä suomalainen tuotekuvaus.</p>",
            "status": "ACTIVE",
            "vendor": "Testibrändi",
            "productType": "Lakanat",
            "category": {"fullName": "Home & Garden > Linens & Bedding > Bedding > Bed Sheets"},
            "media": {"nodes": [{"__typename": "MediaImage", "image": {"url": "https://cdn.example.test/a.jpg"}}]},
            "metafields": {"nodes": [
                {"namespace": "mm-google-shopping", "key": "custom_label_0", "value": "Royal Textile"},
                {"namespace": "mm-google-shopping", "key": "custom_label_1", "value": "G0039"},
            ]},
            "variants": {"pageInfo": {"hasNextPage": False}, "nodes": [{
                "id": "gid://shopify/ProductVariant/456",
                "title": "80x200",
                "sku": "SKU-1",
                "barcode": "4006381333931",
                "price": "23.75",
                "compareAtPrice": "60.60",
                "availableForSale": True,
                "selectedOptions": [{"name": "Koko", "value": "80x200"}],
                "inventoryItem": {
                    "unitCost": {"amount": "10.25", "currencyCode": "EUR"},
                    "measurement": {"weight": {"value": 1000, "unit": "GRAMS"}},
                },
            }]},
        }

    def empty_report(self):
        return {
            "sale_price_items": 0, "category_items": 0, "gtin_items": 0,
            "mpn_items": 0, "cogs_items": 0, "weight_fallback_items": 0,
            "labels": {key: feed.Counter() for key in feed.LABEL_KEYS},
        }

    def test_complete_sale_offer_and_isolation(self):
        channel = ET.Element("channel")
        report = self.empty_report()
        self.assertEqual(feed.add_product(channel, "Royal Textile", self.sample_product(), report), 1)
        item = channel.find("item")
        value = lambda name: item.find(f"{{{feed.G}}}{name}").text
        self.assertEqual(value("id"), "pilot_FI_123_456")
        self.assertEqual(value("title"), "Testilakana, 80x200")
        self.assertEqual(value("price"), "60.60 EUR")
        self.assertEqual(value("sale_price"), "23.75 EUR")
        self.assertEqual(value("shipping_weight"), "1000 g")
        self.assertEqual(value("cost_of_goods_sold"), "10.25 EUR")
        self.assertEqual(value("custom_label_1"), "G0039")
        self.assertEqual(value("size"), "80x200")
        destinations = [node.text for node in item.findall(f"{{{feed.G}}}excluded_destination")]
        self.assertEqual(destinations, list(feed.EXCLUDED_DESTINATIONS))
        self.assertEqual(value("pause"), "all")

    def test_invalid_weight_uses_one_kilogram(self):
        product = self.sample_product()
        product["variants"]["nodes"][0]["inventoryItem"]["measurement"]["weight"] = None
        channel = ET.Element("channel")
        report = self.empty_report()
        feed.add_product(channel, "Royal Textile", product, report)
        self.assertEqual(channel.find(f"item/{{{feed.G}}}shipping_weight").text, "1 kg")
        self.assertEqual(report["weight_fallback_items"], 1)

    def test_bad_compare_at_is_not_a_sale(self):
        product = self.sample_product()
        product["variants"]["nodes"][0]["compareAtPrice"] = "10.00"
        channel = ET.Element("channel")
        feed.add_product(channel, "VidaXL", product, self.empty_report())
        item = channel.find("item")
        self.assertEqual(item.find(f"{{{feed.G}}}price").text, "23.75 EUR")
        self.assertIsNone(item.find(f"{{{feed.G}}}sale_price"))


if __name__ == "__main__":
    unittest.main()
