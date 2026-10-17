export function isRetirementAccount(account) {
  const name = String(account.name || "")
    .replace(/^\d+\s+/, "")
    .replace(/\s+-\s+Combined Holdings$/i, "")
    .trim()
    .toLowerCase();
  return ["jamie rrsp", "michelle rrsp", "michelle rsp"].includes(name)
    || String(account.account_entity || "").trim().toLowerCase() === "corporate";
}

export function buildPortfolioPerformanceSeries(snapshots, accountIds) {
  const selectedIds = new Set(accountIds.map(String));
  return snapshots
    .map((snapshot) => {
      const balances = (snapshot.accounts || []).filter(
        (account) => selectedIds.has(String(account.account_id))
          && account.value !== null
          && account.value !== undefined
          && Number.isFinite(Number(account.value)),
      );
      const time = Date.parse(`${snapshot.market_date}T00:00:00`);
      if (!balances.length || !Number.isFinite(time)) return null;
      return {
        date: snapshot.market_date,
        time,
        value: balances.reduce((total, account) => total + Number(account.value), 0),
      };
    })
    .filter(Boolean)
    .sort((left, right) => left.time - right.time);
}
