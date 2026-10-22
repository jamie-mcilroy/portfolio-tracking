import { projectDrawdown } from './drawdown.js';

// Invert the existing monthly drawdown model, including its dividend reinvestment
// and account withdrawal order, rather than treating price growth as total return.
export function projectDieWithZero(inputs) {
  const project = (annualWithdrawal) => projectDrawdown({ ...inputs, annualWithdrawal });
  const unspent = project(0); // Validate inputs before attempting to solve.
  const residual = (result) => result.rows.at(-1).ending
    - result.rows.reduce((sum, row) => sum + row.shortfall, 0);
  if (residual(unspent) === 0) return { ...unspent, annualWithdrawal: 0 };
  let lower = 0;
  let upper = Math.max(1, residual(unspent));
  while (residual(project(upper)) > 0) {
    upper *= 2;
    if (!Number.isFinite(upper)) throw new Error('These assumptions produce an annual income too large to calculate.');
  }
  // Keep the funded side of the bracket: no early shortfall is hidden by
  // rounding or by forcing the final balance to zero.
  for (let iteration = 0; iteration < 70; iteration += 1) {
    const middle = lower + (upper - lower) / 2;
    if (residual(project(middle)) >= 0) lower = middle;
    else upper = middle;
  }
  return { ...project(lower), annualWithdrawal: lower };
}
