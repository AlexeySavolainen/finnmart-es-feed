import unittest
import xml.etree.ElementTree as ET

import sync_feed as feed


class SyncFeedTests(unittest.TestCase):
    def tearDown(self):
        feed.configure_target("ES")

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

    def test_ireland_target_and_shipping_brackets(self):
        feed.configure_target("IE")
        self.assertEqual(feed.LOCALE, "en")
        self.assertEqual(feed.COUNTRY, "IE")
        self.assertEqual(feed.PATH_PREFIX, "")
        self.assertEqual(feed.shipping_price(500), "11.50 EUR")
        self.assertEqual(feed.shipping_price(2000), "11.50 EUR")
        self.assertEqual(feed.shipping_price(2001), "16.65 EUR")
        self.assertEqual(feed.shipping_price(5001), "24.15 EUR")
        self.assertEqual(feed.shipping_price(10001), "30.70 EUR")
        self.assertEqual(feed.shipping_price(15001), "32.15 EUR")
        self.assertIn('translations(locale: "en")', feed.BULK_PRODUCT_FIELDS)
        self.assertIn('contextualPricing(context: {country: IE})', feed.BULK_PRODUCT_FIELDS)

    def test_offer_contains_ireland_shipping(self):
        feed.configure_target("IE")
        channel = ET.Element("channel")
        candidate = feed.Candidate(123, "english-product", "NovaEngel")
        product = {
            "title": "English product",
            "description": "English description",
            "type": "Perfume",
            "vendor": "Brand",
            "images": ["https://cdn.example.test/image.jpg"],
            "options": ["Title"],
            "variants": [{
                "id": 456, "price": 1999, "available": True,
                "weight": 5001, "sku": "SKU-IE", "barcode": "",
                "options": ["Default Title"],
            }],
        }
        self.assertEqual(feed.add_product(channel, candidate, product), 1)
        item = channel.find("item")
        values = {
            node.tag.rsplit("}", 1)[-1]: node.text
            for node in item.find(f"{{{feed.G}}}shipping")
        }
        self.assertEqual(item.find(f"{{{feed.G}}}id").text, "shopify_IE_123_456")
        self.assertEqual(item.find(f"{{{feed.G}}}link").text,
                         "https://finnmart.eu/products/english-product?variant=456")
        self.assertEqual(values["country"], "IE")
        self.assertEqual(values["price"], "24.15 EUR")
        self.assertEqual(values["min_transit_time"], "5")
        self.assertEqual(values["max_transit_time"], "8")

    def test_france_target_and_offer_shipping(self):
        feed.configure_target("FR")
        self.assertEqual(feed.LOCALE, "fr")
        self.assertEqual(feed.COUNTRY, "FR")
        self.assertEqual(feed.PATH_PREFIX, "/fr")
        self.assertEqual(feed.HANDLE_LOCALE, "en")
        self.assertEqual(feed.shipping_price(500), "7.95 EUR")
        self.assertEqual(feed.shipping_price(2001), "9.05 EUR")
        self.assertEqual(feed.shipping_price(5001), "13.80 EUR")
        self.assertEqual(feed.shipping_price(10001), "14.95 EUR")
        self.assertEqual(feed.shipping_price(15001), "21.90 EUR")
        self.assertIn('translations(locale: "fr")', feed.BULK_PRODUCT_FIELDS)
        self.assertIn('handleTranslations: translations(locale: "en")', feed.BULK_PRODUCT_FIELDS)
        self.assertIn('contextualPricing(context: {country: FR})', feed.BULK_PRODUCT_FIELDS)

        channel = ET.Element("channel")
        candidate = feed.Candidate(123, "produit-francais", "Royal Textile")
        product = {
            "title": "Produit français",
            "description": "Description française",
            "type": "Textile",
            "vendor": "Royal Textile",
            "images": ["https://cdn.example.test/image.jpg"],
            "options": ["Title"],
            "variants": [{
                "id": 456, "price": 1999, "available": True,
                "weight": 5001, "sku": "SKU-FR", "barcode": "",
                "options": ["Default Title"],
            }],
        }
        self.assertEqual(feed.add_product(channel, candidate, product), 1)
        item = channel.find("item")
        values = {
            node.tag.rsplit("}", 1)[-1]: node.text
            for node in item.find(f"{{{feed.G}}}shipping")
        }
        self.assertEqual(item.find(f"{{{feed.G}}}id").text, "shopify_FR_123_456")
        self.assertEqual(item.find(f"{{{feed.G}}}link").text,
                         "https://finnmart.eu/fr/products/produit-francais?variant=456")
        self.assertEqual(values["country"], "FR")
        self.assertEqual(values["price"], "13.80 EUR")
        self.assertEqual(values["min_transit_time"], "5")
        self.assertEqual(values["max_transit_time"], "8")

    def test_portugal_target_and_offer_shipping(self):
        feed.configure_target("PT")
        self.assertEqual(feed.LOCALE, "pt-PT")
        self.assertEqual(feed.COUNTRY, "PT")
        self.assertEqual(feed.PATH_PREFIX, "/pt-pt")
        self.assertEqual(feed.HANDLE_LOCALE, "en")
        self.assertEqual(feed.shipping_price(1), "10.50 EUR")
        self.assertEqual(feed.shipping_price(20_000), "10.50 EUR")
        self.assertIn('translations(locale: "pt-PT")', feed.BULK_PRODUCT_FIELDS)
        self.assertIn('handleTranslations: translations(locale: "en")', feed.BULK_PRODUCT_FIELDS)
        self.assertIn('contextualPricing(context: {country: PT})', feed.BULK_PRODUCT_FIELDS)

        channel = ET.Element("channel")
        candidate = feed.Candidate(123, "produto-portugues", "NovaEngel")
        product = {
            "title": "Produto português",
            "description": "Descrição portuguesa",
            "type": "Perfume",
            "vendor": "Marca",
            "images": ["https://cdn.example.test/image.jpg"],
            "options": ["Title"],
            "variants": [{
                "id": 456, "price": 1999, "available": True,
                "weight": 20_000, "sku": "SKU-PT", "barcode": "",
                "options": ["Default Title"],
            }],
        }
        self.assertEqual(feed.add_product(channel, candidate, product), 1)
        item = channel.find("item")
        values = {
            node.tag.rsplit("}", 1)[-1]: node.text
            for node in item.find(f"{{{feed.G}}}shipping")
        }
        self.assertEqual(item.find(f"{{{feed.G}}}id").text, "shopify_PT_123_456")
        self.assertEqual(item.find(f"{{{feed.G}}}link").text,
                         "https://finnmart.eu/pt-pt/products/produto-portugues?variant=456")
        self.assertEqual(values["country"], "PT")
        self.assertEqual(values["price"], "10.50 EUR")
        self.assertEqual(values["min_transit_time"], "5")
        self.assertEqual(values["max_transit_time"], "8")

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

    def test_invalid_weight_uses_one_kilogram(self):
        channel = ET.Element("channel")
        candidate = feed.Candidate(123, "producto-nova", "NovaEngel")
        product = {
            "title": "Producto Royal",
            "description": "Descripción española",
            "type": "Textil",
            "vendor": "NovaEngel",
            "images": ["https://cdn.example.test/image.jpg"],
            "options": ["Title"],
            "variants": [{
                "id": 789,
                "price": 2999,
                "available": True,
                "weight": 0,
                "sku": "ROYAL-1",
                "barcode": "",
                "options": ["Default Title"],
            }],
        }
        report = {}
        self.assertEqual(feed.add_product(channel, candidate, product, report), 1)
        self.assertEqual(report["weight_fallback_items"], 1)
        self.assertEqual(report["weight_fallback_by_supplier"], {"NovaEngel": 1})
        self.assertEqual(channel.find(f"item/{{{feed.G}}}shipping_weight").text, "1000 g")
        price = channel.find(f"item/{{{feed.G}}}shipping/{{{feed.G}}}price")
        self.assertEqual(price.text, "11.50 EUR")


if __name__ == "__main__":
    unittest.main()
