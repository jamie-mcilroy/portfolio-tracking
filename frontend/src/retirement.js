// Monthly cash flows: investment growth, then contributions or retirement spending.
export function projectRetirement({ startingBalance, startDate, retirementDate, endYear,
  annualReturn, inflation, monthlyContribution, monthlySpending, monthlyIncome,
  taxRate, oneTimeExpense, expenseYear }) {
  const start = new Date(`${startDate}T00:00:00Z`);
  const retirement = new Date(`${retirementDate}T00:00:00Z`);
  const numbers = [startingBalance, endYear, annualReturn, inflation, monthlyContribution,
    monthlySpending, monthlyIncome, taxRate, oneTimeExpense, expenseYear];
  if (numbers.some((value) => !Number.isFinite(value)) || !Number.isFinite(start.getTime())
    || !Number.isFinite(retirement.getTime())) throw new Error('Enter valid dates and numbers.');
  if (startingBalance < 0 || monthlyContribution < 0 || monthlySpending < 0 || monthlyIncome < 0
    || oneTimeExpense < 0) throw new Error('Balances, contributions, spending and income must be zero or greater.');
  if (annualReturn <= -100 || annualReturn > 100 || inflation < 0 || inflation > 25
    || taxRate < 0 || taxRate >= 100) throw new Error('Use a return above −100% and at most 100%, inflation from 0–25%, and tax below 100%.');
  if (!Number.isInteger(endYear) || endYear <= start.getUTCFullYear() || endYear > start.getUTCFullYear() + 60)
    throw new Error('Choose an end year within the next 60 years.');
  if (retirement < start || retirement.getUTCFullYear() > endYear)
    throw new Error('Retirement must fall between the starting date and projection end year.');
  if (oneTimeExpense && (!Number.isInteger(expenseYear) || expenseYear < start.getUTCFullYear() || expenseYear > endYear))
    throw new Error('Choose an expense year within the projection.');
  const monthlyRate = Math.pow(1 + annualReturn / 100, 1 / 12) - 1;
  let balance = startingBalance;
  let growthOnly = startingBalance;
  let expensePaid = false;
  let depletionDate = startingBalance === 0 ? startDate : null;
  let shortfallDate = null;
  const rows = [];
  const points = [{ date: startDate, balance, growthOnly, factor: 1 }];
  for (let month = 0; month < 61 * 12; month += 1) {
    const date = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + month, 1));
    date.setUTCDate(Math.min(start.getUTCDate(), new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 0)).getUTCDate()));
    const next = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + month + 1, 1));
    next.setUTCDate(Math.min(start.getUTCDate(), new Date(Date.UTC(next.getUTCFullYear(), next.getUTCMonth() + 1, 0)).getUTCDate()));
    // Include only complete monthly periods through the end year.
    if (next.getUTCFullYear() > endYear) break;
    const year = next.getUTCFullYear();
    const factor = Math.pow(1 + inflation / 100, (month + 1) / 12);
    let row = rows.at(-1);
    if (!row || row.year !== year) {
      row = { year, opening: balance, growth: 0, contributions: 0, withdrawals: 0, spending: 0, shortfall: 0 };
      rows.push(row);
    }
    const retired = date >= retirement;
    const contribution = retired ? 0 : monthlyContribution;
    let spending = retired ? Math.max(0, monthlySpending - monthlyIncome) * factor : 0;
    if (!expensePaid && date.getUTCFullYear() === expenseYear && oneTimeExpense > 0) {
      spending += oneTimeExpense * factor;
      expensePaid = true;
    }
    const growth = balance * monthlyRate;
    const available = Math.max(0, balance + growth + contribution);
    const withdrawal = Math.min(available, spending / (1 - taxRate / 100));
    const funded = withdrawal * (1 - taxRate / 100);
    const shortfall = Math.max(0, spending - funded);
    balance = Math.max(0, available - withdrawal);
    growthOnly = growthOnly * (1 + monthlyRate) + contribution;
    const dateText = next.toISOString().slice(0, 10);
    if (balance <= 0 && startingBalance > 0 && !depletionDate) depletionDate = dateText;
    if (shortfall > 0.005 && !shortfallDate) shortfallDate = dateText;
    row.growth += growth;
    row.contributions += contribution;
    row.withdrawals += withdrawal;
    row.spending += funded;
    row.shortfall += shortfall;
    row.ending = balance;
    row.growthOnly = growthOnly;
    row.factor = factor;
    points.push({ date: dateText, balance, growthOnly, factor });
  }
  return { rows, points, depletionDate, shortfallDate };
}
