"""A single-page, vector PDF snapshot of the household's net worth."""
from datetime import datetime
from io import BytesIO
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import landscape, letter
from reportlab.pdfgen import canvas

CATEGORIES = {'property': 'Property equity', 'vehicle': 'Vehicles', 'art': 'Art',
              'jewellery': 'Jewellery', 'other': 'Other assets', 'debt': 'Other debts'}
NAVY = '#17251B'
MUTED = '#637266'
TEAL = '#267537'
BLUE = '#47964B'
RED = '#B95151'
LOGO_PATH = Path(__file__).resolve().parent.parent / 'resources' / 'wbfc-logo.png'


def amount(value):
    return f'-${abs(value):,.0f}' if value < 0 else f'${value:,.0f}'


def build_report(accounts, items, generated_at=None, comparison=None):
    generated_at = generated_at or datetime.now(ZoneInfo('America/Edmonton'))
    rows = [(str(a['name']), float(a.get('current_total_value') if a.get('current_total_value') is not None
                                 else a.get('total_closing_value') or 0)) for a in accounts]
    if not all(math.isfinite(value) for _, value in rows):
        raise ValueError('Investment balances must be finite to generate a report.')
    investments = sum(value for _, value in rows)
    assets = investments + sum(i['value'] for i in items if i['category'] != 'debt')
    mortgages = sum(i['mortgage'] for i in items if i['category'] == 'property')
    other_debt = sum(i['value'] for i in items if i['category'] == 'debt')
    debts = mortgages + other_debt
    category_rows = [('Investment accounts', investments)]
    for key, label in CATEGORIES.items():
        selected = [i for i in items if i['category'] == key]
        category_rows.append((label, sum(-i['value'] if key == 'debt' else i['value'] - i['mortgage'] for i in selected)))

    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=landscape(letter), pageCompression=0)
    pdf.setTitle('Household Net Worth Change' if comparison else 'Household Net Worth')
    pdf.setAuthor('West Bragg Forestry Corp.')
    width, height = landscape(letter)

    def text(x, y, value, size=10, color=NAVY, bold=False, right=False):
        pdf.setFont('Helvetica-Bold' if bold else 'Helvetica', size)
        pdf.setFillColor(HexColor(color))
        # Built-in fonts have reliable Latin coverage; replace unsupported glyphs.
        value = str(value).encode('cp1252', errors='replace').decode('cp1252')
        (pdf.drawRightString if right else pdf.drawString)(x, y, value)

    def fit(label, max_width, size):
        label = str(label).replace('\n', ' ').replace('\r', ' ')
        if pdf.stringWidth(label, 'Helvetica', size) <= max_width:
            return label
        while label and pdf.stringWidth(label + '...', 'Helvetica', size) > max_width:
            label = label[:-1]
        return label + '...'

    pdf.setFillColor(HexColor('#000000'))
    pdf.rect(0, height - 105, width, 105, fill=1, stroke=0)
    pdf.drawImage(str(LOGO_PATH), 36, 519, width=84, height=84, preserveAspectRatio=True, mask='auto')
    text(140, 577, 'WEST BRAGG FORESTRY CORP.', 9, '#8CCA80', True)
    text(140, 542, 'Net worth change' if comparison else 'Net worth', 29, '#FFFFFF', True)
    text(140, 523, 'HOUSEHOLD FINANCIAL SNAPSHOT', 7, '#BCD3B8')
    text(756, 574, generated_at.strftime('%B %d, %Y').replace(' 0', ' '), 11, '#FFFFFF', right=True)
    text(756, 552, 'All amounts in Canadian dollars', 9, '#BCD3B8', right=True)
    pdf.setFillColor(HexColor(BLUE))
    pdf.rect(0, height - 105, width, 3, fill=1, stroke=0)
    cards = [('NET WORTH', assets - debts, TEAL), ('TOTAL ASSETS', assets, NAVY),
             ('TOTAL DEBTS', debts, RED), ('INVESTMENTS', investments, BLUE)]
    if comparison:
        delta = comparison['totals']
        cards = [('NET WORTH (LATER)', assets - debts, TEAL), ('NET WORTH CHANGE', delta['netWorth'], TEAL if delta['netWorth'] >= 0 else RED),
                 ('ASSET CHANGE', delta['assets'], NAVY), ('DEBT CHANGE', delta['debts'], RED if delta['debts'] > 0 else TEAL)]
        rows = [(row['name'], row['change']) for row in comparison['accounts']]
        category_rows = [(row['name'], row['change']) for row in comparison['categories']]
    for index, (label, value, color) in enumerate(cards):
        x = 36 + index * 184
        pdf.setFillColor(HexColor('#F0F5EF'))
        pdf.roundRect(x, 430, 168, 62, 5, fill=1, stroke=0)
        text(x + 12, 472, label, 8, MUTED, True)
        label_value = amount(value)
        size = min(20, 140 / max(1, pdf.stringWidth(label_value, 'Helvetica-Bold', 1)))
        text(x + 12, 445, label_value, size, color, True)

    def chart(x, top, chart_width, entries, color, title, subtitle, max_rows=18):
        text(x, top, title, 13, NAVY, True)
        text(x, top - 16, subtitle, 8, MUTED)
        entries = sorted(entries, key=lambda row: abs(row[1]), reverse=True)
        grouped = len(entries) > max_rows
        if grouped:
            tail = entries[max_rows - 1:]
            entries = entries[:max_rows - 1] + [(f'Other {len(tail)} accounts (net)', sum(v for _, v in tail))]
        if not entries:
            text(x, top - 50, 'No investment accounts available.', 10, MUTED)
            return
        row_height = min(31, 264 / max(1, len(entries)))
        font_size = 8 if len(entries) > 13 else 9
        label_width, value_width = 124, 82
        plot_x = x + label_width
        plot_width = chart_width - label_width - value_width - 8
        negative = max([abs(v) for _, v in entries if v < 0], default=0)
        positive = max([v for _, v in entries if v >= 0], default=0)
        extent = max(1, negative + positive)
        zero_x = plot_x + plot_width * negative / extent
        chart_top = top - 36
        pdf.setStrokeColor(HexColor('#D7E3D5'))
        pdf.line(zero_x, chart_top + 7, zero_x, chart_top - row_height * (len(entries) - 1) - 8)
        for index, (label, value) in enumerate(entries):
            y = chart_top - index * row_height
            text(x, y - 3, fit(label, label_width - 9, font_size), font_size)
            bar_width = plot_width * abs(value) / extent
            pdf.setFillColor(HexColor(RED if value < 0 else color))
            pdf.rect(zero_x - bar_width if value < 0 else zero_x, y - 5, bar_width, 10, stroke=0, fill=1)
            value_label = amount(value)
            value_size = min(font_size, (value_width - 4) / max(1, pdf.stringWidth(value_label, 'Helvetica', 1)))
            text(x + chart_width, y - 3, value_label, value_size, RED if value < 0 else NAVY, right=True)
        if grouped:
            text(x, 91, 'Smallest accounts grouped to keep this report to one page.', 7, MUTED)

    chart(36, 404, 346, rows, BLUE, 'Change by account' if comparison else 'Investments by account',
          'Difference between saved investment balances' if comparison else 'Current portfolio balances, including account cash')
    chart(410, 404, 346, category_rows, TEAL, 'Change by category' if comparison else 'Net worth by category',
          'Net worth contributions: increases right, decreases left' if comparison else 'Net contributions; property is shown after its mortgage')
    property_value = sum(i['value'] for i in items if i['category'] == 'property')
    text(410, 119, 'PROPERTY EQUITY (LATER SNAPSHOT)' if comparison else 'PROPERTY EQUITY', 8, MUTED, True)
    property_label = f'{amount(property_value)} value - {amount(mortgages)} mortgage = {amount(property_value - mortgages)}'
    property_size = min(9, 346 / max(1, pdf.stringWidth(property_label, 'Helvetica-Bold', 1)))
    text(410, 104, property_label, property_size, NAVY, True)
    pdf.setStrokeColor(HexColor('#D7E3D5'))
    pdf.line(36, 80, 756, 80)
    dates = sorted(i['valuation_date'] for i in items)
    valuation_note = (f'Manual valuation dates: {dates[0]} to {dates[-1]}.' if dates else 'No manual assets or debts have been added.')
    if comparison:
        def saved_label(snapshot):
            return datetime.fromisoformat(snapshot['captured_at']).astimezone(ZoneInfo('America/Edmonton')).strftime('%b %d, %Y %H:%M')
        text(36, 65, f"Saved snapshots: {saved_label(comparison['before'])} to {saved_label(comparison['after'])} (Edmonton time).", 8, MUTED)
    else:
        text(36, 65, valuation_note + ' Investment balances are from the current portfolio snapshot.', 8, MUTED)
    text(36, 51, 'Changes include trades, transfers, debt payments and valuation edits; they are not investment returns.' if comparison
         else 'Mortgages are deducted once. Other debts are separate. Values are estimates; taxes and selling costs are excluded.', 8, MUTED)
    text(36, 27, 'PRIVATE & CONFIDENTIAL', 7, MUTED, True)
    text(756, 27, 'West Bragg Forestry Corp.  /  1 of 1', 7, MUTED, right=True)
    pdf.showPage()
    pdf.save()
    return output.getvalue()
