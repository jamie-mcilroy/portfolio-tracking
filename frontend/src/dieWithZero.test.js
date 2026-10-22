import assert from 'node:assert/strict';
import test from 'node:test';
import { projectDieWithZero } from './dieWithZero.js';
import { projectDrawdown } from './drawdown.js';

const base = { startDate: '2026-01-01', retirementAge: 53, endAge: 63, priceGrowth: 0,
  accounts: [{ id: 1, name: 'West Bragg', balance: 100000, cash: 100000, annualIncome: 0, westBragg: true }] };
const near = (actual, expected, tolerance = .01) => assert.ok(Math.abs(actual - expected) < tolerance, `${actual} != ${expected}`);
function checkZero(result) {
  near(result.rows.at(-1).ending, 0);
  near(result.rows.reduce((sum, row) => sum + row.shortfall, 0), 0);
  assert.equal(result.firstShortfall, null);
  assert.ok(result.rows.every((row) => row.ending >= 0));
  for (const row of result.rows) near(row.opening + row.investmentGain - row.westWithdrawal - row.rspWithdrawal, row.ending);
}

test('flat cash solves the prorated duration, with no stock growth on cash', () => {
  const result = projectDieWithZero(base);
  const duration = 9 + (Date.parse('2035-12-02') - Date.parse('2035-01-01')) / (365 * 86400000);
  near(result.annualWithdrawal, 100000 / duration);
  checkZero(result);
  near(projectDieWithZero({ ...base, priceGrowth: 10 }).annualWithdrawal, result.annualWithdrawal);
});

test('growth, dividends, deferred retirement and negative returns all solve to zero', () => {
  const accounts = [
    { id: 1, name: 'West Bragg', westBragg: true, balance: 200000, cash: 50000, annualIncome: 6000 },
    { id: 2, name: 'Jamie RSP', westBragg: false, balance: 500000, cash: 0, annualIncome: 20000 },
  ];
  const original = structuredClone(accounts);
  for (const priceGrowth of [-10, 0, 4]) {
    const inputs = { ...base, accounts, retirementAge: 60, endAge: 90, priceGrowth };
    const result = projectDieWithZero(inputs);
    checkZero(result);
    assert.ok(result.annualWithdrawal > 0);
    assert.equal(result.retirementDate, '2032-12-02');
    near(result.rows[0].westWithdrawal + result.rows[0].rspWithdrawal, 0);
    // The solved rate also works when entered into the existing Drawdown tab.
    const replay = projectDrawdown({ ...inputs, annualWithdrawal: result.annualWithdrawal });
    near(replay.rows.at(-1).ending, 0);
    const tooMuch = projectDrawdown({ ...inputs, annualWithdrawal: result.annualWithdrawal * 1.01 });
    assert.ok(tooMuch.firstShortfall);
  }
  assert.deepEqual(accounts, original);
});

test('more growth allows more income; a longer retirement reduces it', () => {
  const inputs = { ...base, accounts: [{ ...base.accounts[0], cash: 0 }] };
  assert.ok(projectDieWithZero({ ...inputs, priceGrowth: 4 }).annualWithdrawal > projectDieWithZero(inputs).annualWithdrawal);
  assert.ok(projectDieWithZero({ ...inputs, endAge: 80 }).annualWithdrawal < projectDieWithZero(inputs).annualWithdrawal);
});

test('zero balances return zero income, while invalid inputs fail clearly', () => {
  const empty = projectDieWithZero({ ...base, accounts: [{ ...base.accounts[0], balance: 0, cash: 0 }] });
  near(empty.annualWithdrawal, 0);
  checkZero(empty);
  for (const overrides of [{ accounts: [] }, { endAge: 53 }, { retirementAge: 52 }, { priceGrowth: NaN }, { priceGrowth: -100 }]) {
    assert.throws(() => projectDieWithZero({ ...base, ...overrides }));
  }
});
