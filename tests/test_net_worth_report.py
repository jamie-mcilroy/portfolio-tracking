import re
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from backend import main as api
from backend.net_worth_report import build_report


class NetWorthReportTests(unittest.TestCase):
    def setUp(self):
        self.accounts = [{'name': 'Jamie RRSP', 'current_total_value': 500000, 'total_closing_value': 1}]
        self.items = [dict(category='property', value=1000000, mortgage=300000, valuation_date='2026-10-22'),
                      dict(category='debt', value=20000, mortgage=0, valuation_date='2026-10-01')]

    def test_report_is_one_page_and_reconciles_equity_and_totals(self):
        report = build_report(self.accounts, self.items)
        self.assertTrue(report.startswith(b'%PDF-'))
        self.assertEqual(len(re.findall(rb'/Type /Page\b', report)), 1)
        for label in (b'Jamie RRSP', b'$1,180,000', b'$1,500,000', b'$320,000', b'$700,000', b'-$20,000'):
            self.assertIn(label, report)

    def test_empty_and_many_accounts_stay_one_page(self):
        for accounts in ([], [{'name': 'Very long account name ' * 10, 'current_total_value': (-1 if i == 0 else 1) * i * 10000} for i in range(35)]):
            report = build_report(accounts, [])
            self.assertEqual(len(re.findall(rb'/Type /Page\b', report)), 1)
        self.assertIn(b'Other 18 accounts', report)

    def test_download_endpoint_uses_saved_data_and_prevents_caching(self):
        with patch.object(api, 'get_summary', return_value={'accounts': self.accounts}), patch.object(api.net_worth, 'list_items', return_value=self.items):
            response = api.net_worth_report(username='test')
        self.assertEqual(response.media_type, 'application/pdf')
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertIn('attachment;', response.headers['content-disposition'])
        self.assertIn(b'$1,180,000', response.body)

    def test_negative_equity_and_date_are_explicit(self):
        report = build_report([], [{**self.items[0], 'value': 100000, 'mortgage': 150000}],
                              datetime(2026, 10, 22, tzinfo=ZoneInfo('America/Edmonton')))
        self.assertIn(b'-$50,000', report)
        self.assertIn(b'October 22, 2026', report)
