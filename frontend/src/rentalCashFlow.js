// Fixed-rate Canadian mortgage: semi-annual compounding, monthly payments.
// https://www.bmo.com/en-ca/popups/main/personal/mortgages/terms-and-conditions-en/
export function modelRental(input) {
  const keys = ['price', 'rent', 'downPaymentPct', 'interestRate', 'amortizationYears',
    'condoFee', 'annualTax', 'insurance', 'utilities', 'maintenancePct', 'managementPct',
    'vacancyPct', 'otherCosts', 'closingCosts'];
  if (keys.some((key) => !Number.isFinite(input[key]) || input[key] < 0))
    throw new Error('Complete the rent, financing and expense inputs with zero or positive numbers.');
  const { price, rent, downPaymentPct, interestRate, amortizationYears, condoFee, annualTax,
    insurance, utilities, maintenancePct, managementPct, vacancyPct, otherCosts, closingCosts } = input;
  if (price <= 0 || downPaymentPct > 100 || interestRate > 100 || !Number.isInteger(amortizationYears)
    || amortizationYears < 1 || amortizationYears > 50)
    throw new Error('Use a positive purchase price, a down payment of 0–100%, an interest rate of 0–100%, and 1–50 whole amortization years.');
  if (vacancyPct >= 100 || maintenancePct + managementPct >= 100)
    throw new Error('Vacancy must be below 100%, and maintenance plus management must total less than 100%.');
  const downPayment = price * downPaymentPct / 100;
  const mortgage = price - downPayment;
  const monthlyRate = Math.expm1(Math.log1p(interestRate / 200) / 6);
  const periods = amortizationYears * 12;
  const mortgagePayment = mortgage === 0 ? 0 : monthlyRate === 0 ? mortgage / periods
    : mortgage * monthlyRate / -Math.expm1(-periods * Math.log1p(monthlyRate));
  const vacancy = rent * vacancyPct / 100;
  const collectedRent = rent - vacancy;
  const maintenance = collectedRent * maintenancePct / 100;
  const management = collectedRent * managementPct / 100;
  const monthlyTax = annualTax / 12;
  const fixedOperating = condoFee + monthlyTax + insurance + utilities + otherCosts;
  const operatingCosts = fixedOperating + maintenance + management;
  const totalCosts = mortgagePayment + operatingCosts;
  const cashFlow = collectedRent - totalCosts;
  const annualCashFlow = cashFlow * 12;
  const initialCash = downPayment + closingCosts;
  const downPaymentReturnPct = downPayment > 0 ? annualCashFlow / downPayment * 100 : null;
  const cashOnCashReturnPct = initialCash > 0 ? annualCashFlow / initialCash * 100 : null;
  const breakEvenRent = (fixedOperating + mortgagePayment) / ((1 - vacancyPct / 100) * (1 - (maintenancePct + managementPct) / 100));
  return { downPayment, mortgage, mortgagePayment, vacancy, collectedRent, maintenance, management,
    monthlyTax, operatingCosts, totalCosts, cashFlow, annualCashFlow,
    breakEvenRent, initialCash, downPaymentReturnPct, cashOnCashReturnPct,
    netOperatingIncome: collectedRent - operatingCosts };
}
