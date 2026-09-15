"""Change only IE/FR transit fields in a previously validated Pages artifact."""
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

from sync_feed import TARGETS

G = '{http://base.google.com/ns/1.0}'


def update(root):
    root = Path(root)
    before = {p.relative_to(root): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in root.rglob('*') if p.is_file()}
    changed = set()
    for country in ('IE', 'FR'):
        prefix = 'finnmart-' + country.lower()
        path = root / (prefix + '.xml')
        data = path.read_text()
        old_items = ET.fromstring(data).findall('./channel/item')
        assert old_items, 'Empty feed'
        service = TARGETS[country]['shipping_service']
        for item in old_items:
            shipping = item.find(G + 'shipping')
            assert shipping is not None and shipping.findtext(G + 'country') == country
            assert shipping.findtext(G + 'min_transit_time') in ('3', '5')
            assert shipping.findtext(G + 'max_transit_time') in ('5', '8')
            assert shipping.find(G + 'service') is not None
        for field, value in [('service', service), ('min_transit_time', '3'),
                             ('max_transit_time', '5')]:
            data, count = re.subn(r'(<g:' + field + r'>)[^<]*(</g:' + field + r'>)',
                                 lambda m: m[1] + value + m[2], data)
            assert count == len(old_items), 'Unexpected shipping field count'
        new_items = ET.fromstring(data).findall('./channel/item')
        assert len(new_items) == len(old_items)
        for old, new in zip(old_items, new_items):
            shipping = old.find(G + 'shipping')
            for field, value in [('service', service), ('min_transit_time', '3'),
                                 ('max_transit_time', '5')]:
                shipping.find(G + field).text = value
            assert ET.tostring(old) == ET.tostring(new), 'Non-transit field changed'
        path.write_text(data)
        changed.add(path.relative_to(root))
        summary_path = root / (prefix + '-summary.json')
        summary = json.loads(summary_path.read_text())
        summary['shipping']['service'] = service
        summary['shipping']['delivery_days'] = '3-5'
        summary['xml_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
        changed.add(summary_path.relative_to(root))
        print(country, len(new_items), 'offers: only service and transit fields changed')
    for name, digest in before.items():
        if name not in changed:
            assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, name
    print('All other publication files are byte-identical')


if __name__ == '__main__':
    update(sys.argv[1])
