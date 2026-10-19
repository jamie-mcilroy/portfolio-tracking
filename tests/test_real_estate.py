import asyncio
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
import server
from backend import main as api, real_estate as re

URL = 'https://www.calgarylistings.com/calgary-mls/A2338340-example/'

def fixture(fees=True, price=270000, currency='CAD', mls='A2338340'):
    entities = {'@graph': [
        {'@type': 'RealEstateListing', 'url': f'https://www.calgarylistings.com/calgary-mls/{mls}-example/',
         'offers': {'price': price, 'priceCurrency': currency}, 'dateModified': '2026-10-18', 'mainEntity': {'@id': '#home'}},
        {'@type': 'Apartment', '@id': '#home', 'identifier': f'MLS {mls}',
         'numberOfBedrooms': 2, 'numberOfBathroomsTotal': 1.5, 'image': ['https://www.calgarylistings.com/photos/main.jpg'],
         'address': {'streetAddress': '903, 3830 Brentwood Road NW', 'addressLocality': 'Calgary', 'addressRegion': 'AB'}},
    ]}
    details = '<div><span>Monthly Condo Fee</span><span>$480/mo</span></div>' if fees else ''
    return f'<html><script type="application/ld+json">{json.dumps(entities)}</script>{details}<div><div>Property Taxes</div><div>$2,061/yr <span>(2026)</span></div></div></html>'


class ListingParserTests(unittest.TestCase):
    def test_extracts_required_fields_and_units(self):
        result = re.parse_listing(fixture(), 'a2338340', URL)
        self.assertEqual(result['mls'], 'A2338340')
        self.assertEqual(result['price'], 270000)
        self.assertEqual(result['condo_fee_monthly'], 480)
        self.assertEqual(result['property_tax_annual'], 2061)
        self.assertEqual(result['tax_year'], 2026)
        self.assertEqual(result['bedrooms'], 2)
        self.assertEqual(result['bathrooms'], 1.5)
        self.assertEqual(result['source_url'], URL)
        self.assertIn('Calgary', result['address'])
        self.assertEqual(result['photo_url'], 'https://www.calgarylistings.com/photos/main.jpg')
        self.assertNotIn('fetched_at', result)

    def test_missing_fees_are_not_zero(self):
        self.assertIsNone(re.parse_listing(fixture(fees=False), 'A2338340', URL)['condo_fee_monthly'])

    def test_missing_or_unsafe_photos_are_not_returned(self):
        for url in ['javascript:alert(1)', 'http://127.0.0.1/photo.jpg']:
            page = fixture().replace('https://www.calgarylistings.com/photos/main.jpg', url)
            self.assertIsNone(re.parse_listing(page, 'A2338340', URL)['photo_url'])

    def test_rejects_wrong_listing_unavailable_price_and_currency(self):
        with self.assertRaises(re.ListingNotFound):
            re.parse_listing(fixture(mls='A9999999'), 'A2338340', URL)
        for price in [None, 0, -1]:
            with self.assertRaises(re.ListingUnavailable):
                re.parse_listing(fixture(price=price), 'A2338340', URL)
        with self.assertRaises(re.ListingUnavailable):
            re.parse_listing(fixture(currency='USD'), 'A2338340', URL)
        with self.assertRaises(re.ListingUnavailable):
            re.parse_listing(fixture(), 'A2338340', 'https://unrelated.example/')

    def test_input_validation_prevents_url_injection(self):
        for value in ['', '../A2338340', 'A2338340?url=http://localhost', 'https://example.com', 'A123', 'A23383400']:
            with self.assertRaises(ValueError): re.normalize_mls(value)
        self.assertEqual(re.normalize_mls(' a2338340 '), 'A2338340')

    def test_failed_fetch_reports_lookup_error(self):
        with patch('backend.real_estate.urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = urllib.error.URLError('offline')
            with self.assertRaises(re.ListingUnavailable): re.fetch_listing('A2338340')


class StatelessCondoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        paths = patch.multiple(server, BASE_DIR=root, DATA_DIR=root / 'data', IMPORT_DIR=root / 'data/imports', DB_PATH=root / 'data/portfolio.db')
        paths.start(); self.addCleanup(paths.stop)

    def test_old_saved_listings_are_removed_without_touching_accounts(self):
        server.create_user('member', 'test-password')
        with server.get_connection() as conn:
            conn.execute('CREATE TABLE rental_condo_listings (payload TEXT)')
            conn.execute("INSERT INTO rental_condo_listings VALUES ('old saved listing')")
        re.clear_saved_listings()
        with server.get_connection() as conn:
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='rental_condo_listings'").fetchone())
        self.assertIsNotNone(server.get_user_by_username('member'))

    def test_api_auth_lookup_and_error_statuses(self):
        server.create_user('member', 'test-password')
        async def request(method='GET', username=None, payload=None):
            messages = []
            headers = [(b'content-type', b'application/json')]
            if username: headers.append((b'cookie', f'{api.SESSION_COOKIE}={api.create_session(username)}'.encode()))
            scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': method,
                     'scheme': 'http', 'path': '/api/real-estate/condos', 'query_string': b'', 'root_path': '',
                     'headers': headers, 'client': ('127.0.0.1', 1234), 'server': ('test', 80)}
            async def receive(): return {'type': 'http.request', 'body': json.dumps(payload).encode() if payload else b'', 'more_body': False}
            async def send(message): messages.append(message)
            await api.app(scope, receive, send)
            status = next(message['status'] for message in messages if message['type'] == 'http.response.start')
            if status == 200:
                headers = dict(next(message['headers'] for message in messages if message['type'] == 'http.response.start'))
                self.assertEqual(headers.get(b'cache-control'), b'no-store')
            body = b''.join(message.get('body', b'') for message in messages if message['type'] == 'http.response.body')
            return status, json.loads(body)
        self.assertEqual(asyncio.run(request('POST', payload={'mls': 'A2338340'}))[0], 401)
        listing = re.parse_listing(fixture(), 'A2338340', URL)
        with patch.object(re, 'fetch_listing', return_value=listing):
            status, body = asyncio.run(request('POST', 'member', {'mls': 'A2338340'}))
            self.assertEqual(status, 200)
            self.assertEqual(body['listing']['mls'], 'A2338340')
            self.assertNotIn('listings', body)
        with server.get_connection() as conn:
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='rental_condo_listings'").fetchone())
        for error, expected in [(ValueError('bad MLS'), 400), (re.ListingNotFound('missing'), 404), (re.ListingUnavailable('offline'), 502)]:
            with patch.object(re, 'fetch_listing', side_effect=error):
                self.assertEqual(asyncio.run(request('POST', 'member', {'mls': 'A2338340'}))[0], expected)
