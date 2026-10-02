import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from backend import main as api


CSV = b"""CSV account name
CSV owner
2026-10-01
Holdings report
Asset type,Currency held in,Symbol,Market,Description,Quantity,Average Cost,Closing price,Closing value,Book value,Gain/loss $,Gain/loss %,% of portfolio
Stock,CAD,TEST,CDN,Test stock,10,8,10,100,80,20,25,100
"""


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        paths = patch.multiple(server, BASE_DIR=root, DATA_DIR=root / "data",
                               IMPORT_DIR=root / "data/imports", DB_PATH=root / "data/portfolio.db")
        paths.start()
        self.addCleanup(paths.stop)
        self.account = server.save_account("Selected account", owner="Configured owner")["account"]
        self.other = server.save_account("Other account")["account"]

    def test_import_requires_existing_account_without_storing_file(self):
        for name in ("", "   ", "Typo account"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                server.import_content(CSV, "holdings.csv", name)
        with server.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM accounts").fetchone()["n"], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM import_batches").fetchone()["n"], 0)
        self.assertEqual(list(server.IMPORT_DIR.rglob("*.csv")), [])

    def test_import_uses_selection_and_preserves_owner(self):
        result = server.import_content(CSV, "holdings.csv", " Selected account ", 50)
        self.assertTrue(result["imported"])
        self.assertEqual(result["account_name"], self.account["name"])
        with server.get_connection() as conn:
            account = conn.execute("SELECT * FROM accounts WHERE id = ?", (self.account["id"],)).fetchone()
            self.assertEqual(account["owner"], "Configured owner")
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM accounts").fetchone()["n"], 2)
        self.assertFalse(server.import_content(CSV, "holdings.csv", self.account["name"])["imported"])
        with self.assertRaisesRegex(ValueError, "already been imported into Selected account"):
            server.import_content(CSV, "holdings.csv", self.other["name"])

    def seed_history(self, date, accounts):
        now = server.utc_now()
        with server.get_connection() as conn:
            conn.execute("INSERT INTO balance_snapshots (market_date, total_value, created_at, updated_at) VALUES (?, ?, ?, ?)",
                         (date, sum(value for _, value in accounts), now, now))
            for account, value in accounts:
                conn.execute("""INSERT INTO account_balance_snapshots
                    (market_date, account_id, account_name, value, imported_value, book_value,
                     gain_loss, day_change, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (date, account["id"], account["name"], value, value, value * .8,
                     value * .2, value * .1, now, now))

    def test_delete_cleans_related_records_and_recalculates_history(self):
        imported = server.import_content(CSV, "holdings.csv", self.account["name"], 50)
        other = server.import_content(CSV.replace(b"TEST", b"OTHER"), "other.csv", self.other["name"])
        self.seed_history("2026-10-01", [(self.account, 100), (self.other, 200)])
        self.seed_history("2026-10-02", [(self.account, 100)])
        self.seed_history("2026-09-30", [(self.other, 300)])
        now = server.utc_now()
        with server.get_connection() as conn:
            conn.execute("INSERT INTO manual_holdings (account_id, symbol, created_at, updated_at) VALUES (?, 'MANUAL', ?, ?)", (self.account["id"], now, now))
            conn.execute("INSERT INTO portfolio_transactions (account_id, transaction_date, transaction_type, created_at, updated_at) VALUES (?, '2026-10-01', 'BUY', ?, ?)", (self.account["id"], now, now))
            conn.execute("INSERT INTO private_fund_marks (account_id, mark_date, created_at, updated_at) VALUES (?, '2026-10-01', ?, ?)", (self.account["id"], now, now))
            conn.execute("INSERT INTO account_holding_orders (account_id, holding_key, sort_order, updated_at) VALUES (?, 'TEST', 0, ?)", (self.account["id"], now))
        self.assertTrue(server.delete_account(self.account["id"])["deleted"])
        with server.get_connection() as conn:
            for table in ("import_batches", "manual_holdings", "portfolio_transactions", "private_fund_marks", "account_holding_orders", "account_balance_snapshots"):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE account_id = ?", (self.account["id"],)).fetchone()["n"], 0)
            for table in ("holdings", "cash_balances", "currency_summaries"):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE batch_id = ?", (imported["batch_id"],)).fetchone()["n"], 0)
            self.assertIsNotNone(conn.execute("SELECT id FROM import_batches WHERE id = ?", (other["batch_id"],)).fetchone())
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            remaining = conn.execute("SELECT * FROM balance_snapshots WHERE market_date = '2026-10-01'").fetchone()
            self.assertEqual(remaining["total_value"], 200)
            self.assertEqual(remaining["book_value"], 160)
            self.assertEqual(remaining["gain_loss_pct"], 25)
            self.assertEqual(remaining["day_change"], 20)
            self.assertAlmostEqual(remaining["day_change_pct"], 20 / 180 * 100)
            self.assertIsNone(conn.execute("SELECT * FROM balance_snapshots WHERE market_date = '2026-10-02'").fetchone())
            self.assertEqual(conn.execute("SELECT total_value FROM balance_snapshots WHERE market_date = '2026-09-30'").fetchone()["total_value"], 300)
        self.assertTrue((server.BASE_DIR / imported["stored_path"]).exists())
        self.assertTrue(server.import_content(CSV, "holdings.csv", self.other["name"])["imported"])

    def test_delete_unknown_account_leaves_accounts_intact(self):
        with self.assertRaisesRegex(ValueError, "Account not found"):
            server.delete_account(99999)
        with server.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM accounts").fetchone()["n"], 2)

    def summary_without_network(self):
        with patch.object(server, "ensure_stock_analytics_cache_for_holdings"), \
             patch.object(server, "stock_forward_dividend_map_for_holdings", return_value={}):
            return server.get_summary()

    def test_statement_replaces_manual_position_despite_different_company_name(self):
        server.save_manual_holding(self.account["id"], "TEST", "CDN", "Short name", quantity=10, average_cost=7)
        server.save_manual_holding(self.account["id"], "EXTRA", "CDN", "Unmatched", quantity=2, average_cost=5)
        server.save_manual_holding(self.other["id"], "TEST", "CDN", "Other account", quantity=3, average_cost=7)
        server.import_content(CSV, "holdings.csv", self.account["name"])
        summary = self.summary_without_network()
        holdings = [h for h in summary["holdings"] if h["account_id"] == self.account["id"]]
        self.assertEqual({h["symbol"] for h in holdings}, {"TEST", "EXTRA"})
        self.assertEqual(len(holdings), 2)
        imported = next(h for h in holdings if h["symbol"] == "TEST")
        self.assertEqual(imported["description"], "Test stock")
        self.assertEqual(imported["quantity"], 10)
        self.assertEqual(imported["book_value"], 80)
        self.assertEqual(summary["totals"]["current_value"], 131)
        with server.get_connection() as conn:
            manual = conn.execute("SELECT active FROM manual_holdings WHERE account_id = ? AND symbol = 'TEST'", (self.account["id"],)).fetchone()
            self.assertEqual(manual["active"], 0)

    def test_existing_overlap_is_hidden_and_new_trade_updates_imported_position(self):
        server.import_content(CSV, "holdings.csv", self.account["name"], 100)
        # Reproduce pre-fix database state, where a statement left a manual position active.
        server.save_manual_holding(self.account["id"], "TEST", "CDN", "Short name", quantity=10, average_cost=7)
        with server.get_connection() as conn:
            manual_id = conn.execute("SELECT id FROM manual_holdings WHERE account_id = ?", (self.account["id"],)).fetchone()["id"]
        summary = self.summary_without_network()
        securities = [h for h in summary["holdings"] if h["asset_type"] != "Cash"]
        self.assertEqual(len(securities), 1)
        self.assertEqual(summary["totals"]["current_value"], 200)
        result = server.save_transaction(self.account["id"], "2026-10-02", "BUY",
                                         symbol="TEST", market="CDN", quantity=2, price=10,
                                         gross_amount=20)
        self.assertEqual(result["position_effect"]["kind"], "holding")
        with server.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT quantity FROM holdings").fetchone()["quantity"], 12)
            self.assertEqual(conn.execute("SELECT quantity FROM manual_holdings").fetchone()["quantity"], 10)
        self.assertEqual(server.list_manual_holdings(self.account["id"])["holdings"], [])
        with self.assertRaisesRegex(ValueError, "Holding not found"):
            server.apply_account_trade(self.account["id"], "2026-10-02", 1, 10, manual_holding_id=manual_id)

    def test_old_manual_position_does_not_reappear_after_security_leaves_statement(self):
        server.import_content(CSV, "holdings.csv", self.account["name"])
        server.save_manual_holding(self.account["id"], "TEST", "CDN", quantity=10, average_cost=7)
        server.import_content(CSV.replace(b"TEST", b"NEW"), "next.csv", self.account["name"])
        holdings = self.summary_without_network()["holdings"]
        self.assertEqual([h["symbol"] for h in holdings], ["NEW"])

    def test_delete_api_requires_admin_and_returns_not_found(self):
        server.create_user("admin-test", "test-password", is_admin=True)
        server.create_user("member-test", "test-password")

        async def request(username=None):
            messages = []
            headers = []
            if username:
                token = api.create_session(username)
                headers = [(b"cookie", f"{api.SESSION_COOKIE}={token}".encode())]
            scope = {
                "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                "method": "DELETE", "scheme": "http", "path": f"/api/accounts/{self.account['id']}",
                "query_string": b"", "root_path": "", "headers": headers,
                "client": ("127.0.0.1", 1234), "server": ("test", 80),
            }

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message):
                messages.append(message)

            await api.app(scope, receive, send)
            status = next(item["status"] for item in messages if item["type"] == "http.response.start")
            body = b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body")
            return status, json.loads(body)

        self.assertEqual(asyncio.run(request())[0], 401)
        self.assertEqual(asyncio.run(request("member-test"))[0], 403)
        status, body = asyncio.run(request("admin-test"))
        self.assertEqual(status, 200)
        self.assertTrue(body["deleted"])
        self.assertEqual(asyncio.run(request("admin-test"))[0], 404)


if __name__ == "__main__":
    unittest.main()
