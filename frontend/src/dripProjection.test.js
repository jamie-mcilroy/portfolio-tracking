import assert from 'node:assert/strict';
import test from 'node:test';
import { projectDrip } from './dripProjection.js';
const defaults = { balance: 100000, annualIncome: 4000, capitalGrowth: 0, dividendGrowth: 0,
  inflation: 2, desiredIncome: 50000, startDate: '2026-01-01', retirementDate: '2030-01-01' };
const run = (overrides = {}) => projectDrip({ ...defaults, ...overrides });
const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-6, `${actual} != ${expected}`);

test('DRIP increases both portfolio value and future income even without price or dividend growth', () => {
  const points = run();
  const last = points.at(-1);
  let expected = defaults.balance;
  for (let i = 1; i < points.length; i += 1) {
    const years = (Date.parse(points[i].date) - Date.parse(points[i - 1].date)) / (365.25 * 86400000);
    expected *= 1 + .04 * years;
  }
  near(last.balance, expected);
  near(last.annualIncome, defaults.annualIncome * last.balance / defaults.balance);
  assert.ok(last.balance > 116000);
});

test('capital appreciation is separate from dividend growth, with no dividends counted twice', () => {
  const last = run({ annualIncome: 0, capitalGrowth: 10 }).at(-1);
  near(last.balance, 100000 * 1.1 ** 4);
  assert.equal(last.annualIncome, 0);
  const income = run({ capitalGrowth: 10, dividendGrowth: 5 }).at(-1);
  const units = income.balance / (100000 * 1.1 ** 4);
  near(income.annualIncome, 4000 * 1.05 ** 4 * units);
  assert.ok(units > 1);
});

test('inflation affects the income target, without reducing nominal balance or dividends', () => {
  const nominal = run({ inflation: 0 }).at(-1);
  const inflated = run({ inflation: 3 }).at(-1);
  near(inflated.targetIncome, 50000 * 1.03 ** 4);
  near(inflated.balance, nominal.balance);
  near(inflated.annualIncome, nominal.annualIncome);
});

test('includes the exact retirement birthday, partial months and month-end dates', () => {
  const points = run({ startDate: '2026-01-31', retirementDate: '2032-12-02' });
  assert.equal(points[1].date, '2026-02-28');
  assert.equal(points[2].date, '2026-03-31');
  assert.equal(points.at(-1).date, '2032-12-02');
  assert.equal(run({ retirementDate: '2026-01-01' }).length, 1);
});

test('allows dividend cuts and rejects incomplete or invalid assumptions', () => {
  assert.ok(run({ dividendGrowth: -20 }).at(-1).annualIncome < 4000);
  for (const overrides of [{ balance: 0 }, { desiredIncome: -1 }, { retirementDate: null },
    { retirementDate: '2020-01-01' }, { retirementDate: '2100-01-01' },
    { capitalGrowth: -100 }, { dividendGrowth: NaN }, { inflation: -1 }]) {
    assert.throws(() => run(overrides));
  }
});

test('drawdown reduces capital and dividend-producing units after retirement', () => {
  const points = run({ retirementDate: '2027-01-01', endDate: '2030-01-01', desiredIncome: 20000, inflation: 0 });
  const retired = points.find((point) => point.date === '2027-01-01');
  const last = points.at(-1);
  assert.ok(retired.balance > 100000);
  assert.equal(retired.withdrawal, 0);
  assert.ok(last.balance < retired.balance);
  near(last.annualIncome / retired.annualIncome, last.balance / retired.balance);
  assert.equal(last.date, '2030-01-01');
});

test('withdrawals with no dividends or growth reconcile exactly to the requested income', () => {
  const points = run({ annualIncome: 0, desiredIncome: 10000, inflation: 0,
    retirementDate: '2026-01-01', endDate: '2030-01-01' });
  near(points.at(-1).balance, 60000);
  near(points.reduce((total, point) => total + (point.withdrawal || 0), 0), 40000);
});

test('depleted funds stay at zero and unfunded income is tracked', () => {
  const points = run({ balance: 1000, annualIncome: 0, desiredIncome: 12000, inflation: 0,
    retirementDate: '2026-01-01', endDate: '2030-01-01' });
  assert.ok(points.some((point) => point.shortfall > 0));
  near(points.reduce((total, point) => total + (point.withdrawal || 0), 0), 1000);
  near(points.reduce((total, point) => total + (point.shortfall || 0), 0), 47000);
  assert.equal(points.at(-1).balance, 0);
  assert.equal(points.at(-1).annualIncome, 0);
  assert.ok(points.every((point) => point.balance >= 0));
});

test('surplus retirement dividends are reinvested and the exact retirement date splits periods', () => {
  const points = run({ retirementDate: '2027-06-02', endDate: '2030-01-01', desiredIncome: 1000, inflation: 0 });
  const retired = points.find((point) => point.date === '2027-06-02');
  assert.equal(retired.withdrawal, 0);
  assert.ok(points.at(-1).annualIncome > retired.annualIncome);
  assert.ok(points.at(-1).balance > retired.balance);
  for (const point of points.filter((point) => point.date <= '2027-06-02')) assert.equal(point.withdrawal || 0, 0);
  const yearsAfterRetirement = (Date.parse('2030-01-01') - Date.parse('2027-06-02')) / (365.25 * 86400000);
  near(points.reduce((total, point) => total + (point.withdrawal || 0), 0), 1000 * yearsAfterRetirement);
});

test('end age cannot precede retirement and invalid end dates are rejected', () => {
  assert.throws(() => run({ endDate: '2028-01-01' }));
  assert.throws(() => run({ endDate: null }));
});

test('income visualization reconciles dividends, investment sales and unfunded income to target', () => {
  for (const desiredIncome of [1000, 20000, 1000000]) {
    const points = run({ retirementDate: '2027-01-01', endDate: '2030-01-01', desiredIncome });
    for (const point of points.filter((point) => point.retired)) {
      near(point.dividendFunding + point.capitalFunding + point.incomeShortfall, point.targetIncome);
      assert.ok(point.dividendFunding >= 0 && point.capitalFunding >= 0 && point.incomeShortfall >= 0);
    }
  }
});

test('selling a non-dividend account first preserves RRSP dividends', () => {
  const accounts = [
    { id: 'wb', name: 'West Bragg', balance: 10000, annualIncome: 0 },
    { id: 'rrsp', name: 'RRSP', balance: 10000, annualIncome: 1000 },
  ];
  const input = { balance: 20000, annualIncome: 1000, accounts, desiredIncome: 5000,
    inflation: 0, capitalGrowth: 0, dividendGrowth: 0, retirementDate: '2026-01-01', endDate: '2027-01-01' };
  const result = run(input).at(-1);
  near(result.accounts.find((account) => account.id === 'rrsp').balance, 10000);
  near(result.annualIncome, 1000);
  assert.ok(result.accounts.find((account) => account.id === 'wb').balance < 10000);
  const reverse = run({ ...input, accounts: [...accounts].reverse() }).at(-1);
  assert.ok(reverse.annualIncome < 1000);
  near(reverse.accounts.find((account) => account.id === 'wb').balance, 10000);
  assert.equal(accounts[0].balance, 10000);
});

test('finishes the first account before moving to the next, and reconciles account totals', () => {
  const points = run({ balance: 10100, annualIncome: 1000, desiredIncome: 10000, inflation: 0,
    accounts: [{ id: 1, balance: 100, annualIncome: 0 }, { id: 2, balance: 10000, annualIncome: 1000 }],
    retirementDate: '2026-01-01', endDate: '2030-01-01' });
  assert.equal(points[1].accounts[0].balance, 0);
  assert.ok(points[1].accounts[1].balance < 10000);
  for (const point of points) {
    near(point.balance, point.accounts.reduce((sum, account) => sum + account.balance, 0));
    near(point.annualIncome, point.accounts.reduce((sum, account) => sum + account.annualIncome, 0));
    if (point.retired) near(point.capitalFunding, point.accounts.reduce((sum, account) => sum + account.capitalFunding, 0));
  }
  assert.equal(points.at(-1).balance, 0);
});

test('DRIP stays in the account that earned the dividends before retirement', () => {
  const last = run({ balance: 20000, annualIncome: 1000, accounts: [
    { id: 1, balance: 10000, annualIncome: 0 }, { id: 2, balance: 10000, annualIncome: 1000 },
  ] }).at(-1);
  near(last.accounts[0].balance, 10000);
  assert.ok(last.accounts[1].balance > 10000);
  assert.ok(last.accounts[1].annualIncome > 1000);
});

test('estimated retirement balances replace calculated values and scale income at projected yield', () => {
  const accounts = [
    { id: 1, balance: 10000, annualIncome: 1000, retirementBalance: '20000' },
    { id: 2, balance: 10000, annualIncome: 0, retirementBalance: '30000' },
  ];
  const points = run({ balance: 20000, annualIncome: 1000, accounts, endDate: '2031-01-01', desiredIncome: 0 });
  const retired = points.find((point) => point.date === '2030-01-01');
  near(retired.balance, 50000);
  near(retired.annualIncome, 2000);
  near(retired.accounts[1].annualIncome, 0);
  assert.ok(points.find((point) => point.date === '2029-12-01').balance < 50000);
  assert.ok(points.at(-1).balance > 50000);
});

test('blank estimates preserve projections and zero estimates empty the specified account', () => {
  const input = { accounts: [{ id: 1, balance: 100000, annualIncome: 4000, retirementBalance: '' }] };
  near(run(input).at(-1).balance, run().at(-1).balance);
  const zero = run({ accounts: [{ ...input.accounts[0], retirementBalance: 0 }] }).at(-1);
  assert.equal(zero.balance, 0);
  assert.equal(zero.annualIncome, 0);
});

test('estimates apply when retiring today, including to previously empty accounts', () => {
  const points = run({ retirementDate: '2026-01-01', endDate: '2027-01-01', desiredIncome: 0,
    accounts: [{ id: 1, balance: 100000, annualIncome: 4000, retirementBalance: 200000 },
      { id: 2, balance: 0, annualIncome: 0, retirementBalance: 50000 }] });
  near(points[0].balance, 250000);
  near(points[0].annualIncome, 8000);
  assert.ok(points.at(-1).balance > 250000);
  near(points.at(-1).accounts[1].balance, 50000);
});

test('rejects negative and nonnumeric balance estimates', () => {
  for (const retirementBalance of [-1, 'invalid']) assert.throws(() => run({ accounts: [
    { id: 1, balance: 100000, annualIncome: 4000, retirementBalance },
  ] }));
});
