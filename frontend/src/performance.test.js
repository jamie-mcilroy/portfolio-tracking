import assert from "node:assert/strict";
import test from "node:test";
import { buildPortfolioPerformanceSeries, isRetirementAccount } from "./performance.js";

test("retirement includes the two RRSPs and WBFC corporate accounts only", () => {
  const accounts = [
    { id: 1, name: "Jamie RRSP", account_entity: "Personal" },
    { id: 2, name: "Michelle RRSP", account_entity: "Personal" },
    { id: 3, name: "WB Investor's Edge", account_entity: "Corporate" },
    { id: 4, name: "WB IBRK", account_entity: "Corporate" },
    { id: 5, name: "ATNV Capital Account", account_entity: "Corporate" },
    { id: 6, name: "Jamie TFSA", account_entity: "Personal" },
    { id: 7, name: "Michelle Cash", account_entity: "Personal" },
    { id: 8, name: "RESP", account_entity: "Personal" },
  ];
  const selectedIds = accounts.filter(isRetirementAccount).map((account) => account.id);
  assert.deepEqual(selectedIds, [1, 2, 3, 4, 5]);
  const series = buildPortfolioPerformanceSeries([{
    market_date: "2026-10-16",
    accounts: accounts.map((account) => ({ account_id: account.id, value: account.id * 100 })),
  }], selectedIds);
  assert.equal(series[0].value, 1500);
  assert.equal(isRetirementAccount({ name: "  Michelle RSP  " }), true);
  assert.equal(isRetirementAccount({ name: "123 Jamie RRSP - Combined Holdings" }), true);
});

const snapshots = [
  { market_date: "2026-10-16", total_value: 9999, accounts: [
    { account_id: 1, value: 120 }, { account_id: 2, value: "250" },
  ] },
  { market_date: "2026-10-15", accounts: [
    { account_id: 1, value: 100 }, { account_id: 2, value: 200 },
  ] },
];

test("sums selected account balances in date order without changing history", () => {
  const original = structuredClone(snapshots);
  const series = buildPortfolioPerformanceSeries(snapshots, [1, 2]);
  assert.deepEqual(series.map(({ date, value }) => [date, value]), [
    ["2026-10-15", 300], ["2026-10-16", 370],
  ]);
  assert.deepEqual(snapshots, original);
});

test("removing and restoring accounts recalculates the full history", () => {
  assert.deepEqual(buildPortfolioPerformanceSeries(snapshots, ["2"]).map((point) => point.value), [200, 250]);
  assert.deepEqual(buildPortfolioPerformanceSeries(snapshots, [1]).map((point) => point.value), [100, 120]);
  assert.deepEqual(buildPortfolioPerformanceSeries(snapshots, []).map((point) => point.value), []);
  assert.deepEqual(buildPortfolioPerformanceSeries(snapshots, [1, 2]).map((point) => point.value), [300, 370]);
});

test("keeps zero and negative balances; skips unavailable balances and invalid dates", () => {
  const series = buildPortfolioPerformanceSeries([
    { market_date: "2026-10-15", accounts: [{ account_id: 1, value: 0 }] },
    { market_date: "2026-10-16", accounts: [{ account_id: 1, value: -10 }, { account_id: 2, value: null }] },
    { market_date: "2026-10-17", accounts: [{ account_id: 2, value: "invalid" }] },
    { market_date: "2026-10-18" },
    { market_date: "invalid", accounts: [{ account_id: 1, value: 100 }] },
  ], [1, 2]);
  assert.deepEqual(series.map((point) => point.value), [0, -10]);
  assert.deepEqual(buildPortfolioPerformanceSeries(snapshots, [3]), []);
});
