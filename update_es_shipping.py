"""Update one authorized country's shipping in an existing Pages artifact."""
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

G = "{http://base.google.com/ns/1.0}"
root = Path(sys.argv[1])
country = sys.argv[2] if len(sys.argv) > 2 else 'ES'
assert country in ('ES', 'PT'), 'Only Spain and Portugal are supported'
prefix = 'finnmart-' + country.lower()
allowed_prices = {'ES': ('11.50 EUR', '13.10 EUR', '4.90 EUR', '6.90 EUR'),
                  'PT': ('10.50 EUR', '4.90 EUR', '6.90 EUR')}[country]
before = {p.relative_to(root): hashlib.sha256(p.read_bytes()).hexdigest()
          for p in root.rglob('*') if p.is_file()}
changed = set()
assert (root / (prefix + '.xml')).exists(), 'Full target feed missing'
for name in (prefix + '.xml', prefix + '-pilot.xml'):
    path = root / name
    if not path.exists():
        continue
    data = path.read_text()
    original = ET.fromstring(data)
    offers = original.findall('./channel/item')
    assert offers, name
    for offer in offers:
        shipping = offer.find(G + 'shipping')
        assert shipping is not None and shipping.findtext(G + 'country') == country
        grams = int(offer.findtext(G + 'shipping_weight').split()[0])
        assert 0 <= grams <= 20000
        assert shipping.findtext(G + 'price') in allowed_prices

    def replace_offer(match):
        block = match.group()
        grams = int(re.search(r'<g:shipping_weight>(\d+) g</g:shipping_weight>', block)[1])
        rate = '4.90 EUR' if grams <= 10000 else '6.90 EUR'
        return re.sub(r'(<g:shipping>.*?<g:price>)[^<]+(</g:price>)',
                      lambda m: m[1] + rate + m[2], block, count=1, flags=re.S)

    updated, count = re.subn(r'<item>.*?</item>', replace_offer, data, flags=re.S)
    assert count == len(offers)
    parsed = ET.fromstring(updated)
    updated_offers = parsed.findall('./channel/item')
    assert len(updated_offers) == len(offers)
    for old, new in zip(offers, updated_offers):
        grams = int(new.findtext(G + 'shipping_weight').split()[0])
        price = new.find(G + 'shipping').find(G + 'price')
        assert price.text == ('4.90 EUR' if grams <= 10000 else '6.90 EUR')
        old.find(G + 'shipping').find(G + 'price').text = price.text
        assert ET.tostring(old) == ET.tostring(new), 'Non-shipping field changed'
    path.write_text(updated)
    changed.add(Path(name))
    print(f'{name}: validated {count} offers; only {country} shipping prices changed')

summary_path = root / (prefix + '-summary.json')
if summary_path.exists():
    summary = json.loads(summary_path.read_text())
    shipping = summary['shipping']
    shipping.pop('flat_up_to_20kg', None)
    shipping.pop('up_to_20kg', None)
    if 'up_to_10kg' in shipping:
        shipping['up_to_10kg'] = '4.90 EUR'
        shipping['over_10kg_to_20kg'] = '6.90 EUR'
    if 'weight_bands' in shipping:
        shipping['weight_bands'] = [{'maximum_grams': 10000, 'price': '4.90 EUR'},
                                    {'maximum_grams': 20000, 'price': '6.90 EUR'}]
    summary['xml_sha256'] = hashlib.sha256((root / (prefix + '.xml')).read_bytes()).hexdigest()
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    changed.add(Path(prefix + '-summary.json'))
for relative, digest in before.items():
    if relative not in changed:
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == digest, relative
print('All other countries and Finland files are byte-identical')
