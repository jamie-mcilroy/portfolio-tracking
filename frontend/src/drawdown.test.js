import assert from 'node:assert/strict';
import test from 'node:test';
import { ageOn, drawdownAccounts, projectDrawdown } from './drawdown.js';

const base = { startDate: '2026-01-01', retirementAge: 53, endAge: 55, annualWithdrawal: 12000, priceGrowth: 0,
  accounts: [
    { id: 1, name: 'Jamie RSP', westBragg: false, balance: 100000, cash: 0, annualIncome: 6000 },
    { id: 2, name: 'West Bragg', westBragg: true, balance: 20000, cash: 20000, annualIncome: 0 },
  ] };
const run = (overrides) => projectDrawdown({ ...base, ...overrides });
const near = (actual, expected, tolerance = .001) => assert.ok(Math.abs(actual - expected) < tolerance, `${actual} != ${expected}`);

test('includes only Jamie/Michelle RSP and named West Bragg accounts, using live balances and matching income', () => {
  const result = drawdownAccounts({ accounts: [
    { id: 1, name: 'Jamie RRSP', current_total_value: 10, total_closing_value: 1 },
    { id: 2, name: 'Michelle RSP', total_closing_value: 20 },
    { id: 3, name: "WB Investor's Edge", current_total_value: 30 },
    { id: 4, name: 'WB IBRK', current_total_value: 40 },
    { id: 5, name: 'ATNV Capital Account', account_entity: 'Corporate' },
    { id: 6, name: 'Jamie TFSA' },
  ], holdings: [{ account_id: '1', annual_forward_income: 2 }, { account_id: 5, annual_forward_income: 999 }] });
  assert.deepEqual(result.map((a) => a.id), [4, 3, 1, 2]);
  assert.equal(result.find((a) => a.id === 1).balance, 10);
  assert.equal(result.find((a) => a.id === 1).annualIncome, 2);
});

test('West Bragg funds withdrawals first while RSP dividends keep reinvesting', () => {
  const { rows } = run();
  near(rows[0].westWithdrawal, 12000);
  near(rows[0].rspWithdrawal, 0);
  assert.ok(rows[0].drip > 6000);
  assert.ok(rows[0].accounts.find((a) => a.id === 1).balance > 106000);
  assert.ok(rows[1].rspWithdrawal > 0);
  near(rows[1].accounts.find((a) => a.id === 2).balance, 0);
});

test('dividends fund withdrawals without double counting; surplus buys fractional units', () => {
  const result = run({ annualWithdrawal: 0, accounts: [{ id: 1, balance: 100000, cash: 0, annualIncome: 12000, westBragg: true }] });
  near(result.rows[0].ending - 100000, result.rows[0].dividends);
  near(result.rows[0].drip, result.rows[0].dividends);
  const spending = run({ annualWithdrawal: 12000, accounts: [{ id: 1, balance: 100000, cash: 0, annualIncome: 12000, westBragg: true }] });
  near(spending.rows[0].westWithdrawal, 12000);
  near(spending.rows[0].ending, 100000 + spending.rows[0].dividends - 12000);
});

test('cash does not grow and price increases buy fewer units for subsequent dividends', () => {
  const accounts = [{ id: 1, balance: 100000, cash: 0, annualIncome: 10000 }];
  const flat = run({ annualWithdrawal: 0, accounts });
  const growing = run({ annualWithdrawal: 0, accounts, priceGrowth: 4 });
  assert.ok(growing.rows[1].dividends < flat.rows[1].dividends);
  assert.ok(growing.rows[1].ending > flat.rows[1].ending);
  near(run({ annualWithdrawal: 0, priceGrowth: 4, accounts: [{ id: 1, balance: 100000, cash: 100000, annualIncome: 0 }] }).rows.at(-1).ending, 100000);
});

test('retirement birthday splits periods, spending stops at end birthday, and years reconcile', () => {
  const result = run({ retirementAge: 54 });
  assert.equal(result.retirementDate, '2026-12-02');
  near(result.rows[0].westWithdrawal, 12000 * 30 / 365);
  assert.equal(result.rows.at(-1).date, '2027-12-02');
  assert.equal(result.endDate, '2027-12-02');
  for (const row of result.rows) {
    near(row.ending, row.opening + row.dividends - row.westWithdrawal - row.rspWithdrawal);
    assert.ok(row.accounts.every((a) => a.balance >= 0));
  }
  assert.equal(ageOn('2026-10-20'), 53);
  assert.equal(ageOn('2026-12-02'), 54);
});

test('depleted accounts report unfunded withdrawals and never become negative', () => {
  const result = run({ accounts: [{ id: 1, balance: 100, cash: 100, annualIncome: 0, westBragg: true }] });
  assert.ok(result.firstShortfall);
  near(result.rows[0].ending, 0);
  near(result.rows[0].shortfall, 11900);
});

test('sales reduce subsequent dividends and withdrawals wait until retirement', () => {
  const accounts = [{ id: 1, balance: 100000, cash: 0, annualIncome: 10000, westBragg: true }];
  const spending = run({ accounts, annualWithdrawal: 50000 });
  assert.ok(spending.rows[1].dividends < spending.rows[0].dividends);
  const deferred = run({ accounts, retirementAge: 55, endAge: 57 });
  near(deferred.rows[0].westWithdrawal, 0);
  near(deferred.rows[0].drip, deferred.rows[0].dividends);
  assert.ok(deferred.rows[1].westWithdrawal > 0);
});

test('validates inputs and leaves source accounts intact', () => {
  const original = structuredClone(base.accounts);
  run();
  assert.deepEqual(base.accounts, original);
  for (const overrides of [{ retirementAge: 52 }, { retirementAge: 53.5 }, { endAge: 53 }, { endAge: 121 },
    { annualWithdrawal: NaN }, { annualWithdrawal: -1 }, { priceGrowth: -100 }, { accounts: [] }]) {
    assert.throws(() => run(overrides));
  }
});

test('ROI combines price gains and reinvested dividends without counting DRIP twice', () => {
  const accounts = [{ id: 1, balance: 100000, cash: 0, annualIncome: 5000, westBragg: true }];
  const row = run({ accounts, priceGrowth: 2, annualWithdrawal: 0 }).rows[0];
  near(row.investmentGain, row.ending - row.opening);
  near(row.roi, (row.ending - row.opening) / row.opening);
  assert.ok(row.roi > .07 && row.roi < .073);
});

test('ROI adds funded withdrawals back and does not treat shortfalls as gains', () => {
  const flat = run({ annualWithdrawal: 50000, accounts: [{ id: 1, balance: 100000, cash: 0, annualIncome: 0 }] });
  near(flat.rows[0].roi, 0);
  const depleted = run({ accounts: [{ id: 1, balance: 100, cash: 100, annualIncome: 0 }] });
  near(depleted.rows[0].roi, 0);
  assert.equal(depleted.rows[1].roi, null);
  const dividends = run({ accounts: [{ id: 1, balance: 100000, cash: 0, annualIncome: 10000 }], annualWithdrawal: 50000 }).rows[0];
  near(dividends.investmentGain, dividends.dividends);
  near(dividends.roi, dividends.dividends / dividends.opening);
});

test('ROI reports negative returns and actual partial-year gains', () => {
  const accounts = [{ id: 1, balance: 100000, cash: 0, annualIncome: 0 }];
  const loss = run({ accounts, annualWithdrawal: 0, priceGrowth: -10 }).rows[0];
  near(loss.roi, .9 ** (365 / 365.25) - 1);
  const partial = run({ accounts, startDate: '2026-10-20', priceGrowth: 2, annualWithdrawal: 0 }).rows[0];
  const years = (Date.parse('2027-01-01') - Date.parse('2026-10-20')) / (365.25 * 86400000);
  near(partial.roi, 1.02 ** years - 1);
});
