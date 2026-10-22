import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from backend import main as api, net_worth


class NetWorthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        paths = patch.multiple(server, DATA_DIR=root, IMPORT_DIR=root / 'imports', DB_PATH=root / 'portfolio.db')
        paths.start()
        self.addCleanup(paths.stop)
        self.payload = dict(name='Family home', category='property', value=1000000,
                            mortgage=300000, valuation_date='2026-10-22', notes='Estimate')

    def test_create_read_update_delete_persisted_item(self):
        item = api.add_net_worth_item(api.NetWorthPayload(**self.payload), username='test')['item']
        self.assertEqual(net_worth.list_items()[0]['mortgage'], 300000)
        self.assertEqual(item['name'], 'Family home')
        updated = api.edit_net_worth_item(item['id'], api.NetWorthPayload(**{**self.payload, 'mortgage': 0}), username='test')
        self.assertEqual(updated['item']['mortgage'], 0)
        self.assertEqual(len(api.net_worth_items(username='test')['items']), 1)
        self.assertTrue(api.remove_net_worth_item(item['id'], username='test')['deleted'])
        self.assertEqual(net_worth.list_items(), [])
        with self.assertRaises(api.HTTPException) as error:
            api.edit_net_worth_item(item['id'], api.NetWorthPayload(**self.payload), username='test')
        self.assertEqual(error.exception.status_code, 404)

    def test_validation_rejects_invalid_values_without_writing(self):
        for fields in ({'name': ' '}, {'category': 'invalid'}, {'value': -1}, {'value': float('nan')},
                       {'mortgage': float('inf')}, {'mortgage': -1}, {'valuation_date': '2026-02-30'},
                       {'category': 'vehicle'}, {'notes': 'a' * 2001}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                net_worth.save_item({**self.payload, **fields})
        self.assertEqual(net_worth.list_items(), [])

    def test_all_categories_and_underwater_property_are_supported(self):
        for category in net_worth.CATEGORIES:
            item = net_worth.save_item({**self.payload, 'category': category,
                                       'mortgage': 1100000 if category == 'property' else 0})
            self.assertEqual(item['category'], category)
        self.assertEqual(len(net_worth.list_items()), 6)

    def test_routes_require_authentication(self):
        routes = [route for route in api.app.routes if getattr(route, 'path', '').startswith('/api/net-worth')]
        self.assertEqual(len(routes), 9)
        for route in routes:
            self.assertIn(api.require_auth, [dependency.call for dependency in route.dependant.dependencies])
