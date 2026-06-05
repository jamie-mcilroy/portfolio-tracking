#!/usr/bin/env python3
import argparse
import csv
import hashlib
import hmac
import io
import json
import math
import re
import secrets
import shutil
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
RESOURCE_DIR = BASE_DIR / "resources"
IMPORT_DIR = DATA_DIR / "imports"
DB_PATH = DATA_DIR / "portfolio.db"
STATIC_DIR = BASE_DIR / "static"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
STOCK_ANALYTICS_TTL_SECONDS = 24 * 60 * 60
PASSWORD_HASH_ALGORITHM = "pbkdf2_sha256"
PASSWORD_HASH_ITERATIONS = 260_000
USD_CAD_SYMBOL = "USDCAD"
USD_CAD_MARKET = "FX"
USD_CAD_YAHOO_SYMBOL = "CAD=X"
PRICE_REFRESH_LOCK = threading.Lock()
PRICE_REFRESH_STALE_AFTER_SECONDS = 30 * 60
YFINANCE_DOWNLOAD_TIMEOUT_SECONDS = 20
EPS_HISTORY_YEARS = 10
EPS_PIVOT_CSV_PATHS = (
    DATA_DIR / "eps_10y_pivot.csv",
    RESOURCE_DIR / "eps_10y_pivot.csv",
)
ALPHAQUERY_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
TRANSACTION_TYPES = {
    "DIVIDEND",
    "DRIP",
    "BUY",
    "SELL",
    "DEPOSIT",
    "WITHDRAWAL",
    "FEE",
    "TAX",
    "INTEREST",
    "FX",
    "TRANSFER_IN",
    "TRANSFER_OUT",
    "ADJUSTMENT",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "account"


def parse_number(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    text = text.replace(",", "").replace("$", "").replace("%", "")

    try:
        number = float(text)
    except ValueError:
        return None

    return -number if negative else number


def finite_float(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def first_cell(row):
    return row[0].strip() if row else ""


def non_empty(value):
    return value is not None and str(value).strip() != ""


def read_csv_rows(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [[cell.strip() for cell in row] for row in csv.reader(handle)]


def detect_account_metadata(rows):
    non_blank_rows = [row for row in rows if any(non_empty(cell) for cell in row)]
    account_name = first_cell(non_blank_rows[0]) if non_blank_rows else "Imported Account"
    owner = first_cell(non_blank_rows[1]) if len(non_blank_rows) > 1 else ""
    report_timestamp = first_cell(non_blank_rows[2]) if len(non_blank_rows) > 2 else ""
    as_of_note = first_cell(non_blank_rows[3]) if len(non_blank_rows) > 3 else ""

    return {
        "account_name": account_name,
        "owner": owner,
        "report_timestamp": report_timestamp,
        "as_of_note": as_of_note,
    }


def find_row_index(rows, label):
    for index, row in enumerate(rows):
        if first_cell(row).lower() == label.lower():
            return index
    return None


def parse_cash_balances_from_rows(rows):
    cash_rows = []
    cash_table_index = find_row_index(rows, "Cash account")

    if cash_table_index is not None:
        headers = rows[cash_table_index]
        header_map = {header.strip().lower(): index for index, header in enumerate(headers)}
        amount_index = header_map.get("cash amount")

        if amount_index is not None:
            for row in rows[cash_table_index + 1 :]:
                if not any(non_empty(cell) for cell in row):
                    break
                label = first_cell(row)
                if not label.lower().startswith("cash "):
                    continue
                currency = label.split()[-1].strip().upper()
                amount = parse_number(row[amount_index] if amount_index < len(row) else None) or 0.0
                cash_rows.append({"currency": normalize_currency(currency), "amount": amount})

    if cash_rows:
        return cash_rows

    for row in rows:
        if first_cell(row).lower().startswith("today's combined cash balance"):
            amount = parse_number(row[1] if len(row) > 1 else None) or 0.0
            label = first_cell(row)
            match = re.search(r"\((?:in\s+)?([A-Z]{3})\)", label, re.IGNORECASE)
            currency = match.group(1).upper() if match else "CAD"
            return [{"currency": normalize_currency(currency), "amount": amount}]

    return []


def parse_holdings_csv(path: Path):
    rows = read_csv_rows(path)
    metadata = detect_account_metadata(rows)
    cash_balances = parse_cash_balances_from_rows(rows)

    summary_index = find_row_index(rows, "Securities held in")
    holdings_index = find_row_index(rows, "Asset type")

    if holdings_index is None:
        if cash_balances:
            metadata["row_count"] = 0
            return {
                "metadata": metadata,
                "currency_summaries": [],
                "holdings": [],
                "cash_balances": cash_balances,
            }
        raise ValueError("Could not find a holdings table headed by 'Asset type'.")

    currency_summaries = []
    if summary_index is not None:
        for row in rows[summary_index + 1 : holdings_index]:
            if not any(non_empty(cell) for cell in row):
                continue
            currency = first_cell(row)
            if not currency:
                continue
            currency_summaries.append(
                {
                    "currency": currency,
                    "closing_value": parse_number(row[1] if len(row) > 1 else None),
                    "book_value": parse_number(row[2] if len(row) > 2 else None),
                    "gain_loss": parse_number(row[3] if len(row) > 3 else None),
                    "gain_loss_pct": parse_number(row[4] if len(row) > 4 else None),
                }
            )

    headers = rows[holdings_index]
    header_map = {header: index for index, header in enumerate(headers)}

    required_headers = [
        "Asset type",
        "Currency held in",
        "Symbol",
        "Market",
        "Description",
        "Quantity",
        "Average Cost",
        "Closing price",
        "Closing value",
        "Book value",
        "Gain/loss $",
        "Gain/loss %",
        "% of portfolio",
    ]
    missing = [header for header in required_headers if header not in header_map]
    if missing:
        raise ValueError(f"Missing expected holdings columns: {', '.join(missing)}")

    def cell(row, header):
        index = header_map[header]
        return row[index].strip() if index < len(row) else ""

    holdings = []
    for row in rows[holdings_index + 1 :]:
        if not any(non_empty(cell_value) for cell_value in row):
            continue
        if not cell(row, "Symbol") and not cell(row, "Description"):
            continue

        holdings.append(
            {
                "asset_type": cell(row, "Asset type"),
                "currency": cell(row, "Currency held in"),
                "symbol": cell(row, "Symbol"),
                "market": cell(row, "Market"),
                "description": cell(row, "Description"),
                "quantity": parse_number(cell(row, "Quantity")),
                "average_cost": parse_number(cell(row, "Average Cost")),
                "closing_price": parse_number(cell(row, "Closing price")),
                "closing_value": parse_number(cell(row, "Closing value")) or 0.0,
                "book_value": parse_number(cell(row, "Book value")) or 0.0,
                "gain_loss": parse_number(cell(row, "Gain/loss $")) or 0.0,
                "gain_loss_pct": parse_number(cell(row, "Gain/loss %")),
                "portfolio_pct": parse_number(cell(row, "% of portfolio")),
            }
        )

    if not currency_summaries and holdings:
        by_currency = {}
        for holding in holdings:
            currency = holding["currency"] or "UNKNOWN"
            bucket = by_currency.setdefault(
                currency,
                {"currency": currency, "closing_value": 0.0, "book_value": 0.0, "gain_loss": 0.0},
            )
            bucket["closing_value"] += holding["closing_value"]
            bucket["book_value"] += holding["book_value"]
            bucket["gain_loss"] += holding["gain_loss"]

        for bucket in by_currency.values():
            book_value = bucket["book_value"]
            bucket["gain_loss_pct"] = (bucket["gain_loss"] / book_value * 100.0) if book_value else None
            currency_summaries.append(bucket)

    metadata["row_count"] = len(holdings)
    return {
        "metadata": metadata,
        "currency_summaries": currency_summaries,
        "holdings": holdings,
        "cash_balances": cash_balances,
    }


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IMPORT_DIR.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                owner TEXT,
                account_entity TEXT NOT NULL DEFAULT 'Personal',
                account_type TEXT NOT NULL DEFAULT 'Investment',
                base_currency TEXT NOT NULL DEFAULT 'CAD',
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                last_login_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS login_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT NOT NULL,
                success INTEGER NOT NULL DEFAULT 0,
                ip_address TEXT,
                user_agent TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS import_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                source_filename TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                file_sha256 TEXT NOT NULL UNIQUE,
                imported_at TEXT NOT NULL,
                report_timestamp TEXT,
                as_of_note TEXT,
                row_count INTEGER NOT NULL,
                total_closing_value REAL NOT NULL DEFAULT 0,
                total_book_value REAL NOT NULL DEFAULT 0,
                total_gain_loss REAL NOT NULL DEFAULT 0,
                total_gain_loss_pct REAL,
                metadata_json TEXT NOT NULL,
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            );

            CREATE TABLE IF NOT EXISTS currency_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER NOT NULL,
                currency TEXT NOT NULL,
                closing_value REAL NOT NULL DEFAULT 0,
                book_value REAL NOT NULL DEFAULT 0,
                gain_loss REAL NOT NULL DEFAULT 0,
                gain_loss_pct REAL,
                FOREIGN KEY (batch_id) REFERENCES import_batches(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS holdings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER NOT NULL,
                asset_type TEXT,
                currency TEXT,
                symbol TEXT,
                market TEXT,
                description TEXT,
                quantity REAL,
                average_cost REAL,
                closing_price REAL,
                closing_value REAL NOT NULL DEFAULT 0,
                book_value REAL NOT NULL DEFAULT 0,
                gain_loss REAL NOT NULL DEFAULT 0,
                gain_loss_pct REAL,
                portfolio_pct REAL,
                FOREIGN KEY (batch_id) REFERENCES import_batches(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS cash_balances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER NOT NULL,
                currency TEXT NOT NULL,
                amount REAL NOT NULL DEFAULT 0,
                FOREIGN KEY (batch_id) REFERENCES import_batches(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS manual_holdings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                asset_type TEXT NOT NULL DEFAULT 'Stock',
                currency TEXT NOT NULL DEFAULT 'CAD',
                symbol TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT '',
                description TEXT,
                quantity REAL NOT NULL DEFAULT 0,
                average_cost REAL,
                manual_price REAL,
                notes TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (account_id, symbol, market),
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS portfolio_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                transaction_date TEXT NOT NULL,
                transaction_type TEXT NOT NULL,
                symbol TEXT,
                market TEXT,
                description TEXT,
                currency TEXT NOT NULL DEFAULT 'CAD',
                quantity REAL,
                price REAL,
                dividend_per_share REAL,
                gross_amount REAL NOT NULL DEFAULT 0,
                fees REAL NOT NULL DEFAULT 0,
                tax REAL NOT NULL DEFAULT 0,
                net_amount REAL NOT NULL DEFAULT 0,
                notes TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS private_fund_marks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                mark_date TEXT NOT NULL,
                beginning_balance REAL NOT NULL DEFAULT 0,
                net_income REAL NOT NULL DEFAULT 0,
                withdrawal REAL NOT NULL DEFAULT 0,
                contribution REAL NOT NULL DEFAULT 0,
                ending_balance REAL NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'USD',
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (account_id, mark_date),
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS latest_prices (
                symbol TEXT NOT NULL,
                market TEXT NOT NULL,
                yahoo_symbol TEXT NOT NULL,
                price REAL,
                price_currency TEXT,
                fx_to_cad REAL NOT NULL DEFAULT 1,
                price_cad REAL,
                previous_close REAL,
                previous_close_cad REAL,
                day_change REAL,
                day_change_pct REAL,
                quote_time TEXT,
                fetched_at TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'yfinance',
                error TEXT,
                PRIMARY KEY (symbol, market)
            );

            CREATE TABLE IF NOT EXISTS price_refreshes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL,
                symbol_count INTEGER NOT NULL DEFAULT 0,
                error TEXT
            );

            CREATE TABLE IF NOT EXISTS stock_price_history (
                yahoo_symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                close REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'CAD',
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (yahoo_symbol, date)
            );

            CREATE TABLE IF NOT EXISTS stock_dividend_history (
                yahoo_symbol TEXT NOT NULL,
                ex_date TEXT NOT NULL,
                dividend_per_share REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'CAD',
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (yahoo_symbol, ex_date)
            );

            CREATE TABLE IF NOT EXISTS stock_analytics_refreshes (
                yahoo_symbol TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL,
                currency TEXT NOT NULL DEFAULT 'CAD',
                fetched_at TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT
            );

            CREATE TABLE IF NOT EXISTS stock_fundamentals (
                yahoo_symbol TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL,
                currency TEXT NOT NULL DEFAULT 'CAD',
                eps_current REAL,
                pe_ratio REAL,
                book_value_per_share REAL,
                fifty_two_week_high REAL,
                fifty_two_week_low REAL,
                fetched_at TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT
            );

            CREATE TABLE IF NOT EXISTS stock_eps_history (
                yahoo_symbol TEXT NOT NULL,
                fiscal_year INTEGER NOT NULL,
                eps REAL NOT NULL,
                fetched_at TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'alphaquery',
                PRIMARY KEY (yahoo_symbol, fiscal_year)
            );

            CREATE TABLE IF NOT EXISTS stock_eps_refreshes (
                yahoo_symbol TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT
            );

            CREATE TABLE IF NOT EXISTS fundamentals_watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT 'CDN',
                description TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (symbol, market)
            );

            CREATE TABLE IF NOT EXISTS balance_snapshots (
                market_date TEXT PRIMARY KEY,
                total_value REAL NOT NULL DEFAULT 0,
                imported_value REAL NOT NULL DEFAULT 0,
                book_value REAL NOT NULL DEFAULT 0,
                gain_loss REAL NOT NULL DEFAULT 0,
                gain_loss_pct REAL,
                day_change REAL,
                day_change_pct REAL,
                price_fetched_at TEXT,
                source TEXT NOT NULL DEFAULT 'auto',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS account_balance_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_date TEXT NOT NULL,
                account_id INTEGER NOT NULL,
                account_name TEXT NOT NULL,
                account_entity TEXT NOT NULL DEFAULT 'Personal',
                account_type TEXT,
                value REAL NOT NULL DEFAULT 0,
                imported_value REAL NOT NULL DEFAULT 0,
                book_value REAL NOT NULL DEFAULT 0,
                cash_balance REAL NOT NULL DEFAULT 0,
                gain_loss REAL NOT NULL DEFAULT 0,
                gain_loss_pct REAL,
                day_change REAL,
                day_change_pct REAL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (market_date, account_id),
                FOREIGN KEY (market_date) REFERENCES balance_snapshots(market_date) ON DELETE CASCADE,
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            );

            """
        )
        account_columns = {row[1] for row in conn.execute("PRAGMA table_info(accounts)").fetchall()}
        account_column_defs = {
            "account_entity": "TEXT NOT NULL DEFAULT 'Personal'",
            "account_type": "TEXT NOT NULL DEFAULT 'Investment'",
            "base_currency": "TEXT NOT NULL DEFAULT 'CAD'",
            "notes": "TEXT",
        }
        for column, definition in account_column_defs.items():
            if column not in account_columns:
                conn.execute(f"ALTER TABLE accounts ADD COLUMN {column} {definition}")

        snapshot_columns = {row[1] for row in conn.execute("PRAGMA table_info(account_balance_snapshots)").fetchall()}
        snapshot_column_defs = {
            "account_entity": "TEXT NOT NULL DEFAULT 'Personal'",
        }
        for column, definition in snapshot_column_defs.items():
            if column not in snapshot_columns:
                conn.execute(f"ALTER TABLE account_balance_snapshots ADD COLUMN {column} {definition}")

        price_columns = {row[1] for row in conn.execute("PRAGMA table_info(latest_prices)").fetchall()}
        price_column_defs = {
            "previous_close": "REAL",
            "previous_close_cad": "REAL",
            "day_change": "REAL",
            "day_change_pct": "REAL",
        }
        for column, definition in price_column_defs.items():
            if column not in price_columns:
                conn.execute(f"ALTER TABLE latest_prices ADD COLUMN {column} {definition}")

        fundamentals_columns = {row[1] for row in conn.execute("PRAGMA table_info(stock_fundamentals)").fetchall()}
        fundamentals_column_defs = {
            "eps_current": "REAL",
        }
        for column, definition in fundamentals_column_defs.items():
            if column not in fundamentals_columns:
                conn.execute(f"ALTER TABLE stock_fundamentals ADD COLUMN {column} {definition}")


def dict_from_row(cursor, row):
    return {column[0]: row[index] for index, column in enumerate(cursor.description)}


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = dict_from_row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def hash_password(password):
    text = str(password or "")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        text.encode("utf-8"),
        salt.encode("utf-8"),
        PASSWORD_HASH_ITERATIONS,
    ).hex()
    return f"{PASSWORD_HASH_ALGORITHM}${PASSWORD_HASH_ITERATIONS}${salt}${digest}"


def verify_password(password, stored_hash):
    try:
        algorithm, iterations_text, salt, expected = str(stored_hash or "").split("$", 3)
        iterations = int(iterations_text)
    except (TypeError, ValueError):
        return False

    if algorithm != PASSWORD_HASH_ALGORITHM or iterations <= 0:
        return False

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        str(password or "").encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
    return hmac.compare_digest(digest, expected)


def clean_username(username):
    return str(username or "").strip()


def public_user(row):
    if not row:
        return None
    user = dict(row)
    user.pop("password_hash", None)
    user["is_admin"] = bool(user.get("is_admin"))
    user["active"] = bool(user.get("active"))
    return user


def ensure_auth_user(username, password, is_admin=True):
    init_db()
    clean_name = clean_username(username)
    if not clean_name:
        raise ValueError("Username is required.")

    with get_connection() as conn:
        existing = conn.execute("SELECT id FROM users WHERE username = ?", (clean_name,)).fetchone()
        if existing:
            return public_user(conn.execute("SELECT * FROM users WHERE id = ?", (existing["id"],)).fetchone())

        now = utc_now()
        conn.execute(
            """
            INSERT INTO users (username, password_hash, is_admin, active, created_at, updated_at)
            VALUES (?, ?, ?, 1, ?, ?)
            """,
            (clean_name, hash_password(password), 1 if is_admin else 0, now, now),
        )
        return public_user(conn.execute("SELECT * FROM users WHERE username = ?", (clean_name,)).fetchone())


def create_user(username, password, is_admin=False, active=True):
    init_db()
    clean_name = clean_username(username)
    if not clean_name:
        raise ValueError("Username is required.")
    if len(clean_name) > 80:
        raise ValueError("Username must be 80 characters or fewer.")
    if not str(password or ""):
        raise ValueError("Password is required.")
    if len(str(password)) < 8:
        raise ValueError("Password must be at least 8 characters.")

    now = utc_now()
    with get_connection() as conn:
        try:
            conn.execute(
                """
                INSERT INTO users (username, password_hash, is_admin, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_name,
                    hash_password(password),
                    1 if is_admin else 0,
                    1 if active else 0,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"User {clean_name} already exists.") from exc
        return public_user(conn.execute("SELECT * FROM users WHERE username = ?", (clean_name,)).fetchone())


def get_user_by_username(username):
    init_db()
    clean_name = clean_username(username)
    if not clean_name:
        return None
    with get_connection() as conn:
        return public_user(conn.execute("SELECT * FROM users WHERE username = ?", (clean_name,)).fetchone())


def authenticate_user(username, password):
    init_db()
    clean_name = clean_username(username)
    if not clean_name:
        return None

    with get_connection() as conn:
        user = conn.execute("SELECT * FROM users WHERE username = ?", (clean_name,)).fetchone()
        if not user or not user["active"] or not verify_password(password, user["password_hash"]):
            return None

        now = utc_now()
        conn.execute("UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?", (now, now, user["id"]))
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        return public_user(user)


def list_users():
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, username, is_admin, active, last_login_at, created_at, updated_at
            FROM users
            ORDER BY username
            """
        ).fetchall()
    return [public_user(row) for row in rows]


def record_login_event(username, user_id=None, success=False, ip_address="", user_agent=""):
    init_db()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO login_events (user_id, username, success, ip_address, user_agent, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                clean_username(username) or "(blank)",
                1 if success else 0,
                str(ip_address or "")[:120],
                str(user_agent or "")[:500],
                utc_now(),
            ),
        )


def list_login_events(limit=100):
    init_db()
    safe_limit = max(1, min(int(limit or 100), 500))
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                login_events.id,
                login_events.user_id,
                login_events.username,
                login_events.success,
                login_events.ip_address,
                login_events.user_agent,
                login_events.created_at
            FROM login_events
            ORDER BY login_events.created_at DESC, login_events.id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    events = [dict(row) for row in rows]
    for event in events:
        event["success"] = bool(event.get("success"))
    return events


def upsert_account(conn, name, owner):
    now = utc_now()
    conn.execute(
        """
        INSERT INTO accounts (name, owner, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            owner = excluded.owner,
            updated_at = excluded.updated_at
        """,
        (name, owner, now, now),
    )
    row = conn.execute("SELECT id FROM accounts WHERE name = ?", (name,)).fetchone()
    return row["id"]


def normalize_currency(value):
    currency = (value or "CAD").strip().upper()
    return currency or "CAD"


def normalize_account_type(value):
    account_type = (value or "Investment").strip()
    return account_type or "Investment"


def normalize_account_entity(value):
    text = str(value or "Personal").strip().lower()
    if text in {"corporate", "corp", "business", "company"}:
        return "Corporate"
    return "Personal"


def normalize_private_fund_mark_date(value):
    text = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    raise ValueError("Mark date must be YYYY-MM-DD.")


def normalize_transaction_date(value):
    text = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    raise ValueError("Transaction date must be YYYY-MM-DD.")


def normalize_transaction_type(value):
    clean_type = str(value or "").strip().upper().replace(" ", "_").replace("-", "_")
    if clean_type not in TRANSACTION_TYPES:
        raise ValueError("Unsupported transaction type.")
    return clean_type


def money_value(value):
    number = finite_float(parse_number(value) if isinstance(value, str) else value)
    return number if number is not None else 0.0


def optional_money_value(value):
    if value is None or str(value).strip() == "":
        return None
    return money_value(value)


def default_transaction_net_amount(transaction_type, quantity, price, gross_amount, fees, tax):
    quantity_value = money_value(quantity)
    price_value = money_value(price)
    gross_value = money_value(gross_amount)
    fee_value = money_value(fees)
    tax_value = money_value(tax)
    trade_value = quantity_value * price_value if quantity_value and price_value else gross_value

    if transaction_type in {"BUY", "DRIP"}:
        return -abs(trade_value) - abs(fee_value) - abs(tax_value)
    if transaction_type == "SELL":
        return abs(trade_value) - abs(fee_value) - abs(tax_value)
    if transaction_type in {"DIVIDEND", "INTEREST", "TRANSFER_IN", "DEPOSIT"}:
        return abs(gross_value) - abs(fee_value) - abs(tax_value)
    if transaction_type in {"WITHDRAWAL", "TRANSFER_OUT"}:
        return -abs(gross_value)
    if transaction_type == "FEE":
        return -abs(gross_value or fee_value)
    if transaction_type == "TAX":
        return -abs(gross_value or tax_value)
    return gross_value - abs(fee_value) - abs(tax_value)


def normalize_cash_balances(cash_balances):
    totals = {}
    for item in cash_balances or []:
        if item is None:
            continue
        if isinstance(item, dict):
            currency = normalize_currency(item.get("currency"))
            amount = money_value(item.get("amount"))
        else:
            currency = normalize_currency(getattr(item, "currency", "CAD"))
            amount = money_value(getattr(item, "amount", 0.0))
        if not amount:
            continue
        totals[currency] = totals.get(currency, 0.0) + amount
    return [
        {"currency": currency, "amount": amount}
        for currency, amount in sorted(totals.items())
        if amount
    ]


def latest_usd_cad_rate_from_conn(conn):
    row = conn.execute(
        """
        SELECT price
        FROM latest_prices
        WHERE symbol = ? AND market = ? AND price IS NOT NULL
        ORDER BY fetched_at DESC
        LIMIT 1
        """,
        (USD_CAD_SYMBOL, USD_CAD_MARKET),
    ).fetchone()
    rate = finite_float(row["price"] if row else None)
    if rate and rate > 0:
        return rate

    row = conn.execute(
        """
        SELECT fx_to_cad
        FROM latest_prices
        WHERE UPPER(market) = 'US' AND fx_to_cad IS NOT NULL AND fx_to_cad > 0
        ORDER BY fetched_at DESC
        LIMIT 1
        """
    ).fetchone()
    rate = finite_float(row["fx_to_cad"] if row else None)
    return rate if rate and rate > 0 else 1.0


def fx_to_cad_for_currency(currency, usd_cad_rate=1.0):
    clean_currency = normalize_currency(currency)
    if clean_currency == "CAD":
        return 1.0
    if clean_currency == "USD":
        rate = finite_float(usd_cad_rate)
        return rate if rate and rate > 0 else 1.0
    return 1.0


def cash_balances_with_cad_values(cash_balances, usd_cad_rate=1.0):
    balances = []
    for item in normalize_cash_balances(cash_balances):
        fx_to_cad = fx_to_cad_for_currency(item["currency"], usd_cad_rate)
        balances.append(
            {
                **item,
                "fx_to_cad": fx_to_cad,
                "value_cad": item["amount"] * fx_to_cad,
            }
        )
    return balances


def cash_total_cad(cash_balances, usd_cad_rate=1.0):
    return sum(item["value_cad"] for item in cash_balances_with_cad_values(cash_balances, usd_cad_rate))


def save_account(name, owner="", account_type="Investment", base_currency="CAD", notes="", account_entity="Personal"):
    init_db()
    account_name = (name or "").strip()
    if not account_name:
        raise ValueError("Account name is required.")

    now = utc_now()
    with get_connection() as conn:
        existing = conn.execute("SELECT id FROM accounts WHERE name = ?", (account_name,)).fetchone()
        conn.execute(
            """
            INSERT INTO accounts (
                name, owner, account_entity, account_type, base_currency, notes, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                owner = excluded.owner,
                account_entity = excluded.account_entity,
                account_type = excluded.account_type,
                base_currency = excluded.base_currency,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (
                account_name,
                (owner or "").strip(),
                normalize_account_entity(account_entity),
                normalize_account_type(account_type),
                normalize_currency(base_currency),
                (notes or "").strip(),
                now,
                now,
            ),
        )
        account = conn.execute(
            """
            SELECT id, name, owner, account_entity, account_type, base_currency, notes, created_at, updated_at
            FROM accounts
            WHERE name = ?
            """,
            (account_name,),
        ).fetchone()

    return {"created": existing is None, "account": account}


def update_account(account_id, name, owner="", account_type="Investment", base_currency="CAD", notes="", account_entity="Personal"):
    init_db()
    account_name = (name or "").strip()
    if not account_name:
        raise ValueError("Account name is required.")

    now = utc_now()
    with get_connection() as conn:
        existing = conn.execute("SELECT id FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not existing:
            raise ValueError("Account not found.")

        duplicate = conn.execute(
            "SELECT id FROM accounts WHERE name = ? AND id != ?",
            (account_name, account_id),
        ).fetchone()
        if duplicate:
            raise ValueError("Another account already uses that name.")

        conn.execute(
            """
            UPDATE accounts
            SET
                name = ?,
                owner = ?,
                account_entity = ?,
                account_type = ?,
                base_currency = ?,
                notes = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                account_name,
                (owner or "").strip(),
                normalize_account_entity(account_entity),
                normalize_account_type(account_type),
                normalize_currency(base_currency),
                (notes or "").strip(),
                now,
                account_id,
            ),
        )
        account = conn.execute(
            """
            SELECT id, name, owner, account_entity, account_type, base_currency, notes, created_at, updated_at
            FROM accounts
            WHERE id = ?
            """,
            (account_id,),
        ).fetchone()

    return {"updated": True, "account": account}


def create_manual_cash_batch(conn, account_id, cash_balances, now):
    account = conn.execute(
        """
        SELECT id, name, owner, base_currency
        FROM accounts
        WHERE id = ?
        """,
        (account_id,),
    ).fetchone()
    if not account:
        raise ValueError("Account not found.")

    usd_cad_rate = latest_usd_cad_rate_from_conn(conn)
    valued_cash_balances = cash_balances_with_cad_values(cash_balances, usd_cad_rate)
    total_cash_cad = sum(item["value_cad"] for item in valued_cash_balances)
    currencies = {item["currency"] for item in valued_cash_balances}
    cash_currency_label = next(iter(currencies)) if len(currencies) == 1 else ("MIXED" if currencies else "")
    report_timestamp = f"Manual cash balance {now[:10]}"
    file_sha = hashlib.sha256(
        f"manual-cash:{account_id}:{now}:{secrets.token_hex(8)}".encode("utf-8")
    ).hexdigest()
    metadata = {
        "account_name": account["name"],
        "owner": account.get("owner") or "",
        "report_timestamp": report_timestamp,
        "as_of_note": "Initialized without holdings import",
        "cash_balance": total_cash_cad,
        "cash_balances": valued_cash_balances,
        "cash_currency": cash_currency_label,
        "manual_cash_initialization": True,
    }

    cursor = conn.execute(
        """
        INSERT INTO import_batches (
            account_id,
            source_filename,
            stored_path,
            file_sha256,
            imported_at,
            report_timestamp,
            as_of_note,
            row_count,
            total_closing_value,
            total_book_value,
            total_gain_loss,
            total_gain_loss_pct,
            metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account_id,
            "Manual cash balance",
            f"manual://cash/{account_id}/{now}",
            file_sha,
            now,
            report_timestamp,
            "Initialized without holdings import",
            0,
            total_cash_cad,
            total_cash_cad,
            0.0,
            0.0 if total_cash_cad else None,
            json.dumps(metadata, sort_keys=True),
        ),
    )
    batch_id = cursor.lastrowid
    if valued_cash_balances:
        conn.executemany(
            """
            INSERT INTO cash_balances (batch_id, currency, amount)
            VALUES (?, ?, ?)
            """,
            [(batch_id, item["currency"], item["amount"]) for item in valued_cash_balances],
        )
    return batch_id


def private_fund_summary_from_rows(rows):
    if not rows:
        return {
            "latest": None,
            "currency": "USD",
            "balance": 0.0,
            "beginning_balance": 0.0,
            "total_contributions": 0.0,
            "total_withdrawals": 0.0,
            "total_income": 0.0,
            "book_value": 0.0,
            "gain_loss": 0.0,
            "roi_pct": None,
        }

    ordered = sorted(rows, key=lambda row: row["mark_date"])
    latest = ordered[-1]
    first_beginning = ordered[0]["beginning_balance"] or 0.0
    total_contributions = sum(row["contribution"] or 0.0 for row in ordered)
    total_withdrawals = sum(row["withdrawal"] or 0.0 for row in ordered)
    total_income = sum(row["net_income"] or 0.0 for row in ordered)
    book_value = first_beginning + total_contributions - total_withdrawals
    balance = latest["ending_balance"] or 0.0
    gain_loss = balance - book_value
    return {
        "latest": latest,
        "currency": latest["currency"] or "USD",
        "balance": balance,
        "beginning_balance": first_beginning,
        "total_contributions": total_contributions,
        "total_withdrawals": total_withdrawals,
        "total_income": total_income,
        "book_value": book_value,
        "gain_loss": gain_loss,
        "roi_pct": (gain_loss / book_value * 100.0) if book_value else None,
    }


def sync_private_fund_current_batch(conn, account_id):
    account = conn.execute(
        """
        SELECT id, name, owner, base_currency
        FROM accounts
        WHERE id = ?
        """,
        (account_id,),
    ).fetchone()
    if not account:
        raise ValueError("Account not found.")

    marks = conn.execute(
        """
        SELECT *
        FROM private_fund_marks
        WHERE account_id = ?
        ORDER BY mark_date
        """,
        (account_id,),
    ).fetchall()
    if not marks:
        return None

    summary = private_fund_summary_from_rows(marks)
    latest = summary["latest"]
    now = utc_now()
    stored_path = f"manual://private-fund/current/{account_id}"
    file_sha = hashlib.sha256(stored_path.encode("utf-8")).hexdigest()
    report_timestamp = latest["mark_date"]
    metadata = {
        "account_name": account["name"],
        "owner": account.get("owner") or "",
        "report_timestamp": report_timestamp,
        "as_of_note": "Manual private fund mark",
        "private_fund": True,
        "private_fund_mark_date": latest["mark_date"],
        "currency": summary["currency"],
        "total_contributions": summary["total_contributions"],
        "total_withdrawals": summary["total_withdrawals"],
        "total_income": summary["total_income"],
    }

    existing = conn.execute(
        """
        SELECT id
        FROM import_batches
        WHERE account_id = ? AND stored_path = ?
        """,
        (account_id, stored_path),
    ).fetchone()

    total_value = summary["balance"]
    book_value = summary["book_value"]
    gain_loss = summary["gain_loss"]
    gain_loss_pct = summary["roi_pct"]

    if existing:
        batch_id = existing["id"]
        conn.execute(
            """
            UPDATE import_batches
            SET
                source_filename = ?,
                file_sha256 = ?,
                imported_at = ?,
                report_timestamp = ?,
                as_of_note = ?,
                row_count = 1,
                total_closing_value = ?,
                total_book_value = ?,
                total_gain_loss = ?,
                total_gain_loss_pct = ?,
                metadata_json = ?
            WHERE id = ?
            """,
            (
                "Manual private fund mark",
                file_sha,
                now,
                report_timestamp,
                "Manual private fund mark",
                total_value,
                book_value,
                gain_loss,
                gain_loss_pct,
                json.dumps(metadata, sort_keys=True),
                batch_id,
            ),
        )
        conn.execute("DELETE FROM holdings WHERE batch_id = ?", (batch_id,))
        conn.execute("DELETE FROM currency_summaries WHERE batch_id = ?", (batch_id,))
        conn.execute("DELETE FROM cash_balances WHERE batch_id = ?", (batch_id,))
    else:
        cursor = conn.execute(
            """
            INSERT INTO import_batches (
                account_id,
                source_filename,
                stored_path,
                file_sha256,
                imported_at,
                report_timestamp,
                as_of_note,
                row_count,
                total_closing_value,
                total_book_value,
                total_gain_loss,
                total_gain_loss_pct,
                metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                "Manual private fund mark",
                stored_path,
                file_sha,
                now,
                report_timestamp,
                "Manual private fund mark",
                1,
                total_value,
                book_value,
                gain_loss,
                gain_loss_pct,
                json.dumps(metadata, sort_keys=True),
            ),
        )
        batch_id = cursor.lastrowid

    currency = summary["currency"]
    conn.execute(
        """
        INSERT INTO currency_summaries (
            batch_id, currency, closing_value, book_value, gain_loss, gain_loss_pct
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (batch_id, currency, total_value, book_value, gain_loss, gain_loss_pct),
    )
    conn.execute(
        """
        INSERT INTO holdings (
            batch_id,
            asset_type,
            currency,
            symbol,
            market,
            description,
            quantity,
            average_cost,
            closing_price,
            closing_value,
            book_value,
            gain_loss,
            gain_loss_pct,
            portfolio_pct
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            batch_id,
            "Private Fund",
            currency,
            "ATNV",
            "PRIVATE",
            account["name"],
            1.0,
            book_value,
            total_value,
            total_value,
            book_value,
            gain_loss,
            gain_loss_pct,
            100.0,
        ),
    )
    conn.execute("UPDATE accounts SET updated_at = ? WHERE id = ?", (now, account_id))
    return batch_id


def get_private_fund_marks(account_id):
    init_db()
    with get_connection() as conn:
        account = conn.execute(
            """
            SELECT id, name, account_type, base_currency
            FROM accounts
            WHERE id = ?
            """,
            (account_id,),
        ).fetchone()
        if not account:
            raise ValueError("Account not found.")
        marks = conn.execute(
            """
            SELECT *
            FROM private_fund_marks
            WHERE account_id = ?
            ORDER BY mark_date DESC
            """,
            (account_id,),
        ).fetchall()

    ordered_for_summary = sorted(marks, key=lambda row: row["mark_date"])
    return {
        "account": account,
        "marks": marks,
        "summary": private_fund_summary_from_rows(ordered_for_summary),
    }


def save_private_fund_mark(
    account_id,
    mark_date,
    beginning_balance=0.0,
    net_income=0.0,
    withdrawal=0.0,
    contribution=0.0,
    ending_balance=0.0,
    currency="USD",
    notes="",
):
    init_db()
    clean_date = normalize_private_fund_mark_date(mark_date)
    clean_currency = normalize_currency(currency or "USD")
    now = utc_now()

    with get_connection() as conn:
        account = conn.execute("SELECT id FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not account:
            raise ValueError("Account not found.")
        conn.execute(
            """
            INSERT INTO private_fund_marks (
                account_id, mark_date, beginning_balance, net_income, withdrawal,
                contribution, ending_balance, currency, notes, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, mark_date) DO UPDATE SET
                beginning_balance = excluded.beginning_balance,
                net_income = excluded.net_income,
                withdrawal = excluded.withdrawal,
                contribution = excluded.contribution,
                ending_balance = excluded.ending_balance,
                currency = excluded.currency,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (
                account_id,
                clean_date,
                money_value(beginning_balance),
                money_value(net_income),
                money_value(withdrawal),
                money_value(contribution),
                money_value(ending_balance),
                clean_currency,
                str(notes or "").strip(),
                now,
                now,
            ),
        )
        sync_private_fund_current_batch(conn, account_id)

    return get_private_fund_marks(account_id)


def manual_holding_values(row, usd_cad_rate=1.0):
    quantity = money_value(row.get("quantity"))
    average_cost_native = finite_float(row.get("average_cost"))
    manual_price_native = finite_float(row.get("manual_price"))
    fx_to_cad = fx_to_cad_for_currency(row.get("currency"), usd_cad_rate)
    price_native = manual_price_native if manual_price_native is not None else (average_cost_native or 0.0)
    book_value = quantity * (average_cost_native or 0.0)
    closing_price = price_native if price_native is not None else None
    closing_value = quantity * closing_price if closing_price is not None else 0.0
    gain_loss = closing_value - book_value
    return {
        "quantity": quantity,
        "average_cost": average_cost_native,
        "closing_price": closing_price,
        "closing_value": closing_value,
        "book_value": book_value,
        "gain_loss": gain_loss,
        "gain_loss_pct": (gain_loss / book_value * 100.0) if book_value else None,
        "fx_to_cad": fx_to_cad,
        "source_average_cost": average_cost_native,
        "source_manual_price": manual_price_native,
    }


def manual_holding_summary_rows(conn, usd_cad_rate=1.0):
    rows = conn.execute(
        """
        SELECT
            manual_holdings.*,
            accounts.name AS account_name,
            accounts.account_entity AS account_entity
        FROM manual_holdings
        JOIN accounts ON accounts.id = manual_holdings.account_id
        WHERE manual_holdings.active = 1
        ORDER BY accounts.name, manual_holdings.symbol
        """
    ).fetchall()

    holdings = []
    for row in rows:
        values = manual_holding_values(row, usd_cad_rate)
        holdings.append(
            {
                "id": f"manual-{row['id']}",
                "manual_holding_id": row["id"],
                "batch_id": None,
                "asset_type": row["asset_type"] or "Stock",
                "currency": normalize_currency(row["currency"]),
                "symbol": str(row["symbol"] or "").strip().upper(),
                "market": str(row["market"] or "").strip().upper(),
                "description": row["description"] or row["symbol"],
                "quantity": values["quantity"],
                "average_cost": values["average_cost"],
                "closing_price": values["closing_price"],
                "closing_value": values["closing_value"],
                "book_value": values["book_value"],
                "gain_loss": values["gain_loss"],
                "gain_loss_pct": values["gain_loss_pct"],
                "portfolio_pct": None,
                "account_name": row["account_name"],
                "account_entity": row.get("account_entity") or "Personal",
                "report_timestamp": "Manual holding",
                "imported_at": row["updated_at"],
                "manual_holding": True,
                "notes": row.get("notes") or "",
                "source_average_cost": values["source_average_cost"],
                "source_manual_price": values["source_manual_price"],
                "source_currency": normalize_currency(row["currency"]),
            }
        )
    return holdings


def list_manual_holdings(account_id):
    init_db()
    with get_connection() as conn:
        account = conn.execute(
            """
            SELECT id, name, account_type, base_currency
            FROM accounts
            WHERE id = ?
            """,
            (account_id,),
        ).fetchone()
        if not account:
            raise ValueError("Account not found.")
        rows = conn.execute(
            """
            SELECT *
            FROM manual_holdings
            WHERE account_id = ? AND active = 1
            ORDER BY symbol, market
            """,
            (account_id,),
        ).fetchall()

    return {"account": account, "holdings": rows}


def save_manual_holding(
    account_id,
    symbol,
    market="",
    description="",
    currency="CAD",
    quantity=0.0,
    average_cost=None,
    manual_price=None,
    asset_type="Stock",
    notes="",
):
    init_db()
    clean_symbol = str(symbol or "").strip().upper()
    clean_market = str(market or "").strip().upper()
    if not clean_symbol:
        raise ValueError("Ticker is required.")
    clean_quantity = money_value(quantity)
    if clean_quantity <= 0:
        raise ValueError("Quantity must be greater than zero.")

    clean_average_cost = finite_float(parse_number(average_cost) if isinstance(average_cost, str) else average_cost)
    clean_manual_price = finite_float(parse_number(manual_price) if isinstance(manual_price, str) else manual_price)
    now = utc_now()

    with get_connection() as conn:
        account = conn.execute("SELECT id FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not account:
            raise ValueError("Account not found.")
        conn.execute(
            """
            INSERT INTO manual_holdings (
                account_id, asset_type, currency, symbol, market, description,
                quantity, average_cost, manual_price, notes, active, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(account_id, symbol, market) DO UPDATE SET
                asset_type = excluded.asset_type,
                currency = excluded.currency,
                description = excluded.description,
                quantity = excluded.quantity,
                average_cost = excluded.average_cost,
                manual_price = excluded.manual_price,
                notes = excluded.notes,
                active = 1,
                updated_at = excluded.updated_at
            """,
            (
                account_id,
                normalize_account_type(asset_type),
                normalize_currency(currency),
                clean_symbol,
                clean_market,
                str(description or clean_symbol).strip(),
                clean_quantity,
                clean_average_cost,
                clean_manual_price,
                str(notes or "").strip(),
                now,
                now,
            ),
        )
        conn.execute("UPDATE accounts SET updated_at = ? WHERE id = ?", (now, account_id))

    return list_manual_holdings(account_id)


def delete_manual_holding(account_id, holding_id):
    init_db()
    now = utc_now()
    with get_connection() as conn:
        holding = conn.execute(
            """
            SELECT id
            FROM manual_holdings
            WHERE id = ? AND account_id = ?
            """,
            (holding_id, account_id),
        ).fetchone()
        if not holding:
            raise ValueError("Manual holding not found.")
        conn.execute(
            """
            UPDATE manual_holdings
            SET active = 0, updated_at = ?
            WHERE id = ? AND account_id = ?
            """,
            (now, holding_id, account_id),
        )
        conn.execute("UPDATE accounts SET updated_at = ? WHERE id = ?", (now, account_id))

    return list_manual_holdings(account_id)


def list_transactions(account_id=None, limit=250):
    init_db()
    bounded_limit = max(1, min(int(limit or 250), 1000))
    filters = ["portfolio_transactions.active = 1"]
    params = []
    if account_id:
        filters.append("portfolio_transactions.account_id = ?")
        params.append(int(account_id))
    params.append(bounded_limit)

    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT
                portfolio_transactions.*,
                accounts.name AS account_name,
                accounts.account_entity AS account_entity,
                accounts.account_type AS account_type
            FROM portfolio_transactions
            JOIN accounts ON accounts.id = portfolio_transactions.account_id
            WHERE {' AND '.join(filters)}
            ORDER BY portfolio_transactions.transaction_date DESC, portfolio_transactions.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

    return {"transactions": rows}


def save_transaction(
    account_id,
    transaction_date,
    transaction_type,
    symbol="",
    market="",
    description="",
    currency="CAD",
    quantity=None,
    price=None,
    dividend_per_share=None,
    gross_amount=0.0,
    fees=0.0,
    tax=0.0,
    net_amount=None,
    notes="",
):
    init_db()
    with get_connection() as conn:
        transaction_id = insert_portfolio_transaction(
            conn,
            account_id,
            transaction_date,
            transaction_type,
            symbol,
            market,
            description,
            currency,
            quantity,
            price,
            dividend_per_share,
            gross_amount,
            fees,
            tax,
            net_amount,
            notes,
        )

    return {"transaction": get_transaction(transaction_id), **list_transactions()}


def insert_portfolio_transaction(
    conn,
    account_id,
    transaction_date,
    transaction_type,
    symbol="",
    market="",
    description="",
    currency="CAD",
    quantity=None,
    price=None,
    dividend_per_share=None,
    gross_amount=0.0,
    fees=0.0,
    tax=0.0,
    net_amount=None,
    notes="",
):
    clean_date = normalize_transaction_date(transaction_date)
    clean_type = normalize_transaction_type(transaction_type)
    clean_symbol = str(symbol or "").strip().upper()
    clean_market = str(market or "").strip().upper()
    clean_currency = normalize_currency(currency)
    clean_quantity = optional_money_value(quantity)
    clean_price = optional_money_value(price)
    clean_dividend = optional_money_value(dividend_per_share)
    clean_gross = money_value(gross_amount)
    clean_fees = money_value(fees)
    clean_tax = money_value(tax)
    clean_net = (
        money_value(net_amount)
        if net_amount is not None and str(net_amount).strip() != ""
        else default_transaction_net_amount(clean_type, clean_quantity, clean_price, clean_gross, clean_fees, clean_tax)
    )
    now = utc_now()

    account = conn.execute("SELECT id FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if not account:
        raise ValueError("Account not found.")
    cursor = conn.execute(
        """
        INSERT INTO portfolio_transactions (
            account_id, transaction_date, transaction_type, symbol, market, description,
            currency, quantity, price, dividend_per_share, gross_amount, fees, tax,
            net_amount, notes, active, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            account_id,
            clean_date,
            clean_type,
            clean_symbol,
            clean_market,
            str(description or "").strip(),
            clean_currency,
            clean_quantity,
            clean_price,
            clean_dividend,
            clean_gross,
            clean_fees,
            clean_tax,
            clean_net,
            str(notes or "").strip(),
            now,
            now,
        ),
    )
    conn.execute("UPDATE accounts SET updated_at = ? WHERE id = ?", (now, account_id))
    return cursor.lastrowid


def get_transaction(transaction_id):
    init_db()
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                portfolio_transactions.*,
                accounts.name AS account_name,
                accounts.account_entity AS account_entity,
                accounts.account_type AS account_type
            FROM portfolio_transactions
            JOIN accounts ON accounts.id = portfolio_transactions.account_id
            WHERE portfolio_transactions.id = ?
            """,
            (transaction_id,),
        ).fetchone()
    return row


def delete_transaction(transaction_id):
    init_db()
    now = utc_now()
    with get_connection() as conn:
        transaction = conn.execute(
            """
            SELECT id, account_id
            FROM portfolio_transactions
            WHERE id = ? AND active = 1
            """,
            (transaction_id,),
        ).fetchone()
        if not transaction:
            raise ValueError("Transaction not found.")
        conn.execute(
            """
            UPDATE portfolio_transactions
            SET active = 0, updated_at = ?
            WHERE id = ?
            """,
            (now, transaction_id),
        )
        conn.execute("UPDATE accounts SET updated_at = ? WHERE id = ?", (now, transaction["account_id"]))

    return list_transactions()


def latest_batch_for_account(conn, account_id):
    return conn.execute(
        """
        SELECT id, metadata_json
        FROM import_batches
        WHERE account_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (account_id,),
    ).fetchone()


def cash_balances_for_batch(conn, batch_id):
    rows = conn.execute(
        """
        SELECT currency, amount
        FROM cash_balances
        WHERE batch_id = ?
        ORDER BY currency
        """,
        (batch_id,),
    ).fetchall()
    return [{"currency": row["currency"], "amount": row["amount"] or 0.0} for row in rows]


def sync_batch_totals_from_holdings_and_cash(conn, batch_id, metadata_updates=None):
    usd_cad_rate = latest_usd_cad_rate_from_conn(conn)
    holding_rows = conn.execute(
        """
        SELECT
            currency,
            COALESCE(SUM(closing_value), 0) AS closing_value,
            COALESCE(SUM(book_value), 0) AS book_value,
            COALESCE(SUM(gain_loss), 0) AS gain_loss
        FROM holdings
        WHERE batch_id = ?
        GROUP BY currency
        """,
        (batch_id,),
    ).fetchall()

    conn.execute("DELETE FROM currency_summaries WHERE batch_id = ?", (batch_id,))
    for row in holding_rows:
        book_value = row["book_value"] or 0.0
        gain_loss = row["gain_loss"] or 0.0
        conn.execute(
            """
            INSERT INTO currency_summaries (
                batch_id, currency, closing_value, book_value, gain_loss, gain_loss_pct
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                normalize_currency(row["currency"]),
                row["closing_value"] or 0.0,
                book_value,
                gain_loss,
                (gain_loss / book_value * 100.0) if book_value else None,
            ),
        )

    cash_balances = cash_balances_for_batch(conn, batch_id)
    valued_cash_balances = cash_balances_with_cad_values(cash_balances, usd_cad_rate)
    cash_cad_total = sum(item["value_cad"] for item in valued_cash_balances)
    currencies = {item["currency"] for item in valued_cash_balances}
    cash_currency_label = next(iter(currencies)) if len(currencies) == 1 else ("MIXED" if currencies else "")

    securities_closing = 0.0
    securities_book = 0.0
    securities_gain = 0.0
    for row in holding_rows:
        fx_to_cad = fx_to_cad_for_currency(row["currency"], usd_cad_rate)
        securities_closing += (row["closing_value"] or 0.0) * fx_to_cad
        securities_book += (row["book_value"] or 0.0) * fx_to_cad
        securities_gain += (row["gain_loss"] or 0.0) * fx_to_cad

    total_closing = securities_closing + cash_cad_total
    total_book = securities_book + cash_cad_total
    total_gain_pct = (securities_gain / total_book * 100.0) if total_book else None

    batch = conn.execute(
        """
        SELECT metadata_json
        FROM import_batches
        WHERE id = ?
        """,
        (batch_id,),
    ).fetchone()
    try:
        metadata = json.loads((batch or {}).get("metadata_json") or "{}")
    except json.JSONDecodeError:
        metadata = {}
    metadata["cash_balance"] = cash_cad_total
    metadata["cash_balances"] = valued_cash_balances
    metadata["cash_currency"] = cash_currency_label
    if metadata_updates:
        metadata.update(metadata_updates)

    conn.execute(
        """
        UPDATE import_batches
        SET
            total_closing_value = ?,
            total_book_value = ?,
            total_gain_loss = ?,
            total_gain_loss_pct = ?,
            metadata_json = ?
        WHERE id = ?
        """,
        (
            total_closing,
            total_book,
            securities_gain,
            total_gain_pct,
            json.dumps(metadata, sort_keys=True),
            batch_id,
        ),
    )

    return {
        "batch_id": batch_id,
        "cash_balance": cash_cad_total,
        "cash_balances": valued_cash_balances,
        "cash_currency": cash_currency_label,
    }


def set_latest_cash_balances_for_account(conn, account_id, normalized_balances, now):
    usd_cad_rate = latest_usd_cad_rate_from_conn(conn)
    valued_cash_balances = cash_balances_with_cad_values(normalized_balances, usd_cad_rate)
    cash_cad_total = sum(item["value_cad"] for item in valued_cash_balances)
    currencies = {item["currency"] for item in valued_cash_balances}
    cash_currency_label = next(iter(currencies)) if len(currencies) == 1 else ("MIXED" if currencies else "")
    batch = latest_batch_for_account(conn, account_id)
    if not batch:
        batch_id = create_manual_cash_batch(conn, account_id, normalized_balances, now)
        conn.execute("UPDATE accounts SET updated_at = ? WHERE id = ?", (now, account_id))
        return {
            "batch_id": batch_id,
            "cash_balance": cash_cad_total,
            "cash_balances": valued_cash_balances,
            "cash_currency": cash_currency_label,
            "initialized": True,
        }

    batch_id = batch["id"]
    conn.execute("DELETE FROM cash_balances WHERE batch_id = ?", (batch_id,))
    if valued_cash_balances:
        conn.executemany(
            """
            INSERT INTO cash_balances (batch_id, currency, amount)
            VALUES (?, ?, ?)
            """,
            [(batch_id, item["currency"], item["amount"]) for item in valued_cash_balances],
        )
    result = sync_batch_totals_from_holdings_and_cash(conn, batch_id)
    conn.execute("UPDATE accounts SET updated_at = ? WHERE id = ?", (now, account_id))
    return result


def update_latest_cash_balances(account_id, cash_balances=None):
    init_db()
    now = utc_now()
    normalized_balances = normalize_cash_balances(cash_balances)

    with get_connection() as conn:
        result = set_latest_cash_balances_for_account(conn, account_id, normalized_balances, now)

    return {"updated": True, **result}


def update_latest_cash_balance(account_id, cash_balance=None, cash_currency: str = "CAD"):
    return update_latest_cash_balances(
        account_id,
        [{"currency": cash_currency, "amount": normalize_cash_balance(cash_balance)}],
    )


def apply_account_trade(
    account_id,
    transaction_date,
    shares,
    price,
    drip=False,
    holding_id="",
    manual_holding_id=None,
    symbol="",
    market="",
    description="",
):
    init_db()
    clean_date = normalize_transaction_date(transaction_date)
    clean_shares = money_value(shares)
    clean_price = money_value(price)
    if clean_shares <= 0:
        raise ValueError("Shares must be greater than zero.")
    if clean_price <= 0:
        raise ValueError("Price must be greater than zero.")

    clean_symbol = str(symbol or "").strip().upper()
    clean_market = str(market or "").strip().upper()
    trade_value = clean_shares * clean_price
    now = utc_now()
    drip_trade = bool(drip)

    with get_connection() as conn:
        account = conn.execute(
            """
            SELECT id, name
            FROM accounts
            WHERE id = ?
            """,
            (account_id,),
        ).fetchone()
        if not account:
            raise ValueError("Account not found.")

        batch = latest_batch_for_account(conn, account_id)
        batch_id = batch["id"] if batch else None
        holding = None
        manual_holding = None

        clean_manual_holding_id = None
        if manual_holding_id not in {None, ""}:
            clean_manual_holding_id = int(manual_holding_id)
        else:
            holding_text = str(holding_id or "")
            if holding_text.startswith("manual-"):
                clean_manual_holding_id = int(holding_text.replace("manual-", "", 1))

        if clean_manual_holding_id is not None:
            manual_holding = conn.execute(
                """
                SELECT *
                FROM manual_holdings
                WHERE id = ? AND account_id = ? AND active = 1
                """,
                (clean_manual_holding_id, account_id),
            ).fetchone()
            if not manual_holding:
                raise ValueError("Holding not found.")
        else:
            if not batch_id:
                raise ValueError("No current holdings snapshot found for this account.")
            holding_text = str(holding_id or "")
            params = [batch_id]
            filters = ["batch_id = ?"]
            if holding_text.isdigit():
                filters.append("id = ?")
                params.append(int(holding_text))
            else:
                if not clean_symbol:
                    raise ValueError("Ticker is required.")
                filters.append("UPPER(COALESCE(symbol, '')) = ?")
                params.append(clean_symbol)
                filters.append("UPPER(COALESCE(market, '')) = ?")
                params.append(clean_market)
            holding = conn.execute(
                f"""
                SELECT *
                FROM holdings
                WHERE {' AND '.join(filters)}
                LIMIT 1
                """,
                params,
            ).fetchone()
            if not holding:
                raise ValueError("Holding not found.")
            if str(holding["asset_type"] or "").strip().lower() in {"cash", "private fund"}:
                raise ValueError("Trades are only supported for stock holdings.")

        trade_currency = normalize_currency(
            (manual_holding or holding).get("currency") or "CAD"
        )
        if not drip_trade:
            cash_by_currency = {
                item["currency"]: item["amount"]
                for item in normalize_cash_balances(cash_balances_for_batch(conn, batch_id) if batch_id else [])
            }
            current_cash = cash_by_currency.get(trade_currency, 0.0)
            if trade_value - current_cash > 0.005:
                raise ValueError(
                    f"Purchase amount {trade_currency} {trade_value:,.2f} exceeds "
                    f"{trade_currency} cash balance {current_cash:,.2f}."
                )
            cash_by_currency[trade_currency] = current_cash - trade_value

        if manual_holding:
            old_quantity = money_value(manual_holding["quantity"])
            old_average_cost = money_value(manual_holding["average_cost"])
            old_book_value = old_quantity * old_average_cost
            new_quantity = old_quantity + clean_shares
            new_book_value = old_book_value + trade_value
            new_average_cost = new_book_value / new_quantity if new_quantity else clean_price
            clean_symbol = str(manual_holding["symbol"] or "").strip().upper()
            clean_market = str(manual_holding["market"] or "").strip().upper()
            clean_description = str(description or manual_holding["description"] or clean_symbol).strip()
            conn.execute(
                """
                UPDATE manual_holdings
                SET quantity = ?, average_cost = ?, description = ?, updated_at = ?
                WHERE id = ? AND account_id = ?
                """,
                (
                    new_quantity,
                    new_average_cost,
                    clean_description,
                    now,
                    manual_holding["id"],
                    account_id,
                ),
            )
        else:
            old_quantity = money_value(holding["quantity"])
            old_book_value = money_value(holding["book_value"])
            closing_price = finite_float(holding["closing_price"])
            if closing_price is None or closing_price <= 0:
                closing_price = clean_price
            new_quantity = old_quantity + clean_shares
            new_book_value = old_book_value + trade_value
            new_average_cost = new_book_value / new_quantity if new_quantity else clean_price
            new_closing_value = new_quantity * closing_price
            new_gain_loss = new_closing_value - new_book_value
            new_gain_loss_pct = (new_gain_loss / new_book_value * 100.0) if new_book_value else None
            clean_symbol = str(holding["symbol"] or "").strip().upper()
            clean_market = str(holding["market"] or "").strip().upper()
            clean_description = str(description or holding["description"] or clean_symbol).strip()
            conn.execute(
                """
                UPDATE holdings
                SET
                    quantity = ?,
                    average_cost = ?,
                    closing_value = ?,
                    book_value = ?,
                    gain_loss = ?,
                    gain_loss_pct = ?
                WHERE id = ? AND batch_id = ?
                """,
                (
                    new_quantity,
                    new_average_cost,
                    new_closing_value,
                    new_book_value,
                    new_gain_loss,
                    new_gain_loss_pct,
                    holding["id"],
                    batch_id,
                ),
            )

        cash_result = None
        if not drip_trade:
            cash_result = set_latest_cash_balances_for_account(
                conn,
                account_id,
                [{"currency": currency, "amount": amount} for currency, amount in cash_by_currency.items()],
                now,
            )
        elif batch_id:
            sync_batch_totals_from_holdings_and_cash(conn, batch_id)

        transaction_id = insert_portfolio_transaction(
            conn,
            account_id,
            clean_date,
            "DRIP" if drip_trade else "BUY",
            clean_symbol,
            clean_market,
            clean_description,
            trade_currency,
            clean_shares,
            clean_price,
            None,
            trade_value,
            0.0,
            0.0,
            0.0 if drip_trade else -abs(trade_value),
            "DRIP - cash unchanged" if drip_trade else "",
        )
        conn.execute("UPDATE accounts SET updated_at = ? WHERE id = ?", (now, account_id))

    return {
        "updated": True,
        "transaction": get_transaction(transaction_id),
        "cash": cash_result,
        "message": (
            f"Recorded DRIP for {clean_shares:g} {clean_symbol}."
            if drip_trade
            else f"Recorded purchase of {clean_shares:g} {clean_symbol}."
        ),
    }


def get_accounts():
    init_db()
    with get_connection() as conn:
        accounts = conn.execute(
            """
            WITH latest AS (
                SELECT account_id, MAX(id) AS batch_id
                FROM import_batches
                GROUP BY account_id
            )
            SELECT
                accounts.id,
                accounts.name,
                accounts.owner,
                accounts.account_entity,
                accounts.account_type,
                accounts.base_currency,
                accounts.notes,
                accounts.created_at,
                accounts.updated_at,
                import_batches.id AS batch_id,
                import_batches.imported_at,
                import_batches.report_timestamp,
                import_batches.row_count,
                import_batches.total_closing_value,
                import_batches.total_book_value,
                import_batches.total_gain_loss,
                import_batches.total_gain_loss_pct
            FROM accounts
            LEFT JOIN latest ON latest.account_id = accounts.id
            LEFT JOIN import_batches ON import_batches.id = latest.batch_id
            ORDER BY accounts.name
            """
        ).fetchall()

        cash_rows = conn.execute(
            """
            WITH latest AS (
                SELECT account_id, MAX(id) AS batch_id
                FROM import_batches
                GROUP BY account_id
            )
            SELECT
                cash_balances.batch_id,
                cash_balances.currency,
                cash_balances.amount
            FROM cash_balances
            JOIN latest ON latest.batch_id = cash_balances.batch_id
            ORDER BY cash_balances.batch_id, cash_balances.currency
            """
        ).fetchall()
        usd_cad_rate = latest_usd_cad_rate_from_conn(conn)

    cash_by_batch = {}
    for row in cash_rows:
        fx_to_cad = fx_to_cad_for_currency(row["currency"], usd_cad_rate)
        cash_by_batch.setdefault(row["batch_id"], []).append(
            {
                "currency": row["currency"],
                "amount": row["amount"] or 0.0,
                "fx_to_cad": fx_to_cad,
                "value_cad": (row["amount"] or 0.0) * fx_to_cad,
            }
        )
    for account in accounts:
        cash_balances = cash_by_batch.get(account["batch_id"], [])
        currencies = {item["currency"] for item in cash_balances}
        account["cash_balances"] = cash_balances
        account["cash_balance"] = sum(item["value_cad"] for item in cash_balances)
        account["cash_currency"] = next(iter(currencies)) if len(currencies) == 1 else ("MIXED" if currencies else account.get("base_currency") or "CAD")
        account["has_import"] = account["batch_id"] is not None

    return accounts


def totals_from_summaries(currency_summaries):
    total_closing = sum(item.get("closing_value") or 0.0 for item in currency_summaries)
    total_book = sum(item.get("book_value") or 0.0 for item in currency_summaries)
    total_gain = sum(item.get("gain_loss") or 0.0 for item in currency_summaries)
    total_gain_pct = (total_gain / total_book * 100.0) if total_book else None
    return total_closing, total_book, total_gain, total_gain_pct


def import_content(
    content: bytes,
    filename: str,
    account_override: str = "",
    cash_balance=None,
    cash_currency: str = "CAD",
):
    init_db()
    return import_content_with_cash(content, filename, account_override, cash_balance, cash_currency)


def normalize_cash_balance(cash_balance):
    amount = parse_number(cash_balance)
    if amount is None:
        return 0.0
    return amount


def import_content_with_cash(
    content: bytes,
    filename: str,
    account_override: str = "",
    cash_balance=None,
    cash_currency: str = "CAD",
):
    init_db()
    sha256 = hashlib.sha256(content).hexdigest()
    source_filename = Path(filename or "holdings.csv").name
    cash_provided = cash_balance is not None and str(cash_balance).strip() != ""
    cash_amount = normalize_cash_balance(cash_balance) if cash_provided else 0.0
    cash_currency = (cash_currency or "CAD").strip().upper()

    incoming_dir = IMPORT_DIR / "_incoming"
    incoming_dir.mkdir(parents=True, exist_ok=True)
    temp_path = incoming_dir / f"{sha256}.csv"
    temp_path.write_bytes(content)

    try:
        parsed = parse_holdings_csv(temp_path)
        metadata = parsed["metadata"]
        import_cash_balances = (
            [{"currency": cash_currency, "amount": cash_amount}]
            if cash_provided
            else [
                {"currency": normalize_currency(item.get("currency")), "amount": item.get("amount") or 0.0}
                for item in parsed.get("cash_balances", [])
                if item.get("amount")
            ]
        )
        cash_total = sum(item["amount"] for item in import_cash_balances)
        cash_currencies = {item["currency"] for item in import_cash_balances if item["amount"]}
        cash_currency_label = next(iter(cash_currencies)) if len(cash_currencies) == 1 else ("MIXED" if cash_currencies else "")
        account_name = account_override.strip() or metadata["account_name"] or "Imported Account"
        account_slug = slugify(account_name)
        account_dir = IMPORT_DIR / account_slug
        account_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        stored_filename = f"{timestamp}-{source_filename}"
        stored_path = account_dir / stored_filename

        with get_connection() as conn:
            existing = conn.execute(
                """
                SELECT import_batches.id, accounts.name
                FROM import_batches
                JOIN accounts ON accounts.id = import_batches.account_id
                WHERE import_batches.file_sha256 = ?
                """,
                (sha256,),
            ).fetchone()

            if existing:
                return {
                    "imported": False,
                    "batch_id": existing["id"],
                    "account_name": existing["name"],
                    "message": "This exact file has already been imported.",
                }

            shutil.move(str(temp_path), stored_path)
            relative_stored_path = str(stored_path.relative_to(BASE_DIR))
            account_id = upsert_account(conn, account_name, metadata.get("owner", ""))
            securities_closing, securities_book, total_gain, _ = totals_from_summaries(
                parsed["currency_summaries"]
            )
            total_closing = securities_closing + cash_total
            total_book = securities_book + cash_total
            total_gain_pct = (total_gain / total_book * 100.0) if total_book else None
            stored_metadata = {
                **metadata,
                "cash_balance": cash_total,
                "cash_currency": cash_currency_label,
            }

            cursor = conn.execute(
                """
                INSERT INTO import_batches (
                    account_id,
                    source_filename,
                    stored_path,
                    file_sha256,
                    imported_at,
                    report_timestamp,
                    as_of_note,
                    row_count,
                    total_closing_value,
                    total_book_value,
                    total_gain_loss,
                    total_gain_loss_pct,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    source_filename,
                    relative_stored_path,
                    sha256,
                    utc_now(),
                    metadata.get("report_timestamp", ""),
                    metadata.get("as_of_note", ""),
                    len(parsed["holdings"]),
                    total_closing,
                    total_book,
                    total_gain,
                    total_gain_pct,
                    json.dumps(stored_metadata, sort_keys=True),
                ),
            )
            batch_id = cursor.lastrowid

            conn.executemany(
                """
                INSERT INTO cash_balances (batch_id, currency, amount)
                VALUES (?, ?, ?)
                """,
                [
                    (batch_id, item["currency"], item["amount"])
                    for item in import_cash_balances
                    if item["amount"]
                ],
            )

            conn.executemany(
                """
                INSERT INTO currency_summaries (
                    batch_id, currency, closing_value, book_value, gain_loss, gain_loss_pct
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        batch_id,
                        item["currency"],
                        item.get("closing_value") or 0.0,
                        item.get("book_value") or 0.0,
                        item.get("gain_loss") or 0.0,
                        item.get("gain_loss_pct"),
                    )
                    for item in parsed["currency_summaries"]
                ],
            )

            conn.executemany(
                """
                INSERT INTO holdings (
                    batch_id,
                    asset_type,
                    currency,
                    symbol,
                    market,
                    description,
                    quantity,
                    average_cost,
                    closing_price,
                    closing_value,
                    book_value,
                    gain_loss,
                    gain_loss_pct,
                    portfolio_pct
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        batch_id,
                        holding["asset_type"],
                        holding["currency"],
                        holding["symbol"],
                        holding["market"],
                        holding["description"],
                        holding["quantity"],
                        holding["average_cost"],
                        holding["closing_price"],
                        holding["closing_value"],
                        holding["book_value"],
                        holding["gain_loss"],
                        holding["gain_loss_pct"],
                        holding["portfolio_pct"],
                    )
                    for holding in parsed["holdings"]
                ],
            )

            return {
                "imported": True,
                "batch_id": batch_id,
                "account_name": account_name,
                "row_count": len(parsed["holdings"]),
                "cash_balance": cash_total,
                "cash_currency": cash_currency_label,
                "stored_path": relative_stored_path,
            }
    finally:
        if temp_path.exists():
            temp_path.unlink()


def import_path(
    path: Path,
    account_override: str = "",
    cash_balance=None,
    cash_currency: str = "CAD",
):
    return import_content(path.read_bytes(), path.name, account_override, cash_balance, cash_currency)


def normalize_history_key(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def normalize_history_account_name(value):
    text = str(value or "").strip()
    text = re.sub(r"^\d+\s+", "", text)
    text = re.sub(r"\s+-\s+Combined Holdings$", "", text, flags=re.IGNORECASE)
    return normalize_history_key(text)


def parse_history_date(value):
    text = str(value or "").strip()
    if not text:
        return None

    if re.fullmatch(r"\d+(\.\d+)?", text):
        try:
            serial = float(text)
            if serial > 10000:
                return datetime.fromordinal(datetime(1899, 12, 30).toordinal() + int(serial)).date().isoformat()
        except ValueError:
            pass

    iso_text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso_text).date().isoformat()
    except ValueError:
        pass

    for date_format in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%d/%m/%Y",
        "%d/%m/%y",
        "%m-%d-%Y",
        "%d-%m-%Y",
        "%b %d, %Y",
        "%B %d, %Y",
        "%d-%b-%Y",
        "%d-%B-%Y",
    ):
        try:
            return datetime.strptime(text, date_format).date().isoformat()
        except ValueError:
            continue

    return None


def read_history_csv_dicts(content: bytes):
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    rows = [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(text))]
    date_keys = {"date", "marketdate", "markdate", "asof", "asofdate"}
    header_index = None
    for index, row in enumerate(rows[:25]):
        keys = {normalize_history_key(cell) for cell in row}
        if keys & date_keys:
            header_index = index
            break

    if header_index is None:
        raise ValueError("Could not find a history header row with a Date column.")

    headers = rows[header_index]
    records = []
    for row in rows[header_index + 1 :]:
        if not any(non_empty(cell) for cell in row):
            continue
        record = {}
        for index, header in enumerate(headers):
            name = header or f"Column {index + 1}"
            record[name] = row[index].strip() if index < len(row) else ""
        records.append(record)

    return headers, records


def first_matching_header(headers, keys):
    for header in headers:
        if normalize_history_key(header) in keys:
            return header
    return None


def history_account_lookup(accounts):
    lookup = {}
    for account in accounts:
        candidates = {
            account.get("name", ""),
            normalize_history_account_name(account.get("name", "")),
            short_history_account_name(account.get("name", "")),
        }
        for candidate in candidates:
            key = normalize_history_account_name(candidate)
            if key:
                lookup.setdefault(key, account)
    return lookup


def short_history_account_name(value):
    text = str(value or "").strip()
    text = re.sub(r"^\d+\s+", "", text)
    text = re.sub(r"\s+-\s+Combined Holdings$", "", text, flags=re.IGNORECASE)
    return text


def parse_history_snapshot_records(content: bytes):
    init_db()
    accounts = get_accounts()
    account_lookup = history_account_lookup(accounts)
    headers, rows = read_history_csv_dicts(content)

    date_header = first_matching_header(headers, {"date", "marketdate", "markdate", "asof", "asofdate"})
    account_header = first_matching_header(headers, {"account", "accountname", "accountid", "name"})
    value_header = first_matching_header(
        headers,
        {"value", "balance", "accountvalue", "marketvalue", "closingvalue", "totalvalue", "amount"},
    )
    total_header = first_matching_header(
        headers,
        {"total", "totalvalue", "portfoliototal", "portfolio", "portfoliovalue", "accountvalue"},
    )
    imported_header = first_matching_header(headers, {"importedvalue", "closingvalue", "statementvalue"})
    book_header = first_matching_header(headers, {"bookvalue", "cost", "costbase", "book"})
    gain_header = first_matching_header(headers, {"gainloss", "gainlossdollar", "gainloss$", "gain", "profitloss"})
    gain_pct_header = first_matching_header(headers, {"gainlosspercent", "gainlosspercentage", "return", "returnpct"})
    day_change_header = first_matching_header(headers, {"daychange", "dailychange", "changedollar", "change"})
    day_change_pct_header = first_matching_header(headers, {"daychangepercent", "dailychangepercent", "changepct"})

    groups = {}
    unmatched_accounts = set()
    skipped_rows = 0

    def ensure_group(market_date):
        return groups.setdefault(
            market_date,
            {
                "market_date": market_date,
                "accounts": {},
                "total_value": None,
                "imported_value": None,
                "book_value": None,
                "gain_loss": None,
                "gain_loss_pct": None,
                "day_change": None,
                "day_change_pct": None,
            },
        )

    if account_header and value_header:
        total_names = {"total", "portfoliototal", "portfolio", "allaccounts", "combined"}
        for row in rows:
            market_date = parse_history_date(row.get(date_header, ""))
            value = parse_number(row.get(value_header, ""))
            if not market_date or value is None:
                skipped_rows += 1
                continue

            group = ensure_group(market_date)
            account_name = row.get(account_header, "")
            account_key = normalize_history_account_name(account_name)
            account = account_lookup.get(account_key)

            if account:
                group["accounts"][account["id"]] = {"account": account, "value": value}
            elif account_key in total_names:
                group["total_value"] = value
            else:
                unmatched_accounts.add(account_name)

            if book_header:
                group["book_value"] = parse_number(row.get(book_header, "")) or group["book_value"]
            if gain_header:
                group["gain_loss"] = parse_number(row.get(gain_header, "")) or group["gain_loss"]
            if gain_pct_header:
                group["gain_loss_pct"] = parse_number(row.get(gain_pct_header, "")) or group["gain_loss_pct"]
            if day_change_header:
                group["day_change"] = parse_number(row.get(day_change_header, "")) or group["day_change"]
            if day_change_pct_header:
                group["day_change_pct"] = parse_number(row.get(day_change_pct_header, "")) or group["day_change_pct"]
    else:
        reserved_headers = {
            header
            for header in (
                date_header,
                total_header,
                imported_header,
                book_header,
                gain_header,
                gain_pct_header,
                day_change_header,
                day_change_pct_header,
            )
            if header
        }
        account_headers = []
        for header in headers:
            if header in reserved_headers:
                continue
            account = account_lookup.get(normalize_history_account_name(header))
            if account:
                account_headers.append((header, account))

        if not account_headers:
            raise ValueError("No history columns matched existing account names.")

        for row in rows:
            market_date = parse_history_date(row.get(date_header, ""))
            if not market_date:
                skipped_rows += 1
                continue

            group = ensure_group(market_date)
            if total_header:
                group["total_value"] = parse_number(row.get(total_header, ""))
            if imported_header:
                group["imported_value"] = parse_number(row.get(imported_header, ""))
            if book_header:
                group["book_value"] = parse_number(row.get(book_header, ""))
            if gain_header:
                group["gain_loss"] = parse_number(row.get(gain_header, ""))
            if gain_pct_header:
                group["gain_loss_pct"] = parse_number(row.get(gain_pct_header, ""))
            if day_change_header:
                group["day_change"] = parse_number(row.get(day_change_header, ""))
            if day_change_pct_header:
                group["day_change_pct"] = parse_number(row.get(day_change_pct_header, ""))

            for header, account in account_headers:
                value = parse_number(row.get(header, ""))
                if value is not None:
                    group["accounts"][account["id"]] = {"account": account, "value": value}

    records = sorted(groups.values(), key=lambda item: item["market_date"])
    records = [record for record in records if record["accounts"] or record["total_value"] is not None]
    if not records:
        raise ValueError("No usable history rows were found.")

    previous_total = None
    previous_account_values = {}
    for record in records:
        account_total = sum(item["value"] for item in record["accounts"].values())
        if record["total_value"] is None:
            record["total_value"] = account_total
        if record["imported_value"] is None:
            record["imported_value"] = record["total_value"]
        if record["book_value"] is None:
            record["book_value"] = record["total_value"]
        if record["gain_loss"] is None:
            record["gain_loss"] = record["total_value"] - record["book_value"]
        if record["gain_loss_pct"] is None:
            record["gain_loss_pct"] = (record["gain_loss"] / record["book_value"] * 100.0) if record["book_value"] else None
        if record["day_change"] is None and previous_total is not None:
            record["day_change"] = record["total_value"] - previous_total
        if record["day_change_pct"] is None and previous_total:
            record["day_change_pct"] = (record["day_change"] / previous_total * 100.0) if record["day_change"] is not None else None

        for account_id, item in record["accounts"].items():
            previous_value = previous_account_values.get(account_id)
            item["day_change"] = item["value"] - previous_value if previous_value is not None else None
            item["day_change_pct"] = (item["day_change"] / previous_value * 100.0) if previous_value else None

        previous_total = record["total_value"]
        previous_account_values = {account_id: item["value"] for account_id, item in record["accounts"].items()}

    return {
        "records": records,
        "unmatched_accounts": sorted(name for name in unmatched_accounts if str(name).strip()),
        "skipped_rows": skipped_rows,
    }


def import_history_content(content: bytes, filename: str = "history.csv"):
    init_db()
    parsed = parse_history_snapshot_records(content)
    records = parsed["records"]
    now = utc_now()
    source_filename = Path(filename or "history.csv").name

    history_dir = IMPORT_DIR / "_history"
    history_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stored_path = history_dir / f"{timestamp}-{source_filename}"
    stored_path.write_bytes(content)
    relative_stored_path = str(stored_path.relative_to(BASE_DIR))

    with get_connection() as conn:
        for record in records:
            existing = conn.execute(
                "SELECT created_at FROM balance_snapshots WHERE market_date = ?",
                (record["market_date"],),
            ).fetchone()
            created_at = existing["created_at"] if existing else now

            conn.execute(
                """
                INSERT INTO balance_snapshots (
                    market_date, total_value, imported_value, book_value, gain_loss, gain_loss_pct,
                    day_change, day_change_pct, price_fetched_at, source, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(market_date) DO UPDATE SET
                    total_value = excluded.total_value,
                    imported_value = excluded.imported_value,
                    book_value = excluded.book_value,
                    gain_loss = excluded.gain_loss,
                    gain_loss_pct = excluded.gain_loss_pct,
                    day_change = excluded.day_change,
                    day_change_pct = excluded.day_change_pct,
                    price_fetched_at = excluded.price_fetched_at,
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (
                    record["market_date"],
                    record["total_value"] or 0.0,
                    record["imported_value"] or 0.0,
                    record["book_value"] or 0.0,
                    record["gain_loss"] or 0.0,
                    record["gain_loss_pct"],
                    record["day_change"],
                    record["day_change_pct"],
                    None,
                    "history import",
                    created_at,
                    now,
                ),
            )

            conn.execute(
                "DELETE FROM account_balance_snapshots WHERE market_date = ?",
                (record["market_date"],),
            )
            for item in record["accounts"].values():
                account = item["account"]
                value = item["value"] or 0.0
                conn.execute(
                    """
                    INSERT INTO account_balance_snapshots (
                        market_date, account_id, account_name, account_entity, account_type, value, imported_value,
                        book_value, cash_balance, gain_loss, gain_loss_pct, day_change, day_change_pct,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["market_date"],
                        account["id"],
                        account["name"],
                        account.get("account_entity") or "Personal",
                        account.get("account_type") or "Investment",
                        value,
                        value,
                        value,
                        0.0,
                        0.0,
                        0.0,
                        item.get("day_change"),
                        item.get("day_change_pct"),
                        now,
                        now,
                    ),
                )

    return {
        "imported": True,
        "snapshot_count": len(records),
        "account_row_count": sum(len(record["accounts"]) for record in records),
        "unmatched_accounts": parsed["unmatched_accounts"],
        "skipped_rows": parsed["skipped_rows"],
        "stored_path": relative_stored_path,
        "message": f"Imported {len(records)} history closes.",
    }


def yahoo_symbol_for(symbol, market):
    cleaned_symbol = str(symbol or "").strip().upper()
    cleaned_market = str(market or "").strip().upper()
    if not cleaned_symbol or cleaned_symbol == "CASH":
        return ""
    if cleaned_market in {"PRIVATE", "MANUAL", "FUND", "PRIVATE FUND"}:
        return ""
    if cleaned_market in {"CDN", "CAN", "CA", "TSX", "TSXV"}:
        return f"{cleaned_symbol}.TO"
    if cleaned_market == "US":
        return cleaned_symbol.replace(".", "-")
    return cleaned_symbol


def active_price_targets():
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT DISTINCT
                symbol,
                market,
                currency
            FROM (
                SELECT
                    holdings.symbol,
                    holdings.market,
                    holdings.currency
                FROM holdings
                WHERE holdings.batch_id IN ({latest_batch_filter_sql()})
                    AND COALESCE(holdings.symbol, '') != ''
                    AND UPPER(COALESCE(holdings.symbol, '')) != 'CASH'
                UNION ALL
                SELECT
                    manual_holdings.symbol,
                    manual_holdings.market,
                    manual_holdings.currency
                FROM manual_holdings
                WHERE manual_holdings.active = 1
                    AND COALESCE(manual_holdings.symbol, '') != ''
                    AND UPPER(COALESCE(manual_holdings.symbol, '')) != 'CASH'
            )
            ORDER BY market, symbol
            """
        ).fetchall()

    targets = []
    seen = set()
    for row in rows:
        key = (row["symbol"], row["market"])
        if key in seen:
            continue
        seen.add(key)
        yahoo_symbol = yahoo_symbol_for(row["symbol"], row["market"])
        if yahoo_symbol:
            targets.append({**row, "yahoo_symbol": yahoo_symbol})
    return targets


def portfolio_needs_usd_cad():
    init_db()
    with get_connection() as conn:
        cash_row = conn.execute(
            f"""
            SELECT 1
            FROM cash_balances
            WHERE batch_id IN ({latest_batch_filter_sql()})
                AND UPPER(currency) = 'USD'
            LIMIT 1
            """
        ).fetchone()
        if cash_row:
            return True

        private_row = conn.execute(
            f"""
            SELECT 1
            FROM holdings
            WHERE batch_id IN ({latest_batch_filter_sql()})
                AND UPPER(currency) = 'USD'
            LIMIT 1
            """
        ).fetchone()
        return private_row is not None


def last_close(downloaded, ticker):
    if downloaded is None or getattr(downloaded, "empty", True):
        return None, None

    close_series = None
    columns = getattr(downloaded, "columns", None)
    if getattr(columns, "nlevels", 1) > 1:
        if ticker in columns.get_level_values(0):
            close_series = downloaded[ticker].get("Close")
        elif ticker in columns.get_level_values(1) and "Close" in columns.get_level_values(0):
            close_series = downloaded["Close"].get(ticker)
    elif "Close" in downloaded:
        close_series = downloaded["Close"]

    if close_series is None:
        return None, None

    close_series = close_series.dropna()
    if close_series.empty:
        return None, None

    value = float(close_series.iloc[-1])
    timestamp = close_series.index[-1]
    quote_time = timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp)
    return value, quote_time


def previous_daily_close(downloaded, ticker):
    if downloaded is None or getattr(downloaded, "empty", True):
        return None

    close_series = None
    columns = getattr(downloaded, "columns", None)
    if getattr(columns, "nlevels", 1) > 1:
        if ticker in columns.get_level_values(0):
            close_series = downloaded[ticker].get("Close")
        elif ticker in columns.get_level_values(1) and "Close" in columns.get_level_values(0):
            close_series = downloaded["Close"].get(ticker)
    elif "Close" in downloaded:
        close_series = downloaded["Close"]

    if close_series is None:
        return None

    close_series = close_series.dropna()
    if close_series.empty:
        return None
    if len(close_series) >= 2:
        return float(close_series.iloc[-2])
    return float(close_series.iloc[-1])


def latest_price_status():
    init_db()
    with get_connection() as conn:
        refresh = conn.execute(
            """
            SELECT *
            FROM price_refreshes
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        count_row = conn.execute(
            """
            SELECT
                COUNT(*) AS price_count,
                MAX(fetched_at) AS latest_fetched_at,
                SUM(day_change) AS total_day_change
            FROM latest_prices
            WHERE price_cad IS NOT NULL
            """
        ).fetchone()

    return {
        "refresh": refresh,
        "price_count": count_row["price_count"] if count_row else 0,
        "latest_fetched_at": count_row["latest_fetched_at"] if count_row else None,
        "total_day_change": count_row["total_day_change"] if count_row else None,
    }


def mark_stale_price_refreshes(now: str):
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=PRICE_REFRESH_STALE_AFTER_SECONDS)).isoformat(
        timespec="seconds"
    )
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE price_refreshes
            SET completed_at = ?, status = ?, error = COALESCE(error, ?)
            WHERE status = ? AND started_at < ?
            """,
            (now, "error", "Stale running refresh marked failed.", "running", cutoff),
        )


def balance_snapshot_exists(market_date: str) -> bool:
    init_db()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM balance_snapshots WHERE market_date = ?",
            (market_date,),
        ).fetchone()
    return row is not None


def latest_balance_snapshot_status():
    init_db()
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT *
            FROM balance_snapshots
            ORDER BY market_date DESC
            LIMIT 1
            """
        ).fetchone()


def get_balance_snapshots(limit: int = 90):
    init_db()
    bounded_limit = max(1, min(int(limit or 90), 5000))
    with get_connection() as conn:
        snapshots = conn.execute(
            """
            SELECT *
            FROM balance_snapshots
            ORDER BY market_date DESC
            LIMIT ?
            """,
            (bounded_limit,),
        ).fetchall()

        if not snapshots:
            return {"snapshots": []}

        dates = [row["market_date"] for row in snapshots]
        placeholders = ",".join("?" for _ in dates)
        account_rows = conn.execute(
            f"""
            SELECT *
            FROM account_balance_snapshots
            WHERE market_date IN ({placeholders})
            ORDER BY market_date DESC, account_name
            """,
            dates,
        ).fetchall()

    accounts_by_date = {}
    for row in account_rows:
        accounts_by_date.setdefault(row["market_date"], []).append(row)

    return {
        "snapshots": [
            {
                **snapshot,
                "accounts": accounts_by_date.get(snapshot["market_date"], []),
            }
            for snapshot in snapshots
        ]
    }


def update_balance_snapshot_values(market_date: str, updates: dict):
    init_db()
    date_text = str(market_date or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
        raise ValueError("Snapshot date must be YYYY-MM-DD.")

    allowed_snapshot_fields = {
        "total_value",
        "imported_value",
        "book_value",
        "gain_loss",
        "gain_loss_pct",
        "day_change",
        "day_change_pct",
    }
    account_values = updates.get("account_values") or []
    now = utc_now()

    with get_connection() as conn:
        snapshot = conn.execute(
            "SELECT * FROM balance_snapshots WHERE market_date = ?",
            (date_text,),
        ).fetchone()
        if not snapshot:
            raise ValueError("Saved close not found.")

        for item in account_values:
            account_id = int(item.get("account_id") or 0)
            value = money_value(item.get("value"))
            account = conn.execute(
                "SELECT id, name, account_entity, account_type FROM accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
            if not account:
                raise ValueError("Account not found.")

            existing = conn.execute(
                """
                SELECT created_at
                FROM account_balance_snapshots
                WHERE market_date = ? AND account_id = ?
                """,
                (date_text, account_id),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO account_balance_snapshots (
                    market_date, account_id, account_name, account_entity, account_type,
                    value, imported_value, book_value, cash_balance, gain_loss, gain_loss_pct,
                    day_change, day_change_pct, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, NULL, NULL, NULL, ?, ?)
                ON CONFLICT(market_date, account_id) DO UPDATE SET
                    account_name = excluded.account_name,
                    account_entity = excluded.account_entity,
                    account_type = excluded.account_type,
                    value = excluded.value,
                    imported_value = excluded.imported_value,
                    updated_at = excluded.updated_at
                """,
                (
                    date_text,
                    account_id,
                    account["name"],
                    account.get("account_entity") or "Personal",
                    account.get("account_type") or "Investment",
                    value,
                    value,
                    value,
                    created_at,
                    now,
                ),
            )

        snapshot_updates = {
            key: money_value(value)
            for key, value in updates.items()
            if key in allowed_snapshot_fields and value is not None and str(value).strip() != ""
        }

        if account_values and "total_value" not in snapshot_updates:
            total_row = conn.execute(
                """
                SELECT COALESCE(SUM(value), 0) AS total_value
                FROM account_balance_snapshots
                WHERE market_date = ?
                """,
                (date_text,),
            ).fetchone()
            snapshot_updates["total_value"] = total_row["total_value"] or 0.0
        if "total_value" in snapshot_updates and "imported_value" not in snapshot_updates:
            snapshot_updates["imported_value"] = snapshot_updates["total_value"]

        if snapshot_updates:
            assignments = ", ".join(f"{field} = ?" for field in snapshot_updates)
            conn.execute(
                f"""
                UPDATE balance_snapshots
                SET {assignments}, updated_at = ?
                WHERE market_date = ?
                """,
                [*snapshot_updates.values(), now, date_text],
            )
        else:
            conn.execute(
                "UPDATE balance_snapshots SET updated_at = ? WHERE market_date = ?",
                (now, date_text),
            )

    return get_balance_snapshots(5000)


def save_balance_snapshot(market_date: str, source: str = "auto"):
    init_db()
    date_text = str(market_date or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
        raise ValueError("Snapshot date must be YYYY-MM-DD.")

    summary = get_summary()
    totals = summary["totals"]
    price_refresh = summary.get("price_refresh", {})
    price_fetched_at = price_refresh.get("latest_fetched_at")
    now = utc_now()
    total_value = totals.get("current_value") or totals.get("closing_value") or 0.0

    with get_connection() as conn:
        existing = conn.execute(
            "SELECT created_at FROM balance_snapshots WHERE market_date = ?",
            (date_text,),
        ).fetchone()
        created_at = existing["created_at"] if existing else now

        conn.execute(
            """
            INSERT INTO balance_snapshots (
                market_date, total_value, imported_value, book_value, gain_loss, gain_loss_pct,
                day_change, day_change_pct, price_fetched_at, source, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(market_date) DO UPDATE SET
                total_value = excluded.total_value,
                imported_value = excluded.imported_value,
                book_value = excluded.book_value,
                gain_loss = excluded.gain_loss,
                gain_loss_pct = excluded.gain_loss_pct,
                day_change = excluded.day_change,
                day_change_pct = excluded.day_change_pct,
                price_fetched_at = excluded.price_fetched_at,
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            (
                date_text,
                total_value,
                totals.get("closing_value") or 0.0,
                totals.get("book_value") or 0.0,
                totals.get("current_gain_loss") or totals.get("gain_loss") or 0.0,
                totals.get("current_gain_loss_pct"),
                totals.get("day_change"),
                totals.get("day_change_pct"),
                price_fetched_at,
                source or "auto",
                created_at,
                now,
            ),
        )

        for account in summary["accounts"]:
            account_existing = conn.execute(
                """
                SELECT created_at
                FROM account_balance_snapshots
                WHERE market_date = ? AND account_id = ?
                """,
                (date_text, account["id"]),
            ).fetchone()
            account_created_at = account_existing["created_at"] if account_existing else now
            account_value = account.get("current_total_value") or account.get("total_closing_value") or 0.0

            conn.execute(
                """
                INSERT INTO account_balance_snapshots (
                    market_date, account_id, account_name, account_entity, account_type, value, imported_value,
                    book_value, cash_balance, gain_loss, gain_loss_pct, day_change, day_change_pct,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(market_date, account_id) DO UPDATE SET
                    account_name = excluded.account_name,
                    account_entity = excluded.account_entity,
                    account_type = excluded.account_type,
                    value = excluded.value,
                    imported_value = excluded.imported_value,
                    book_value = excluded.book_value,
                    cash_balance = excluded.cash_balance,
                    gain_loss = excluded.gain_loss,
                    gain_loss_pct = excluded.gain_loss_pct,
                    day_change = excluded.day_change,
                    day_change_pct = excluded.day_change_pct,
                    updated_at = excluded.updated_at
                """,
                (
                    date_text,
                    account["id"],
                    account["name"],
                    account.get("account_entity") or "Personal",
                    account.get("account_type") or "Investment",
                    account_value,
                    account.get("total_closing_value") or 0.0,
                    account.get("total_book_value") or 0.0,
                    account.get("cash_balance") or 0.0,
                    account.get("current_total_gain_loss") or account.get("total_gain_loss") or 0.0,
                    account.get("current_total_gain_loss_pct"),
                    account.get("day_change"),
                    account.get("day_change_pct"),
                    account_created_at,
                    now,
                ),
            )

        saved = conn.execute(
            "SELECT * FROM balance_snapshots WHERE market_date = ?",
            (date_text,),
        ).fetchone()

    return {"saved": True, "snapshot": saved}


def refresh_current_prices():
    if not PRICE_REFRESH_LOCK.acquire(blocking=False):
        init_db()
        completed_at = utc_now()
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO price_refreshes (started_at, completed_at, status, symbol_count, error)
                VALUES (?, ?, ?, ?, ?)
                """,
                (completed_at, completed_at, "skipped", 0, "Price refresh already running."),
            )
        return {
            "status": "skipped",
            "symbol_count": 0,
            "completed_at": completed_at,
            "error": "Price refresh already running.",
        }

    try:
        return _refresh_current_prices()
    finally:
        PRICE_REFRESH_LOCK.release()


def _refresh_current_prices():
    init_db()
    started_at = utc_now()
    mark_stale_price_refreshes(started_at)
    targets = active_price_targets()
    needs_usd_cad = portfolio_needs_usd_cad() or any(str(target["market"] or "").upper() == "US" for target in targets)

    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO price_refreshes (started_at, status, symbol_count)
            VALUES (?, ?, ?)
            """,
            (started_at, "running", len(targets)),
        )
        refresh_id = cursor.lastrowid

    if not targets and not needs_usd_cad:
        completed_at = utc_now()
        with get_connection() as conn:
            conn.execute(
                """
                UPDATE price_refreshes
                SET completed_at = ?, status = ?
                WHERE id = ?
                """,
                (completed_at, "empty", refresh_id),
            )
        return {"status": "empty", "symbol_count": 0, "completed_at": completed_at}

    try:
        import yfinance as yf

        yahoo_symbols = sorted({target["yahoo_symbol"] for target in targets})
        download_symbols = yahoo_symbols + ([USD_CAD_YAHOO_SYMBOL] if needs_usd_cad else [])
        downloaded = yf.download(
            tickers=download_symbols,
            period="1d",
            interval="1m",
            group_by="ticker",
            auto_adjust=False,
            progress=False,
            threads=False,
            timeout=YFINANCE_DOWNLOAD_TIMEOUT_SECONDS,
        )
        daily_downloaded = yf.download(
            tickers=download_symbols,
            period="5d",
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
            progress=False,
            threads=False,
            timeout=YFINANCE_DOWNLOAD_TIMEOUT_SECONDS,
        )
        fetched_at = utc_now()

        usd_cad_rate = 1.0
        if needs_usd_cad:
            fx_price, _ = last_close(downloaded, USD_CAD_YAHOO_SYMBOL)
            if fx_price:
                usd_cad_rate = fx_price
        previous_usd_cad_rate = usd_cad_rate
        if needs_usd_cad:
            previous_fx_price = previous_daily_close(daily_downloaded, USD_CAD_YAHOO_SYMBOL)
            if previous_fx_price:
                previous_usd_cad_rate = previous_fx_price

        with get_connection() as conn:
            if needs_usd_cad:
                fx_change = (
                    usd_cad_rate - previous_usd_cad_rate
                    if usd_cad_rate is not None and previous_usd_cad_rate is not None
                    else None
                )
                fx_change_pct = (
                    fx_change / previous_usd_cad_rate * 100.0
                    if fx_change is not None and previous_usd_cad_rate
                    else None
                )
                conn.execute(
                    """
                    INSERT INTO latest_prices (
                        symbol, market, yahoo_symbol, price, price_currency, fx_to_cad, price_cad,
                        previous_close, previous_close_cad, day_change, day_change_pct,
                        quote_time, fetched_at, source, error
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, market) DO UPDATE SET
                        yahoo_symbol = excluded.yahoo_symbol,
                        price = excluded.price,
                        price_currency = excluded.price_currency,
                        fx_to_cad = excluded.fx_to_cad,
                        price_cad = excluded.price_cad,
                        previous_close = excluded.previous_close,
                        previous_close_cad = excluded.previous_close_cad,
                        day_change = excluded.day_change,
                        day_change_pct = excluded.day_change_pct,
                        quote_time = excluded.quote_time,
                        fetched_at = excluded.fetched_at,
                        source = excluded.source,
                        error = excluded.error
                    """,
                    (
                        USD_CAD_SYMBOL,
                        USD_CAD_MARKET,
                        USD_CAD_YAHOO_SYMBOL,
                        usd_cad_rate,
                        "CAD",
                        1.0,
                        usd_cad_rate,
                        previous_usd_cad_rate,
                        previous_usd_cad_rate,
                        fx_change,
                        fx_change_pct,
                        fetched_at,
                        fetched_at,
                        "yfinance",
                        None if usd_cad_rate else "No FX rate returned.",
                    ),
                )

            for target in targets:
                source_price, quote_time = last_close(downloaded, target["yahoo_symbol"])
                previous_close = previous_daily_close(daily_downloaded, target["yahoo_symbol"])
                market = str(target["market"] or "").upper()
                price_currency = "USD" if market == "US" else "CAD"
                fx_to_cad = usd_cad_rate if market == "US" else 1.0
                previous_fx_to_cad = previous_usd_cad_rate if market == "US" else 1.0
                price_cad = source_price * fx_to_cad if source_price is not None else None
                previous_close_cad = previous_close * previous_fx_to_cad if previous_close is not None else None
                day_change = (
                    price_cad - previous_close_cad
                    if price_cad is not None and previous_close_cad is not None
                    else None
                )
                day_change_pct = (
                    day_change / previous_close_cad * 100.0
                    if day_change is not None and previous_close_cad
                    else None
                )
                error = None if source_price is not None else "No price returned."

                conn.execute(
                    """
                    INSERT INTO latest_prices (
                        symbol, market, yahoo_symbol, price, price_currency, fx_to_cad, price_cad,
                        previous_close, previous_close_cad, day_change, day_change_pct,
                        quote_time, fetched_at, source, error
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, market) DO UPDATE SET
                        yahoo_symbol = excluded.yahoo_symbol,
                        price = excluded.price,
                        price_currency = excluded.price_currency,
                        fx_to_cad = excluded.fx_to_cad,
                        price_cad = excluded.price_cad,
                        previous_close = excluded.previous_close,
                        previous_close_cad = excluded.previous_close_cad,
                        day_change = excluded.day_change,
                        day_change_pct = excluded.day_change_pct,
                        quote_time = excluded.quote_time,
                        fetched_at = excluded.fetched_at,
                        source = excluded.source,
                        error = excluded.error
                    """,
                    (
                        target["symbol"],
                        target["market"],
                        target["yahoo_symbol"],
                        source_price,
                        price_currency,
                        fx_to_cad,
                        price_cad,
                        previous_close,
                        previous_close_cad,
                        day_change,
                        day_change_pct,
                        quote_time,
                        fetched_at,
                        "yfinance",
                        error,
                    ),
                )

            conn.execute(
                """
                UPDATE price_refreshes
                SET completed_at = ?, status = ?
                WHERE id = ?
                """,
                (fetched_at, "ok", refresh_id),
            )

        return {"status": "ok", "symbol_count": len(targets), "completed_at": fetched_at}
    except Exception as exc:
        completed_at = utc_now()
        with get_connection() as conn:
            conn.execute(
                """
                UPDATE price_refreshes
                SET completed_at = ?, status = ?, error = ?
                WHERE id = ?
                """,
                (completed_at, "error", str(exc), refresh_id),
            )
        return {"status": "error", "symbol_count": len(targets), "completed_at": completed_at, "error": str(exc)}


def parse_iso_day(value: str):
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def stock_currency_for(market):
    return "USD" if str(market or "").strip().upper() == "US" else "CAD"


def index_value_to_date_text(value):
    if hasattr(value, "date"):
        return value.date().isoformat()
    text = str(value or "")
    return text[:10] if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", text) else ""


def stock_cache_meta(conn, yahoo_symbol):
    return conn.execute(
        """
        SELECT *
        FROM stock_analytics_refreshes
        WHERE yahoo_symbol = ?
        """,
        (yahoo_symbol,),
    ).fetchone()


def stock_cache_is_stale(meta):
    if not meta or not meta.get("fetched_at"):
        return True
    if meta.get("status") != "ok":
        return True
    try:
        fetched_at = datetime.fromisoformat(meta["fetched_at"])
    except ValueError:
        return True
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - fetched_at > timedelta(seconds=STOCK_ANALYTICS_TTL_SECONDS)


def stock_fundamentals_cache_is_stale(meta):
    if not meta or not meta.get("fetched_at"):
        return True
    if meta.get("status") != "ok":
        return True
    try:
        fetched_at = datetime.fromisoformat(meta["fetched_at"])
    except ValueError:
        return True
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - fetched_at > timedelta(seconds=STOCK_ANALYTICS_TTL_SECONDS)


def info_float(info, *keys):
    for key in keys:
        value = finite_float(info.get(key))
        if value is not None:
            return value
    return None


class AlphaQueryEPSTableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_target_table = False
        self.table_depth = 0
        self.in_row = False
        self.in_cell = False
        self.current_cell = []
        self.current_row = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "table":
            class_name = attrs_dict.get("class", "")
            if self.in_target_table:
                self.table_depth += 1
            elif "table-basic" in class_name and "table-bordered" in class_name:
                self.in_target_table = True
                self.table_depth = 1
            return

        if not self.in_target_table:
            return
        if tag == "tr":
            self.in_row = True
            self.current_row = []
        elif tag == "td" and self.in_row:
            self.in_cell = True
            self.current_cell = []

    def handle_data(self, data):
        if self.in_cell:
            self.current_cell.append(data)

    def handle_endtag(self, tag):
        if not self.in_target_table:
            return
        if tag == "td" and self.in_cell:
            self.current_row.append("".join(self.current_cell).strip())
            self.current_cell = []
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            if self.current_row:
                self.rows.append(self.current_row)
            self.current_row = []
            self.in_row = False
        elif tag == "table":
            self.table_depth -= 1
            if self.table_depth <= 0:
                self.in_target_table = False
                self.table_depth = 0


def alphaquery_symbol_candidates(symbol, market):
    clean_symbol = str(symbol or "").strip().upper()
    clean_market = str(market or "").strip().upper()
    candidates = []

    if clean_market in {"CDN", "CAN", "CA", "TSX", "TSXV"}:
        candidates.append(f"T.{clean_symbol.split('.', 1)[0]}")
    else:
        candidates.append(clean_symbol.replace("-", "."))
        candidates.append(clean_symbol.replace(".", "-"))
        candidates.append(clean_symbol)

    seen = set()
    return [candidate for candidate in candidates if candidate and not (candidate in seen or seen.add(candidate))]


def fetch_earnings_history_page(symbol, market):
    errors = []
    for alpha_symbol in alphaquery_symbol_candidates(symbol, market):
        url = f"https://www.alphaquery.com/stock/{alpha_symbol}/earnings-history"
        request = Request(url, headers={"User-Agent": ALPHAQUERY_USER_AGENT})
        try:
            with urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8", errors="replace")
        except Exception as exc:
            errors.append(f"{alpha_symbol}: {exc}")
    raise ValueError("; ".join(errors) or "No AlphaQuery symbol candidates.")


def parse_alphaquery_date(value):
    text = str(value or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return parse_iso_day(text)


def parse_eps_value(value):
    text = str(value or "").strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = re.sub(r"[^\d.-]", "", text)
    parsed = finite_float(text)
    if parsed is None:
        return None
    return -abs(parsed) if negative else parsed


def parse_eps_table(html_text):
    parser = AlphaQueryEPSTableParser()
    parser.feed(html_text)

    eps_rows = []
    for row in parser.rows:
        if len(row) != 4:
            continue
        fiscal_quarter_end = parse_alphaquery_date(row[1])
        actual_eps = parse_eps_value(row[3])
        if fiscal_quarter_end and actual_eps is not None:
            eps_rows.append({"fiscal_quarter_end": fiscal_quarter_end, "actual_eps": actual_eps})
    return eps_rows


def selected_eps_years(num_years=EPS_HISTORY_YEARS):
    end_year = datetime.now(timezone.utc).year - 1
    return list(range(end_year - num_years + 1, end_year + 1))


def build_yearly_eps_series_from_alphaquery(symbol, market, num_years=EPS_HISTORY_YEARS):
    html_text = fetch_earnings_history_page(symbol, market)
    rows = parse_eps_table(html_text)
    years = selected_eps_years(num_years)
    selected = set(years)
    totals = {}
    for row in rows:
        year = row["fiscal_quarter_end"].year
        if year in selected:
            totals[year] = totals.get(year, 0.0) + row["actual_eps"]
    return {year: round(value, 2) for year, value in sorted(totals.items())}


def normalize_eps_lookup_symbol(symbol):
    text = str(symbol or "").strip().upper()
    if text.startswith("T."):
        text = text[2:]
    if text.endswith(".TO"):
        text = text[:-3]
    return text.replace("-", ".")


def build_yearly_eps_series_from_csv(symbol, market, num_years=EPS_HISTORY_YEARS):
    target_symbol = normalize_eps_lookup_symbol(symbol)
    if not target_symbol:
        return {}

    years = selected_eps_years(num_years)
    for path in EPS_PIVOT_CSV_PATHS:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8", newline="") as file:
            for row in csv.DictReader(file):
                if normalize_eps_lookup_symbol(row.get("Symbol")) != target_symbol:
                    continue
                yearly_eps = {}
                for year in years:
                    value = parse_eps_value(row.get(str(year)))
                    if value is not None:
                        yearly_eps[year] = value
                return yearly_eps
    return {}


def yfinance_eps_report_year(timestamp):
    if hasattr(timestamp, "to_pydatetime"):
        timestamp = timestamp.to_pydatetime()
    elif isinstance(timestamp, str):
        parsed = parse_iso_day(timestamp)
        if parsed is None:
            return None
        return parsed.year

    month = getattr(timestamp, "month", None)
    year = getattr(timestamp, "year", None)
    if not month or not year:
        return None
    return year - 1 if month <= 3 else year


def build_yearly_eps_series_from_yfinance(symbol, market, num_years=EPS_HISTORY_YEARS):
    import yfinance as yf

    ticker = yahoo_symbol_for(symbol, market)
    if not ticker:
        return {}

    years = selected_eps_years(num_years)
    selected = set(years)
    totals = {}
    df = yf.Ticker(ticker).get_earnings_dates(limit=max(64, (num_years + 3) * 4))
    if df is None or getattr(df, "empty", True):
        return {}

    reported_eps_column = next(
        (column for column in df.columns if str(column).strip().lower() == "reported eps"),
        None,
    )
    if reported_eps_column is None:
        return {}

    for timestamp, row in df.iterrows():
        eps = finite_float(row.get(reported_eps_column))
        year = yfinance_eps_report_year(timestamp)
        if eps is not None and year in selected:
            totals[year] = totals.get(year, 0.0) + eps

    return {year: round(value, 2) for year, value in sorted(totals.items())}


def build_yearly_eps_series_with_source(symbol, market, num_years=EPS_HISTORY_YEARS):
    source_builders = (
        ("alphaquery", build_yearly_eps_series_from_alphaquery),
        ("eps_csv", build_yearly_eps_series_from_csv),
        ("yfinance_earnings_dates", build_yearly_eps_series_from_yfinance),
    )
    errors = []
    for source, builder in source_builders:
        try:
            years = builder(symbol, market, num_years)
        except Exception as exc:
            errors.append(f"{source}: {exc}")
            continue
        if years:
            return years, source, None

    error = "; ".join(errors)
    if error:
        error = f"No EPS data available. {error}"
    else:
        error = "No EPS data available."
    return {}, "none", error


def build_yearly_eps_series(symbol, market, num_years=EPS_HISTORY_YEARS):
    years, _source, _error = build_yearly_eps_series_with_source(symbol, market, num_years)
    return years


def stock_eps_meta(conn, yahoo_symbol):
    return conn.execute(
        """
        SELECT *
        FROM stock_eps_refreshes
        WHERE yahoo_symbol = ?
        """,
        (yahoo_symbol,),
    ).fetchone()


def stock_eps_cache_is_stale(meta):
    if not meta or not meta.get("fetched_at"):
        return True
    if meta.get("status") in {"ok", "empty"}:
        try:
            fetched_at = datetime.fromisoformat(meta["fetched_at"])
        except ValueError:
            return True
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - fetched_at > timedelta(seconds=STOCK_ANALYTICS_TTL_SECONDS)
    return True


def refresh_stock_eps_history(symbol, market):
    init_db()
    clean_symbol = str(symbol or "").strip().upper()
    clean_market = str(market or "").strip().upper()
    yahoo_symbol = yahoo_symbol_for(clean_symbol, clean_market)
    if not yahoo_symbol:
        raise ValueError("A stock symbol is required.")

    fetched_at = utc_now()
    try:
        years, source, source_error = build_yearly_eps_series_with_source(clean_symbol, clean_market, EPS_HISTORY_YEARS)
        status = "ok" if years else "empty"
        error = None if years else source_error

        with get_connection() as conn:
            conn.execute("DELETE FROM stock_eps_history WHERE yahoo_symbol = ?", (yahoo_symbol,))
            conn.executemany(
                """
                INSERT INTO stock_eps_history (yahoo_symbol, fiscal_year, eps, fetched_at, source)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(yahoo_symbol, fiscal_year) DO UPDATE SET
                    eps = excluded.eps,
                    fetched_at = excluded.fetched_at,
                    source = excluded.source
                """,
                [(yahoo_symbol, year, eps, fetched_at, source) for year, eps in years.items()],
            )
            conn.execute(
                """
                INSERT INTO stock_eps_refreshes (
                    yahoo_symbol, symbol, market, fetched_at, status, error
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(yahoo_symbol) DO UPDATE SET
                    symbol = excluded.symbol,
                    market = excluded.market,
                    fetched_at = excluded.fetched_at,
                    status = excluded.status,
                    error = excluded.error
                """,
                (yahoo_symbol, clean_symbol, clean_market, fetched_at, status, error),
            )

        return {
            "status": status,
            "symbol": clean_symbol,
            "market": clean_market,
            "yahoo_symbol": yahoo_symbol,
            "eps_count": len(years),
            "fetched_at": fetched_at,
        }
    except Exception as exc:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO stock_eps_refreshes (
                    yahoo_symbol, symbol, market, fetched_at, status, error
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(yahoo_symbol) DO UPDATE SET
                    symbol = excluded.symbol,
                    market = excluded.market,
                    fetched_at = excluded.fetched_at,
                    status = excluded.status,
                    error = excluded.error
                """,
                (yahoo_symbol, clean_symbol, clean_market, fetched_at, "error", str(exc)),
            )
        raise


def refresh_stock_fundamentals(symbol, market):
    init_db()
    clean_symbol = str(symbol or "").strip().upper()
    clean_market = str(market or "").strip().upper()
    yahoo_symbol = yahoo_symbol_for(clean_symbol, clean_market)
    if not yahoo_symbol:
        raise ValueError("A stock symbol is required.")

    fetched_at = utc_now()
    try:
        import yfinance as yf

        ticker = yf.Ticker(yahoo_symbol)
        if hasattr(ticker, "get_info"):
            info = ticker.get_info() or {}
        else:
            info = ticker.info or {}

        currency = str(info.get("currency") or info.get("financialCurrency") or stock_currency_for(clean_market)).upper()
        eps_current = info_float(info, "trailingEps", "forwardEps")
        pe_ratio = info_float(info, "trailingPE", "forwardPE")
        book_value_per_share = info_float(info, "bookValue")
        fifty_two_week_high = info_float(info, "fiftyTwoWeekHigh")
        fifty_two_week_low = info_float(info, "fiftyTwoWeekLow")

        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO stock_fundamentals (
                    yahoo_symbol, symbol, market, currency, eps_current, pe_ratio, book_value_per_share,
                    fifty_two_week_high, fifty_two_week_low, fetched_at, status, error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(yahoo_symbol) DO UPDATE SET
                    symbol = excluded.symbol,
                    market = excluded.market,
                    currency = excluded.currency,
                    eps_current = excluded.eps_current,
                    pe_ratio = excluded.pe_ratio,
                    book_value_per_share = excluded.book_value_per_share,
                    fifty_two_week_high = excluded.fifty_two_week_high,
                    fifty_two_week_low = excluded.fifty_two_week_low,
                    fetched_at = excluded.fetched_at,
                    status = excluded.status,
                    error = excluded.error
                """,
                (
                    yahoo_symbol,
                    clean_symbol,
                    clean_market,
                    currency,
                    eps_current,
                    pe_ratio,
                    book_value_per_share,
                    fifty_two_week_high,
                    fifty_two_week_low,
                    fetched_at,
                    "ok",
                    None,
                ),
            )

        return {
            "status": "ok",
            "symbol": clean_symbol,
            "market": clean_market,
            "yahoo_symbol": yahoo_symbol,
            "fetched_at": fetched_at,
        }
    except Exception as exc:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO stock_fundamentals (
                    yahoo_symbol, symbol, market, currency, fetched_at, status, error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(yahoo_symbol) DO UPDATE SET
                    symbol = excluded.symbol,
                    market = excluded.market,
                    currency = excluded.currency,
                    fetched_at = excluded.fetched_at,
                    status = excluded.status,
                    error = excluded.error
                """,
                (
                    yahoo_symbol,
                    clean_symbol,
                    clean_market,
                    stock_currency_for(clean_market),
                    fetched_at,
                    "error",
                    str(exc),
                ),
            )
        raise


def refresh_stock_analytics(symbol, market):
    init_db()
    clean_symbol = str(symbol or "").strip().upper()
    clean_market = str(market or "").strip().upper()
    yahoo_symbol = yahoo_symbol_for(clean_symbol, clean_market)
    if not yahoo_symbol:
        raise ValueError("A stock symbol is required.")

    fetched_at = utc_now()
    currency = stock_currency_for(clean_market)

    try:
        import yfinance as yf

        history = yf.Ticker(yahoo_symbol).history(
            period="5y",
            interval="1d",
            auto_adjust=False,
            actions=True,
        )
        if history is None or getattr(history, "empty", True) or "Close" not in history:
            raise ValueError(f"No historical price data returned for {yahoo_symbol}.")

        price_rows = []
        dividend_rows = []
        for index, row in history.iterrows():
            date_text = index_value_to_date_text(index)
            close = finite_float(row.get("Close"))
            if date_text and close is not None:
                price_rows.append((yahoo_symbol, date_text, close, currency, fetched_at))

            dividend = finite_float(row.get("Dividends")) if "Dividends" in history.columns else None
            if date_text and dividend is not None and dividend > 0:
                dividend_rows.append((yahoo_symbol, date_text, dividend, currency, fetched_at))

        if not price_rows:
            raise ValueError(f"No usable historical prices returned for {yahoo_symbol}.")

        with get_connection() as conn:
            conn.execute("DELETE FROM stock_price_history WHERE yahoo_symbol = ?", (yahoo_symbol,))
            conn.execute("DELETE FROM stock_dividend_history WHERE yahoo_symbol = ?", (yahoo_symbol,))
            conn.executemany(
                """
                INSERT INTO stock_price_history (yahoo_symbol, date, close, currency, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(yahoo_symbol, date) DO UPDATE SET
                    close = excluded.close,
                    currency = excluded.currency,
                    fetched_at = excluded.fetched_at
                """,
                price_rows,
            )
            conn.executemany(
                """
                INSERT INTO stock_dividend_history (
                    yahoo_symbol, ex_date, dividend_per_share, currency, fetched_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(yahoo_symbol, ex_date) DO UPDATE SET
                    dividend_per_share = excluded.dividend_per_share,
                    currency = excluded.currency,
                    fetched_at = excluded.fetched_at
                """,
                dividend_rows,
            )
            conn.execute(
                """
                INSERT INTO stock_analytics_refreshes (
                    yahoo_symbol, symbol, market, currency, fetched_at, status, error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(yahoo_symbol) DO UPDATE SET
                    symbol = excluded.symbol,
                    market = excluded.market,
                    currency = excluded.currency,
                    fetched_at = excluded.fetched_at,
                    status = excluded.status,
                    error = excluded.error
                """,
                (yahoo_symbol, clean_symbol, clean_market, currency, fetched_at, "ok", None),
            )

        return {
            "status": "ok",
            "symbol": clean_symbol,
            "market": clean_market,
            "yahoo_symbol": yahoo_symbol,
            "price_count": len(price_rows),
            "dividend_count": len(dividend_rows),
            "fetched_at": fetched_at,
        }
    except Exception as exc:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO stock_analytics_refreshes (
                    yahoo_symbol, symbol, market, currency, fetched_at, status, error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(yahoo_symbol) DO UPDATE SET
                    symbol = excluded.symbol,
                    market = excluded.market,
                    currency = excluded.currency,
                    fetched_at = excluded.fetched_at,
                    status = excluded.status,
                    error = excluded.error
                """,
                (yahoo_symbol, clean_symbol, clean_market, currency, fetched_at, "error", str(exc)),
            )
        raise


def trailing_dividend(dividends, as_of_date):
    cutoff = as_of_date - timedelta(days=365)
    return sum(
        dividend["dividend_per_share"] or 0.0
        for dividend in dividends
        if (parsed := parse_iso_day(dividend["ex_date"])) and cutoff < parsed <= as_of_date
    )


def monthly_yield_series(prices, dividends):
    monthly_prices = {}
    for price in prices:
        price_date = parse_iso_day(price["date"])
        if not price_date:
            continue
        month_key = price["date"][:7]
        existing = monthly_prices.get(month_key)
        if not existing or price["date"] > existing["date"]:
            monthly_prices[month_key] = price

    yields = []
    for price in sorted(monthly_prices.values(), key=lambda item: item["date"]):
        price_date = parse_iso_day(price["date"])
        close = finite_float(price["close"])
        if not price_date or not close:
            continue
        ttm = trailing_dividend(dividends, price_date)
        if ttm <= 0:
            continue
        yields.append(
            {
                "date": price["date"],
                "close": close,
                "ttm_dividend": ttm,
                "yield_pct": (ttm / close) * 100.0,
            }
        )
    return yields


def annual_forward_dividend_stats(dividends):
    parsed_dividends = []
    for dividend in dividends:
        ex_date = parse_iso_day(dividend["ex_date"])
        amount = finite_float(dividend.get("dividend_per_share"))
        if ex_date and amount is not None and amount > 0:
            parsed_dividends.append(
                {
                    "ex_date": dividend["ex_date"],
                    "date": ex_date,
                    "dividend_per_share": amount,
                }
            )

    parsed_dividends.sort(key=lambda item: item["date"])
    if not parsed_dividends:
        return {
            "latest_dividend": None,
            "latest_ex_date": None,
            "payments_per_year": 0,
            "annual_forward_dividend": 0.0,
        }

    latest = parsed_dividends[-1]
    payments_by_year = {}
    for item in parsed_dividends:
        payments_by_year[item["date"].year] = payments_by_year.get(item["date"].year, 0) + 1

    completed_years = sorted(
        (year for year, count in payments_by_year.items() if year < latest["date"].year and count > 0),
        reverse=True,
    )
    if completed_years:
        payments_per_year = payments_by_year[completed_years[0]]
    else:
        recent = parsed_dividends[-6:]
        gaps = [
            (right["date"] - left["date"]).days
            for left, right in zip(recent, recent[1:])
            if (right["date"] - left["date"]).days > 0
        ]
        if gaps:
            median_gap = sorted(gaps)[len(gaps) // 2]
            if median_gap <= 45:
                payments_per_year = 12
            elif median_gap <= 75:
                payments_per_year = 6
            elif median_gap <= 120:
                payments_per_year = 4
            elif median_gap <= 210:
                payments_per_year = 2
            else:
                payments_per_year = 1
        else:
            payments_per_year = 1
    annual_forward_dividend = latest["dividend_per_share"] * payments_per_year

    return {
        "latest_dividend": latest["dividend_per_share"],
        "latest_ex_date": latest["ex_date"],
        "payments_per_year": payments_per_year,
        "annual_forward_dividend": annual_forward_dividend,
    }


def five_year_dividend_growth_stats(dividends):
    parsed_dividends = []
    for dividend in dividends:
        ex_date = parse_iso_day(dividend["ex_date"])
        amount = finite_float(dividend.get("dividend_per_share"))
        if ex_date and amount is not None and amount > 0:
            parsed_dividends.append(
                {
                    "ex_date": dividend["ex_date"],
                    "date": ex_date,
                    "dividend_per_share": amount,
                }
            )

    parsed_dividends.sort(key=lambda item: item["date"])
    if len(parsed_dividends) < 2:
        return {
            "five_year_dividend_growth_pct": None,
            "five_year_dividend_growth_years": 0,
            "five_year_dividend_growth_start_date": None,
            "five_year_dividend_growth_end_date": None,
        }

    latest = parsed_dividends[-1]
    five_year_cutoff = latest["date"] - timedelta(days=round(365.25 * 5))
    start = next((item for item in parsed_dividends if item["date"] >= five_year_cutoff), parsed_dividends[0])
    years = (latest["date"] - start["date"]).days / 365.25
    if years <= 0 or start["dividend_per_share"] <= 0:
        growth_pct = None
    else:
        growth_pct = ((latest["dividend_per_share"] / start["dividend_per_share"]) ** (1 / years) - 1) * 100.0

    return {
        "five_year_dividend_growth_pct": growth_pct,
        "five_year_dividend_growth_years": years,
        "five_year_dividend_growth_start_date": start["ex_date"],
        "five_year_dividend_growth_end_date": latest["ex_date"],
        "five_year_dividend_growth_start_dividend": start["dividend_per_share"],
        "five_year_dividend_growth_end_dividend": latest["dividend_per_share"],
    }


def unique_stock_targets_from_holdings(holdings):
    targets = {}
    for holding in holdings:
        if holding.get("asset_type") == "Cash":
            continue
        symbol = str(holding.get("symbol") or "").strip().upper()
        market = str(holding.get("market") or "").strip().upper()
        if not symbol or symbol == "CASH":
            continue
        yahoo_symbol = yahoo_symbol_for(symbol, market)
        if yahoo_symbol:
            targets[yahoo_symbol] = {"symbol": symbol, "market": market, "yahoo_symbol": yahoo_symbol}
    return targets


def ensure_stock_analytics_cache_for_holdings(holdings):
    targets = unique_stock_targets_from_holdings(holdings)
    if not targets:
        return

    with get_connection() as conn:
        placeholders = ",".join("?" for _ in targets)
        existing = conn.execute(
            f"""
            SELECT *
            FROM stock_analytics_refreshes
            WHERE yahoo_symbol IN ({placeholders})
            """,
            list(targets),
        ).fetchall()

    cached = {
        row["yahoo_symbol"]
        for row in existing
        if row["status"] == "ok" or not stock_cache_is_stale(row)
    }
    missing = [target for yahoo_symbol, target in targets.items() if yahoo_symbol not in cached]
    for target in missing:
        try:
            refresh_stock_analytics(target["symbol"], target["market"])
        except Exception:
            continue


def stock_forward_dividend_map_for_holdings(holdings):
    targets = unique_stock_targets_from_holdings(holdings)
    if not targets:
        return {}

    with get_connection() as conn:
        placeholders = ",".join("?" for _ in targets)
        rows = conn.execute(
            f"""
            SELECT yahoo_symbol, ex_date, dividend_per_share
            FROM stock_dividend_history
            WHERE yahoo_symbol IN ({placeholders})
            ORDER BY yahoo_symbol, ex_date
            """,
            list(targets),
        ).fetchall()

    dividends_by_symbol = {}
    for row in rows:
        dividends_by_symbol.setdefault(row["yahoo_symbol"], []).append(row)

    dividend_stats = {}
    for yahoo_symbol in targets:
        dividends = dividends_by_symbol.get(yahoo_symbol, [])
        stats = annual_forward_dividend_stats(dividends)
        stats.update(five_year_dividend_growth_stats(dividends))
        dividend_stats[yahoo_symbol] = stats

    return dividend_stats


def stock_holdings(symbol, market, annual_forward_dividend=0.0):
    summary = get_summary()
    clean_symbol = str(symbol or "").strip().upper()
    clean_market = str(market or "").strip().upper()
    visible = [
        holding
        for holding in summary["holdings"]
        if str(holding.get("symbol") or "").strip().upper() == clean_symbol
        and str(holding.get("market") or "").strip().upper() == clean_market
        and holding.get("asset_type") != "Cash"
    ]

    accounts = {}
    for holding in visible:
        account = accounts.setdefault(
            holding["account_name"],
            {
                "account_name": holding["account_name"],
                "quantity": 0.0,
                "current_value": 0.0,
                "book_value": 0.0,
                "gain_loss": 0.0,
            },
        )
        account["quantity"] += Number_or_zero(holding.get("quantity"))
        account["current_value"] += Number_or_zero(holding.get("current_value") or holding.get("closing_value"))
        account["book_value"] += Number_or_zero(holding.get("book_value"))
        account["gain_loss"] += Number_or_zero(holding.get("current_gain_loss") or holding.get("gain_loss"))

    account_rows = []
    for account in accounts.values():
        account["average_cost"] = account["book_value"] / account["quantity"] if account["quantity"] else None
        account["annual_forward_dividend"] = annual_forward_dividend
        account["annual_forward_income"] = account["quantity"] * annual_forward_dividend
        account["forward_yield_on_value_pct"] = (
            account["annual_forward_income"] / account["current_value"] * 100.0
            if account["current_value"]
            else None
        )
        account["yield_on_cost_pct"] = (
            account["annual_forward_income"] / account["book_value"] * 100.0
            if account["book_value"]
            else None
        )
        account["gain_loss_pct"] = (
            account["gain_loss"] / account["book_value"] * 100.0 if account["book_value"] else None
        )
        account_rows.append(account)

    return {
        "description": next((holding.get("description") for holding in visible if holding.get("description")), ""),
        "accounts": sorted(account_rows, key=lambda item: item["current_value"], reverse=True),
        "total_quantity": sum(item["quantity"] for item in account_rows),
        "total_current_value": sum(item["current_value"] for item in account_rows),
        "total_book_value": sum(item["book_value"] for item in account_rows),
        "total_annual_forward_income": sum(item["annual_forward_income"] for item in account_rows),
    }


def Number_or_zero(value):
    number = finite_float(value)
    return number if number is not None else 0.0


def get_stock_detail(symbol, market, refresh=False):
    init_db()
    clean_symbol = str(symbol or "").strip().upper()
    clean_market = str(market or "").strip().upper()
    yahoo_symbol = yahoo_symbol_for(clean_symbol, clean_market)
    if not yahoo_symbol:
        raise ValueError("A stock symbol is required.")

    refresh_error = None
    with get_connection() as conn:
        meta = stock_cache_meta(conn, yahoo_symbol)
    if refresh or stock_cache_is_stale(meta):
        try:
            refresh_stock_analytics(clean_symbol, clean_market)
        except Exception as exc:
            refresh_error = str(exc)

    with get_connection() as conn:
        meta = stock_cache_meta(conn, yahoo_symbol)
        prices = conn.execute(
            """
            SELECT date, close, currency, fetched_at
            FROM stock_price_history
            WHERE yahoo_symbol = ?
            ORDER BY date
            """,
            (yahoo_symbol,),
        ).fetchall()
        dividends = conn.execute(
            """
            SELECT ex_date, dividend_per_share, currency, fetched_at
            FROM stock_dividend_history
            WHERE yahoo_symbol = ?
            ORDER BY ex_date
            """,
            (yahoo_symbol,),
        ).fetchall()
        latest_live = conn.execute(
            """
            SELECT price, price_currency, price_cad, quote_time, fetched_at
            FROM latest_prices
            WHERE symbol = ? AND market = ?
            """,
            (clean_symbol, clean_market),
        ).fetchone()

    latest_price_point = prices[-1] if prices else None
    latest_price_date = latest_price_point["date"] if latest_price_point else None
    history_price = latest_price_point["close"] if latest_price_point else None
    current_price = latest_live["price"] if latest_live and latest_live["price"] is not None else history_price
    current_price_date = latest_live["quote_time"] if latest_live and latest_live["quote_time"] else latest_price_date
    currency = (
        latest_live["price_currency"]
        if latest_live and latest_live["price_currency"]
        else (latest_price_point["currency"] if latest_price_point else stock_currency_for(clean_market))
    )

    latest_date = parse_iso_day(latest_price_date) if latest_price_date else None
    ttm = trailing_dividend(dividends, latest_date) if latest_date else 0.0
    forward_stats = annual_forward_dividend_stats(dividends)
    dividend_growth_stats = five_year_dividend_growth_stats(dividends)
    current_yield_pct = (ttm / current_price * 100.0) if ttm and current_price else None
    forward_yield_pct = (
        forward_stats["annual_forward_dividend"] / current_price * 100.0
        if forward_stats["annual_forward_dividend"] and current_price
        else None
    )
    monthly_yields = monthly_yield_series(prices, dividends)
    five_year_avg_yield_pct = (
        sum(item["yield_pct"] for item in monthly_yields) / len(monthly_yields) if monthly_yields else None
    )
    holdings = stock_holdings(clean_symbol, clean_market, forward_stats["annual_forward_dividend"])

    return {
        "symbol": clean_symbol,
        "market": clean_market,
        "yahoo_symbol": yahoo_symbol,
        "description": holdings["description"],
        "currency": currency,
        "fetched_at": meta["fetched_at"] if meta else None,
        "status": meta["status"] if meta else "empty",
        "error": refresh_error or (meta["error"] if meta else None),
        "stats": {
            "current_price": current_price,
            "current_price_date": current_price_date,
            "ttm_dividend": ttm,
            "latest_dividend": forward_stats["latest_dividend"],
            "latest_ex_date": forward_stats["latest_ex_date"],
            "payments_per_year": forward_stats["payments_per_year"],
            "annual_forward_dividend": forward_stats["annual_forward_dividend"],
            "current_yield_pct": current_yield_pct,
            "forward_yield_pct": forward_yield_pct,
            "five_year_avg_yield_pct": five_year_avg_yield_pct,
            **dividend_growth_stats,
            "price_count": len(prices),
            "dividend_count": len(dividends),
        },
        "prices": prices,
        "dividends": dividends,
        "monthly_yields": monthly_yields,
        "holdings": holdings,
    }


def current_stock_targets_from_summary(summary):
    targets = {}
    for holding in summary.get("holdings", []):
        if holding.get("asset_type") == "Cash":
            continue
        symbol = str(holding.get("symbol") or "").strip().upper()
        market = str(holding.get("market") or "").strip().upper()
        yahoo_symbol = yahoo_symbol_for(symbol, market)
        if not yahoo_symbol:
            continue

        target = targets.setdefault(
            yahoo_symbol,
            {
                "symbol": symbol,
                "market": market,
                "yahoo_symbol": yahoo_symbol,
                "description": holding.get("description") or symbol,
                "currency": stock_currency_for(market),
                "quantity": 0.0,
                "current_value": 0.0,
                "current_price": None,
                "current_price_date": None,
                "owned": True,
                "watchlist": False,
            },
        )
        target["owned"] = True
        if not target.get("description") and holding.get("description"):
            target["description"] = holding["description"]

        target["quantity"] += Number_or_zero(holding.get("quantity"))
        target["current_value"] += Number_or_zero(holding.get("current_value") or holding.get("closing_value"))

        source_currency = str(
            holding.get("source_price_currency")
            or holding.get("source_currency")
            or holding.get("currency")
            or stock_currency_for(market)
        ).upper()
        target["currency"] = source_currency or target["currency"]

        source_price = finite_float(holding.get("source_price"))
        source_closing_price = finite_float(holding.get("source_closing_price"))
        cad_current_price = finite_float(holding.get("current_price"))
        if source_price is not None:
            target["current_price"] = source_price
        elif source_closing_price is not None:
            target["current_price"] = source_closing_price
        elif market != "US" and cad_current_price is not None:
            target["current_price"] = cad_current_price

        price_date = holding.get("price_quote_time") or holding.get("current_price_fetched_at") or holding.get("imported_at")
        if price_date and (not target["current_price_date"] or str(price_date) > str(target["current_price_date"])):
            target["current_price_date"] = price_date

    return targets


def normalize_fundamentals_symbol(symbol, market="CDN"):
    clean_symbol = str(symbol or "").strip().upper()
    clean_market = str(market or "CDN").strip().upper()

    if ":" in clean_symbol:
        prefix, _, suffix = clean_symbol.partition(":")
        clean_symbol = suffix.strip().upper()
        if prefix in {"TSE", "TSX", "TSXV", "CDN", "CAN", "CA"}:
            clean_market = "CDN"
        elif prefix in {"NYSE", "NASDAQ", "US", "AMEX"}:
            clean_market = "US"

    if clean_symbol.endswith(".TO"):
        clean_symbol = clean_symbol[:-3]
        clean_market = "CDN"

    if clean_market in {"TSE", "TSX", "TSXV", "CAN", "CA"}:
        clean_market = "CDN"

    return clean_symbol, clean_market


def fundamentals_watchlist_rows(conn):
    return conn.execute(
        """
        SELECT *
        FROM fundamentals_watchlist
        WHERE active = 1
        ORDER BY market, symbol
        """
    ).fetchall()


def merge_watchlist_targets(targets):
    with get_connection() as conn:
        rows = fundamentals_watchlist_rows(conn)

    for row in rows:
        symbol = str(row["symbol"] or "").strip().upper()
        market = str(row["market"] or "").strip().upper()
        yahoo_symbol = yahoo_symbol_for(symbol, market)
        if not yahoo_symbol:
            continue

        target = targets.setdefault(
            yahoo_symbol,
            {
                "symbol": symbol,
                "market": market,
                "yahoo_symbol": yahoo_symbol,
                "description": row.get("description") or symbol,
                "currency": stock_currency_for(market),
                "quantity": 0.0,
                "current_value": 0.0,
                "current_price": None,
                "current_price_date": None,
                "owned": False,
                "watchlist": True,
            },
        )
        target["watchlist"] = True
        if not target.get("description") or target.get("description") == target["symbol"]:
            target["description"] = row.get("description") or target["description"]


def ensure_stock_analytics_cache_for_targets(targets, refresh=False):
    if not targets:
        return

    with get_connection() as conn:
        placeholders = ",".join("?" for _ in targets)
        existing = conn.execute(
            f"""
            SELECT *
            FROM stock_analytics_refreshes
            WHERE yahoo_symbol IN ({placeholders})
            """,
            list(targets),
        ).fetchall()

    cached = {
        row["yahoo_symbol"]
        for row in existing
        if row["status"] == "ok" and not stock_cache_is_stale(row)
    }

    for yahoo_symbol, target in targets.items():
        if refresh or yahoo_symbol not in cached:
            try:
                refresh_stock_analytics(target["symbol"], target["market"])
            except Exception:
                continue


def ensure_stock_eps_cache_for_targets(targets, refresh=False):
    if not targets:
        return

    with get_connection() as conn:
        placeholders = ",".join("?" for _ in targets)
        existing = conn.execute(
            f"""
            SELECT *
            FROM stock_eps_refreshes
            WHERE yahoo_symbol IN ({placeholders})
            """,
            list(targets),
        ).fetchall()

    cached = {
        row["yahoo_symbol"]
        for row in existing
        if not stock_eps_cache_is_stale(row)
    }

    for yahoo_symbol, target in targets.items():
        if refresh or yahoo_symbol not in cached:
            try:
                refresh_stock_eps_history(target["symbol"], target["market"])
            except Exception:
                continue


def save_fundamentals_watchlist_stock(symbol, market="CDN", description=""):
    init_db()
    clean_symbol, clean_market = normalize_fundamentals_symbol(symbol, market)
    if not clean_symbol:
        raise ValueError("Ticker is required.")
    if not yahoo_symbol_for(clean_symbol, clean_market):
        raise ValueError("Ticker cannot be mapped to a Yahoo Finance symbol.")

    now = utc_now()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO fundamentals_watchlist (
                symbol, market, description, active, created_at, updated_at
            )
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(symbol, market) DO UPDATE SET
                description = excluded.description,
                active = 1,
                updated_at = excluded.updated_at
            """,
            (
                clean_symbol,
                clean_market,
                str(description or "").strip(),
                now,
                now,
            ),
        )

    try:
        refresh_stock_analytics(clean_symbol, clean_market)
    except Exception:
        pass
    try:
        refresh_stock_fundamentals(clean_symbol, clean_market)
    except Exception:
        pass
    try:
        refresh_stock_eps_history(clean_symbol, clean_market)
    except Exception:
        pass

    return get_fundamentals(refresh=False)


def delete_fundamentals_watchlist_stock(symbol, market="CDN"):
    init_db()
    clean_symbol, clean_market = normalize_fundamentals_symbol(symbol, market)
    if not clean_symbol:
        raise ValueError("Ticker is required.")

    now = utc_now()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE fundamentals_watchlist
            SET active = 0, updated_at = ?
            WHERE symbol = ? AND market = ?
            """,
            (now, clean_symbol, clean_market),
        )

    return get_fundamentals(refresh=False)


def recent_price_range(prices):
    parsed_prices = []
    for price in prices:
        price_date = parse_iso_day(price["date"])
        close = finite_float(price.get("close"))
        if price_date and close is not None:
            parsed_prices.append((price_date, close))
    if not parsed_prices:
        return (None, None)

    latest_date = max(date for date, _ in parsed_prices)
    cutoff = latest_date - timedelta(days=365)
    recent_closes = [close for date, close in parsed_prices if cutoff <= date <= latest_date]
    if not recent_closes:
        return (None, None)
    return (max(recent_closes), min(recent_closes))


def get_fundamentals(refresh=False):
    init_db()
    summary = get_summary()
    targets = current_stock_targets_from_summary(summary)
    merge_watchlist_targets(targets)
    if not targets:
        return {"rows": [], "eps_years": selected_eps_years(EPS_HISTORY_YEARS), "fetched_at": utc_now()}

    ensure_stock_analytics_cache_for_targets(targets, refresh)
    ensure_stock_eps_cache_for_targets(targets, refresh)

    with get_connection() as conn:
        placeholders = ",".join("?" for _ in targets)
        cached_rows = conn.execute(
            f"""
            SELECT *
            FROM stock_fundamentals
            WHERE yahoo_symbol IN ({placeholders})
            """,
            list(targets),
        ).fetchall()

    cached_by_symbol = {row["yahoo_symbol"]: row for row in cached_rows}
    for yahoo_symbol, target in targets.items():
        meta = cached_by_symbol.get(yahoo_symbol)
        if refresh or stock_fundamentals_cache_is_stale(meta):
            try:
                refresh_stock_fundamentals(target["symbol"], target["market"])
            except Exception:
                continue

    with get_connection() as conn:
        placeholders = ",".join("?" for _ in targets)
        fundamentals_rows = conn.execute(
            f"""
            SELECT *
            FROM stock_fundamentals
            WHERE yahoo_symbol IN ({placeholders})
            """,
            list(targets),
        ).fetchall()
        price_rows = conn.execute(
            f"""
            SELECT yahoo_symbol, date, close, currency, fetched_at
            FROM stock_price_history
            WHERE yahoo_symbol IN ({placeholders})
            ORDER BY yahoo_symbol, date
            """,
            list(targets),
        ).fetchall()
        dividend_rows = conn.execute(
            f"""
            SELECT yahoo_symbol, ex_date, dividend_per_share, currency, fetched_at
            FROM stock_dividend_history
            WHERE yahoo_symbol IN ({placeholders})
            ORDER BY yahoo_symbol, ex_date
            """,
            list(targets),
        ).fetchall()
        eps_rows = conn.execute(
            f"""
            SELECT yahoo_symbol, fiscal_year, eps, fetched_at
            FROM stock_eps_history
            WHERE yahoo_symbol IN ({placeholders})
            ORDER BY yahoo_symbol, fiscal_year
            """,
            list(targets),
        ).fetchall()
        eps_refresh_rows = conn.execute(
            f"""
            SELECT *
            FROM stock_eps_refreshes
            WHERE yahoo_symbol IN ({placeholders})
            """,
            list(targets),
        ).fetchall()
        latest_price_rows = conn.execute(
            """
            SELECT symbol, market, price, price_currency, quote_time, fetched_at
            FROM latest_prices
            """
        ).fetchall()

    fundamentals_by_symbol = {row["yahoo_symbol"]: row for row in fundamentals_rows}
    latest_price_by_target = {
        (str(row["symbol"] or "").strip().upper(), str(row["market"] or "").strip().upper()): row
        for row in latest_price_rows
    }
    prices_by_symbol = {}
    for row in price_rows:
        prices_by_symbol.setdefault(row["yahoo_symbol"], []).append(row)
    dividends_by_symbol = {}
    for row in dividend_rows:
        dividends_by_symbol.setdefault(row["yahoo_symbol"], []).append(row)
    eps_by_symbol = {}
    for row in eps_rows:
        eps_by_symbol.setdefault(row["yahoo_symbol"], {})[int(row["fiscal_year"])] = row["eps"]
    eps_refresh_by_symbol = {row["yahoo_symbol"]: row for row in eps_refresh_rows}

    eps_years = selected_eps_years(EPS_HISTORY_YEARS)
    rows = []
    for yahoo_symbol, target in targets.items():
        fundamentals = fundamentals_by_symbol.get(yahoo_symbol)
        prices = prices_by_symbol.get(yahoo_symbol, [])
        dividends = dividends_by_symbol.get(yahoo_symbol, [])
        eps_history = eps_by_symbol.get(yahoo_symbol, {})
        eps_refresh = eps_refresh_by_symbol.get(yahoo_symbol)
        latest_price = latest_price_by_target.get((target["symbol"], target["market"]))

        current_price = finite_float(latest_price["price"] if latest_price else None)
        current_price_date = None
        currency = target["currency"]
        if latest_price:
            current_price_date = latest_price.get("quote_time") or latest_price.get("fetched_at")
            currency = latest_price.get("price_currency") or currency
        if current_price is None:
            current_price = target["current_price"]
            current_price_date = target["current_price_date"]

        latest_price_point = prices[-1] if prices else None
        if current_price is None and latest_price_point:
            current_price = latest_price_point["close"]
            current_price_date = latest_price_point["date"]
        latest_price_date = parse_iso_day(latest_price_point["date"]) if latest_price_point else None
        ttm = trailing_dividend(dividends, latest_price_date) if latest_price_date else 0.0
        dividend_yield_pct = (ttm / current_price * 100.0) if ttm and current_price else None
        monthly_yields = monthly_yield_series(prices, dividends)
        five_year_dividend_yield_pct = (
            sum(item["yield_pct"] for item in monthly_yields) / len(monthly_yields) if monthly_yields else None
        )
        fallback_high, fallback_low = recent_price_range(prices)
        fifty_two_week_high = (
            fundamentals["fifty_two_week_high"]
            if fundamentals and fundamentals.get("fifty_two_week_high") is not None
            else fallback_high
        )
        fifty_two_week_low = (
            fundamentals["fifty_two_week_low"]
            if fundamentals and fundamentals.get("fifty_two_week_low") is not None
            else fallback_low
        )
        price_position_52w_pct = (
            (current_price - fifty_two_week_low) / (fifty_two_week_high - fifty_two_week_low) * 100.0
            if current_price is not None
            and fifty_two_week_high is not None
            and fifty_two_week_low is not None
            and fifty_two_week_high > fifty_two_week_low
            else None
        )
        eps_values = [eps_history[year] for year in eps_years if eps_history.get(year) is not None]
        eps_recent_year = max((year for year in eps_years if eps_history.get(year) is not None), default=None)
        eps_recent = eps_history.get(eps_recent_year) if eps_recent_year is not None else None
        eps_avg_10y = sum(eps_values) / len(eps_values) if eps_values else None
        book_value_per_share = fundamentals["book_value_per_share"] if fundamentals else None
        graham_price = (
            math.sqrt(22.5 * eps_avg_10y * book_value_per_share)
            if eps_avg_10y is not None
            and book_value_per_share is not None
            and eps_avg_10y > 0
            and book_value_per_share > 0
            else None
        )
        graham_delta_pct = (
            (current_price - graham_price) / graham_price * 100.0
            if current_price is not None and graham_price is not None and graham_price > 0
            else None
        )

        rows.append(
            {
                "symbol": target["symbol"],
                "market": target["market"],
                "yahoo_symbol": yahoo_symbol,
                "description": target["description"],
                "currency": (currency or (fundamentals["currency"] if fundamentals else target["currency"]) or "CAD").upper(),
                "quantity": target["quantity"],
                "current_value": target["current_value"],
                "owned": bool(target.get("owned")),
                "watchlist": bool(target.get("watchlist")),
                "position_status": "Owned" if target.get("owned") else "Watchlist",
                "current_price": current_price,
                "current_price_date": current_price_date,
                "fifty_two_week_high": fifty_two_week_high,
                "fifty_two_week_low": fifty_two_week_low,
                "price_position_52w_pct": price_position_52w_pct,
                "eps_current": fundamentals["eps_current"] if fundamentals else None,
                "eps_recent": eps_recent,
                "eps_recent_year": eps_recent_year,
                "eps_avg_10y": eps_avg_10y,
                "eps_history": {str(year): eps_history.get(year) for year in eps_years},
                "pe_ratio": fundamentals["pe_ratio"] if fundamentals else None,
                "book_value_per_share": book_value_per_share,
                "graham_price": graham_price,
                "graham_delta_pct": graham_delta_pct,
                "dividend_yield_pct": dividend_yield_pct,
                "five_year_dividend_yield_pct": five_year_dividend_yield_pct,
                "fundamentals_fetched_at": fundamentals["fetched_at"] if fundamentals else None,
                "eps_fetched_at": eps_refresh["fetched_at"] if eps_refresh else None,
                "eps_status": eps_refresh["status"] if eps_refresh else "empty",
                "eps_error": eps_refresh["error"] if eps_refresh else None,
                "analytics_fetched_at": latest_price_point["fetched_at"] if latest_price_point else None,
                "status": fundamentals["status"] if fundamentals else "empty",
                "error": fundamentals["error"] if fundamentals else None,
            }
        )
        for year in eps_years:
            rows[-1][f"eps_{year}"] = eps_history.get(year)

    rows.sort(key=lambda row: row["symbol"])
    return {"rows": rows, "eps_years": eps_years, "fetched_at": utc_now()}


def latest_batch_filter_sql():
    return """
        SELECT MAX(import_batches.id)
        FROM import_batches
        GROUP BY account_id
    """


def get_summary():
    init_db()
    all_accounts = get_accounts()
    with get_connection() as conn:
        accounts = conn.execute(
            f"""
            WITH latest AS (
                SELECT account_id, MAX(id) AS batch_id
                FROM import_batches
                GROUP BY account_id
            )
            SELECT
                accounts.id,
                accounts.name,
                accounts.owner,
                accounts.account_entity,
                accounts.account_type,
                accounts.base_currency,
                accounts.notes,
                import_batches.id AS batch_id,
                import_batches.imported_at,
                import_batches.report_timestamp,
                import_batches.as_of_note,
                import_batches.row_count,
                import_batches.total_closing_value,
                import_batches.total_book_value,
                import_batches.total_gain_loss,
                import_batches.total_gain_loss_pct
            FROM accounts
            LEFT JOIN latest ON latest.account_id = accounts.id
            LEFT JOIN import_batches ON import_batches.id = latest.batch_id
            ORDER BY accounts.name
            """
        ).fetchall()

        holdings = conn.execute(
            f"""
            SELECT
                holdings.*,
                accounts.name AS account_name,
                accounts.account_entity AS account_entity,
                import_batches.report_timestamp,
                import_batches.imported_at
            FROM holdings
            JOIN import_batches ON import_batches.id = holdings.batch_id
            JOIN accounts ON accounts.id = import_batches.account_id
            WHERE holdings.batch_id IN ({latest_batch_filter_sql()})
            ORDER BY holdings.closing_value DESC, holdings.symbol
            """
        ).fetchall()

        currency_rows = conn.execute(
            f"""
            SELECT
                accounts.name AS account_name,
                accounts.account_entity AS account_entity,
                import_batches.id AS batch_id,
                currency_summaries.currency,
                currency_summaries.closing_value,
                currency_summaries.book_value,
                currency_summaries.gain_loss,
                currency_summaries.gain_loss_pct
            FROM currency_summaries
            JOIN import_batches ON import_batches.id = currency_summaries.batch_id
            JOIN accounts ON accounts.id = import_batches.account_id
            WHERE currency_summaries.batch_id IN ({latest_batch_filter_sql()})
            ORDER BY accounts.name, currency_summaries.currency
            """
        ).fetchall()

        cash_rows = conn.execute(
            f"""
            SELECT
                accounts.name AS account_name,
                accounts.account_entity AS account_entity,
                cash_balances.batch_id,
                cash_balances.currency,
                cash_balances.amount,
                import_batches.report_timestamp,
                import_batches.imported_at
            FROM cash_balances
            JOIN import_batches ON import_batches.id = cash_balances.batch_id
            JOIN accounts ON accounts.id = import_batches.account_id
            WHERE cash_balances.batch_id IN ({latest_batch_filter_sql()})
            ORDER BY accounts.name, cash_balances.currency
            """
        ).fetchall()

        imports = conn.execute(
            """
            SELECT
                import_batches.id,
                accounts.name AS account_name,
                accounts.account_entity AS account_entity,
                import_batches.source_filename,
                import_batches.stored_path,
                import_batches.imported_at,
                import_batches.report_timestamp,
                import_batches.row_count,
                import_batches.total_closing_value,
                import_batches.total_book_value,
                import_batches.total_gain_loss
            FROM import_batches
            JOIN accounts ON accounts.id = import_batches.account_id
            ORDER BY import_batches.imported_at DESC, import_batches.id DESC
            LIMIT 25
            """
        ).fetchall()

        price_rows = conn.execute(
            """
            SELECT *
            FROM latest_prices
            """
        ).fetchall()
        usd_cad_rate = latest_usd_cad_rate_from_conn(conn)
        manual_holdings = manual_holding_summary_rows(conn, usd_cad_rate)

    total_closing = sum(account["total_closing_value"] or 0.0 for account in accounts)
    total_book = sum(account["total_book_value"] or 0.0 for account in accounts)
    total_gain = sum(account["total_gain_loss"] or 0.0 for account in accounts)
    total_gain_pct = (total_gain / total_book * 100.0) if total_book else None

    cash_by_batch = {}
    cash_details_by_batch = {}
    cash_currencies_by_batch = {}
    for cash in cash_rows:
        amount = cash["amount"] or 0.0
        fx_to_cad = fx_to_cad_for_currency(cash["currency"], usd_cad_rate)
        value_cad = amount * fx_to_cad
        cash_by_batch[cash["batch_id"]] = cash_by_batch.get(cash["batch_id"], 0.0) + value_cad
        cash_details_by_batch.setdefault(cash["batch_id"], []).append(
            {
                "currency": cash["currency"],
                "amount": amount,
                "fx_to_cad": fx_to_cad,
                "value_cad": value_cad,
            }
        )
        cash_currencies_by_batch.setdefault(cash["batch_id"], set()).add(cash["currency"])

    for account in accounts:
        account["cash_balance"] = cash_by_batch.get(account["batch_id"], 0.0)
        account["cash_balances"] = cash_details_by_batch.get(account["batch_id"], [])
        currencies = cash_currencies_by_batch.get(account["batch_id"], set())
        account["cash_currency"] = next(iter(currencies)) if len(currencies) == 1 else ("MIXED" if currencies else account.get("base_currency") or "CAD")

    cash_holdings = []
    for cash in cash_rows:
        amount = cash["amount"] or 0.0
        fx_to_cad = fx_to_cad_for_currency(cash["currency"], usd_cad_rate)
        amount_cad = amount * fx_to_cad
        cash_holdings.append(
            {
                "id": f"cash-{cash['batch_id']}-{cash['currency']}",
                "batch_id": cash["batch_id"],
                "asset_type": "Cash",
                "currency": cash["currency"],
                "symbol": "CASH",
                "market": "",
                "description": f"{cash['currency']} Cash",
                "quantity": amount,
                "average_cost": fx_to_cad,
                "closing_price": fx_to_cad,
                "closing_value": amount_cad,
                "book_value": amount_cad,
                "gain_loss": 0.0,
                "gain_loss_pct": 0.0,
                "portfolio_pct": None,
                "account_name": cash["account_name"],
                "account_entity": cash.get("account_entity") or "Personal",
                "report_timestamp": cash["report_timestamp"],
                "imported_at": cash["imported_at"],
                "source_amount": amount,
                "source_currency": cash["currency"],
                "fx_to_cad": fx_to_cad,
            }
        )

    holdings = holdings + manual_holdings
    security_count = len(holdings)
    holdings = holdings + cash_holdings
    ensure_stock_analytics_cache_for_holdings(holdings)
    forward_dividends_by_symbol = stock_forward_dividend_map_for_holdings(holdings)
    price_by_symbol = {
        (row["symbol"], row["market"]): row
        for row in price_rows
    }
    for holding in holdings:
        if holding["asset_type"] == "Cash":
            fx_to_cad = holding.get("fx_to_cad") or fx_to_cad_for_currency(holding["currency"], usd_cad_rate)
            native_amount = holding.get("source_amount", holding.get("quantity") or 0.0)
            current_value = native_amount * fx_to_cad
            holding["average_cost"] = fx_to_cad
            holding["closing_price"] = fx_to_cad
            holding["closing_value"] = current_value
            holding["book_value"] = current_value
            holding["current_price"] = fx_to_cad
            holding["current_price_source"] = "cash"
            holding["current_price_currency"] = "CAD"
            holding["current_price_fetched_at"] = holding["imported_at"]
            holding["price_quote_time"] = holding["imported_at"]
            holding["day_change"] = 0.0
            holding["day_change_pct"] = 0.0
            holding["day_value_change"] = 0.0
            holding["previous_value"] = current_value
            holding["annual_forward_dividend"] = 0.0
            holding["annual_forward_income"] = 0.0
            holding["five_year_dividend_growth_pct"] = None
            holding["payments_per_year"] = 0
            holding["latest_dividend"] = None
            holding["latest_ex_date"] = None
            holding["current_value"] = current_value
            holding["current_gain_loss"] = 0.0
            holding["current_gain_loss_pct"] = 0.0
            holding["fx_to_cad"] = fx_to_cad
            continue

        price_row = price_by_symbol.get((holding["symbol"], holding["market"]))
        price_cad = price_row["price_cad"] if price_row else None
        has_live_price = price_cad is not None
        holding_fx_to_cad = price_row["fx_to_cad"] if price_row else fx_to_cad_for_currency(holding.get("currency"), usd_cad_rate)
        source_closing_price = holding["closing_price"] or 0.0
        source_book_value = holding["book_value"] or 0.0
        source_closing_value = holding["closing_value"] or 0.0
        current_price = price_cad if has_live_price else source_closing_price * holding_fx_to_cad
        quantity = holding["quantity"] or 0.0
        book_value_cad = source_book_value * holding_fx_to_cad
        closing_value_cad = source_closing_value * holding_fx_to_cad
        current_value = (quantity * current_price) if current_price is not None else closing_value_cad
        current_gain = current_value - book_value_cad
        current_gain_pct = (current_gain / book_value_cad * 100.0) if book_value_cad else None
        previous_close_cad = price_row["previous_close_cad"] if price_row else None
        previous_value = (
            previous_close_cad * quantity
            if previous_close_cad is not None
            else closing_value_cad
        )
        yahoo_symbol = price_row["yahoo_symbol"] if price_row else yahoo_symbol_for(holding["symbol"], holding["market"])
        forward_stats = forward_dividends_by_symbol.get(yahoo_symbol, {})
        annual_forward_dividend = (forward_stats.get("annual_forward_dividend") or 0.0) * holding_fx_to_cad

        holding["source_average_cost"] = holding.get("source_average_cost", holding.get("average_cost"))
        holding["source_closing_price"] = source_closing_price
        holding["source_closing_value"] = source_closing_value
        holding["source_book_value"] = source_book_value
        holding["average_cost"] = (holding["average_cost"] or 0.0) * holding_fx_to_cad if holding["average_cost"] is not None else None
        holding["closing_price"] = source_closing_price * holding_fx_to_cad if source_closing_price is not None else None
        holding["closing_value"] = closing_value_cad
        holding["book_value"] = book_value_cad
        holding["gain_loss"] = closing_value_cad - book_value_cad
        holding["gain_loss_pct"] = (holding["gain_loss"] / book_value_cad * 100.0) if book_value_cad else None
        holding["current_price"] = current_price
        holding["current_price_source"] = "yfinance" if has_live_price else "import"
        holding["current_price_currency"] = "CAD"
        holding["source_price"] = price_row["price"] if price_row else None
        holding["source_price_currency"] = price_row["price_currency"] if price_row else None
        holding["fx_to_cad"] = holding_fx_to_cad
        holding["yahoo_symbol"] = yahoo_symbol
        holding["current_price_fetched_at"] = price_row["fetched_at"] if price_row else None
        holding["price_quote_time"] = price_row["quote_time"] if price_row else None
        holding["previous_close"] = previous_close_cad
        holding["previous_value"] = previous_value
        holding["day_change"] = price_row["day_change"] if price_row else None
        holding["day_change_pct"] = price_row["day_change_pct"] if price_row else None
        holding["day_value_change"] = (holding["day_change"] or 0.0) * quantity if holding["day_change"] is not None else None
        holding["latest_dividend"] = forward_stats.get("latest_dividend")
        holding["latest_ex_date"] = forward_stats.get("latest_ex_date")
        holding["payments_per_year"] = forward_stats.get("payments_per_year") or 0
        holding["annual_forward_dividend"] = annual_forward_dividend
        holding["annual_forward_income"] = annual_forward_dividend * quantity
        holding["five_year_dividend_growth_pct"] = forward_stats.get("five_year_dividend_growth_pct")
        holding["current_price_error"] = price_row["error"] if price_row else None
        holding["current_value"] = current_value
        holding["current_gain_loss"] = current_gain
        holding["current_gain_loss_pct"] = current_gain_pct

    total_closing = sum(holding.get("closing_value") or 0.0 for holding in holdings)
    total_book = sum(holding.get("book_value") or 0.0 for holding in holdings)
    total_gain = total_closing - total_book
    total_gain_pct = (total_gain / total_book * 100.0) if total_book else None
    current_total_closing = sum(holding.get("current_value") or 0.0 for holding in holdings)
    current_total_gain = current_total_closing - total_book
    current_total_gain_pct = (current_total_gain / total_book * 100.0) if total_book else None
    total_day_change = sum(holding.get("day_value_change") or 0.0 for holding in holdings)
    previous_total_value = current_total_closing - total_day_change
    total_day_change_pct = (total_day_change / previous_total_value * 100.0) if previous_total_value else None

    current_values_by_account = {}
    book_values_by_account = {}
    closing_values_by_account = {}
    day_change_by_account = {}
    for holding in holdings:
        current_values_by_account[holding["account_name"]] = current_values_by_account.get(holding["account_name"], 0.0) + (
            holding.get("current_value") or 0.0
        )
        book_values_by_account[holding["account_name"]] = book_values_by_account.get(holding["account_name"], 0.0) + (
            holding.get("book_value") or 0.0
        )
        closing_values_by_account[holding["account_name"]] = closing_values_by_account.get(holding["account_name"], 0.0) + (
            holding.get("closing_value") or 0.0
        )
        day_change_by_account[holding["account_name"]] = day_change_by_account.get(holding["account_name"], 0.0) + (
            holding.get("day_value_change") or 0.0
        )

    for account in accounts:
        account_book_value = book_values_by_account.get(account["name"], account["total_book_value"] or 0.0)
        account["total_book_value"] = account_book_value
        account["total_closing_value"] = closing_values_by_account.get(account["name"], account["total_closing_value"] or 0.0)
        account["current_total_value"] = current_values_by_account.get(account["name"], account["total_closing_value"] or 0.0)
        account["current_total_gain_loss"] = account["current_total_value"] - account_book_value
        account["current_total_gain_loss_pct"] = (
            account["current_total_gain_loss"] / account_book_value * 100.0
            if account_book_value
            else None
        )
        account["day_change"] = day_change_by_account.get(account["name"], 0.0)
        account_previous_value = account["current_total_value"] - account["day_change"]
        account["day_change_pct"] = (
            account["day_change"] / account_previous_value * 100.0
            if account_previous_value
            else None
        )

    for holding in holdings:
        holding["combined_portfolio_pct"] = (
            (holding.get("current_value") or 0.0) / current_total_closing * 100.0 if current_total_closing else 0.0
        )
    holdings.sort(key=lambda holding: (holding.get("current_value") or 0.0), reverse=True)

    merged_currency_rows = {}
    for row in currency_rows:
        key = (row["account_name"], row["currency"])
        merged_currency_rows[key] = {
            **row,
            "securities_value": row["closing_value"] or 0.0,
            "cash_value": 0.0,
        }

    for cash in cash_rows:
        key = (cash["account_name"], cash["currency"])
        row = merged_currency_rows.setdefault(
            key,
            {
                "account_name": cash["account_name"],
                "account_entity": cash.get("account_entity") or "Personal",
                "batch_id": cash["batch_id"],
                "currency": cash["currency"],
                "closing_value": 0.0,
                "book_value": 0.0,
                "gain_loss": 0.0,
                "gain_loss_pct": 0.0,
                "securities_value": 0.0,
                "cash_value": 0.0,
            },
        )
        amount = cash["amount"] or 0.0
        row["closing_value"] = (row["closing_value"] or 0.0) + amount
        row["book_value"] = (row["book_value"] or 0.0) + amount
        row["cash_value"] = (row["cash_value"] or 0.0) + amount
        row["gain_loss_pct"] = (
            ((row["gain_loss"] or 0.0) / row["book_value"] * 100.0)
            if row["book_value"]
            else None
        )
    currency_rows = sorted(merged_currency_rows.values(), key=lambda row: (row["account_name"], row["currency"]))

    allocation = [
        {
            "symbol": holding["symbol"],
            "description": holding["description"],
            "closing_value": holding.get("current_value") or holding["closing_value"],
            "currency": holding["currency"],
            "account_name": holding["account_name"],
            "account_entity": holding.get("account_entity") or "Personal",
            "portfolio_pct": holding["combined_portfolio_pct"],
        }
        for holding in holdings
    ]

    return {
        "totals": {
            "closing_value": total_closing,
            "current_value": current_total_closing,
            "book_value": total_book,
            "gain_loss": total_gain,
            "current_gain_loss": current_total_gain,
            "day_change": total_day_change,
            "gain_loss_pct": total_gain_pct,
            "current_gain_loss_pct": current_total_gain_pct,
            "day_change_pct": total_day_change_pct,
            "account_count": len(accounts),
            "setup_account_count": len(all_accounts),
            "holding_count": len(holdings),
            "security_count": security_count,
            "cash_count": len(cash_rows),
        },
        "accounts": accounts,
        "all_accounts": all_accounts,
        "currency_summaries": currency_rows,
        "cash_balances": cash_rows,
        "holdings": holdings,
        "allocation": allocation,
        "imports": imports,
        "price_refresh": latest_price_status(),
        "balance_snapshot": {
            "latest": latest_balance_snapshot_status(),
        },
    }


def parse_multipart(content_type: str, body: bytes):
    if not content_type.lower().startswith("multipart/form-data"):
        raise ValueError("Expected multipart/form-data upload.")

    raw = (
        f"Content-Type: {content_type}\r\n"
        "MIME-Version: 1.0\r\n"
        "\r\n"
    ).encode("utf-8") + body
    message = BytesParser(policy=policy.default).parsebytes(raw)

    fields = {}
    files = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename:
            files[name] = {"filename": filename, "content": payload}
        else:
            fields[name] = payload.decode(part.get_content_charset() or "utf-8").strip()

    return fields, files


class PortfolioHandler(BaseHTTPRequestHandler):
    server_version = "LocalPortfolioTracker/0.1"

    def log_message(self, format, *args):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"{timestamp} {self.address_string()} {format % args}")

    def send_json(self, data, status=HTTPStatus.OK):
        payload = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_error_json(self, message, status=HTTPStatus.BAD_REQUEST):
        self.send_json({"error": message}, status)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/health":
            self.send_json({"ok": True})
            return

        if path == "/api/summary":
            self.send_json(get_summary())
            return

        if path == "/api/fundamentals":
            params = parse_qs(parsed.query)
            refresh = params.get("refresh", ["false"])[0].lower() in {"1", "true", "yes", "on"}
            self.send_json(get_fundamentals(refresh))
            return

        if path == "/api/accounts":
            self.send_json({"accounts": get_accounts()})
            return

        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return

        if path == "/":
            self.serve_static("index.html")
            return

        if path.startswith("/static/"):
            self.serve_static(path.removeprefix("/static/"))
            return

        self.send_error_json("Not found.", HTTPStatus.NOT_FOUND)

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/accounts":
            length = int(self.headers.get("Content-Length", "0"))
            try:
                body = self.rfile.read(length).decode("utf-8") if length else "{}"
                payload = json.loads(body)
                result = save_account(
                    payload.get("name", ""),
                    payload.get("owner", ""),
                    payload.get("account_type", ""),
                    payload.get("base_currency", ""),
                    payload.get("notes", ""),
                    payload.get("account_entity", ""),
                )
                self.send_json(result, HTTPStatus.CREATED if result["created"] else HTTPStatus.OK)
            except Exception as exc:
                self.send_error_json(str(exc))
            return

        if parsed.path == "/api/import":
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                self.send_error_json("No upload body was provided.")
                return
            if length > MAX_UPLOAD_BYTES:
                self.send_error_json("Upload is too large.", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                return

            try:
                body = self.rfile.read(length)
                fields, files = parse_multipart(self.headers.get("Content-Type", ""), body)
                uploaded = files.get("file")
                if not uploaded or not uploaded["content"]:
                    raise ValueError("No CSV file was uploaded.")
                result = import_content(
                    uploaded["content"],
                    uploaded["filename"],
                    fields.get("account_name", ""),
                    fields.get("cash_balance", ""),
                    fields.get("cash_currency", "CAD"),
                )
                self.send_json(result, HTTPStatus.CREATED if result.get("imported") else HTTPStatus.OK)
            except Exception as exc:
                self.send_error_json(str(exc))
            return

        if parsed.path == "/api/import-path":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else ""
            params = parse_qs(body)
            local_path = params.get("path", [""])[0]
            account_name = params.get("account_name", [""])[0]
            cash_balance = params.get("cash_balance", [""])[0]
            cash_currency = params.get("cash_currency", ["CAD"])[0]
            try:
                path = Path(local_path).expanduser()
                if not path.exists():
                    raise ValueError("The requested file path does not exist.")
                result = import_path(path, account_name, cash_balance, cash_currency)
                self.send_json(result, HTTPStatus.CREATED if result.get("imported") else HTTPStatus.OK)
            except Exception as exc:
                self.send_error_json(str(exc))
            return

        self.send_error_json("Not found.", HTTPStatus.NOT_FOUND)

    def serve_static(self, relative_path):
        safe_path = Path(relative_path)
        if safe_path.is_absolute() or ".." in safe_path.parts:
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        full_path = STATIC_DIR / safe_path
        if not full_path.exists() or not full_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        content_types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
        }
        payload = full_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_types.get(full_path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def run_server(port):
    init_db()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), PortfolioHandler)
    print(f"Portfolio tracker running at http://127.0.0.1:{port}")
    httpd.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="Local portfolio tracker")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--import", dest="import_file", type=Path)
    parser.add_argument("--account-name", default="")
    parser.add_argument("--cash-balance", default="")
    parser.add_argument("--cash-currency", default="CAD")
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()

    if args.import_file:
        result = import_path(args.import_file, args.account_name, args.cash_balance, args.cash_currency)
        print(json.dumps(result, indent=2))
        if not args.serve:
            return

    run_server(args.port)


if __name__ == "__main__":
    main()
