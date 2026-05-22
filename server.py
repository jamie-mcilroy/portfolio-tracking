#!/usr/bin/env python3
import argparse
import csv
import hashlib
import io
import json
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
IMPORT_DIR = DATA_DIR / "imports"
DB_PATH = DATA_DIR / "portfolio.db"
STATIC_DIR = BASE_DIR / "static"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


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


def parse_holdings_csv(path: Path):
    rows = read_csv_rows(path)
    metadata = detect_account_metadata(rows)

    summary_index = find_row_index(rows, "Securities held in")
    holdings_index = find_row_index(rows, "Asset type")

    if holdings_index is None:
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
                account_type TEXT NOT NULL DEFAULT 'Investment',
                base_currency TEXT NOT NULL DEFAULT 'CAD',
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
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
            "account_type": "TEXT NOT NULL DEFAULT 'Investment'",
            "base_currency": "TEXT NOT NULL DEFAULT 'CAD'",
            "notes": "TEXT",
        }
        for column, definition in account_column_defs.items():
            if column not in account_columns:
                conn.execute(f"ALTER TABLE accounts ADD COLUMN {column} {definition}")

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


def dict_from_row(cursor, row):
    return {column[0]: row[index] for index, column in enumerate(cursor.description)}


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = dict_from_row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


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


def save_account(name, owner="", account_type="Investment", base_currency="CAD", notes=""):
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
                name, owner, account_type, base_currency, notes, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                owner = excluded.owner,
                account_type = excluded.account_type,
                base_currency = excluded.base_currency,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (
                account_name,
                (owner or "").strip(),
                normalize_account_type(account_type),
                normalize_currency(base_currency),
                (notes or "").strip(),
                now,
                now,
            ),
        )
        account = conn.execute(
            """
            SELECT id, name, owner, account_type, base_currency, notes, created_at, updated_at
            FROM accounts
            WHERE name = ?
            """,
            (account_name,),
        ).fetchone()

    return {"created": existing is None, "account": account}


def update_account(account_id, name, owner="", account_type="Investment", base_currency="CAD", notes=""):
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
                account_type = ?,
                base_currency = ?,
                notes = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                account_name,
                (owner or "").strip(),
                normalize_account_type(account_type),
                normalize_currency(base_currency),
                (notes or "").strip(),
                now,
                account_id,
            ),
        )
        account = conn.execute(
            """
            SELECT id, name, owner, account_type, base_currency, notes, created_at, updated_at
            FROM accounts
            WHERE id = ?
            """,
            (account_id,),
        ).fetchone()

    return {"updated": True, "account": account}


def update_latest_cash_balance(account_id, cash_balance=None, cash_currency: str = "CAD"):
    init_db()
    amount = normalize_cash_balance(cash_balance)
    currency = normalize_currency(cash_currency)
    now = utc_now()

    with get_connection() as conn:
        batch = conn.execute(
            """
            SELECT id, metadata_json
            FROM import_batches
            WHERE account_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (account_id,),
        ).fetchone()
        if not batch:
            raise ValueError("Import holdings before setting a cash balance.")

        batch_id = batch["id"]
        summary = conn.execute(
            """
            SELECT
                COALESCE(SUM(closing_value), 0) AS securities_closing,
                COALESCE(SUM(book_value), 0) AS securities_book,
                COALESCE(SUM(gain_loss), 0) AS securities_gain
            FROM currency_summaries
            WHERE batch_id = ?
            """,
            (batch_id,),
        ).fetchone()

        total_closing = (summary["securities_closing"] or 0.0) + amount
        total_book = (summary["securities_book"] or 0.0) + amount
        total_gain = summary["securities_gain"] or 0.0
        total_gain_pct = (total_gain / total_book * 100.0) if total_book else None

        try:
            metadata = json.loads(batch["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        metadata["cash_balance"] = amount
        metadata["cash_currency"] = currency if amount else ""

        conn.execute("DELETE FROM cash_balances WHERE batch_id = ?", (batch_id,))
        if amount:
            conn.execute(
                """
                INSERT INTO cash_balances (batch_id, currency, amount)
                VALUES (?, ?, ?)
                """,
                (batch_id, currency, amount),
            )

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
                total_gain,
                total_gain_pct,
                json.dumps(metadata, sort_keys=True),
                batch_id,
            ),
        )
        conn.execute("UPDATE accounts SET updated_at = ? WHERE id = ?", (now, account_id))

    return {"updated": True, "batch_id": batch_id, "cash_balance": amount, "cash_currency": currency if amount else ""}


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
                SUM(cash_balances.amount) AS cash_balance,
                CASE
                    WHEN COUNT(DISTINCT cash_balances.currency) = 1 THEN MIN(cash_balances.currency)
                    ELSE 'MIXED'
                END AS cash_currency
            FROM cash_balances
            JOIN latest ON latest.batch_id = cash_balances.batch_id
            GROUP BY cash_balances.batch_id
            """
        ).fetchall()

    cash_by_batch = {
        row["batch_id"]: {
            "cash_balance": row["cash_balance"] or 0.0,
            "cash_currency": row["cash_currency"] or "",
        }
        for row in cash_rows
    }
    for account in accounts:
        cash = cash_by_batch.get(account["batch_id"], {})
        account["cash_balance"] = cash.get("cash_balance", 0.0)
        account["cash_currency"] = cash.get("cash_currency", account.get("base_currency") or "CAD")
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
    cash_amount = normalize_cash_balance(cash_balance)
    cash_currency = (cash_currency or "CAD").strip().upper()

    incoming_dir = IMPORT_DIR / "_incoming"
    incoming_dir.mkdir(parents=True, exist_ok=True)
    temp_path = incoming_dir / f"{sha256}.csv"
    temp_path.write_bytes(content)

    try:
        parsed = parse_holdings_csv(temp_path)
        metadata = parsed["metadata"]
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
            total_closing = securities_closing + cash_amount
            total_book = securities_book + cash_amount
            total_gain_pct = (total_gain / total_book * 100.0) if total_book else None
            stored_metadata = {
                **metadata,
                "cash_balance": cash_amount,
                "cash_currency": cash_currency if cash_amount else "",
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

            if cash_amount:
                conn.execute(
                    """
                    INSERT INTO cash_balances (batch_id, currency, amount)
                    VALUES (?, ?, ?)
                    """,
                    (batch_id, cash_currency, cash_amount),
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
                "cash_balance": cash_amount,
                "cash_currency": cash_currency if cash_amount else "",
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
                        market_date, account_id, account_name, account_type, value, imported_value,
                        book_value, cash_balance, gain_loss, gain_loss_pct, day_change, day_change_pct,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["market_date"],
                        account["id"],
                        account["name"],
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
    if cleaned_market in {"CDN", "CAN", "CA", "TSX", "TSXV"}:
        return f"{cleaned_symbol}.TO"
    return cleaned_symbol


def active_price_targets():
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT DISTINCT
                holdings.symbol,
                holdings.market,
                holdings.currency
            FROM holdings
            WHERE holdings.batch_id IN ({latest_batch_filter_sql()})
                AND COALESCE(holdings.symbol, '') != ''
                AND UPPER(COALESCE(holdings.symbol, '')) != 'CASH'
            ORDER BY holdings.market, holdings.symbol
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
                    market_date, account_id, account_name, account_type, value, imported_value,
                    book_value, cash_balance, gain_loss, gain_loss_pct, day_change, day_change_pct,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(market_date, account_id) DO UPDATE SET
                    account_name = excluded.account_name,
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
    init_db()
    started_at = utc_now()
    targets = active_price_targets()

    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO price_refreshes (started_at, status, symbol_count)
            VALUES (?, ?, ?)
            """,
            (started_at, "running", len(targets)),
        )
        refresh_id = cursor.lastrowid

    if not targets:
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
        needs_usd_cad = any(str(target["market"] or "").upper() == "US" for target in targets)
        download_symbols = yahoo_symbols + (["CAD=X"] if needs_usd_cad else [])
        downloaded = yf.download(
            tickers=download_symbols,
            period="1d",
            interval="1m",
            group_by="ticker",
            auto_adjust=False,
            progress=False,
            threads=True,
        )
        daily_downloaded = yf.download(
            tickers=download_symbols,
            period="5d",
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
            progress=False,
            threads=True,
        )
        fetched_at = utc_now()

        usd_cad_rate = 1.0
        if needs_usd_cad:
            fx_price, _ = last_close(downloaded, "CAD=X")
            if fx_price:
                usd_cad_rate = fx_price
        previous_usd_cad_rate = usd_cad_rate
        if needs_usd_cad:
            previous_fx_price = previous_daily_close(daily_downloaded, "CAD=X")
            if previous_fx_price:
                previous_usd_cad_rate = previous_fx_price

        with get_connection() as conn:
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
            SELECT
                accounts.id,
                accounts.name,
                accounts.owner,
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
            JOIN import_batches ON import_batches.account_id = accounts.id
            WHERE import_batches.id IN ({latest_batch_filter_sql()})
            ORDER BY accounts.name
            """
        ).fetchall()

        holdings = conn.execute(
            f"""
            SELECT
                holdings.*,
                accounts.name AS account_name,
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

    total_closing = sum(account["total_closing_value"] or 0.0 for account in accounts)
    total_book = sum(account["total_book_value"] or 0.0 for account in accounts)
    total_gain = sum(account["total_gain_loss"] or 0.0 for account in accounts)
    total_gain_pct = (total_gain / total_book * 100.0) if total_book else None

    cash_by_batch = {}
    cash_currencies_by_batch = {}
    for cash in cash_rows:
        cash_by_batch[cash["batch_id"]] = cash_by_batch.get(cash["batch_id"], 0.0) + (
            cash["amount"] or 0.0
        )
        cash_currencies_by_batch.setdefault(cash["batch_id"], set()).add(cash["currency"])

    for account in accounts:
        account["cash_balance"] = cash_by_batch.get(account["batch_id"], 0.0)
        currencies = cash_currencies_by_batch.get(account["batch_id"], set())
        account["cash_currency"] = next(iter(currencies)) if len(currencies) == 1 else (account.get("base_currency") or "CAD")

    cash_holdings = []
    for cash in cash_rows:
        amount = cash["amount"] or 0.0
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
                "average_cost": 1.0,
                "closing_price": 1.0,
                "closing_value": amount,
                "book_value": amount,
                "gain_loss": 0.0,
                "gain_loss_pct": 0.0,
                "portfolio_pct": None,
                "account_name": cash["account_name"],
                "report_timestamp": cash["report_timestamp"],
                "imported_at": cash["imported_at"],
            }
        )

    security_count = len(holdings)
    holdings = holdings + cash_holdings
    price_by_symbol = {
        (row["symbol"], row["market"]): row
        for row in price_rows
    }
    for holding in holdings:
        if holding["asset_type"] == "Cash":
            holding["current_price"] = 1.0
            holding["current_price_source"] = "cash"
            holding["current_price_currency"] = holding["currency"]
            holding["current_price_fetched_at"] = holding["imported_at"]
            holding["price_quote_time"] = holding["imported_at"]
            holding["day_change"] = 0.0
            holding["day_change_pct"] = 0.0
            holding["day_value_change"] = 0.0
            holding["current_value"] = holding["closing_value"] or 0.0
            holding["current_gain_loss"] = 0.0
            holding["current_gain_loss_pct"] = 0.0
            continue

        price_row = price_by_symbol.get((holding["symbol"], holding["market"]))
        price_cad = price_row["price_cad"] if price_row else None
        has_live_price = price_cad is not None
        current_price = price_cad if has_live_price else holding["closing_price"]
        quantity = holding["quantity"] or 0.0
        current_value = (quantity * current_price) if current_price is not None else (holding["closing_value"] or 0.0)
        current_gain = current_value - (holding["book_value"] or 0.0)
        current_gain_pct = (current_gain / (holding["book_value"] or 0.0) * 100.0) if holding["book_value"] else None

        holding["current_price"] = current_price
        holding["current_price_source"] = "yfinance" if has_live_price else "import"
        holding["current_price_currency"] = "CAD"
        holding["source_price"] = price_row["price"] if price_row else None
        holding["source_price_currency"] = price_row["price_currency"] if price_row else None
        holding["fx_to_cad"] = price_row["fx_to_cad"] if price_row else 1.0
        holding["yahoo_symbol"] = price_row["yahoo_symbol"] if price_row else yahoo_symbol_for(holding["symbol"], holding["market"])
        holding["current_price_fetched_at"] = price_row["fetched_at"] if price_row else None
        holding["price_quote_time"] = price_row["quote_time"] if price_row else None
        holding["previous_close"] = price_row["previous_close_cad"] if price_row else None
        holding["day_change"] = price_row["day_change"] if price_row else None
        holding["day_change_pct"] = price_row["day_change_pct"] if price_row else None
        holding["day_value_change"] = (holding["day_change"] or 0.0) * quantity if holding["day_change"] is not None else None
        holding["current_price_error"] = price_row["error"] if price_row else None
        holding["current_value"] = current_value
        holding["current_gain_loss"] = current_gain
        holding["current_gain_loss_pct"] = current_gain_pct

    current_total_closing = sum(holding.get("current_value") or 0.0 for holding in holdings)
    current_total_gain = current_total_closing - total_book
    current_total_gain_pct = (current_total_gain / total_book * 100.0) if total_book else None
    total_day_change = sum(holding.get("day_value_change") or 0.0 for holding in holdings)
    previous_total_value = current_total_closing - total_day_change
    total_day_change_pct = (total_day_change / previous_total_value * 100.0) if previous_total_value else None

    current_values_by_account = {}
    day_change_by_account = {}
    for holding in holdings:
        current_values_by_account[holding["account_name"]] = current_values_by_account.get(holding["account_name"], 0.0) + (
            holding.get("current_value") or 0.0
        )
        day_change_by_account[holding["account_name"]] = day_change_by_account.get(holding["account_name"], 0.0) + (
            holding.get("day_value_change") or 0.0
        )

    for account in accounts:
        account["current_total_value"] = current_values_by_account.get(account["name"], account["total_closing_value"] or 0.0)
        account["current_total_gain_loss"] = account["current_total_value"] - (account["total_book_value"] or 0.0)
        account["current_total_gain_loss_pct"] = (
            account["current_total_gain_loss"] / (account["total_book_value"] or 0.0) * 100.0
            if account["total_book_value"]
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
