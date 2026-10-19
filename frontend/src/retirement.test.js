import assert from 'node:assert/strict';
import test from 'node:test';
import { projectRetirement } from './retirement.js';

const defaults = {
  startingBalance: 100000, startDate: '2026-01-01', retirementDate: '2026-01-01', endYear: 2027,
  annualReturn: 0, inflation: 0, monthlyContribution: 0, monthlySpending: 0,
  monthlyIncome: 0, taxRate: 0, oneTimeExpense: 0, expenseYear: 2027,
};
const run = (overrides = {}) => projectRetirement({ ...defaults, ...overrides });
const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 0.00001, `${actual} != ${expected}`);

test('compounds the effective annual return monthly and preserves the growth-only baseline', () => {
  const result = run({ annualReturn: 12 });
  near(result.points[12].balance, 112000);
  near(result.points[12].growthOnly, 112000);
  assert.equal(result.points[12].date, '2027-01-01');
});

test('contributions stop at retirement; withdrawals start at retirement', () => {
  const result = run({ retirementDate: '2026-03-01', monthlyContribution: 100, monthlySpending: 200 });
  near(result.points[2].balance, 100200);
  near(result.points[3].balance, 100000);
  near(result.points[3].growthOnly, 100200);
});

test('other income offsets after-tax spending before tax gross-up', () => {
  const result = run({ monthlySpending: 1000, monthlyIncome: 200, taxRate: 20 });
  near(result.points[1].balance, 99000);
  near(result.rows[0].withdrawals, 11000);
  near(result.rows[0].spending, 8800);
  near(run({ monthlySpending: 100, monthlyIncome: 200 }).points[1].balance, 100000);
});

test('inflation escalates spending and correctly discounts future values', () => {
  const result = run({ inflation: 12, monthlySpending: 100 });
  near(result.points[12].factor, 1.12);
  near(result.points[12].growthOnly / result.points[12].factor, 100000 / 1.12);
  assert.ok(result.rows[1].withdrawals > 1200);
});

test('one-time expense is charged once in its specified year, including before retirement', () => {
  const result = run({ oneTimeExpense: 10000, expenseYear: 2026, retirementDate: '2027-01-01' });
  near(result.points[1].balance, 90000);
  near(result.points.at(-1).balance, 90000);
  near(result.rows.reduce((total, row) => total + row.withdrawals, 0), 10000);
});

test('tracks depletion and unfunded spending without negative balances or repeated withdrawals', () => {
  const result = run({ startingBalance: 1500, monthlySpending: 1000 });
  assert.equal(result.depletionDate, '2026-03-01');
  assert.equal(result.shortfallDate, '2026-03-01');
  near(result.rows[0].withdrawals, 1500);
  near(result.rows[0].shortfall, 9500);
  assert.ok(result.points.every((point) => point.balance >= 0));
  near(result.points.at(-1).growthOnly, 1500);
});

test('negative returns work; annual rows reconcile with monthly balances', () => {
  const result = run({ annualReturn: -10, monthlySpending: 50, monthlyContribution: 100 });
  for (const row of result.rows) {
    near(row.ending, row.opening + row.growth + row.contributions - row.withdrawals);
  }
  near(run({ annualReturn: -10 }).points[12].balance, 90000);
});

test('zero balances and month-end starting dates remain valid', () => {
  const result = run({ startingBalance: 0, monthlySpending: 100, startDate: '2026-01-31', retirementDate: '2026-01-31' });
  assert.equal(result.depletionDate, '2026-01-31');
  assert.equal(result.shortfallDate, '2026-02-28');
  assert.equal(result.points[1].date, '2026-02-28');
  assert.equal(result.points[2].date, '2026-03-31');
  assert.equal(result.points.at(-1).date, '2027-12-31');
});

test('rejects invalid assumptions and caps the projection horizon', () => {
  for (const overrides of [{ taxRate: 100 }, { annualReturn: -100 }, { inflation: -1 },
    { monthlySpending: -1 }, { endYear: 2100 }, { endYear: NaN },
    { startDate: '' }, { retirementDate: '2025-01-01' }, { retirementDate: '2028-01-01' },
    { oneTimeExpense: 10, expenseYear: 2030 }]) {
    assert.throws(() => run(overrides));
  }
});
