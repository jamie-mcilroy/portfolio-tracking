import assert from 'node:assert/strict';
import test from 'node:test';
import { buildRspSummary, retirementDateForAge } from './retirementSummary.js';

const accounts = [
  { id: 1, name: 'Jamie RRSP', current_total_value: 1200, total_closing_value: 1000 },
  { id: 2, name: 'Michelle RRSP', total_closing_value: 800 },
];
test('birth date maps age 60 to December 2, 2032 and validates whole ages', () => {
  assert.equal(retirementDateForAge(60), '2032-12-02');
  assert.equal(retirementDateForAge('65'), '2037-12-02');
  for (const age of ['', 'invalid', -1, 60.5, 121]) assert.equal(retirementDateForAge(age), null);
});

test('sums current RSP balances and dividend income, excluding other holdings', () => {
  const result = buildRspSummary(accounts, [
    { account_id: '1', annual_forward_income: 100 },
    { account_name: 'Michelle RRSP', annual_forward_income: 50 },
    { account_id: 3, account_name: 'WB IBRK', annual_forward_income: 999 },
  ], []);
  assert.equal(result.balance, 2000);
  assert.equal(result.annualIncome, 150);
});


test('liquidation defaults to corporate accounts first and respects a user-defined order', async () => {
  const { orderLiquidationAccounts } = await import('./retirementSummary.js');
  const accounts = [
    { id: 1, account_entity: 'Personal', annualIncome: 1000 },
    { id: 2, account_entity: 'Corporate', annualIncome: 0 },
    { id: 3, account_entity: 'Corporate', annualIncome: 0 },
    { id: 4, account_entity: 'Personal', annualIncome: 2000 },
  ];
  assert.deepEqual(orderLiquidationAccounts(accounts).map((account) => account.id), [2, 3, 1, 4]);
  assert.deepEqual(orderLiquidationAccounts(accounts, ['4', '3', '1', '2']).map((account) => account.id), [4, 3, 1, 2]);
  assert.equal(accounts[0].id, 1);
});
