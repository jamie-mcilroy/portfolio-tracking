export function retirementDateForAge(age) {
  if (age === "" || !Number.isInteger(Number(age)) || Number(age) < 0 || Number(age) > 120) return null;
  return `${1972 + Number(age)}-12-02`;
}

export function buildRspSummary(accounts, holdings) {
  const ids = new Set(accounts.map((account) => String(account.id)));
  const names = new Set(accounts.map((account) => account.name));
  const balance = accounts.reduce((sum, account) =>
    sum + Number(account.current_total_value ?? account.total_closing_value ?? 0), 0);
  const annualIncome = holdings.reduce((sum, holding) =>
    sum + (ids.has(String(holding.account_id)) || names.has(holding.account_name)
      ? Number(holding.annual_forward_income || 0) : 0), 0);
  return { balance, annualIncome };
}

export function orderLiquidationAccounts(accounts, preferredIds = []) {
  const ranks = new Map(preferredIds.map((id, index) => [String(id), index]));
  return [...accounts].sort((left, right) => {
    const rankDifference = (ranks.get(String(left.id)) ?? Infinity) - (ranks.get(String(right.id)) ?? Infinity);
    if (!Number.isNaN(rankDifference) && rankDifference !== 0) return rankDifference;
    const corporate = (account) => String(account.account_entity).toLowerCase() === 'corporate';
    return Number(corporate(right)) - Number(corporate(left))
      || Number(left.annualIncome > 0) - Number(right.annualIncome > 0);
  });
}
