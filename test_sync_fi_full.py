import unittest
import xml.etree.ElementTree as ET

import sync_fi_full as feed


class FinlandFullFeedTests(unittest.TestCase):
    def sample_product(self):
        return {
            "id": "gid://shopify/Product/123",
            "legacyResourceId": "123",
            "title": "Peitto",
            "handle": "peitto",
            "descriptionHtml": "<p>Lämmin peitto.</p>",
            "status": "ACTIVE",
            "vendor": "Sleeptime",
            "productType": "Peitot",
            "tags": ["New Royal Textile"],
            "category": {"fullName": "Home & Garden > Linens & Bedding > Bedding > Blankets"},
            "_images": ["https://cdn.example.test/peitto.jpg"],
            "_variants": [],
        }

    def sample_variant(self):
        return {
            "id": "gid://shopify/ProductVariant/456",
            "legacyResourceId": "456",
            "title": "140 x 200",
            "sku": "SKU-1",
            "barcode": "4006381333931",
            "price": "30.85",
            "compareAtPrice": "81.00",
            "availableForSale": True,
            "selectedOptions": [{"name": "Koko", "value": "140 x 200"}],
            "customLabel0": {"value": "New Royal Textile"},
            "customLabel1": {"value": "G0014"},
            "customLabel2": {"value": "ADS"},
            "customLabel3": {"value": "ROYAL-PROFIT"},
            "customLabel4": {"value": "ROYAL-BEST"},
            "inventoryItem": {
                "unitCost": {"amount": "13.75", "currencyCode": "EUR"},
                "measurement": {"weight": {"value": 3000, "unit": "GRAMS"}},
            },
        }

    def test_production_offer_uses_variant_labels(self):
        report = feed.empty_report()
        item = feed.build_item(self.sample_product(), self.sample_variant(), report)
        value = lambda name: item.find(f"{{{feed.G}}}{name}").text
        self.assertEqual(value("id"), "shopify_FI_123_456")
        self.assertEqual(value("price"), "81.00 EUR")
        self.assertEqual(value("sale_price"), "30.85 EUR")
        self.assertEqual(value("custom_label_1"), "G0014")
        self.assertEqual(value("custom_label_3"), "ROYAL-PROFIT")
        self.assertEqual(value("custom_label_4"), "ROYAL-BEST")
        self.assertEqual(value("shipping_weight"), "3000 g")
        self.assertEqual(value("cost_of_goods_sold"), "13.75 EUR")
        self.assertEqual(value("size"), "140 x 200")
        self.assertIsNone(item.find(f"{{{feed.G}}}pause"))
        self.assertIsNone(item.find(f"{{{feed.G}}}excluded_destination"))

    def test_product_level_label_is_not_used(self):
        product = self.sample_product()
        product["customLabel1"] = {"value": "WRONG-PRODUCT-LABEL"}
        variant = self.sample_variant()
        variant["customLabel1"] = None
        item = feed.build_item(product, variant, feed.empty_report())
        self.assertIsNone(item.find(f"{{{feed.G}}}custom_label_1"))

    def test_supplier_classification(self):
        self.assertEqual(feed.supplier_from_tags(["Nova Engel"]), "NovaEngel")
        self.assertEqual(feed.supplier_from_tags(["New Royal Textile"]), "Royal Textile")
        self.assertEqual(feed.supplier_from_tags(["vida-xl"]), "VidaXL")
        self.assertEqual(feed.supplier_from_tags(["Unrelated"]), "Other")

    def test_description_and_weight_fallbacks(self):
        product = self.sample_product()
        product["descriptionHtml"] = ""
        variant = self.sample_variant()
        variant["inventoryItem"]["measurement"]["weight"] = None
        report = feed.empty_report()
        item = feed.build_item(product, variant, report)
        self.assertIn("Vuodevaatteet.fi", item.find(f"{{{feed.G}}}description").text)
        self.assertEqual(item.find(f"{{{feed.G}}}shipping_weight").text, "1 kg")
        self.assertEqual(report["description_fallback_items"], 1)
        self.assertEqual(report["weight_fallback_items"], 1)


if __name__ == "__main__":
    unittest.main()
