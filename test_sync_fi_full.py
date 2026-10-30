import unittest
import xml.etree.ElementTree as ET
from io import BytesIO

import sync_fi_full as feed


class FinlandFullFeedTests(unittest.TestCase):
    def test_finland_keeps_zero_stock_variants(self):
        variant = self.sample_variant()
        variant["availableForSale"] = False
        variant["inventoryQuantity"] = 0
        item = feed.build_item(self.sample_product(), variant, feed.empty_report())
        self.assertEqual(item.findtext(f"{{{feed.G}}}availability"), "out_of_stock")
        self.assertEqual(item.findtext(f"{{{feed.G}}}id"), "shopify_FI_123_456")

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

    def test_variant_gmctitle_overrides_generated_title(self):
        variant = self.sample_variant()
        variant["gmcTitle"] = {"value": "Erityinen GMC-otsikko 140 x 200"}
        report = feed.empty_report()
        item = feed.build_item(self.sample_product(), variant, report)
        self.assertEqual(
            item.findtext(f"{{{feed.G}}}title"),
            "Erityinen GMC-otsikko 140 x 200",
        )
        self.assertEqual(report["gmctitle_override_items"], 1)

    def test_empty_variant_gmctitle_keeps_existing_title_logic(self):
        variant = self.sample_variant()
        variant["gmcTitle"] = {"value": "   "}
        report = feed.empty_report()
        item = feed.build_item(self.sample_product(), variant, report)
        self.assertEqual(item.findtext(f"{{{feed.G}}}title"), "Peitto, 140 x 200")
        self.assertEqual(report["gmctitle_override_items"], 0)

    def test_bulk_query_requests_variant_gmctitle(self):
        query = feed.bulk_query_document()
        self.assertIn(
            'gmcTitle: metafield(namespace: "custom", key: "gmctitle") { value }',
            query,
        )

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

    def test_policy_tag_excludes_entire_product_without_failure(self):
        product = self.sample_product()
        product["tags"].append("GMC_EXCLUDE_NUDITY")
        product["_variants"] = [self.sample_variant()]
        report = feed.empty_report()
        out = BytesIO()

        feed.emit_product(out, product, report)

        self.assertEqual(out.getvalue(), b"")
        self.assertEqual(report["products_seen"], 1)
        self.assertEqual(report["products"], 0)
        self.assertEqual(report["items"], 0)
        self.assertEqual(report["policy_excluded_products"], 1)
        self.assertEqual(report["failure_counts"], {})

    def test_policy_tag_match_is_case_insensitive(self):
        product = self.sample_product()
        product["tags"] = ["gmc_exclude_nudity"]
        product["_variants"] = [self.sample_variant()]
        report = feed.empty_report()

        feed.emit_product(BytesIO(), product, report)

        self.assertEqual(report["policy_excluded_products"], 1)

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
