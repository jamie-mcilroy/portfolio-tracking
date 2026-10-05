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

        async def request(username=None, path=None, method="DELETE", payload=None):
            messages = []
            headers = []
            if username:
                token = api.create_session(username)
                headers = [(b"cookie", f"{api.SESSION_COOKIE}={token}".encode())]
            if payload is not None:
                headers.append((b"content-type", b"application/json"))
            scope = {
                "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                "method": method, "scheme": "http", "path": path or f"/api/accounts/{self.account['id']}",
                "query_string": b"", "root_path": "", "headers": headers,
                "client": ("127.0.0.1", 1234), "server": ("test", 80),
            }

            async def receive():
                return {"type": "http.request", "body": json.dumps(payload).encode() if payload is not None else b"", "more_body": False}

            async def send(message):
                messages.append(message)

            await api.app(scope, receive, send)
            status = next(item["status"] for item in messages if item["type"] == "http.response.start")
            body = b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body")
            return status, json.loads(body)

        cash_payload = {"updates": [{"account_id": self.account["id"], "currency": "CAD", "amount": 200, "expected_amount": 0}]}
        for method in ("GET", "PATCH"):
            payload = cash_payload if method == "PATCH" else None
            self.assertEqual(asyncio.run(request(path="/api/admin/cash", method=method, payload=payload))[0], 401)
            self.assertEqual(asyncio.run(request("member-test", path="/api/admin/cash", method=method, payload=payload))[0], 403)
        self.assertEqual(asyncio.run(request("admin-test", path="/api/admin/cash", method="PATCH", payload=cash_payload))[0], 200)
        status, body = asyncio.run(request("admin-test", path="/api/admin/cash", method="GET"))
        self.assertEqual(status, 200)
        self.assertEqual(len(body["accounts"]), 2)
        with patch.object(api, "get_weekly_income_report", return_value={"weeks": []}), \
             patch.object(api, "save_income_snapshot") as save_income:
            self.assertEqual(asyncio.run(request(path="/api/income/weekly", method="GET"))[0], 401)
            self.assertEqual(asyncio.run(request("member-test", path="/api/income/weekly", method="GET"))[0], 200)
            self.assertEqual(asyncio.run(request("member-test", path="/api/income/snapshots/today", method="POST"))[0], 403)
            self.assertEqual(asyncio.run(request("admin-test", path="/api/income/snapshots/today", method="POST"))[0], 200)
            save_income.assert_called_once()
        self.assertEqual(asyncio.run(request())[0], 401)
        self.assertEqual(asyncio.run(request("member-test"))[0], 403)
        status, body = asyncio.run(request("admin-test"))
        self.assertEqual(status, 200)
        self.assertTrue(body["deleted"])
        self.assertEqual(asyncio.run(request("admin-test"))[0], 404)

    def seed_dividends(self, symbol="TEST.TO", amount=.5):
        with server.get_connection() as conn:
            for date in ("2026-04-01", "2026-07-01", "2026-10-01"):
                conn.execute("INSERT INTO stock_dividend_history (yahoo_symbol, ex_date, dividend_per_share, fetched_at) VALUES (?, ?, ?, ?)",
                             (symbol, date, amount, server.utc_now()))

    def test_drip_saves_income_increase_and_leaves_cash_unchanged(self):
        server.import_content(CSV, "holdings.csv", self.account["name"], 50)
        self.seed_dividends()
        result = server.apply_account_trade(self.account["id"], "2026-10-05", 2.5, 10,
                                            drip=True, symbol="TEST", market="CDN")
        impact = result["transaction"]["income_impact"]
        self.assertEqual(impact["annual_income_before"], 20)
        self.assertEqual(impact["annual_income_after"], 25)
        self.assertEqual(impact["annual_income_change"], 5)
        self.assertAlmostEqual(impact["monthly_income_change"], 5 / 12)
        with server.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT amount FROM cash_balances").fetchone()["amount"], 50)
            conn.execute("UPDATE stock_dividend_history SET dividend_per_share = 1")
        self.assertEqual(server.list_transactions()["transactions"][0]["income_impact"], impact)
        server.delete_transaction(result["transaction"]["id"])
        with server.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT quantity FROM holdings").fetchone()["quantity"], 10)
            self.assertEqual(conn.execute("SELECT amount FROM cash_balances").fetchone()["amount"], 50)

    def test_transaction_form_drip_uses_manual_shares_and_frozen_cad_conversion(self):
        server.save_manual_holding(self.account["id"], "TEST", "US", currency="USD", quantity=10, average_cost=8)
        self.seed_dividends("TEST")
        with patch.object(server, "latest_usd_cad_rate_from_conn", return_value=1.4):
            result = server.save_transaction(self.account["id"], "2026-10-05", "DRIP",
                                             symbol="TEST", market="US", currency="USD", quantity=2, price=10)
        impact = result["transaction"]["income_impact"]
        self.assertAlmostEqual(impact["annual_income_before"], 28)
        self.assertAlmostEqual(impact["annual_income_change"], 5.6)
        self.assertEqual(impact["fx_to_cad"], 1.4)
        self.assertEqual(server.get_transaction(result["transaction"]["id"])["income_impact"], impact)

    def test_drip_without_dividend_data_records_no_estimate(self):
        server.import_content(CSV, "holdings.csv", self.account["name"])
        result = server.apply_account_trade(self.account["id"], "2026-10-05", 2, 10,
                                            drip=True, symbol="TEST", market="CDN")
        self.assertIsNone(result["transaction"]["income_impact"])

    def test_sale_records_negative_income_change(self):
        server.import_content(CSV, "holdings.csv", self.account["name"])
        self.seed_dividends()
        result = server.apply_account_trade(self.account["id"], "2026-10-05", -3, 10,
                                            symbol="TEST", market="CDN")
        self.assertEqual(result["transaction"]["income_impact"]["annual_income_change"], -6)

    def test_incomplete_buy_or_drip_is_rejected_without_saving_transaction(self):
        for kind in ("BUY", "DRIP"):
            for fields in ({"symbol": "TEST", "quantity": None, "price": 51.6},
                           {"symbol": "TEST", "quantity": 22, "price": None},
                           {"symbol": "", "quantity": 22, "price": 51.6}):
                with self.subTest(kind=kind, fields=fields), self.assertRaises(ValueError):
                    server.save_transaction(self.account["id"], "2026-10-05", kind, **fields)
        self.assertEqual(server.list_transactions()["transactions"], [])

    def test_dividend_does_not_add_shares(self):
        server.import_content(CSV, "holdings.csv", self.account["name"])
        server.save_transaction(self.account["id"], "2026-10-05", "DIVIDEND",
                                symbol="TEST", market="CDN", quantity=22, price=51.6)
        with server.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT quantity FROM holdings").fetchone()["quantity"], 10)

    def test_income_lookup_uses_cached_dividends_without_owning_stock(self):
        self.seed_dividends()
        estimate = server.get_dividend_income_estimate(" test ", "cdn")
        self.assertTrue(estimate["available"])
        self.assertEqual(estimate["annual_dividend_per_share"], 2)
        self.assertEqual(estimate["currency"], "CAD")
        self.assertFalse(server.get_dividend_income_estimate("TEST", "US")["available"])
        with server.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM holdings").fetchone()["n"], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM manual_holdings").fetchone()["n"], 0)

    def test_new_us_holding_preview_matches_saved_income_estimate(self):
        self.seed_dividends("TEST")
        server.update_latest_cash_balance(self.account["id"], 100, "USD")
        with patch.object(server, "latest_usd_cad_rate_from_conn", return_value=1.4):
            preview = server.get_dividend_income_estimate("TEST", "US")
            result = server.save_transaction(self.account["id"], "2026-10-05", "BUY",
                                             symbol="TEST", market="US", currency="USD", quantity=3, price=10,
                                             gross_amount=30)
        impact = result["transaction"]["income_impact"]
        self.assertEqual(preview["annual_dividend_per_share"], impact["annual_dividend_per_share"])
        self.assertAlmostEqual(impact["annual_income_change"], 8.4)
        self.assertEqual(impact["shares_before"], 0)

    def test_new_trade_reuses_company_name_from_another_account(self):
        server.import_content(CSV, "holdings.csv", self.other["name"])
        server.update_latest_cash_balance(self.account["id"], 100)
        result = server.save_transaction(self.account["id"], "2026-10-05", "BUY",
                                         symbol="TEST", market="CDN", quantity=2, price=10,
                                         gross_amount=20)
        self.assertEqual(result["transaction"]["description"], "Test stock")
        self.assertEqual(server.list_manual_holdings(self.account["id"])["holdings"][0]["description"], "Test stock")
        self.assertEqual(server.get_dividend_income_estimate("TEST", "CDN")["description"], "Test stock")

    def test_existing_ticker_only_holding_displays_known_company_name(self):
        server.import_content(CSV, "holdings.csv", self.other["name"])
        server.save_manual_holding(self.account["id"], "TEST", "CDN", description="TEST", quantity=2, average_cost=10)
        with server.get_connection() as conn:
            rows = server.manual_holding_summary_rows(conn)
        self.assertEqual(rows[0]["description"], "Test stock")

    def test_company_name_lookup_respects_market_and_explicit_name(self):
        server.import_content(CSV, "holdings.csv", self.other["name"])
        with server.get_connection() as conn:
            self.assertEqual(server.known_security_description(conn, "TEST", "US"), "TEST")
            self.assertEqual(server.known_security_description(conn, "TEST", "CDN", "My custom label"), "My custom label")

    def test_admin_cash_updates_preserve_holdings_and_other_currency(self):
        imported = server.import_content(CSV, "holdings.csv", self.account["name"], 50)
        server.update_latest_cash_balances(self.account["id"], [{"currency": "CAD", "amount": 50}, {"currency": "USD", "amount": 80}])
        result = server.update_admin_cash_cells([
            {"account_id": self.account["id"], "currency": "CAD", "amount": 1234.56, "expected_amount": 50},
            {"account_id": self.other["id"], "currency": "USD", "amount": -20, "expected_amount": 0},
        ])
        self.assertEqual(len(result["accounts"]), 2)
        with server.get_connection() as conn:
            cash = server.cash_balances_for_batch(conn, imported["batch_id"])
            self.assertEqual({row["currency"]: row["amount"] for row in cash}, {"CAD": 1234.56, "USD": 80})
            self.assertEqual(conn.execute("SELECT quantity FROM holdings WHERE batch_id = ?", (imported["batch_id"],)).fetchone()["quantity"], 10)
            batch = server.latest_batch_for_account(conn, self.other["id"])
            self.assertEqual(server.cash_balances_for_batch(conn, batch["id"])[0]["amount"], -20)

    def test_admin_cash_paste_is_atomic_on_invalid_account_or_stale_cell(self):
        server.update_latest_cash_balance(self.account["id"], 100)
        for failed in (
            {"account_id": 9999, "currency": "CAD", "amount": 20, "expected_amount": 0},
            {"account_id": self.other["id"], "currency": "USD", "amount": 20, "expected_amount": 7},
            {"account_id": self.other["id"], "currency": "CAD", "amount": float("nan"), "expected_amount": 0},
        ):
            with self.assertRaises(ValueError):
                server.update_admin_cash_cells([
                    {"account_id": self.account["id"], "currency": "CAD", "amount": 150, "expected_amount": 100}, failed,
                ])
            with server.get_connection() as conn:
                batch = server.latest_batch_for_account(conn, self.account["id"])
                self.assertEqual(server.cash_balances_for_batch(conn, batch["id"])[0]["amount"], 100)

    def income_summary(self, income=100, second_income=0):
        return {"accounts": [self.account, self.other], "holdings": [
            {"account_id": self.account["id"], "symbol": "TEST", "market": "CDN",
             "description": "Test stock", "asset_type": "Stock", "quantity": 50,
             "annual_forward_dividend": income / 50, "annual_forward_income": income, "fx_to_cad": 1},
            {"account_id": self.other["id"], "symbol": "OTHER", "market": "US",
             "description": "Other stock", "asset_type": "Stock", "quantity": 10,
             "annual_forward_dividend": second_income / 10, "annual_forward_income": second_income, "fx_to_cad": 1.4},
        ]}

    def test_weekly_income_uses_last_daily_snapshot_and_live_current_week(self):
        for date, income in (("2026-09-21", 80), ("2026-09-25", 100), ("2026-10-02", 120)):
            with patch.object(server, "get_summary", return_value=self.income_summary(income)):
                server.save_income_snapshot(date, source="auto")
        with patch.object(server, "get_summary", return_value=self.income_summary(130, 10)):
            report = server.get_weekly_income_report("2026-10-05")
        weeks = report["weeks"]
        self.assertEqual(len(weeks), 3)
        self.assertEqual(weeks[0]["annual_income"], 140)
        self.assertEqual(weeks[0]["change"], 20)
        self.assertAlmostEqual(weeks[0]["change_pct"], 20 / 120 * 100)
        self.assertEqual(weeks[0]["comparison_weeks"], 1)
        self.assertEqual(weeks[0]["source"], "live")
        self.assertEqual(weeks[1]["change"], 20)
        self.assertEqual(weeks[2]["market_date"], "2026-09-25")
        self.assertIsNone(weeks[2]["change"])
        changes = {account["account_id"]: account["change"] for account in weeks[0]["account_changes"]}
        self.assertEqual(changes, {self.account["id"]: 10, self.other["id"]: 10})
        with server.get_connection() as conn:
            saved = json.loads(conn.execute("SELECT snapshot_json FROM income_snapshots WHERE market_date = '2026-10-02'").fetchone()["snapshot_json"])
        self.assertEqual(saved["holdings"][0]["annual_dividend_per_share"], 2.4)
        self.assertEqual(saved["annual_income"], 120)

    def test_income_baseline_and_missing_weeks_are_not_reported_as_one_week_growth(self):
        with patch.object(server, "get_summary", return_value=self.income_summary(0)):
            empty = server.get_weekly_income_report("2026-10-05")
            self.assertIsNone(empty["first_saved_date"])
            self.assertIsNone(empty["weeks"][0]["change"])
            server.save_income_snapshot("2026-09-18", source="baseline")
        with patch.object(server, "get_summary", return_value=self.income_summary(50)):
            report = server.get_weekly_income_report("2026-10-05")
        self.assertEqual(report["weeks"][0]["comparison_weeks"], 3)
        self.assertIsNone(report["weeks"][0]["change_pct"])
        self.assertEqual(report["weeks"][0]["change"], 50)

    def test_income_close_replaces_same_day_baseline(self):
        with patch.object(server, "get_summary", return_value=self.income_summary(100)):
            server.save_income_snapshot("2026-10-05", source="baseline")
        with patch.object(server, "get_summary", return_value=self.income_summary(110)):
            server.save_income_snapshot("2026-10-05", source="auto")
        self.assertEqual(server.income_snapshot_source("2026-10-05"), "auto")
        with server.get_connection() as conn:
            rows = conn.execute("SELECT snapshot_json FROM income_snapshots").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0]["snapshot_json"])["annual_income"], 110)

    def test_income_snapshot_still_saved_when_balance_close_already_exists(self):
        with patch.object(api, "snapshot_market_date_for_now", return_value="2026-10-05"), \
             patch.object(api, "balance_snapshot_exists", return_value=True), \
             patch.object(api, "latest_price_status", return_value={"latest_fetched_at": "2026-10-05T22:00:00+00:00"}), \
             patch.object(api, "income_snapshot_source", return_value="baseline"), \
             patch.object(api, "save_income_snapshot") as save_income, \
             patch.object(api, "save_balance_snapshot") as save_balance:
            api.maybe_save_end_of_day_snapshot()
        save_income.assert_called_once_with("2026-10-05", source="auto")
        save_balance.assert_not_called()

    def test_successful_stale_dividend_cache_is_refreshed(self):
        with server.get_connection() as conn:
            conn.execute("INSERT INTO stock_analytics_refreshes (yahoo_symbol, symbol, market, currency, fetched_at, status) VALUES ('TEST.TO', 'TEST', 'CDN', 'CAD', '2020-01-01T00:00:00+00:00', 'ok')")
        with patch.object(server, "refresh_stock_analytics") as refresh:
            server.ensure_stock_analytics_cache_for_holdings([{"symbol": "TEST", "market": "CDN", "asset_type": "Stock"}])
        refresh.assert_called_once_with("TEST", "CDN")


if __name__ == "__main__":
    unittest.main()
