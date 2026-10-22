import assert from 'node:assert/strict';
import test from 'node:test';
import { netWorthTotals } from './netWorth.js';

test('property equity and overall net worth subtract mortgages exactly once', () => {
  const result = netWorthTotals([{ current_total_value: 500000, total_closing_value: 400000 }], [
    { category: 'property', value: 1000000, mortgage: 300000 },
    { category: 'vehicle', value: 30000, mortgage: 0 },
    { category: 'art', value: 5000, mortgage: 0 },
    { category: 'jewellery', value: 10000, mortgage: 0 },
    { category: 'debt', value: 20000, mortgage: 0 },
  ]);
  assert.equal(result.investments, 500000);
  assert.equal(result.assets, 1545000);
  assert.equal(result.debts, 320000);
  assert.equal(result.netWorth, 1225000);
  assert.equal(result.breakdown.find((row) => row.category === 'property').net, 700000);
});

test('mortgage-free properties and negative equity retain their full contribution', () => {
  const result = netWorthTotals([{ total_closing_value: 100 }, {}], [
    { category: 'property', value: 100000, mortgage: 0 },
    { category: 'property', value: 200000, mortgage: 250000 },
  ]);
  assert.equal(result.netWorth, 50100);
  assert.equal(netWorthTotals([], []).netWorth, 0);
});
