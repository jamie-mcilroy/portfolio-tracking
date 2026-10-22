"""Persist manual household assets; investment balances stay in the portfolio."""
import math
from contextlib import closing
from datetime import date

import server

CATEGORIES = {'property', 'vehicle', 'art', 'jewellery', 'other', 'debt'}


def list_items():
    server.init_db()
    with closing(server.get_connection()) as conn:
        return conn.execute('SELECT * FROM net_worth_items ORDER BY category, name, id').fetchall()


def save_item(payload, item_id=None):
    name = payload['name'].strip()
    category = payload['category']
    value, mortgage = payload['value'], payload.get('mortgage', 0)
    if not name or len(name) > 200:
        raise ValueError('Enter an asset or debt name of up to 200 characters.')
    if category not in CATEGORIES:
        raise ValueError('Choose a valid category.')
    if not all(math.isfinite(number) and 0 <= number <= 1e15 for number in (value, mortgage)):
        raise ValueError('Values and mortgage balances must be finite, non-negative amounts up to 1 quadrillion CAD.')
    if category != 'property' and mortgage != 0:
        raise ValueError('A mortgage can only be attached to a property.')
    try:
        valuation_date = date.fromisoformat(payload['valuation_date']).isoformat()
    except (ValueError, TypeError):
        raise ValueError('Enter a valid valuation date.') from None
    notes = payload.get('notes', '').strip()
    if len(notes) > 2000:
        raise ValueError('Notes must be at most 2,000 characters.')
    server.init_db()
    now = server.utc_now()
    fields = (name, category, value, mortgage, valuation_date, notes, now)
    with closing(server.get_connection()) as conn, conn:
        if item_id is None:
            cursor = conn.execute('''INSERT INTO net_worth_items
                (name, category, value, mortgage, valuation_date, notes, updated_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', (*fields, now))
            item_id = cursor.lastrowid
        else:
            cursor = conn.execute('''UPDATE net_worth_items SET name=?, category=?, value=?, mortgage=?,
                valuation_date=?, notes=?, updated_at=? WHERE id=?''', (*fields, item_id))
            if cursor.rowcount == 0:
                raise LookupError('This net worth item no longer exists. Refresh the list.')
        return conn.execute('SELECT * FROM net_worth_items WHERE id=?', (item_id,)).fetchone()


def delete_item(item_id):
    server.init_db()
    with closing(server.get_connection()) as conn, conn:
        if conn.execute('DELETE FROM net_worth_items WHERE id=?', (item_id,)).rowcount == 0:
            raise LookupError('This net worth item no longer exists. Refresh the list.')
