import unittest
import xml.etree.ElementTree as ET

import sync_feed as feed


class SyncFeedTests(unittest.TestCase):
    def test_supplier_allowlist(self):
        self.assertEqual(feed.supplier_from_tags(["Nova Engel"]), "NovaEngel")
        self.assertEqual(feed.supplier_from_tags(["Royal Textile"]), "Royal Textile")
        self.assertIsNone(feed.supplier_from_tags(["Other supplier"]))

    def test_weight_and_shipping_brackets(self):
        self.assertEqual(feed.variant_weight_grams({"weight": 999}), 999)
        self.assertEqual(feed.variant_weight_grams({"weight": 10.5, "weight_unit": "kg"}), 10500)
        self.assertEqual(feed.shipping_price(10000), "11.50 EUR")
        self.assertEqual(feed.shipping_price(10001), "13.10 EUR")
        with self.assertRaises(ValueError):
            feed.variant_weight_grams({"weight": 21, "weight_unit": "kg"})

    def test_offer_contains_spanish_shipping(self):
        channel = ET.Element("channel")
        candidate = feed.Candidate(123, "producto-espanol", "NovaEngel")
        product = {
            "title": "Producto español",
            "description": "Descripción española",
            "type": "Perfume",
            "vendor": "Marca",
            "images": ["//cdn.example.test/image.jpg"],
            "options": ["Title"],
            "variants": [{
                "id": 456,
                "price": 1999,
                "available": True,
                "weight": 500,
                "sku": "SKU-1",
                "barcode": "4006381333931",
                "options": ["Default Title"],
            }],
        }
        self.assertEqual(feed.add_product(channel, candidate, product), 1)
        shipping = channel.find(f"item/{{{feed.G}}}shipping")
        values = {node.tag.rsplit("}", 1)[-1]: node.text for node in shipping}
        self.assertEqual(values["country"], "ES")
        self.assertEqual(values["service"], feed.SHIPPING_SERVICE)
        self.assertEqual(values["price"], "11.50 EUR")
        self.assertEqual(values["min_transit_time"], "3")
        self.assertEqual(values["max_transit_time"], "5")


if __name__ == "__main__":
    unittest.main()
