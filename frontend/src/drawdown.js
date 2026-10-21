import { isRspAccount } from './performance.js';
import { retirementDateForAge } from './retirementSummary.js';

export const isWestBraggAccount = (account) => /^(?:\d+\s+)?(?:west\s+bragg\b|wbfc\b|wb\b)/i.test(String(account.name || '').trim());

export function drawdownAccounts(data) {
  // Summary accounts carry live CAD balances; all_accounts is the setup list.
  return (data.accounts || []).filter((account) => isRspAccount(account) || isWestBraggAccount(account))
    .map((account) => {
      const holdings = (data.holdings || []).filter((holding) => String(holding.account_id) === String(account.id));
      return { id: account.id, name: account.name, westBragg: isWestBraggAccount(account),
        balance: Number(account.current_total_value ?? account.total_closing_value ?? 0),
        cash: Number(account.cash_balance || 0),
        annualIncome: holdings.reduce((sum, holding) => sum + Number(holding.annual_forward_income || 0), 0),
        missingIncome: holdings.filter((holding) => String(holding.asset_type).toLowerCase() !== 'cash'
          && holding.annual_forward_income == null).length };
    }).sort((a, b) => Number(b.westBragg) - Number(a.westBragg) || a.name.localeCompare(b.name));
}

export function ageOn(date) {
  const [year, month, day] = date.split('-').map(Number);
  return year - 1972 - Number(month < 12 || (month === 12 && day < 2));
}

// Blended investment units per account: dividends buy units at rising prices;
// sales reduce units and therefore subsequent dividend income. Cash stays flat.
export function projectDrawdown({ accounts, startDate, retirementAge, endAge, annualWithdrawal, priceGrowth = 2 }) {
  const start = Date.parse(`${startDate}T00:00:00Z`);
  const currentAge = ageOn(startDate);
  if (![retirementAge, endAge, annualWithdrawal, priceGrowth].every(Number.isFinite) || !Number.isFinite(start))
    throw new Error('Enter retirement age, end-of-life age, annual withdrawal and price growth.');
  if (!Number.isInteger(retirementAge) || retirementAge < currentAge || retirementAge > 120
    || !Number.isInteger(endAge) || endAge <= retirementAge || endAge > 120)
    throw new Error(`Use whole ages: retirement from ${currentAge}–119, and an end-of-life age after retirement, up to 120.`);
  if (annualWithdrawal < 0 || priceGrowth <= -100 || priceGrowth > 100)
    throw new Error('Withdrawal must be zero or greater; price growth must be above −100% and at most 100%.');
  const retirementDate = retirementAge === currentAge ? startDate : retirementDateForAge(retirementAge);
  const retirement = Date.parse(`${retirementDate}T00:00:00Z`);
  const endDate = retirementDateForAge(endAge);
  const end = Date.parse(`${endDate}T00:00:00Z`);
  const portfolio = accounts.map((account) => ({ ...account, units: account.balance - account.cash,
    dividendPerUnit: account.balance > account.cash ? account.annualIncome / (account.balance - account.cash) : 0 }));
  if (!portfolio.length || portfolio.some((a) => ![a.balance, a.cash, a.annualIncome].every(Number.isFinite)
    || a.balance < 0 || a.cash < 0 || a.cash > a.balance || a.annualIncome < 0
    || (a.units === 0 && a.annualIncome > 0)))
    throw new Error('Selected accounts need valid balances, cash and dividend estimates.');
  portfolio.sort((a, b) => Number(b.westBragg) - Number(a.westBragg));
  const snapshot = (price) => portfolio.map((a) => ({ id: a.id, name: a.name, westBragg: a.westBragg,
    balance: a.cash + a.units * price }));
  const initial = { date: startDate, accounts: snapshot(1) };
  const times = new Set([retirement, end]);
  const origin = new Date(start);
  for (let month = 1; month <= (new Date(end).getUTCFullYear() - origin.getUTCFullYear() + 1) * 12; month += 1) {
    const next = new Date(Date.UTC(origin.getUTCFullYear(), origin.getUTCMonth() + month, 1));
    next.setUTCDate(Math.min(origin.getUTCDate(), new Date(Date.UTC(next.getUTCFullYear(), next.getUTCMonth() + 1, 0)).getUTCDate()));
    if (next.getTime() >= end) break;
    times.add(next.getTime());
  }
  for (let year = origin.getUTCFullYear() + 1; Date.UTC(year, 0, 1) < end; year += 1) times.add(Date.UTC(year, 0, 1));
  const rows = [];
  let previous = start;
  let firstShortfall = null;
  for (const time of [...times].filter((value) => value > start).sort((a, b) => a - b)) {
    const year = new Date(previous).getUTCFullYear();
    let row = rows.at(-1);
    if (!row || row.year !== year) {
      row = { year, opening: snapshot((1 + priceGrowth / 100) ** ((previous - start) / (365.25 * 86400000)))
        .reduce((sum, a) => sum + a.balance, 0), dividends: 0, drip: 0, westWithdrawal: 0, rspWithdrawal: 0, shortfall: 0 };
      rows.push(row);
    }
    const years = (time - previous) / (365.25 * 86400000);
    const price = (1 + priceGrowth / 100) ** ((time - start) / (365.25 * 86400000));
    // Prorate fixed annual spending by calendar year, including partial years.
    let needed = previous >= retirement ? annualWithdrawal * (time - previous) / (Date.UTC(year + 1, 0, 1) - Date.UTC(year, 0, 1)) : 0;
    for (const account of portfolio) {
      // Preserve initial per-unit payout even as cash is spent.
      const dividend = account.units * account.dividendPerUnit * years;
      row.dividends += dividend;
      const dividendUsed = Math.min(dividend, needed);
      needed -= dividendUsed;
      const reinvested = dividend - dividendUsed;
      account.units += reinvested / price;
      row.drip += reinvested;
      const cashUsed = Math.min(account.cash, needed);
      account.cash -= cashUsed;
      needed -= cashUsed;
      const sale = Math.min(account.units * price, needed);
      account.units = Math.max(0, account.units - sale / price);
      needed = Math.max(0, needed - sale);
      row[account.westBragg ? 'westWithdrawal' : 'rspWithdrawal'] += dividendUsed + cashUsed + sale;
    }
    row.shortfall += needed;
    if (needed > .01 && !firstShortfall) firstShortfall = new Date(previous).toISOString().slice(0, 10);
    row.date = new Date(time).toISOString().slice(0, 10);
    row.accounts = snapshot(price);
    row.ending = row.accounts.reduce((sum, a) => sum + a.balance, 0);
    // Add funded withdrawals back to measure gains, including dividends once.
    // This is the period's return on opening capital, not an annualized rate.
    row.investmentGain = row.ending + row.westWithdrawal + row.rspWithdrawal - row.opening;
    row.roi = row.opening > 0 ? row.investmentGain / row.opening : null;
    if (![row.ending, row.dividends, row.drip, row.shortfall].every(Number.isFinite))
      throw new Error('These growth assumptions produce values too large to chart.');
    previous = time;
  }
  return { initial, rows, retirementDate, endDate, firstShortfall };
}
