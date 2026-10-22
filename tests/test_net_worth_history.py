from datetime import datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from zoneinfo import ZoneInfo

import server
from backend import main as api, net_worth, net_worth_history as history
from backend.net_worth_report import build_report

ZONE = ZoneInfo('America/Edmonton')


class NetWorthHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        paths = patch.multiple(server, DATA_DIR=root, IMPORT_DIR=root / 'imports', DB_PATH=root / 'portfolio.db')
        paths.start()
        self.addCleanup(paths.stop)
        self.accounts = [{'id': 1, 'name': 'Jamie RRSP', 'current_total_value': 500000},
                         {'id': 2, 'name': 'Closed later', 'current_total_value': 10000}]
        summary = patch.object(server, 'get_summary', side_effect=lambda: {'accounts': self.accounts})
        summary.start()
        self.addCleanup(summary.stop)
        self.property = net_worth.save_item(dict(name='House', category='property', value=1000000,
                                               mortgage=300000, valuation_date='2026-10-22'))

    def at(self, day, hour=17, minute=0):
        return datetime(2026, 10, day, hour, minute, tzinfo=ZONE)

    def test_baseline_then_friday_at_five_once_per_week(self):
        history.maybe_capture_weekly(self.at(22))
        self.assertEqual([s['source'] for s in history.list_snapshots()], ['baseline'])
        history.maybe_capture_weekly(self.at(23, 16, 59))
        self.assertEqual(len(history.list_snapshots()), 1)
        first = history.maybe_capture_weekly(self.at(23))
        again = history.maybe_capture_weekly(self.at(24))
        self.assertEqual(first['id'], again['id'])
        self.assertEqual(len(history.list_snapshots()), 2)
        self.assertEqual(first['totals']['netWorth'], 1210000)
        history.maybe_capture_weekly(self.at(30))
        self.assertEqual(len(history.list_snapshots()), 3)

    def test_late_restart_saves_actual_time_without_backfilling_weeks(self):
        history.maybe_capture_weekly(self.at(22))
        history.maybe_capture_weekly(self.at(26))  # Missed last Friday; Monday is not a save day.
        self.assertEqual(len(history.list_snapshots()), 1)
        late = history.maybe_capture_weekly(self.at(31, 9))
        self.assertEqual(datetime.fromisoformat(late['captured_at']).astimezone(ZONE), self.at(31, 9))
        self.assertEqual(len(history.list_snapshots()), 2)

    def test_first_start_after_due_time_saves_one_snapshot(self):
        first = history.maybe_capture_weekly(self.at(23, 18))
        self.assertEqual(first['source'], 'weekly')
        self.assertEqual(len(history.list_snapshots()), 1)

    def test_timezone_boundary_uses_edmonton_and_handles_dst(self):
        from datetime import timezone
        history.maybe_capture_weekly(self.at(23, 16, 59).astimezone(timezone.utc))
        self.assertEqual(len(history.list_snapshots()), 1)
        history.maybe_capture_weekly(self.at(23, 17).astimezone(timezone.utc))
        self.assertEqual(len(history.list_snapshots()), 2)
        history.maybe_capture_weekly(datetime(2026, 11, 6, 16, 59, tzinfo=ZONE))
        self.assertEqual(len(history.list_snapshots()), 2)
        history.maybe_capture_weekly(datetime(2026, 11, 6, 17, tzinfo=ZONE))
        self.assertEqual(len(history.list_snapshots()), 3)

    def test_immutable_history_handles_renames_added_deleted_accounts_and_assets(self):
        before = history.capture_snapshot(now=self.at(22))
        self.accounts = [{'id': 1, 'name': 'Renamed RRSP', 'current_total_value': 520000},
                         {'id': 3, 'name': 'New account', 'current_total_value': 25000}]
        net_worth.save_item({**self.property, 'mortgage': 290000}, self.property['id'])
        after = history.capture_snapshot(now=self.at(23))
        net_worth.delete_item(self.property['id'])
        saved = history.get_snapshot(before['id'])
        self.assertEqual(saved['items'][0]['mortgage'], 300000)
        self.assertEqual(saved['accounts'][0]['name'], 'Jamie RRSP')
        comparison = history.compare_snapshots(before['id'], after['id'])
        self.assertEqual(comparison['totals']['netWorth'], 45000)
        rows = {r['key']: r for r in comparison['accounts']}
        self.assertEqual(rows['1']['name'], 'Renamed RRSP')
        self.assertEqual(rows['1']['change'], 20000)
        self.assertEqual(rows['2']['change'], -10000)
        self.assertEqual(rows['3']['change'], 25000)
        self.assertEqual(sum(r['change'] for r in comparison['categories']), 45000)
        self.assertEqual(next(r for r in comparison['categories'] if r['key'] == 'property')['change'], 10000)
        pdf = build_report(after['accounts'], after['items'], comparison=comparison)
        self.assertIn(b'Net worth change', pdf)
        self.assertIn(b'$45,000', pdf)
        self.assertIn(b'Saved snapshots:', pdf)
        for ids in [(after['id'], before['id']), (before['id'], before['id'])]:
            with self.assertRaises(ValueError):
                history.compare_snapshots(*ids)
        with self.assertRaises(LookupError):
            history.get_snapshot(999)

    def test_snapshot_keys_are_idempotent_and_manual_saves_do_not_block_weekly(self):
        manual = history.capture_snapshot(now=self.at(23, 16))
        scheduled = history.maybe_capture_weekly(self.at(23, 17))
        self.assertNotEqual(manual['id'], scheduled['id'])
        self.assertEqual(len(history.list_snapshots()), 2)
        with patch.object(server, 'get_summary', side_effect=AssertionError('Must use saved snapshot')):
            existing = history.capture_snapshot('weekly', 'weekly:2026-10-19')
        self.assertEqual(existing['id'], scheduled['id'])

    def test_bad_comparison_requests_return_clear_http_errors(self):
        before = history.capture_snapshot(now=self.at(22))
        with self.assertRaises(api.HTTPException) as error:
            api.net_worth_changes(before['id'], before['id'], username='test')
        self.assertEqual(error.exception.status_code, 400)
        with self.assertRaises(api.HTTPException) as error:
            api.net_worth_changes(before['id'], 999, username='test')
        self.assertEqual(error.exception.status_code, 404)

    def test_worker_is_enabled_even_when_price_refresh_is_off(self):
        with patch.object(api, 'PRICE_REFRESH_ENABLED', False), patch.object(api, 'net_worth_thread', None), \
                patch.object(api, 'ensure_auth_user'), patch.object(api.real_estate, 'clear_saved_listings'), \
                patch.object(api.threading, 'Thread') as thread:
            api.start_background_workers()
            thread.assert_called_once_with(target=api.net_worth_snapshot_loop, name='net-worth-snapshots', daemon=True)
            thread.return_value.start.assert_called_once()

    def test_worker_retries_after_failure(self):
        stop = Mock()
        stop.is_set.side_effect = [False, False, True]
        with patch.object(api, 'net_worth_stop', stop), patch.object(history, 'maybe_capture_weekly', side_effect=[ValueError('test'), None]) as save, patch('builtins.print'):
            api.net_worth_snapshot_loop()
        self.assertEqual(save.call_count, 2)
