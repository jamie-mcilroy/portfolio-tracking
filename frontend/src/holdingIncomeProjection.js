const dayMs = 86400000;
const yearMs = 365.25 * dayMs;
const finite = (value) => value === null || value === undefined || value === '' || !Number.isFinite(Number(value)) ? null : Number(value);

export function projectHoldingIncome(holding, { asOf, years = 10, priceGrowth = 4, sgovPriceGrowth = 0, sgovDividendGrowth = 0 }) {
  const start = Date.parse(`${asOf}T00:00:00Z`);
  if (!Number.isFinite(start) || !Number.isInteger(years) || years < 1 || years > 30
    || !Number.isFinite(priceGrowth) || priceGrowth <= -100 || priceGrowth > 100)
    throw new Error('Use 1–30 projection years and price growth above −100% and at most 100%.');
  const quantity = finite(holding.quantity);
  const price = finite(holding.current_price ?? holding.closing_price);
  const annualDividend = finite(holding.annual_forward_dividend);
  const historicGrowth = finite(holding.five_year_dividend_growth_pct);
  const sgov = String(holding.symbol || '').trim().toUpperCase() === 'SGOV'
    || String(holding.yahoo_symbol || '').trim().toUpperCase() === 'SGOV';
  if (sgov && (!Number.isFinite(sgovPriceGrowth) || !Number.isFinite(sgovDividendGrowth)
    || sgovPriceGrowth <= -100 || sgovPriceGrowth > 100 || sgovDividendGrowth <= -100 || sgovDividendGrowth > 100))
    throw new Error('SGOV growth assumptions must be above −100% and at most 100%.');
  const growth = sgov ? sgovDividendGrowth : historicGrowth !== null && historicGrowth > -100 ? historicGrowth : 0;
  const appliedPriceGrowth = sgov ? sgovPriceGrowth : priceGrowth;
  const cash = String(holding.asset_type || '').toLowerCase() === 'cash';
  const payments = finite(holding.payments_per_year);
  const frequency = payments > 0 && payments <= 366 ? payments : 12;
  const notes = [];
  if (sgov) notes.push(`SGOV projection uses ${sgovPriceGrowth}% price growth and ${sgovDividendGrowth}% dividend growth; historical payout growth is not extrapolated.`);
  if (!sgov && !cash && annualDividend > 0 && (historicGrowth === null || historicGrowth <= -100)) notes.push('No usable dividend growth history; dividend held flat.');
  if (!cash && annualDividend > 0 && !(payments > 0 && payments <= 366)) notes.push('Payment frequency unavailable; monthly DRIP assumed.');
  if (annualDividend === null) notes.push('Dividend data unavailable; future income is not projected.');
  if (annualDividend === 0 && !cash) notes.push('No current dividend recorded.');
  const valid = quantity !== null && quantity >= 0 && annualDividend !== null && annualDividend >= 0
    && (!(annualDividend > 0) || (price !== null && price > 0));
  if (!valid && annualDividend !== null) notes.push('Missing or invalid shares/price; income projection unavailable.');
  let shares = quantity;
  let payment = 1;
  const paymentTime = (index) => {
    const months = 12 / frequency;
    if (!Number.isInteger(months)) return start + index * yearMs / frequency;
    const origin = new Date(start);
    const date = new Date(Date.UTC(origin.getUTCFullYear(), origin.getUTCMonth() + index * months, 1));
    date.setUTCDate(Math.min(origin.getUTCDate(), new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 0)).getUTCDate()));
    return date.getTime();
  };
  let previousIncome = valid ? quantity * annualDividend : null;
  const projections = [];
  for (let offset = 1; offset <= years; offset += 1) {
    const year = Number(asOf.slice(0, 4)) + offset;
    const target = Date.UTC(year, 0, 1);
    if (valid && !cash && annualDividend > 0) {
      while (paymentTime(payment) <= target) {
        const elapsed = (paymentTime(payment) - start) / yearMs;
        const paymentPrice = price * (1 + appliedPriceGrowth / 100) ** elapsed;
        const paymentDividend = annualDividend * (1 + growth / 100) ** elapsed / frequency;
        shares += shares * paymentDividend / paymentPrice;
        payment += 1;
      }
    }
    const elapsed = (target - start) / yearMs;
    const startPrice = price === null ? null : price * (cash ? 1 : (1 + appliedPriceGrowth / 100) ** elapsed);
    const dividend = annualDividend === null ? null : annualDividend * (cash ? 1 : (1 + growth / 100) ** elapsed);
    const income = valid ? shares * dividend : null;
    const safeIncome = income !== null && Number.isFinite(income) ? income : null;
    projections.push({ year, shares: valid && Number.isFinite(shares) ? shares : null,
      price: startPrice !== null && Number.isFinite(startPrice) ? startPrice : null,
      annualDividend: dividend !== null && Number.isFinite(dividend) ? dividend : null,
      income: safeIncome, increase: safeIncome !== null && previousIncome !== null ? safeIncome - previousIncome : null });
    previousIncome = safeIncome;
  }
  return { quantity, price, annualDividend, currentIncome: valid ? quantity * annualDividend : null,
    historicGrowth, appliedDividendGrowth: cash ? 0 : growth, sgov,
    frequency: annualDividend > 0 ? frequency : null, cash, notes, projections };
}

// Combine display rows after forecasting, preserving each position's DRIP calculation.
export function aggregateHoldingIncomeRows(rows) {
  const groups = new Map();
  for (const row of rows) {
    const symbol = String(row.holding.symbol || row.holding.description || '').trim();
    const key = [symbol.toUpperCase(), row.holding.market || '', row.cash ? row.holding.currency || 'CAD' : ''].join('|');
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  const total = (values) => values.some((value) => value == null) ? null : values.reduce((sum, value) => sum + value, 0);
  const unitValue = (items, valueKey, quantityKey) => {
    const quantity = total(items.map((item) => item[quantityKey]));
    if (!quantity) return items[0][valueKey];
    const value = total(items.map((item) => item[valueKey] == null ? null : item[valueKey] * item[quantityKey]));
    return value == null ? null : value / quantity;
  };
  return [...groups.entries()].map(([key, members]) => {
    const first = members[0];
    const frequency = members.every((item) => item.frequency === first.frequency) ? first.frequency : null;
    const growthMixed = members.some((item) => item.appliedDividendGrowth !== first.appliedDividendGrowth);
    return { ...first, key, quantity: total(members.map((item) => item.quantity)),
      price: unitValue(members, 'price', 'quantity'),
      annualDividend: unitValue(members, 'annualDividend', 'quantity'),
      currentIncome: total(members.map((item) => item.currentIncome)), frequency,
      historicGrowth: growthMixed ? null : first.historicGrowth, growthMixed,
      notes: [...new Set(members.flatMap((item) => item.notes))],
      projections: first.projections.map((point, index) => {
        const items = members.map((item) => item.projections[index]);
        return { year: point.year, shares: total(items.map((item) => item.shares)),
          price: unitValue(items, 'price', 'shares'), annualDividend: unitValue(items, 'annualDividend', 'shares'),
          income: total(items.map((item) => item.income)), increase: total(items.map((item) => item.increase)) };
      }),
    };
  }).sort((left, right) => String(left.holding.symbol || left.holding.description || '').localeCompare(String(right.holding.symbol || right.holding.description || '')));
}
