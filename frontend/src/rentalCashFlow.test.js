import assert from 'node:assert/strict';
import test from 'node:test';
import { modelRental, solveRentalPrice, monthlyCondoFeePerSquareFoot } from './rentalCashFlow.js';
const input = { price: 270000, rent: 1800, downPaymentPct: 20, interestRate: 5, amortizationYears: 25,
  condoFee: 480, annualTax: 2061, insurance: 50, utilities: 0, maintenancePct: 5,
  managementPct: 0, vacancyPct: 5, otherCosts: 0, closingCosts: 0 };
const run = (overrides = {}) => modelRental({ ...input, ...overrides });
const near = (actual, expected, tolerance = 1e-7) => assert.ok(Math.abs(actual - expected) < tolerance, `${actual} != ${expected}`);

test('Canadian semi-annual mortgage convention gives the expected monthly payment', () => {
  const result = run({ price: 375000 });
  near(result.mortgage, 300000);
  near(result.mortgagePayment, 1744.81, .01);
});

test('cash purchases require no mortgage and use rent less vacancy and all expenses', () => {
  const result = run({ downPaymentPct: 100 });
  near(result.mortgagePayment, 0);
  near(result.monthlyTax, 171.75);
  near(result.vacancy, 90);
  near(result.maintenance, 85.5);
  near(result.cashFlow, 922.75);
  near(result.annualCashFlow, 11073);
});

test('break-even rent produces zero cash flow, including vacancy and variable expenses', () => {
  const first = run({ managementPct: 8, utilities: 100, otherCosts: 50 });
  near(run({ rent: first.breakEvenRent, managementPct: 8, utilities: 100, otherCosts: 50 }).cashFlow, 0);
  assert.ok(run({ rent: first.breakEvenRent + 100, managementPct: 8, utilities: 100, otherCosts: 50 }).cashFlow > 0);
  assert.ok(run({ rent: first.breakEvenRent - 100, managementPct: 8, utilities: 100, otherCosts: 50 }).cashFlow < 0);
});

test('zero-interest loans amortize correctly and closing costs affect only initial cash', () => {
  near(run({ interestRate: 0 }).mortgagePayment, 216000 / 300);
  const result = run({ closingCosts: 3000 });
  near(result.initialCash, 57000);
  near(result.cashFlow, run().cashFlow);
});

test('management and maintenance use collected rent and principal repayment reduces cash flow', () => {
  const result = run({ managementPct: 10 });
  near(result.management, 171);
  near(result.cashFlow, result.netOperatingIncome - result.mortgagePayment);
});

test('missing expenses and invalid assumptions never produce misleading results', () => {
  for (const overrides of [{ annualTax: NaN }, { condoFee: NaN }, { rent: -1 }, { downPaymentPct: 101 },
    { vacancyPct: 100 }, { maintenancePct: 90, managementPct: 10 }, { amortizationYears: 0 },
    { amortizationYears: 25.5 }, { price: 0 }, { interestRate: Infinity }]) assert.throws(() => run(overrides));
});

test('annual cash return uses the down payment; closing costs reduce return on total cash', () => {
  const result = run({ rent: 3000, closingCosts: 4000 });
  near(result.downPaymentReturnPct, result.annualCashFlow / 54000 * 100);
  near(result.cashOnCashReturnPct, result.annualCashFlow / 58000 * 100);
  assert.ok(result.cashOnCashReturnPct < result.downPaymentReturnPct);
  assert.ok(run().downPaymentReturnPct < 0);
  near(run({ rent: run().breakEvenRent }).downPaymentReturnPct, 0);
});

test('cash returns handle zero down payment and all-cash purchase without dividing by zero', () => {
  const zeroDown = run({ downPaymentPct: 0 });
  assert.equal(zeroDown.downPaymentReturnPct, null);
  assert.equal(zeroDown.cashOnCashReturnPct, null);
  const costsOnly = run({ downPaymentPct: 0, closingCosts: 4000 });
  assert.equal(costsOnly.downPaymentReturnPct, null);
  near(costsOnly.cashOnCashReturnPct, costsOnly.annualCashFlow / 4000 * 100);
  const cash = run({ downPaymentPct: 100 });
  near(cash.downPaymentReturnPct, cash.annualCashFlow / 270000 * 100);
});


test('monthly condo fee per square foot updates with the fee and handles unknown area', () => {
  near(monthlyCondoFeePerSquareFoot(480, 600), .8);
  near(monthlyCondoFeePerSquareFoot('600', 600), 1);
  near(monthlyCondoFeePerSquareFoot(0, 600), 0);
  for (const [fee, area] of [[null, 600], ['', 600], [480, null], [480, 0], [480, -1], [NaN, 600]])
    assert.equal(monthlyCondoFeePerSquareFoot(fee, area), null);
});


const solve = (overrides = {}) => solveRentalPrice({ ...input, cashDownPayment: 54000, targetCashFlow: 0, ...overrides });

test('reverse pricing reproduces the forward model with a fixed dollar down payment', () => {
  for (const targetCashFlow of [0, 200, 500]) {
    const result = solve({ targetCashFlow, closingCosts: 4000 });
    near(result.downPayment, 54000);
    near(result.initialCash, 58000);
    assert.ok(result.cashFlow >= targetCashFlow - 1e-8);
    near(result.cashFlow, targetCashFlow, .0001);
    const higherPrice = result.price + .01;
    assert.ok(run({ price: higherPrice, downPaymentPct: 54000 / higherPrice * 100, closingCosts: 4000 }).cashFlow < targetCashFlow);
  }
});

test('price ceiling responds to rent, fixed down payment, expenses, rates and surplus goal', () => {
  const base = solve();
  assert.ok(solve({ rent: 2000 }).price > base.price);
  near(solve({ cashDownPayment: 64000 }).price - base.price, 10000, .011);
  assert.ok(solve({ condoFee: 600 }).price < base.price);
  assert.ok(solve({ interestRate: 6 }).price < base.price);
  assert.ok(solve({ targetCashFlow: 200 }).price < base.price);
  near(solve({ closingCosts: 10000 }).price, base.price);
});

test('reverse pricing supports zero-interest mortgages, zero down, and an all-cash boundary', () => {
  const zeroRate = solve({ interestRate: 0 });
  near(zeroRate.price, 54000 + run({ downPaymentPct: 100 }).netOperatingIncome * 300, .011);
  near(solve({ cashDownPayment: 0 }).downPayment, 0);
  const cash = solve({ targetCashFlow: run({ downPaymentPct: 100 }).netOperatingIncome });
  near(cash.price, 54000);
  near(cash.mortgagePayment, 0);
});

test('reverse pricing rejects unattainable goals and missing or invalid required inputs', () => {
  for (const overrides of [{ rent: 100 }, { targetCashFlow: 1000 }, { cashDownPayment: -1 },
    { targetCashFlow: NaN }, { cashDownPayment: NaN }, { condoFee: NaN }, { annualTax: NaN },
    { amortizationYears: 0 }, { vacancyPct: 100 }, { interestRate: Infinity },
    { cashDownPayment: 0, targetCashFlow: run({ downPaymentPct: 100 }).netOperatingIncome }])
    assert.throws(() => solve(overrides));
  // An asking price and percentage down payment are not required in reverse mode.
  assert.ok(solve({ price: NaN, downPaymentPct: NaN }).price > 0);
});
