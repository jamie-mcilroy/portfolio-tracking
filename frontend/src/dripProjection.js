// Each account reinvests its own dividends; capital is sold in the supplied account order.
export function projectDrip({ balance, annualIncome, capitalGrowth, dividendGrowth,
  inflation, desiredIncome, startDate, retirementDate, endDate = retirementDate, accounts }) {
  const start = Date.parse(`${startDate}T00:00:00Z`);
  const retirement = Date.parse(`${retirementDate}T00:00:00Z`);
  const end = Date.parse(`${endDate}T00:00:00Z`);
  if (![balance, annualIncome, capitalGrowth, dividendGrowth, inflation, desiredIncome].every(Number.isFinite)
    || !Number.isFinite(start) || !Number.isFinite(retirement) || !Number.isFinite(end)) throw new Error('Enter valid retirement age and growth assumptions.');
  if (balance <= 0) throw new Error('A positive retirement balance is needed for the projection.');
  if (annualIncome < 0 || desiredIncome < 0) throw new Error('Annual income must be zero or greater.');
  if (capitalGrowth <= -100 || capitalGrowth > 100 || dividendGrowth <= -100 || dividendGrowth > 100
    || inflation < 0 || inflation > 100) throw new Error('Growth rates must be above −100% and at most 100%; inflation must be 0–100%.');
  if (retirement < start) throw new Error('Choose a retirement age with a date today or later.');
  if (end < retirement) throw new Error('Estimated end age must be at least your retirement age.');
  const years = (end - start) / (365.25 * 86400000);
  if (years > 70) throw new Error('Choose an end age within the next 70 years.');
  const portfolio = (accounts || [{ id: 'portfolio', name: 'Portfolio', balance, annualIncome }])
    .map((account) => ({ ...account, balance: Number(account.balance), annualIncome: Number(account.annualIncome),
      basePrice: Number(account.balance) || 1, units: Number(account.balance) > 0 ? 1 : 0,
      retirementBalance: account.retirementBalance === '' || account.retirementBalance == null ? null : Number(account.retirementBalance) }));
  if (portfolio.some((account) => !Number.isFinite(account.balance) || !Number.isFinite(account.annualIncome)
    || account.balance < 0 || account.annualIncome < 0 || (account.balance === 0 && account.annualIncome > 0)))
    throw new Error('Each account needs a valid nonnegative balance and dividend income.');
  if (portfolio.some((account) => account.retirementBalance !== null
    && (!Number.isFinite(account.retirementBalance) || account.retirementBalance < 0)))
    throw new Error('Estimated retirement balances must be zero or greater, or left blank.');
  const initialBalance = portfolio.reduce((sum, account) => sum + account.balance, 0);
  const initialIncome = portfolio.reduce((sum, account) => sum + account.annualIncome, 0);
  if (Math.abs(initialBalance - balance) > .01 || Math.abs(initialIncome - annualIncome) > .01)
    throw new Error('Account balances and income must match the retirement totals.');
  if (retirement === start) {
    for (const account of portfolio) {
      if (account.retirementBalance !== null) account.units = account.retirementBalance / account.basePrice;
    }
  }
  const initialAccounts = portfolio.map((account) => ({ id: account.id, name: account.name,
    balance: account.units * account.basePrice, annualIncome: account.units * account.annualIncome, capitalFunding: 0 }));
  const points = [{ date: startDate, balance: initialAccounts.reduce((sum, account) => sum + account.balance, 0),
    annualIncome: initialAccounts.reduce((sum, account) => sum + account.annualIncome, 0),
    targetIncome: desiredIncome, factor: 1, accounts: initialAccounts }];
  const origin = new Date(start);
  const times = new Set([retirement, end]);
  for (let month = 1; month <= 70 * 12 + 1; month += 1) {
    const next = new Date(Date.UTC(origin.getUTCFullYear(), origin.getUTCMonth() + month, 1));
    next.setUTCDate(Math.min(origin.getUTCDate(), new Date(Date.UTC(next.getUTCFullYear(), next.getUTCMonth() + 1, 0)).getUTCDate()));
    if (next.getTime() >= end) break;
    times.add(next.getTime());
  }
  let previousYears = 0;
  let previousTime = start;
  for (const time of [...times].filter((value) => value > start).sort((left, right) => left - right)) {
    const elapsed = (time - start) / (365.25 * 86400000);
    const periodYears = elapsed - previousYears;
    const factor = Math.pow(1 + inflation / 100, elapsed);
    const retired = previousTime >= retirement;
    const requested = retired ? desiredIncome * factor * periodYears : 0;
    const period = portfolio.map((account) => {
      const price = account.basePrice * Math.pow(1 + capitalGrowth / 100, elapsed);
      const dividendPerUnit = account.annualIncome * Math.pow(1 + dividendGrowth / 100, elapsed);
      return { account, price, dividendPerUnit, dividends: account.units * dividendPerUnit * periodYears, sale: 0 };
    });
    const dividends = period.reduce((sum, item) => sum + item.dividends, 0);
    const dividendUsed = Math.min(dividends, requested);
    let capitalNeeded = Math.max(0, requested - dividendUsed);
    for (const item of period) {
      const surplus = dividends > 0 ? item.dividends * (1 - dividendUsed / dividends) : 0;
      if (item.price > 0) item.account.units += surplus / item.price;
      item.sale = Math.min(item.account.units * item.price, capitalNeeded);
      capitalNeeded = Math.max(0, capitalNeeded - item.sale);
      if (item.price > 0) item.account.units = Math.max(0, item.account.units - item.sale / item.price);
    }
    const sales = period.reduce((sum, item) => sum + item.sale, 0);
    const withdrawal = dividendUsed + sales;
    const shortfall = Math.max(0, requested - withdrawal);
    const dividendFunding = dividendUsed / periodYears;
    const capitalFunding = sales / periodYears;
    if (time === retirement) {
      for (const item of period) {
        if (item.account.retirementBalance !== null) item.account.units = item.account.retirementBalance / item.price;
      }
    }
    const accountPoints = period.map((item) => ({ id: item.account.id, name: item.account.name,
      balance: item.account.units * item.price, annualIncome: item.account.units * item.dividendPerUnit,
      capitalFunding: item.sale / periodYears }));
    const value = accountPoints.reduce((sum, account) => sum + account.balance, 0);
    const income = accountPoints.reduce((sum, account) => sum + account.annualIncome, 0);
    if (![value, income, factor].every(Number.isFinite)) throw new Error('These assumptions produce values too large to chart. Reduce the growth rates or retirement age.');
    points.push({ date: new Date(time).toISOString().slice(0, 10), balance: value,
      annualIncome: income, targetIncome: desiredIncome * factor, factor, withdrawal, shortfall,
      dividendFunding, capitalFunding, incomeShortfall: shortfall / periodYears, retired, accounts: accountPoints });
    previousYears = elapsed;
    previousTime = time;
  }
  return points;
}
