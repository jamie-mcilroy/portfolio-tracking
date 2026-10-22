export const categories = { property: 'Property', vehicle: 'Vehicles', art: 'Art', jewellery: 'Jewellery', other: 'Other assets', debt: 'Other debts' };

export function netWorthTotals(accounts, items) {
  const investments = accounts.reduce((sum, account) => sum + Number(account.current_total_value ?? account.total_closing_value ?? 0), 0);
  const breakdown = Object.keys(categories).map((category) => {
    const selected = items.filter((item) => item.category === category);
    const value = selected.reduce((sum, item) => sum + item.value, 0);
    const debt = category === 'debt' ? value : selected.reduce((sum, item) => sum + item.mortgage, 0);
    return { category, value: category === 'debt' ? 0 : value, debt, net: category === 'debt' ? -value : value - debt };
  });
  const assets = investments + breakdown.reduce((sum, row) => sum + row.value, 0);
  const debts = breakdown.reduce((sum, row) => sum + row.debt, 0);
  return { investments, assets, debts, netWorth: assets - debts, breakdown };
}
