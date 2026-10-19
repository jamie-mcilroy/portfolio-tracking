import assert from 'node:assert/strict';
import test from 'node:test';
import { projectHoldingIncome, aggregateHoldingIncomeRows } from './holdingIncomeProjection.js';
const holding = { quantity: 100, current_price: 100, annual_forward_dividend: 12,
  five_year_dividend_growth_pct: 0, payments_per_year: 12 };
const options = { asOf: '2026-01-01', years: 2, priceGrowth: 0 };
const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-7, `${actual} != ${expected}`);

test('all monthly dividends buy fractional shares, and new shares earn subsequent dividends', () => {
  const result = projectHoldingIncome(holding, options);
  near(result.projections[0].shares, 100 * 1.01 ** 12);
  near(result.projections[1].shares, 100 * 1.01 ** 24);
  near(result.projections[0].income, result.projections[0].shares * 12);
  near(result.projections[0].increase, result.projections[0].income - 1200);
  near(result.projections[1].increase, result.projections[1].income - result.projections[0].income);
});

test('historical dividend growth and rising DRIP prices apply separately', () => {
  const result = projectHoldingIncome({ ...holding, five_year_dividend_growth_pct: 5, payments_per_year: 1 }, { ...options, priceGrowth: 4 });
  const elapsed = 365 / 365.25;
  const price = 100 * 1.04 ** elapsed;
  const dividend = 12 * 1.05 ** elapsed;
  near(result.projections[0].price, price);
  near(result.projections[0].annualDividend, dividend);
  near(result.projections[0].shares, 100 + 100 * dividend / price);
  near(result.projections[0].income, result.projections[0].shares * dividend);
});

test('first January uses only payments after today; higher prices buy fewer DRIP shares', () => {
  const partial = projectHoldingIncome(holding, { ...options, asOf: '2026-10-18' });
  near(partial.projections[0].shares, 100 * 1.01 ** 2);
  const higherPrice = projectHoldingIncome(holding, { ...options, priceGrowth: 4 });
  assert.ok(higherPrice.projections[0].shares < projectHoldingIncome(holding, options).projections[0].shares);
});

test('missing dividend growth is held flat explicitly; dividend cuts are retained', () => {
  const unknown = projectHoldingIncome({ ...holding, five_year_dividend_growth_pct: null }, options);
  assert.equal(unknown.historicGrowth, null);
  assert.ok(unknown.notes.some((note) => note.includes('held flat')));
  near(unknown.projections[0].annualDividend, 12);
  const cut = projectHoldingIncome({ ...holding, five_year_dividend_growth_pct: -50 }, options);
  assert.ok(cut.projections[0].increase < 0);
});

test('cash and non-dividend holdings do not invent dividends or DRIP shares', () => {
  const cash = projectHoldingIncome({ ...holding, asset_type: 'Cash', annual_forward_dividend: 0 }, { ...options, priceGrowth: 4 });
  near(cash.projections[1].shares, 100);
  near(cash.projections[1].price, 100);
  near(cash.projections[1].income, 0);
  const nonPayer = projectHoldingIncome({ ...holding, annual_forward_dividend: 0 }, { ...options, priceGrowth: 4 });
  near(nonPayer.projections[1].shares, 100);
  assert.ok(nonPayer.projections[1].price > 100);
  near(nonPayer.projections[1].income, 0);
});

test('missing data stays unavailable and fallback frequency is labelled', () => {
  const unknown = projectHoldingIncome({ ...holding, annual_forward_dividend: null }, options);
  assert.equal(unknown.currentIncome, null);
  assert.equal(unknown.projections[0].income, null);
  const noPrice = projectHoldingIncome({ ...holding, current_price: null }, options);
  assert.equal(noPrice.projections[0].income, null);
  const noFrequency = projectHoldingIncome({ ...holding, payments_per_year: 0 }, options);
  assert.ok(noFrequency.notes.some((note) => note.includes('monthly')));
  assert.equal(noFrequency.frequency, 12);
});

test('validates inputs and does not mutate holdings', () => {
  const copy = structuredClone(holding);
  projectHoldingIncome(holding, options);
  assert.deepEqual(holding, copy);
  for (const input of [{ years: 0 }, { years: 31 }, { years: 2.5 }, { priceGrowth: -100 }, { priceGrowth: NaN }, { asOf: '' }])
    assert.throws(() => projectHoldingIncome(holding, { ...options, ...input }));
});


test('aggregates duplicate symbols across accounts without rounding DRIP calculations', () => {
  const positions = [
    { ...holding, account_id: 1, symbol: 'TEST', market: 'CDN', quantity: 100.25 },
    { ...holding, account_id: 2, symbol: 'TEST', market: 'CDN', quantity: 200.5 },
  ];
  const original = positions.map((holding) => ({ holding, ...projectHoldingIncome(holding, options) }));
  const rows = aggregateHoldingIncomeRows(original);
  assert.equal(rows.length, 1);
  near(rows[0].quantity, 300.75);
  near(rows[0].price, 100);
  near(rows[0].annualDividend, 12);
  near(rows[0].currentIncome, 3609);
  for (let index = 0; index < 2; index += 1) {
    near(rows[0].projections[index].shares, original.reduce((sum, row) => sum + row.projections[index].shares, 0));
    near(rows[0].projections[index].income, original.reduce((sum, row) => sum + row.projections[index].income, 0));
  }
  const otherMarket = { ...positions[0], market: 'US' };
  assert.equal(aggregateHoldingIncomeRows([...original, { holding: otherMarket, ...projectHoldingIncome(otherMarket, options) }]).length, 2);
});

test('SGOV ignores historical payout growth and global price growth while retaining DRIP', () => {
  const sgov = { ...holding, symbol: 'SGOV', current_price: 100, annual_forward_dividend: 4,
    five_year_dividend_growth_pct: 150 };
  const result = projectHoldingIncome(sgov, { ...options, priceGrowth: 4 });
  assert.equal(result.appliedDividendGrowth, 0);
  assert.ok(result.notes.some((note) => note.includes('not extrapolated')));
  for (const point of result.projections) {
    near(point.price, 100);
    near(point.annualDividend, 4);
    near(point.income, point.shares * 4);
  }
  near(result.projections[0].shares, 100 * (1 + .04 / 12) ** 12);
});

test('SGOV assumptions are editable and apply only to SGOV', () => {
  const inputs = { ...options, sgovPriceGrowth: 1, sgovDividendGrowth: 2 };
  const sgov = projectHoldingIncome({ ...holding, symbol: 'sgov', five_year_dividend_growth_pct: 150 }, inputs);
  assert.equal(sgov.appliedDividendGrowth, 2);
  assert.ok(sgov.projections[0].price > 100);
  assert.ok(sgov.projections[0].annualDividend > 12);
  const ordinary = projectHoldingIncome({ ...holding, symbol: 'TEST', five_year_dividend_growth_pct: 5 }, inputs);
  assert.equal(ordinary.appliedDividendGrowth, 5);
  assert.throws(() => projectHoldingIncome({ ...holding, symbol: 'SGOV' }, { ...inputs, sgovDividendGrowth: -100 }));
});
