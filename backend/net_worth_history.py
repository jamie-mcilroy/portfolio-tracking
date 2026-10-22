"""Immutable weekly net worth snapshots, independent of the price refresh worker."""
from contextlib import closing
from datetime import datetime, timedelta, time, timezone
import json
import uuid
from zoneinfo import ZoneInfo

import server
from backend import net_worth

LOCAL_ZONE = ZoneInfo('America/Edmonton')
CATEGORY_LABELS = {'property': 'Property equity', 'vehicle': 'Vehicles', 'art': 'Art',
                   'jewellery': 'Jewellery', 'other': 'Other assets', 'debt': 'Other debts'}


def totals(accounts, items):
    investments = sum(float(a.get('current_total_value') if a.get('current_total_value') is not None
                            else a.get('total_closing_value') or 0) for a in accounts)
    assets = investments + sum(i['value'] for i in items if i['category'] != 'debt')
    debts = sum(i['mortgage'] for i in items) + sum(i['value'] for i in items if i['category'] == 'debt')
    return dict(investments=investments, assets=assets, debts=debts, netWorth=assets - debts)


def capture_snapshot(source='manual', capture_key=None, now=None):
    server.init_db()
    key = capture_key or f'manual:{uuid.uuid4()}'
    with closing(server.get_connection()) as conn:
        existing = conn.execute('SELECT id FROM net_worth_snapshots WHERE capture_key=?', (key,)).fetchone()
    if existing:
        return get_snapshot(existing['id'])
    # Summary uses cached prices; no network request or recalculation of old snapshots.
    accounts = server.get_summary().get('accounts', [])
    items = net_worth.list_items()
    payload = dict(accounts=accounts, items=items, totals=totals(accounts, items))
    captured_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    with closing(server.get_connection()) as conn, conn:
        conn.execute('''INSERT OR IGNORE INTO net_worth_snapshots
            (capture_key, captured_at, source, payload) VALUES (?, ?, ?, ?)''',
            (key, captured_at, source, json.dumps(payload, allow_nan=False)))
        row = conn.execute('SELECT id FROM net_worth_snapshots WHERE capture_key=?', (key,)).fetchone()
    return get_snapshot(row['id'])


def decode(row, full=False):
    payload = json.loads(row['payload'])
    result = {key: row[key] for key in ('id', 'captured_at', 'source')}
    result['totals'] = payload['totals']
    if full:
        result.update(payload)
    return result


def list_snapshots():
    server.init_db()
    with closing(server.get_connection()) as conn:
        return [decode(row) for row in conn.execute('SELECT * FROM net_worth_snapshots ORDER BY captured_at DESC, id DESC')]


def get_snapshot(snapshot_id):
    server.init_db()
    with closing(server.get_connection()) as conn:
        row = conn.execute('SELECT * FROM net_worth_snapshots WHERE id=?', (snapshot_id,)).fetchone()
    if row is None:
        raise LookupError('Net worth snapshot not found.')
    return decode(row, full=True)


def maybe_capture_weekly(now=None):
    local_now = (now or datetime.now(LOCAL_ZONE)).astimezone(LOCAL_ZONE)
    server.init_db()
    with closing(server.get_connection()) as conn:
        has_history = conn.execute('SELECT 1 FROM net_worth_snapshots LIMIT 1').fetchone()
    monday = local_now.date() - timedelta(days=local_now.weekday())
    due = datetime.combine(monday + timedelta(days=4), time(17), tzinfo=LOCAL_ZONE)
    if local_now >= due:
        # A late restart catches up this week only, stamped with the actual save time.
        return capture_snapshot('weekly', f'weekly:{monday.isoformat()}', local_now)
    if not has_history:
        return capture_snapshot('baseline', 'baseline', local_now)
    return None


def compare_snapshots(before_id, after_id):
    before, after = get_snapshot(before_id), get_snapshot(after_id)
    if (before['captured_at'], before['id']) >= (after['captured_at'], after['id']):
        raise ValueError('Choose an earlier snapshot and a later snapshot.')

    def account_values(snapshot):
        return {str(a['id']): (a['name'], float(a.get('current_total_value') if a.get('current_total_value') is not None
                                              else a.get('total_closing_value') or 0)) for a in snapshot['accounts']}

    def category_values(snapshot):
        result = {'investments': ('Investment accounts', snapshot['totals']['investments'])}
        for category, label in CATEGORY_LABELS.items():
            result[category] = (label, sum(-i['value'] if category == 'debt' else i['value'] - i['mortgage']
                                          for i in snapshot['items'] if i['category'] == category))
        return result

    def changes(old, new):
        rows = []
        for key in old.keys() | new.keys():
            name = new[key][0] if key in new else old[key][0]
            previous, current = old.get(key, ('', 0))[1], new.get(key, ('', 0))[1]
            rows.append(dict(key=key, name=name, before=previous, after=current, change=current - previous))
        return sorted(rows, key=lambda row: (-abs(row['change']), row['name']))

    return dict(before=before, after=after,
                totals={key: after['totals'][key] - before['totals'][key] for key in before['totals']},
                accounts=changes(account_values(before), account_values(after)),
                categories=changes(category_values(before), category_values(after)))
