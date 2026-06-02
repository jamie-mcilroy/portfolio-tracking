import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import wbfcLogoUrl from "./assets/wbfc-logo.png";
import "./styles.css";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "";

const colors = [
  "#2764a5",
  "#227a55",
  "#a26921",
  "#7053a4",
  "#277d83",
  "#b24444",
  "#59656d",
  "#3d7a2b",
  "#9b4d7a",
  "#87612f",
];

const monthLabels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const performanceRanges = [
  ["5d", "5D"],
  ["30d", "30D"],
  ["1yr", "1Y"],
  ["ytd", "YTD"],
  ["max", "Max"],
];
const transactionTypes = [
  ["DIVIDEND", "Dividend"],
  ["DRIP", "DRIP"],
  ["BUY", "Buy"],
  ["SELL", "Sell"],
  ["DEPOSIT", "Deposit"],
  ["WITHDRAWAL", "Withdrawal"],
  ["FEE", "Fee"],
  ["TAX", "Tax"],
  ["INTEREST", "Interest"],
  ["FX", "FX"],
  ["TRANSFER_IN", "Transfer In"],
  ["TRANSFER_OUT", "Transfer Out"],
  ["ADJUSTMENT", "Adjustment"],
];
const dayMs = 24 * 60 * 60 * 1000;
const dividendIncomeForecastDates = [
  ["2032", "2032-12-02"],
  ["2037", "2037-12-02"],
];
const accountDisplayOrder = [
  ["jamie rrsp", 0],
  ["michelle rrsp", 1],
  ["michelle rsp", 1],
  ["resp", 2],
  ["jamie tfsa", 3],
  ["jamie tfsac", 3],
  ["michelle tfsa", 4],
  ["jamie cash", 5],
  ["michelle cash", 6],
];

const money = new Intl.NumberFormat("en-CA", {
  style: "currency",
  currency: "CAD",
  maximumFractionDigits: 2,
});

const wholeMoney = new Intl.NumberFormat("en-CA", {
  style: "currency",
  currency: "CAD",
  maximumFractionDigits: 0,
});

const number = new Intl.NumberFormat("en-CA", {
  maximumFractionDigits: 4,
});

class AuthError extends Error {}

function formatMoney(value) {
  return money.format(Number(value || 0));
}

function formatWholeMoney(value) {
  return wholeMoney.format(Number(value || 0));
}

function formatCurrency(value, currency = "CAD") {
  return new Intl.NumberFormat("en-CA", {
    style: "currency",
    currency: currency || "CAD",
    maximumFractionDigits: 2,
  }).format(Number(value || 0));
}

function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "n/a";
  }
  return `${Number(value).toFixed(2)}%`;
}

function formatDate(value) {
  if (!value) return "";
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return value;
  return new Intl.DateTimeFormat("en-CA", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(parsed));
}

function formatMarketDate(value) {
  if (!value) return "";
  const parsed = Date.parse(`${value}T00:00:00`);
  if (Number.isNaN(parsed)) return value;
  return new Intl.DateTimeFormat("en-CA", { dateStyle: "medium" }).format(new Date(parsed));
}

function todayInputValue() {
  return new Date().toISOString().slice(0, 10);
}

function dateOnlyTime(value) {
  if (!value) return null;
  const dateText = String(value).slice(0, 10);
  const parsed = Date.parse(`${dateText}T00:00:00`);
  return Number.isNaN(parsed) ? null : parsed;
}

function yearsBetweenDates(startDate, endDate) {
  const start = dateOnlyTime(startDate);
  const end = dateOnlyTime(endDate);
  if (start === null || end === null || end <= start) return null;
  return (end - start) / dayMs / 365.25;
}

function forecastDividendIncome(annualIncome, annualGrowthPct, startDate, targetDate) {
  const income = Number(annualIncome || 0);
  if (!Number.isFinite(income)) return null;
  if (income === 0) return 0;

  const growth = Number(annualGrowthPct);
  if (!Number.isFinite(growth) || growth <= -100) return null;

  const years = yearsBetweenDates(startDate, targetDate);
  if (years === null) return null;

  return income * (1 + growth / 100) ** years;
}

function forecastIncomeField(label) {
  return `forecast_income_${label}`;
}

function forecastHoldingDividendIncome(holding, targetDate) {
  return forecastDividendIncome(
    holding?.annual_forward_income,
    holding?.five_year_dividend_growth_pct,
    holding?.current_price_fetched_at || holding?.imported_at || todayInputValue(),
    targetDate,
  );
}

function formatForecastIncome(value) {
  return value === null || value === undefined || Number.isNaN(Number(value)) ? "n/a" : formatWholeMoney(value);
}

function transactionTypeLabel(value) {
  return transactionTypes.find(([type]) => type === value)?.[1] || String(value || "");
}

function tickerLabel(holding) {
  const symbol = String(holding?.symbol || "").trim().toUpperCase();
  const market = String(holding?.market || "").trim().toUpperCase();
  if (!symbol || symbol === "CASH") return symbol;
  if (["CDN", "CAN", "CA", "TSX", "TSXV"].includes(market)) return `TSE:${symbol}`;
  if (market === "US") return symbol;
  return market ? `${market}:${symbol}` : symbol;
}

function formatFx(value) {
  const fx = Number(value || 1);
  return Number.isInteger(fx) ? String(fx) : fx.toFixed(4);
}

function cashAmountForCurrency(account, currency) {
  const match = (account?.cash_balances || []).find((item) => String(item.currency || "").toUpperCase() === currency);
  return match ? Number(match.amount || 0) : 0;
}

function cashInputDefault(account, currency) {
  if (!account?.has_import) return "";
  const amount = cashAmountForCurrency(account, currency);
  return amount || "";
}

function formatCashBalances(account) {
  const balances = (account?.cash_balances || []).filter((item) => Number(item.amount || 0));
  if (!balances.length) {
    return formatMoney(account?.cash_balance || 0);
  }
  const native = balances
    .map((item) => `${item.currency} ${formatCurrency(item.amount, item.currency)}`)
    .join(" / ");
  if (balances.length > 1) {
    return `${native} (${formatMoney(account.cash_balance || 0)} CAD)`;
  }
  return native;
}

function shortAccountName(name) {
  return String(name || "")
    .replace(/^\d+\s+/, "")
    .replace(/\s+-\s+Combined Holdings$/i, "");
}

function accountEntity(account) {
  return String(account?.account_entity || "Personal").toLowerCase() === "corporate" ? "Corporate" : "Personal";
}

function accountDisplayRank(account) {
  const name = shortAccountName(account.name).toLowerCase();
  const match = accountDisplayOrder.find(([label]) => name === label);
  return match ? match[1] : 99;
}

function sortAccountsForDisplay(accounts) {
  return [...accounts].sort((left, right) => {
    const entityDiff = (accountEntity(left) === "Personal" ? 0 : 1) - (accountEntity(right) === "Personal" ? 0 : 1);
    if (entityDiff) return entityDiff;
    const rankDiff = accountDisplayRank(left) - accountDisplayRank(right);
    if (rankDiff) return rankDiff;
    return shortAccountName(left.name).localeCompare(shortAccountName(right.name));
  });
}

function isPrivateFundAccount(account) {
  return String(account?.account_type || "").trim().toLowerCase() === "private fund";
}

function canOpenStock(holding) {
  const symbol = String(holding?.symbol || "").trim().toUpperCase();
  const market = String(holding?.market || "").trim().toUpperCase();
  return Boolean(symbol && symbol !== "CASH" && !["PRIVATE", "MANUAL", "FUND", "PRIVATE FUND"].includes(market));
}

function canTradeHolding(holding) {
  const assetType = String(holding?.asset_type || "").trim().toLowerCase();
  return canOpenStock(holding) && assetType !== "private fund";
}

function incomeByAccountFromHoldings(holdings) {
  return (holdings || []).reduce((incomeByAccount, holding) => {
    const accountName = holding.account_name;
    if (!accountName) return incomeByAccount;
    incomeByAccount.set(
      accountName,
      (incomeByAccount.get(accountName) || 0) + Number(holding.annual_forward_income || 0)
    );
    return incomeByAccount;
  }, new Map());
}

function entityTotalsFromAccounts(accounts, incomeByAccount) {
  return accounts.reduce(
    (totals, account) => {
      const entity = accountEntity(account);
      const balance = Number(account.current_total_value ?? account.total_closing_value ?? 0);
      const dayChange = Number(account.day_change || 0);
      totals[entity].balance += balance;
      totals[entity].cash += Number(account.cash_balance || 0);
      totals[entity].income += Number(incomeByAccount.get(account.name) || 0);
      totals[entity].dayChange += dayChange;
      totals[entity].count += 1;
      return totals;
    },
    {
      Personal: { balance: 0, cash: 0, income: 0, dayChange: 0, count: 0 },
      Corporate: { balance: 0, cash: 0, income: 0, dayChange: 0, count: 0 },
    }
  );
}

function deriskForHolding(holding) {
  const quantity = Number(holding.quantity || 0);
  const currentPrice = Number((holding.current_price ?? holding.closing_price) || 0);
  const bookValue = Number(holding.book_value || 0);
  const currentValue = Number((holding.current_value ?? holding.closing_value) || 0);

  if (!quantity || !currentPrice || !bookValue) {
    return { status: "n/a", possible: false };
  }

  if (currentValue < bookValue) {
    return { status: "not_possible", possible: false };
  }

  const sharesToSell = Math.min(quantity, Math.ceil(bookValue / currentPrice));
  const proceeds = sharesToSell * currentPrice;
  const sharesLeft = Math.max(0, quantity - sharesToSell);
  const valueLeft = sharesLeft * currentPrice;
  const incomePerShare = Number(holding.annual_forward_income || 0) / quantity;
  const incomeLost = sharesToSell * incomePerShare;

  return {
    status: "ok",
    possible: true,
    sharesToSell,
    sellPct: (sharesToSell / quantity) * 100,
    proceeds,
    sharesLeft,
    valueLeft,
    incomeLost,
  };
}

function toneClass(value) {
  if (Number(value) > 0) return "positive";
  if (Number(value) < 0) return "negative";
  return "";
}

async function parseApiResponse(response) {
  const payload = await response.json().catch(() => ({}));
  if (response.status === 401) {
    throw new AuthError(payload.detail || "Authentication required.");
  }
  if (!response.ok) {
    throw new Error(payload.detail || payload.error || "Request failed.");
  }
  return payload;
}

function apiFetch(path, options = {}) {
  return fetch(`${API_BASE}${path}`, {
    credentials: "include",
    ...options,
    headers: {
      ...(options.headers || {}),
    },
  });
}

function App() {
  const [auth, setAuth] = useState({ status: "checking", username: null, isAdmin: false });
  const [data, setData] = useState(null);
  const [historyData, setHistoryData] = useState(null);
  const [usersData, setUsersData] = useState(null);
  const [transactionsData, setTransactionsData] = useState(null);
  const [privateFundData, setPrivateFundData] = useState({});
  const [activeView, setActiveView] = useState("summary");
  const [activeAccountId, setActiveAccountId] = useState(null);
  const [activeStock, setActiveStock] = useState(null);
  const [stockData, setStockData] = useState(null);
  const [stockLoading, setStockLoading] = useState(false);
  const [stockError, setStockError] = useState("");
  const [accountQuery, setAccountQuery] = useState("");
  const [sort, setSort] = useState({ key: "current_value", direction: "desc" });
  const [message, setMessage] = useState(null);
  const [fileLabel, setFileLabel] = useState("Choose CSV");
  const [historyFileLabel, setHistoryFileLabel] = useState("Choose CSV");
  const [editingAccount, setEditingAccount] = useState(null);
  const [tradeDraft, setTradeDraft] = useState(null);
  const [cashDraft, setCashDraft] = useState(null);

  async function loadSummary() {
    const response = await apiFetch("/api/summary");
    const payload = await parseApiResponse(response);
    setData(payload);
    if (
      activeView === "account" &&
      !(payload.all_accounts || payload.accounts).some((account) => account.id === activeAccountId)
    ) {
      setActiveView("summary");
      setActiveAccountId(null);
    }
  }

  async function loadHistory() {
    const response = await apiFetch("/api/balance-snapshots?limit=5000");
    const payload = await parseApiResponse(response);
    setHistoryData(payload);
  }

  async function loadUsers() {
    const response = await apiFetch("/api/users");
    const payload = await parseApiResponse(response);
    setUsersData(payload);
  }

  async function loadTransactions() {
    const response = await apiFetch("/api/transactions?limit=500");
    const payload = await parseApiResponse(response);
    setTransactionsData(payload);
  }

  async function loadPrivateFundMarks(accountId) {
    const response = await apiFetch(`/api/accounts/${accountId}/private-fund-marks`);
    const payload = await parseApiResponse(response);
    setPrivateFundData((current) => ({ ...current, [accountId]: payload }));
    return payload;
  }

  useEffect(() => {
    checkSession();
  }, []);

  useEffect(() => {
    if (auth.status !== "authenticated") {
      return undefined;
    }

    const timer = window.setInterval(() => {
      loadSummary().catch((error) => {
        if (error instanceof AuthError) {
          setAuth({ status: "anonymous", username: null, isAdmin: false });
        } else {
          showMessage(error.message, true);
        }
      });
    }, 60000);
    return () => window.clearInterval(timer);
  }, [auth.status, activeView, activeAccountId]);

  async function checkSession() {
    try {
      const response = await apiFetch("/api/me");
      const payload = await parseApiResponse(response);
      setAuth({ status: "authenticated", username: payload.username, isAdmin: Boolean(payload.is_admin) });
      await loadSummary();
    } catch (error) {
      if (error instanceof AuthError) {
        setAuth({ status: "anonymous", username: null, isAdmin: false });
        return;
      }
      setAuth({ status: "anonymous", username: null, isAdmin: false });
      showMessage(error.message, true);
    }
  }

  function showMessage(text, isError = false) {
    setMessage({ text, isError });
    window.clearTimeout(showMessage.timer);
    showMessage.timer = window.setTimeout(() => setMessage(null), 4500);
  }

  async function handleLogin(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector("button");
    const payload = Object.fromEntries(new FormData(form).entries());
    ["beginning_balance", "net_income", "withdrawal", "contribution", "ending_balance"].forEach((field) => {
      payload[field] = String(payload[field] || "").trim() === "" ? 0 : Number(payload[field]);
    });

    button.disabled = true;
    try {
      const response = await apiFetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await parseApiResponse(response);
      setAuth({ status: "authenticated", username: result.username, isAdmin: Boolean(result.is_admin) });
      form.reset();
      await loadSummary();
    } catch (error) {
      showMessage(error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  async function handleLogout() {
    await apiFetch("/api/logout", { method: "POST" }).catch(() => null);
    setData(null);
    setHistoryData(null);
    setUsersData(null);
    setTransactionsData(null);
    setAuth({ status: "anonymous", username: null, isAdmin: false });
  }

  function activateView(view, accountId = null) {
    setActiveView(view);
    setActiveAccountId(accountId ? Number(accountId) : null);
    if (view !== "stock") {
      setActiveStock(null);
      setStockError("");
    }
    if (view !== "account" && sort.key === "account_weight") {
      setSort({ key: "current_value", direction: "desc" });
    }
    if (view === "history" || (view === "account" && !historyData)) {
      loadHistory().catch((error) => {
        if (error instanceof AuthError) {
          setAuth({ status: "anonymous", username: null, isAdmin: false });
        } else {
          showMessage(error.message, true);
        }
      });
    }
    if (view === "users") {
      loadUsers().catch((error) => {
        if (error instanceof AuthError) {
          setAuth({ status: "anonymous", username: null, isAdmin: false });
        } else {
          showMessage(error.message, true);
        }
      });
    }
    if (view === "transactions") {
      loadTransactions().catch((error) => {
        if (error instanceof AuthError) {
          setAuth({ status: "anonymous", username: null, isAdmin: false });
        } else {
          showMessage(error.message, true);
        }
      });
    }
    if (view === "account" && accountId) {
      const account = (data?.all_accounts || data?.accounts || []).find((item) => Number(item.id) === Number(accountId));
      if (isPrivateFundAccount(account)) {
        loadPrivateFundMarks(accountId).catch((error) => {
          if (error instanceof AuthError) {
            setAuth({ status: "anonymous", username: null, isAdmin: false });
          } else {
            showMessage(error.message, true);
          }
        });
      }
    }
  }

  async function loadStock(stock, refresh = false) {
    if (!stock?.symbol) {
      return;
    }

    const params = new URLSearchParams();
    if (stock.market) params.set("market", stock.market);
    if (refresh) params.set("refresh", "true");
    setStockLoading(true);
    setStockError("");
    try {
      const response = await apiFetch(`/api/stocks/${encodeURIComponent(stock.symbol)}?${params.toString()}`);
      const payload = await parseApiResponse(response);
      setStockData(payload);
      if (payload.error) {
        setStockError(payload.error);
      }
    } catch (error) {
      if (error instanceof AuthError) {
        setAuth({ status: "anonymous", username: null, isAdmin: false });
      } else {
        setStockError(error.message);
      }
    } finally {
      setStockLoading(false);
    }
  }

  function openStock(holding) {
    if (!canOpenStock(holding)) {
      return;
    }
    const stock = {
      symbol: holding.symbol,
      market: holding.market || "",
    };
    setActiveStock(stock);
    setStockData(null);
    setActiveView("stock");
    setActiveAccountId(null);
    loadStock(stock);
  }

  function refreshActiveStock() {
    if (activeStock) {
      loadStock(activeStock, true);
    }
  }

  function changeSort(key) {
    setSort((current) => {
      if (current.key === key) {
        return { key, direction: current.direction === "asc" ? "desc" : "asc" };
      }
      return { key, direction: "desc" };
    });
  }

  async function handleImport(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const input = form.querySelector("input[type='file']");
    if (!input.files.length) {
      showMessage("Choose a CSV file first.", true);
      return;
    }

    const button = form.querySelector("button");
    button.disabled = true;
    try {
      const response = await apiFetch("/api/import", {
        method: "POST",
        body: new FormData(form),
      });
      const result = await parseApiResponse(response);
      const cashText = Number(result.cash_balance || 0) ? ` with ${formatMoney(result.cash_balance)} cash` : "";
      showMessage(result.message || `Imported ${result.row_count} holdings for ${result.account_name}${cashText}.`);
      form.reset();
      setFileLabel("Choose CSV");
      await loadSummary();
    } catch (error) {
      if (error instanceof AuthError) {
        setAuth({ status: "anonymous", username: null, isAdmin: false });
      }
      showMessage(error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  async function handleHistoryImport(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const input = form.querySelector("input[type='file']");
    if (!input.files.length) {
      showMessage("Choose a CSV file first.", true);
      return;
    }

    const button = form.querySelector("button");
    button.disabled = true;
    try {
      const response = await apiFetch("/api/history/import", {
        method: "POST",
        body: new FormData(form),
      });
      const result = await parseApiResponse(response);
      const unmatched = result.unmatched_accounts?.length ? ` ${result.unmatched_accounts.length} account labels did not match.` : "";
      showMessage(`${result.message || `Imported ${result.snapshot_count} history closes.`}${unmatched}`);
      form.reset();
      setHistoryFileLabel("Choose CSV");
      await loadHistory();
      await loadSummary();
    } catch (error) {
      if (error instanceof AuthError) {
        setAuth({ status: "anonymous", username: null, isAdmin: false });
      }
      showMessage(error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  async function handleUpdateHistoryCell(marketDate, payload) {
    try {
      const response = await apiFetch(`/api/balance-snapshots/${marketDate}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await parseApiResponse(response);
      setHistoryData(result);
      showMessage("Updated saved close.");
    } catch (error) {
      if (error instanceof AuthError) {
        setAuth({ status: "anonymous", username: null, isAdmin: false });
      }
      showMessage(error.message, true);
    }
  }

  async function handleSaveAccount(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector("button");
    const payload = Object.fromEntries(new FormData(form).entries());
    const accountId = payload.id;
    delete payload.id;
    const cashCad = String(payload.cash_cad || "").trim();
    const cashUsd = String(payload.cash_usd || "").trim();
    delete payload.cash_cad;
    delete payload.cash_usd;
    const cashBalances = [];
    if (cashCad !== "") {
      cashBalances.push({ currency: "CAD", amount: Number(cashCad) || 0 });
    }
    if (cashUsd !== "") {
      cashBalances.push({ currency: "USD", amount: Number(cashUsd) || 0 });
    }
    if (cashBalances.length || accountId) {
      payload.cash_balances = cashBalances;
    }

    button.disabled = true;
    try {
      const response = await apiFetch(accountId ? `/api/accounts/${accountId}` : "/api/accounts", {
        method: accountId ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await parseApiResponse(response);
      showMessage(`${accountId ? "Updated" : result.created ? "Created" : "Updated"} ${result.account.name}.`);
      form.reset();
      form.elements.account_entity.value = "Personal";
      form.elements.base_currency.value = "CAD";
      setEditingAccount(null);
      await loadSummary();
      setActiveView("accounts");
    } catch (error) {
      if (error instanceof AuthError) {
        setAuth({ status: "anonymous", username: null, isAdmin: false });
      }
      showMessage(error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  async function handleCreateUser(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector("button");
    const formData = new FormData(form);
    const payload = {
      username: formData.get("username"),
      password: formData.get("password"),
      is_admin: formData.get("is_admin") === "on",
      active: formData.get("active") === "on",
    };

    button.disabled = true;
    try {
      const response = await apiFetch("/api/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await parseApiResponse(response);
      setUsersData({ users: result.users, login_events: result.login_events });
      form.reset();
      form.elements.active.checked = true;
      showMessage(`Created user ${result.user.username}.`);
    } catch (error) {
      if (error instanceof AuthError) {
        setAuth({ status: "anonymous", username: null, isAdmin: false });
      }
      showMessage(error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  async function handleSavePrivateFundMark(accountId, event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector("button");
    const payload = Object.fromEntries(new FormData(form).entries());

    button.disabled = true;
    try {
      const response = await apiFetch(`/api/accounts/${accountId}/private-fund-marks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await parseApiResponse(response);
      setPrivateFundData((current) => ({ ...current, [accountId]: result }));
      form.reset();
      form.elements.currency.value = result.summary?.currency || "USD";
      await loadSummary();
      showMessage("Saved private fund mark.");
    } catch (error) {
      if (error instanceof AuthError) {
        setAuth({ status: "anonymous", username: null, isAdmin: false });
      }
      showMessage(error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  async function handleSaveTransaction(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector("button");
    const payload = Object.fromEntries(new FormData(form).entries());
    payload.account_id = Number(payload.account_id);
    ["quantity", "price", "dividend_per_share", "gross_amount", "fees", "tax", "net_amount"].forEach((field) => {
      payload[field] = String(payload[field] || "").trim() === "" ? null : Number(payload[field]);
    });
    ["gross_amount", "fees", "tax"].forEach((field) => {
      if (payload[field] === null) payload[field] = 0;
    });

    button.disabled = true;
    try {
      const response = await apiFetch("/api/transactions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await parseApiResponse(response);
      setTransactionsData({ transactions: result.transactions || [] });
      form.reset();
      form.elements.transaction_date.value = todayInputValue();
      form.elements.transaction_type.value = "DIVIDEND";
      form.elements.currency.value = "CAD";
      await loadSummary();
      showMessage("Saved transaction.");
    } catch (error) {
      if (error instanceof AuthError) {
        setAuth({ status: "anonymous", username: null, isAdmin: false });
      }
      showMessage(error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  async function handleDeleteTransaction(transactionId) {
    try {
      const response = await apiFetch(`/api/transactions/${transactionId}`, { method: "DELETE" });
      const result = await parseApiResponse(response);
      setTransactionsData({ transactions: result.transactions || [] });
      showMessage("Removed transaction.");
    } catch (error) {
      if (error instanceof AuthError) {
        setAuth({ status: "anonymous", username: null, isAdmin: false });
      }
      showMessage(error.message, true);
    }
  }

  async function handleSaveTrade(account, holding, event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector("button[type='submit']");
    const formData = new FormData(form);
    const payload = {
      transaction_date: formData.get("transaction_date"),
      shares: Number(formData.get("shares") || 0),
      price: Number(formData.get("price") || 0),
      drip: formData.get("drip") === "on",
      holding_id: String(holding.id || ""),
      manual_holding_id: holding.manual_holding_id || null,
      symbol: holding.symbol || "",
      market: holding.market || "",
      description: holding.description || "",
    };

    button.disabled = true;
    try {
      const response = await apiFetch(`/api/accounts/${account.id}/trades`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await parseApiResponse(response);
      await loadSummary();
      if (transactionsData) {
        await loadTransactions();
      }
      showMessage(result.message || "Saved trade.");
      setTradeDraft(null);
      return true;
    } catch (error) {
      if (error instanceof AuthError) {
        setAuth({ status: "anonymous", username: null, isAdmin: false });
      }
      showMessage(error.message, true);
      return false;
    } finally {
      button.disabled = false;
    }
  }

  async function handleSaveCash(account, event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector("button[type='submit']");
    const formData = new FormData(form);
    const cashCad = String(formData.get("cash_cad") || "").trim();
    const cashUsd = String(formData.get("cash_usd") || "").trim();
    const cashBalances = [];
    if (cashCad !== "") {
      cashBalances.push({ currency: "CAD", amount: Number(cashCad) || 0 });
    }
    if (cashUsd !== "") {
      cashBalances.push({ currency: "USD", amount: Number(cashUsd) || 0 });
    }

    button.disabled = true;
    try {
      await parseApiResponse(
        await apiFetch(`/api/accounts/${account.id}/cash`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ cash_balances: cashBalances }),
        })
      );
      await loadSummary();
      showMessage("Updated cash balance.");
      setCashDraft(null);
      return true;
    } catch (error) {
      if (error instanceof AuthError) {
        setAuth({ status: "anonymous", username: null, isAdmin: false });
      }
      showMessage(error.message, true);
      return false;
    } finally {
      button.disabled = false;
    }
  }

  const activeAccount =
    (data?.accounts || []).find((account) => account.id === activeAccountId) ||
    (data?.all_accounts || []).find((account) => account.id === activeAccountId) ||
    null;
  const latestReport = data?.accounts
    .map((account) => account.report_timestamp)
    .filter(Boolean)
    .sort()
    .at(-1);

  if (auth.status === "checking") {
    return (
      <main className="loading-screen">
        <div>Checking session...</div>
      </main>
    );
  }

  if (auth.status === "anonymous") {
    return <LoginScreen message={message} onLogin={handleLogin} />;
  }

  if (!data) {
    return (
      <main className="loading-screen">
        <div>Loading portfolio...</div>
      </main>
    );
  }

  return (
    <>
      <header className="topbar">
        <div>
          <h1>Investments</h1>
          <p>{latestReport ? `Latest holdings: ${latestReport}` : "Local holdings snapshots"}</p>
        </div>
        <SessionMenu
          username={auth.username}
          onOpenHoldingsImport={() => activateView("import-holdings")}
          onOpenAccounts={() => activateView("accounts")}
          onOpenTransactions={() => activateView("transactions")}
          onOpenImports={() => activateView("imports")}
          onOpenHistory={() => activateView("history")}
          onOpenUsers={() => activateView("users")}
          onLogout={handleLogout}
          isAdmin={auth.isAdmin}
        />
      </header>

      <main>
        <section className="app-panel">
          <PrimaryTabs onActivate={() => activateView("summary")} />

          {message && <div className={`message ${message.isError ? "error" : ""}`}>{message.text}</div>}

          {activeView === "summary" && (
            <SummaryPage
              data={data}
              onActivate={activateView}
            />
          )}

          {activeView === "account" && activeAccount && (
            <AccountPage
              account={activeAccount}
              holdings={data.holdings}
              history={historyData}
              query={accountQuery}
              sort={sort}
              onQuery={setAccountQuery}
              onSort={changeSort}
              onActivate={activateView}
              onOpenStock={openStock}
              onOpenTrade={(holding) => setTradeDraft({ account: activeAccount, holding })}
              onOpenCash={() => setCashDraft({ account: activeAccount })}
              privateFundData={privateFundData[activeAccount.id]}
              onLoadPrivateFundMarks={loadPrivateFundMarks}
              onSavePrivateFundMark={(event) => handleSavePrivateFundMark(activeAccount.id, event)}
            />
          )}

          {activeView === "stock" && (
            <StockPage
              stock={stockData}
              loading={stockLoading}
              error={stockError}
              requestedStock={activeStock}
              onBack={() => activateView("summary")}
              onRefresh={refreshActiveStock}
            />
          )}

          {activeView === "accounts" && (
            <AccountsSetupPage
              accounts={data.all_accounts || []}
              editingAccount={editingAccount}
              onSubmit={handleSaveAccount}
              onEdit={setEditingAccount}
              onCancelEdit={() => setEditingAccount(null)}
            />
          )}

          {activeView === "transactions" && (
            <TransactionsPage
              data={transactionsData}
              accounts={data.all_accounts || data.accounts || []}
              onSubmit={handleSaveTransaction}
              onDelete={handleDeleteTransaction}
              onRefresh={loadTransactions}
            />
          )}

          {activeView === "import-holdings" && auth.isAdmin && (
            <HoldingsImportPage
              accounts={data.all_accounts || data.accounts}
              fileLabel={fileLabel}
              onFileChange={(event) => setFileLabel(event.target.files[0]?.name || "Choose CSV")}
              onSubmit={handleImport}
            />
          )}

          {activeView === "imports" && <ImportsPage imports={data.imports} />}

          {activeView === "history" && (
            <HistoryPage
              data={historyData}
              accounts={data.all_accounts || data.accounts || []}
              fileLabel={historyFileLabel}
              onFileChange={(event) => setHistoryFileLabel(event.target.files[0]?.name || "Choose CSV")}
              onUpload={handleHistoryImport}
              onUpdate={handleUpdateHistoryCell}
            />
          )}

          {activeView === "users" && auth.isAdmin && (
            <UsersAdminPage data={usersData} onSubmit={handleCreateUser} onRefresh={loadUsers} />
          )}
        </section>
      </main>

      {tradeDraft ? (
        <TradeModal
          account={tradeDraft.account}
          holding={tradeDraft.holding}
          onSubmit={(event) => handleSaveTrade(tradeDraft.account, tradeDraft.holding, event)}
          onClose={() => setTradeDraft(null)}
        />
      ) : null}

      {cashDraft ? (
        <CashBalanceModal
          account={cashDraft.account}
          onSubmit={(event) => handleSaveCash(cashDraft.account, event)}
          onClose={() => setCashDraft(null)}
        />
      ) : null}
    </>
  );
}

function LoginScreen({ message, onLogin }) {
  return (
    <main className="login-screen">
      <form className="login-panel" onSubmit={onLogin}>
        <div className="login-brand">
          <img className="login-logo" src={wbfcLogoUrl} alt="West Bragg Forestry Corp logo" decoding="async" />
          <h1>Investments</h1>
          <p>Sign in to continue</p>
        </div>
        {message && <div className={`message ${message.isError ? "error" : ""}`}>{message.text}</div>}
        <label>
          <span>Username</span>
          <input name="username" type="text" autoComplete="username" required autoFocus />
        </label>
        <label>
          <span>Password</span>
          <input name="password" type="password" autoComplete="current-password" required />
        </label>
        <button type="submit">Sign In</button>
      </form>
    </main>
  );
}

function SessionMenu({
  username,
  onOpenHoldingsImport,
  onOpenAccounts,
  onOpenTransactions,
  onOpenImports,
  onOpenHistory,
  onOpenUsers,
  onLogout,
  isAdmin,
}) {
  const [menuOpen, setMenuOpen] = useState(false);

  function runMenuAction(action) {
    setMenuOpen(false);
    action();
  }

  return (
    <div className="top-actions">
      <div className="session-menu">
        <button className="session-menu-button" type="button" onClick={() => setMenuOpen((open) => !open)}>
          <span>{username}</span>
          <span aria-hidden="true">v</span>
        </button>
        {menuOpen ? (
          <div className="session-menu-panel">
            {isAdmin ? (
              <button type="button" onClick={() => runMenuAction(onOpenHoldingsImport)}>
                Import Holdings
              </button>
            ) : null}
            <button type="button" onClick={() => runMenuAction(onOpenAccounts)}>
              Accounts
            </button>
            <button type="button" onClick={() => runMenuAction(onOpenTransactions)}>
              Transactions
            </button>
            <button type="button" onClick={() => runMenuAction(onOpenImports)}>
              Imports
            </button>
            <button type="button" onClick={() => runMenuAction(onOpenHistory)}>
              History
            </button>
            {isAdmin ? (
              <button type="button" onClick={() => runMenuAction(onOpenUsers)}>
                Users
              </button>
            ) : null}
            <button type="button" onClick={() => runMenuAction(onLogout)}>
              Sign Out
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function HoldingsImportPage({ accounts, fileLabel, onFileChange, onSubmit }) {
  return (
    <section className="view active">
      <section className="section-panel">
        <div className="panel-heading">
          <h2>Import Holdings</h2>
        </div>
        <form className="holdings-import-form" onSubmit={onSubmit}>
          <div className="holdings-import-field">
            <span>Holdings CSV</span>
            <label className="file-picker">
              <input name="file" type="file" accept=".csv,text/csv" onChange={onFileChange} />
              <span>{fileLabel}</span>
            </label>
          </div>
          <label>
            <span>Account override</span>
            <input name="account_name" type="text" list="accountOptions" placeholder="Optional account name" />
          </label>
          <datalist id="accountOptions">
            {accounts.map((account) => (
              <option key={account.id} value={account.name} />
            ))}
          </datalist>
          <label>
            <span>Cash balance</span>
            <input name="cash_balance" type="number" step="0.01" min="0" placeholder="Optional" />
          </label>
          <label>
            <span>Cash currency</span>
            <select name="cash_currency" defaultValue="CAD">
              <option value="CAD">CAD</option>
              <option value="USD">USD</option>
            </select>
          </label>
          <div className="form-actions">
            <button type="submit">Import Holdings</button>
          </div>
        </form>
      </section>
    </section>
  );
}

function PrimaryTabs({ onActivate }) {
  return (
    <div className="tabs primary-tabs" role="tablist" aria-label="Investment sections">
      <button className="tab active" onClick={onActivate}>
        Portfolio
      </button>
    </div>
  );
}

function TradeIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M7 7h10l-3-3" />
      <path d="M17 17H7l3 3" />
      <path d="M17 7 7 17" />
    </svg>
  );
}

function TradeModal({ account, holding, onSubmit, onClose }) {
  const tradeCurrency = holding?.currency || account?.base_currency || "CAD";
  const defaultPrice = Number(holding?.current_price ?? holding?.closing_price ?? holding?.average_cost ?? 0);
  const defaultPriceText = Number.isFinite(defaultPrice) && defaultPrice > 0 ? defaultPrice.toFixed(2) : "";
  const cashBalance = cashAmountForCurrency(account, tradeCurrency);

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="modal-panel" role="dialog" aria-modal="true" aria-labelledby="trade-modal-title">
        <div className="modal-heading">
          <div>
            <h2 id="trade-modal-title">Trade {tickerLabel(holding)}</h2>
            <p>
              {shortAccountName(account?.name)} - {tradeCurrency} cash {formatCurrency(cashBalance, tradeCurrency)}
            </p>
          </div>
          <button className="icon-button" type="button" aria-label="Close" onClick={onClose}>
            x
          </button>
        </div>
        <form className="modal-form" onSubmit={onSubmit}>
          <label>
            <span>Date</span>
            <input name="transaction_date" type="date" defaultValue={todayInputValue()} required />
          </label>
          <label>
            <span>Shares</span>
            <input name="shares" type="number" step="0.0001" min="0" required autoFocus />
          </label>
          <label>
            <span>Price</span>
            <input name="price" type="number" step="0.01" min="0" defaultValue={defaultPriceText} required />
          </label>
          <label className="checkbox-row modal-checkbox">
            <input name="drip" type="checkbox" />
            <span>DRIP</span>
          </label>
          <div className="form-actions">
            <button type="submit">Save Trade</button>
            <button className="secondary-button" type="button" onClick={onClose}>
              Cancel
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

function CashBalanceModal({ account, onSubmit, onClose }) {
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="modal-panel" role="dialog" aria-modal="true" aria-labelledby="cash-modal-title">
        <div className="modal-heading">
          <div>
            <h2 id="cash-modal-title">Cash Balance</h2>
            <p>{shortAccountName(account?.name)}</p>
          </div>
          <button className="icon-button" type="button" aria-label="Close" onClick={onClose}>
            x
          </button>
        </div>
        <form className="modal-form" onSubmit={onSubmit}>
          <label>
            <span>CAD cash</span>
            <input name="cash_cad" type="number" step="0.01" defaultValue={cashInputDefault(account, "CAD")} autoFocus />
          </label>
          <label>
            <span>USD cash</span>
            <input name="cash_usd" type="number" step="0.01" defaultValue={cashInputDefault(account, "USD")} />
          </label>
          <div className="form-actions">
            <button type="submit">Update Cash</button>
            <button className="secondary-button" type="button" onClick={onClose}>
              Cancel
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

function SummaryPage({ data, onActivate }) {
  const incomeByAccount = incomeByAccountFromHoldings(data.holdings);

  return (
    <section className="view active">
      <section className="section-panel">
        <div className="panel-heading">
          <h2>Accounts</h2>
        </div>
        <AccountSummaryTable accounts={data.accounts} incomeByAccount={incomeByAccount} onActivate={onActivate} />
      </section>
      <PriceUpdateFooter status={data.price_refresh} />
    </section>
  );
}

function PriceUpdateFooter({ status }) {
  if (!status) {
    return <div className="price-update-footer">Last price update pending</div>;
  }

  const refresh = status.refresh || {};
  const latest = status.latest_fetched_at || refresh.completed_at;
  return (
    <div className="price-update-footer">
      {latest ? `Last price update ${formatDate(latest)}` : "Last price update pending"}
    </div>
  );
}

function buildAccountMonthlyPivot(snapshots, accountId) {
  const rowsByYear = new Map();
  let previousValue = null;

  [...snapshots]
    .reverse()
    .forEach((snapshot) => {
      const account = (snapshot.accounts || []).find((item) => Number(item.account_id) === Number(accountId));
      if (!account) {
        return;
      }

      const parsed = Date.parse(`${snapshot.market_date}T00:00:00`);
      if (Number.isNaN(parsed)) {
        return;
      }

      const currentValue = Number(account.value || 0);
      let changePct = Number(account.day_change_pct);
      if (!Number.isFinite(changePct) && previousValue) {
        changePct = ((currentValue - previousValue) / previousValue) * 100;
      }
      previousValue = currentValue;

      if (!Number.isFinite(changePct)) {
        return;
      }

      const date = new Date(parsed);
      const year = date.getFullYear();
      const month = date.getMonth();
      const row = rowsByYear.get(year) || {
        year,
        months: Array(12).fill(0),
        counts: Array(12).fill(0),
      };
      row.months[month] += changePct;
      row.counts[month] += 1;
      rowsByYear.set(year, row);
    });

  const rows = Array.from(rowsByYear.values())
    .map((row) => ({
      ...row,
      annual: row.months.reduce((sum, value, index) => sum + (row.counts[index] ? value : 0), 0),
      annualCount: row.counts.reduce((sum, value) => sum + value, 0),
    }))
    .sort((left, right) => right.year - left.year);

  const average = {
    year: "AVG",
    months: monthLabels.map((_, index) => {
      const values = rows.filter((row) => row.counts[index]).map((row) => row.months[index]);
      return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
    }),
    counts: monthLabels.map((_, index) => rows.filter((row) => row.counts[index]).length),
  };
  const annualValues = rows.filter((row) => row.annualCount).map((row) => row.annual);
  average.annual = annualValues.length ? annualValues.reduce((sum, value) => sum + value, 0) / annualValues.length : null;
  average.annualCount = annualValues.length;

  return { rows, average };
}

function buildAccountPerformanceSeries(snapshots, accountId) {
  return [...snapshots]
    .reverse()
    .map((snapshot) => {
      const account = (snapshot.accounts || []).find((item) => Number(item.account_id) === Number(accountId));
      const value = Number(account?.value || 0);
      const time = Date.parse(`${snapshot.market_date}T00:00:00`);
      if (!account || !value || Number.isNaN(time)) {
        return null;
      }
      return {
        date: snapshot.market_date,
        time,
        value,
      };
    })
    .filter(Boolean);
}

function filterPerformanceSeries(series, range) {
  if (range === "max" || series.length < 2) {
    return series;
  }

  const latest = series[series.length - 1];
  const latestDate = new Date(latest.time);
  let cutoff = series[0].time;
  if (range === "5d") {
    cutoff = latest.time - 5 * dayMs;
  } else if (range === "30d") {
    cutoff = latest.time - 30 * dayMs;
  } else if (range === "1yr") {
    cutoff = new Date(latestDate.getFullYear() - 1, latestDate.getMonth(), latestDate.getDate()).getTime();
  } else if (range === "ytd") {
    cutoff = new Date(latestDate.getFullYear(), 0, 1).getTime();
  }
  return series.filter((point) => point.time >= cutoff);
}

function heatmapStyle(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) {
    return {};
  }
  if (Number(value) < 0) {
    const amount = Math.min(Math.abs(Number(value)) / 12, 1);
    return { backgroundColor: `rgba(178, 68, 68, ${0.1 + amount * 0.22})` };
  }
  return {};
}

function formatPivotPercent(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) {
    return "";
  }
  return `${Number(value).toFixed(2)}%`;
}

function AccountMonthlyPivot({ pivot, loading, accountName }) {
  if (loading) {
    return <div className="empty-state">Loading history...</div>;
  }
  if (!pivot.rows.length) {
    return <div className="empty-state">No saved account history yet.</div>;
  }

  return (
    <div className="monthly-pivot-wrap">
      <table className="monthly-pivot-table">
        <thead>
          <tr>
            <th>{shortAccountName(accountName)}</th>
            {monthLabels.map((month) => (
              <th key={month} className="numeric">
                {month}
              </th>
            ))}
            <th className="numeric">Ann</th>
          </tr>
        </thead>
        <tbody>
          {pivot.rows.map((row) => (
            <MonthlyPivotRow key={row.year} row={row} />
          ))}
          <MonthlyPivotRow row={pivot.average} isAverage />
        </tbody>
      </table>
    </div>
  );
}

function MonthlyPivotRow({ row, isAverage = false }) {
  return (
    <tr className={isAverage ? "monthly-pivot-average" : ""}>
      <th>{row.year}</th>
      {monthLabels.map((month, index) => {
        const value = row.counts[index] ? row.months[index] : null;
        return (
          <td key={month} className={`numeric ${toneClass(value)}`} style={heatmapStyle(value)}>
            {formatPivotPercent(value)}
          </td>
        );
      })}
      <td className={`numeric ${toneClass(row.annual)}`} style={heatmapStyle(row.annual)}>
        {row.annualCount ? formatPivotPercent(row.annual) : ""}
      </td>
    </tr>
  );
}

function PerformanceRangeControls({ selectedRange, onChange }) {
  return (
    <div className="performance-range" aria-label="Performance range">
      {performanceRanges.map(([value, label]) => (
        <button
          key={value}
          type="button"
          className={selectedRange === value ? "active" : ""}
          aria-pressed={selectedRange === value}
          onClick={() => onChange(value)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

function AccountPerformanceChart({ series, loading }) {
  const [selectedRange, setSelectedRange] = useState("max");
  const [hoveredIndex, setHoveredIndex] = useState(null);

  if (loading) {
    return null;
  }
  if (series.length < 2) {
    return <div className="empty-state">At least two saved closes are needed for a performance chart.</div>;
  }

  const visibleSeries = filterPerformanceSeries(series, selectedRange);
  if (visibleSeries.length < 2) {
    return (
      <div className="account-performance">
        <PerformanceRangeControls selectedRange={selectedRange} onChange={setSelectedRange} />
        <div className="empty-state">Not enough saved closes in this range.</div>
      </div>
    );
  }

  const first = visibleSeries[0];
  const latest = visibleSeries[visibleSeries.length - 1];
  const change = latest.value - first.value;
  const width = 900;
  const height = 220;
  const pad = { top: 18, right: 22, bottom: 24, left: 112 };
  const values = visibleSeries.map((point) => point.value);
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const padding = (max - min) * 0.08;
  min -= padding;
  max += padding;
  const valueRange = max - min || 1;
  const timeMin = first.time;
  const timeRange = latest.time - first.time || 1;

  function x(point) {
    return pad.left + ((point.time - timeMin) / timeRange) * (width - pad.left - pad.right);
  }

  function y(value) {
    return height - pad.bottom - ((Number(value || 0) - min) / valueRange) * (height - pad.top - pad.bottom);
  }

  const line = visibleSeries.map((point) => `${x(point).toFixed(1)},${y(point.value).toFixed(1)}`).join(" ");
  const gridValues = [min + padding, min + valueRange / 2, max - padding];
  const hoveredPoint =
    hoveredIndex === null ? null : visibleSeries[Math.min(Math.max(hoveredIndex, 0), visibleSeries.length - 1)];
  const hoveredX = hoveredPoint ? x(hoveredPoint) : 0;
  const hoveredY = hoveredPoint ? y(hoveredPoint.value) : 0;
  const tooltipWidth = 164;
  const tooltipHeight = 44;
  const tooltipX = Math.min(Math.max(hoveredX + 12, pad.left), width - pad.right - tooltipWidth);
  const tooltipY = Math.max(pad.top, hoveredY - tooltipHeight - 10);

  function handleChartMove(event) {
    const bounds = event.currentTarget.getBoundingClientRect();
    const pointerX = ((event.clientX - bounds.left) / bounds.width) * width;
    let nearestIndex = 0;
    let nearestDistance = Infinity;
    visibleSeries.forEach((point, index) => {
      const distance = Math.abs(x(point) - pointerX);
      if (distance < nearestDistance) {
        nearestDistance = distance;
        nearestIndex = index;
      }
    });
    setHoveredIndex(nearestIndex);
  }

  return (
    <div className="account-performance">
      <div className="performance-summary">
        <span>
          {formatMarketDate(first.date)} to {formatMarketDate(latest.date)}
        </span>
        <strong className={toneClass(change)}>
          {formatMoney(latest.value)} ({change >= 0 ? "+" : ""}
          {formatMoney(change)})
        </strong>
      </div>
      <PerformanceRangeControls selectedRange={selectedRange} onChange={setSelectedRange} />
      <svg
        className="performance-chart"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Account performance history chart"
        onMouseMove={handleChartMove}
        onMouseLeave={() => setHoveredIndex(null)}
      >
        {gridValues.map((value) => {
          const lineY = y(value);
          return (
            <g key={value}>
              <line x1={pad.left} x2={width - pad.right} y1={lineY} y2={lineY} />
              <text x={pad.left - 10} y={lineY + 4}>
                {formatMoney(value)}
              </text>
            </g>
          );
        })}
        <polyline className={toneClass(change)} points={line} />
        {hoveredPoint ? (
          <g>
            <line className="performance-hover-line" x1={hoveredX} x2={hoveredX} y1={pad.top} y2={height - pad.bottom} />
            <circle className="performance-hover-dot" cx={hoveredX} cy={hoveredY} r="4.5" />
            <g className="performance-tooltip">
              <rect x={tooltipX} y={tooltipY} width={tooltipWidth} height={tooltipHeight} rx="6" />
              <text x={tooltipX + 10} y={tooltipY + 17}>
                {formatMarketDate(hoveredPoint.date)}
              </text>
              <text className="tooltip-value" x={tooltipX + 10} y={tooltipY + 34}>
                {formatMoney(hoveredPoint.value)}
              </text>
            </g>
          </g>
        ) : null}
      </svg>
    </div>
  );
}

function PrivateFundPanel({ data, account, onSubmit }) {
  const marks = data?.marks || [];
  const summary = data?.summary || {};
  const currency = summary.currency || account.base_currency || "USD";

  return (
    <section className="section-panel private-fund-panel">
      <div className="panel-heading private-fund-heading">
        <div>
          <h2>Private Fund Marks</h2>
          <p>Manual capital account updates for {shortAccountName(account.name)}</p>
        </div>
        {summary.latest ? <span>Latest {formatMarketDate(summary.latest.mark_date)}</span> : null}
      </div>

      <Metrics
        metrics={[
          ["Balance", formatCurrency(summary.balance || 0, currency), "raw"],
          ["Net Income", formatCurrency(summary.total_income || 0, currency), "raw"],
          ["Contributions", formatCurrency(summary.total_contributions || 0, currency), "raw"],
          ["Withdrawals", formatCurrency(summary.total_withdrawals || 0, currency), "raw"],
          ["ROI", summary.roi_pct, "percentTone"],
        ]}
      />

      <form className="private-fund-form" onSubmit={onSubmit}>
        <label>
          <span>Date</span>
          <input name="mark_date" type="date" required />
        </label>
        <label>
          <span>Beginning</span>
          <input name="beginning_balance" type="number" step="0.01" defaultValue="0" />
        </label>
        <label>
          <span>Net Income</span>
          <input name="net_income" type="number" step="0.01" defaultValue="0" />
        </label>
        <label>
          <span>Withdrawal</span>
          <input name="withdrawal" type="number" step="0.01" defaultValue="0" />
        </label>
        <label>
          <span>Contribution</span>
          <input name="contribution" type="number" step="0.01" defaultValue="0" />
        </label>
        <label>
          <span>Ending</span>
          <input name="ending_balance" type="number" step="0.01" required />
        </label>
        <label>
          <span>Currency</span>
          <select name="currency" defaultValue={currency}>
            <option value="USD">USD</option>
            <option value="CAD">CAD</option>
          </select>
        </label>
        <label className="private-fund-notes">
          <span>Notes</span>
          <input name="notes" type="text" autoComplete="off" />
        </label>
        <button type="submit">Save Mark</button>
      </form>

      <div className="table-wrap private-fund-table-wrap">
        <table>
          <thead>
            <tr>
              <th>Date</th>
              <th className="numeric">Beginning</th>
              <th className="numeric">Income</th>
              <th className="numeric">Withdrawal</th>
              <th className="numeric">Contribution</th>
              <th className="numeric">Ending</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            {!data ? (
              <tr>
                <td className="empty-state" colSpan="7">
                  Loading private fund marks...
                </td>
              </tr>
            ) : marks.length ? (
              marks.map((mark) => (
                <tr key={mark.id}>
                  <td>{formatMarketDate(mark.mark_date)}</td>
                  <td className="numeric">{formatCurrency(mark.beginning_balance, mark.currency)}</td>
                  <td className={`numeric ${toneClass(mark.net_income)}`}>{formatCurrency(mark.net_income, mark.currency)}</td>
                  <td className="numeric">{formatCurrency(mark.withdrawal, mark.currency)}</td>
                  <td className="numeric">{formatCurrency(mark.contribution, mark.currency)}</td>
                  <td className="numeric">{formatCurrency(mark.ending_balance, mark.currency)}</td>
                  <td>{mark.notes || ""}</td>
                </tr>
              ))
            ) : (
              <tr>
                <td className="empty-state" colSpan="7">
                  No private fund marks yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function AccountPage({
  account,
  holdings,
  history,
  query,
  sort,
  onQuery,
  onSort,
  onActivate,
  onOpenStock,
  onOpenTrade,
  onOpenCash,
  privateFundData,
  onLoadPrivateFundMarks,
  onSavePrivateFundMark,
}) {
  const [showDerisk, setShowDerisk] = useState(false);
  const isPrivateFund = isPrivateFundAccount(account);
  const accountHoldings = useMemo(
    () =>
      holdings
        .filter((holding) => holding.account_name === account.name && holding.asset_type !== "Cash")
        .map((holding) => ({
          ...holding,
          ...Object.fromEntries(
            dividendIncomeForecastDates.map(([label, targetDate]) => [
              forecastIncomeField(label),
              forecastHoldingDividendIncome(holding, targetDate),
            ])
          ),
          account_weight: account.current_total_value
            ? (Number((holding.current_value ?? holding.closing_value) || 0) / account.current_total_value) * 100
            : 0,
        })),
    [account, holdings]
  );

  const sortedAccountHoldings = useMemo(
    () => sortedHoldings(accountHoldings, query, sort),
    [accountHoldings, query, sort]
  );
  const monthlyPivot = useMemo(
    () => buildAccountMonthlyPivot(history?.snapshots || [], account.id),
    [history, account.id]
  );
  const performanceSeries = useMemo(
    () => buildAccountPerformanceSeries(history?.snapshots || [], account.id),
    [history, account.id]
  );

  useEffect(() => {
    if (isPrivateFund && !privateFundData) {
      onLoadPrivateFundMarks(account.id);
    }
  }, [account.id, isPrivateFund, privateFundData, onLoadPrivateFundMarks]);

  return (
    <section className="view active">
      <div className="account-header">
        <div>
          <h2>{account.name}</h2>
          <p>{account.report_timestamp || ""}</p>
        </div>
        <div className="header-actions">
          <div className="account-cash-group">
            <div className="account-cash-pill">
              <span>Cash</span>
              <strong>{formatCashBalances(account)}</strong>
            </div>
            <button className="icon-button trade-button" type="button" title="Edit cash" aria-label="Edit cash" onClick={onOpenCash}>
              <TradeIcon />
            </button>
          </div>
          <button className="quiet-button" onClick={() => onActivate("summary")}>
            Summary
          </button>
        </div>
      </div>

      {isPrivateFund ? (
        <PrivateFundPanel data={privateFundData} account={account} onSubmit={onSavePrivateFundMark} />
      ) : null}

      <section className="section-panel">
        <TableToolbar value={query} onChange={onQuery} count={sortedAccountHoldings.length} placeholder="Filter account holdings">
          <label className="toolbar-checkbox">
            <input
              type="checkbox"
              checked={showDerisk}
              onChange={(event) => setShowDerisk(event.target.checked)}
            />
            <span>Show de-risk</span>
          </label>
        </TableToolbar>
        <HoldingsTable
          holdings={sortedAccountHoldings}
          accountName={account.name}
          sort={sort}
          weightKey="account_weight"
          showAccount={false}
          showDerisk={showDerisk}
          onSort={onSort}
          onOpenStock={onOpenStock}
          onOpenTrade={onOpenTrade}
        />
      </section>

      <section className="summary-grid account-history-grid">
        <section className="section-panel">
          <div className="panel-heading">
            <h2>Monthly Change</h2>
          </div>
          <AccountMonthlyPivot pivot={monthlyPivot} loading={!history} accountName={account.name} />
        </section>

        <section className="section-panel">
          <div className="panel-heading">
            <h2>Performance History</h2>
          </div>
          <AccountPerformanceChart series={performanceSeries} loading={!history} />
        </section>
      </section>
    </section>
  );
}

function stockPointTime(date) {
  const parsed = Date.parse(`${date}T00:00:00`);
  return Number.isNaN(parsed) ? null : parsed;
}

function StockPriceChart({ prices, currency }) {
  const [hoveredIndex, setHoveredIndex] = useState(null);
  const series = (prices || [])
    .map((point) => ({
      date: point.date,
      time: stockPointTime(point.date),
      value: Number(point.close || 0),
    }))
    .filter((point) => point.time && point.value > 0);

  if (series.length < 2) {
    return <div className="empty-state">No price history available yet.</div>;
  }

  const width = 900;
  const height = 260;
  const pad = { top: 18, right: 22, bottom: 26, left: 96 };
  const first = series[0];
  const latest = series[series.length - 1];
  const values = series.map((point) => point.value);
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const padding = (max - min) * 0.08;
  min -= padding;
  max += padding;
  const valueRange = max - min || 1;
  const timeRange = latest.time - first.time || 1;

  function x(point) {
    return pad.left + ((point.time - first.time) / timeRange) * (width - pad.left - pad.right);
  }

  function y(value) {
    return height - pad.bottom - ((Number(value || 0) - min) / valueRange) * (height - pad.top - pad.bottom);
  }

  function handleMove(event) {
    const bounds = event.currentTarget.getBoundingClientRect();
    const pointerX = ((event.clientX - bounds.left) / bounds.width) * width;
    let nearestIndex = 0;
    let nearestDistance = Infinity;
    series.forEach((point, index) => {
      const distance = Math.abs(x(point) - pointerX);
      if (distance < nearestDistance) {
        nearestDistance = distance;
        nearestIndex = index;
      }
    });
    setHoveredIndex(nearestIndex);
  }

  const line = series.map((point) => `${x(point).toFixed(1)},${y(point.value).toFixed(1)}`).join(" ");
  const gridValues = [min + padding, min + valueRange / 2, max - padding];
  const hoveredPoint =
    hoveredIndex === null ? null : series[Math.min(Math.max(hoveredIndex, 0), series.length - 1)];
  const hoveredX = hoveredPoint ? x(hoveredPoint) : 0;
  const hoveredY = hoveredPoint ? y(hoveredPoint.value) : 0;
  const tooltipWidth = 164;
  const tooltipHeight = 44;
  const tooltipX = Math.min(Math.max(hoveredX + 12, pad.left), width - pad.right - tooltipWidth);
  const tooltipY = Math.max(pad.top, hoveredY - tooltipHeight - 10);

  return (
    <div className="stock-chart-wrap">
      <svg
        className="stock-chart"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Historical stock price chart"
        onMouseMove={handleMove}
        onMouseLeave={() => setHoveredIndex(null)}
      >
        {gridValues.map((value) => {
          const lineY = y(value);
          return (
            <g key={value}>
              <line x1={pad.left} x2={width - pad.right} y1={lineY} y2={lineY} />
              <text x={pad.left - 10} y={lineY + 4}>
                {formatCurrency(value, currency)}
              </text>
            </g>
          );
        })}
        <polyline points={line} />
        {hoveredPoint ? (
          <g>
            <line className="stock-hover-line" x1={hoveredX} x2={hoveredX} y1={pad.top} y2={height - pad.bottom} />
            <circle className="stock-hover-dot" cx={hoveredX} cy={hoveredY} r="4.5" />
            <g className="performance-tooltip">
              <rect x={tooltipX} y={tooltipY} width={tooltipWidth} height={tooltipHeight} rx="6" />
              <text x={tooltipX + 10} y={tooltipY + 17}>
                {formatMarketDate(hoveredPoint.date)}
              </text>
              <text className="tooltip-value" x={tooltipX + 10} y={tooltipY + 34}>
                {formatCurrency(hoveredPoint.value, currency)}
              </text>
            </g>
          </g>
        ) : null}
      </svg>
    </div>
  );
}

function StockDividendChart({ dividends, currency }) {
  const [hoveredIndex, setHoveredIndex] = useState(null);
  const series = (dividends || [])
    .map((point) => ({
      date: point.ex_date,
      time: stockPointTime(point.ex_date),
      value: Number(point.dividend_per_share || 0),
    }))
    .filter((point) => point.time && point.value > 0);

  if (!series.length) {
    return <div className="empty-state">No dividend history available yet.</div>;
  }

  const width = 900;
  const height = 220;
  const pad = { top: 18, right: 22, bottom: 26, left: 96 };
  const first = series[0];
  const latest = series[series.length - 1];
  const max = Math.max(...series.map((point) => point.value)) || 1;
  const timeRange = latest.time - first.time || 1;
  const barWidth = Math.max(4, Math.min(18, (width - pad.left - pad.right) / Math.max(series.length, 1) - 3));

  function x(point) {
    return pad.left + ((point.time - first.time) / timeRange) * (width - pad.left - pad.right);
  }

  function y(value) {
    return height - pad.bottom - (Number(value || 0) / max) * (height - pad.top - pad.bottom);
  }

  function handleMove(event) {
    const bounds = event.currentTarget.getBoundingClientRect();
    const pointerX = ((event.clientX - bounds.left) / bounds.width) * width;
    let nearestIndex = 0;
    let nearestDistance = Infinity;
    series.forEach((point, index) => {
      const distance = Math.abs(x(point) - pointerX);
      if (distance < nearestDistance) {
        nearestDistance = distance;
        nearestIndex = index;
      }
    });
    setHoveredIndex(nearestIndex);
  }

  const gridValues = [max / 2, max];
  const hoveredPoint =
    hoveredIndex === null ? null : series[Math.min(Math.max(hoveredIndex, 0), series.length - 1)];
  const hoveredX = hoveredPoint ? x(hoveredPoint) : 0;
  const hoveredY = hoveredPoint ? y(hoveredPoint.value) : 0;
  const tooltipWidth = 170;
  const tooltipHeight = 44;
  const tooltipX = Math.min(Math.max(hoveredX + 12, pad.left), width - pad.right - tooltipWidth);
  const tooltipY = Math.max(pad.top, hoveredY - tooltipHeight - 10);

  return (
    <div className="stock-chart-wrap">
      <svg
        className="stock-chart stock-dividend-chart"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Historical dividend chart"
        onMouseMove={handleMove}
        onMouseLeave={() => setHoveredIndex(null)}
      >
        {gridValues.map((value) => {
          const lineY = y(value);
          return (
            <g key={value}>
              <line x1={pad.left} x2={width - pad.right} y1={lineY} y2={lineY} />
              <text x={pad.left - 10} y={lineY + 4}>
                {formatCurrency(value, currency)}
              </text>
            </g>
          );
        })}
        {series.map((point) => {
          const barX = x(point) - barWidth / 2;
          const barY = y(point.value);
          return (
            <rect
              key={point.date}
              x={barX}
              y={barY}
              width={barWidth}
              height={height - pad.bottom - barY}
              rx="2"
            />
          );
        })}
        {hoveredPoint ? (
          <g>
            <line className="stock-hover-line" x1={hoveredX} x2={hoveredX} y1={pad.top} y2={height - pad.bottom} />
            <circle className="stock-hover-dot" cx={hoveredX} cy={hoveredY} r="4.5" />
            <g className="performance-tooltip">
              <rect x={tooltipX} y={tooltipY} width={tooltipWidth} height={tooltipHeight} rx="6" />
              <text x={tooltipX + 10} y={tooltipY + 17}>
                Ex {formatMarketDate(hoveredPoint.date)}
              </text>
              <text className="tooltip-value" x={tooltipX + 10} y={tooltipY + 34}>
                {formatCurrency(hoveredPoint.value, currency)}
              </text>
            </g>
          </g>
        ) : null}
      </svg>
    </div>
  );
}

function StockPage({ stock, requestedStock, loading, error, onBack, onRefresh }) {
  const displaySymbol = stock?.yahoo_symbol || requestedStock?.symbol || "Stock";
  const currency = stock?.currency || "CAD";
  const stats = stock?.stats || {};
  const dividends = stock?.dividends || [];
  const recentDividends = [...dividends].slice(-8).reverse();
  const accounts = stock?.holdings?.accounts || [];
  const forecastStartDate = stock?.fetched_at || todayInputValue();
  const dividendGrowthPct = stats.five_year_dividend_growth_pct;

  return (
    <section className="view active">
      <div className="account-header">
        <div>
          <h2>{displaySymbol}</h2>
          <p>{stock?.description || "Stock analytics"}</p>
        </div>
        <div className="header-actions">
          <button className="quiet-button" type="button" onClick={onBack}>
            Summary
          </button>
          <button className="quiet-button" type="button" onClick={onRefresh} disabled={loading || !requestedStock}>
            Refresh
          </button>
        </div>
      </div>

      {error ? <div className="message error">{error}</div> : null}
      {loading && !stock ? <div className="empty-state">Loading stock analytics...</div> : null}

      {stock ? (
        <>
          <Metrics
            metrics={[
              ["Current Price", formatCurrency(stats.current_price, currency), "raw"],
              ["TTM Dividend", formatCurrency(stats.ttm_dividend, currency), "raw"],
              ["Annual Forward Dividend", formatCurrency(stats.annual_forward_dividend, currency), "raw"],
              ["Forward Yield", stats.forward_yield_pct, "percentTone"],
              ["5Y Avg Yield", stats.five_year_avg_yield_pct, "percentTone"],
              ["5Y Div Growth", stats.five_year_dividend_growth_pct, "percentTone"],
              ["Payments / Year", `${stats.payments_per_year || 0}`, "raw"],
            ]}
          />

          <section className="summary-grid stock-detail-grid">
            <section className="section-panel">
              <div className="panel-heading">
                <h2>Historical Price</h2>
                <span>{stats.price_count || 0} closes</span>
              </div>
              <StockPriceChart prices={stock.prices} currency={currency} />
            </section>

            <section className="section-panel">
              <div className="panel-heading">
                <h2>Historical Dividends</h2>
                <span>{stats.dividend_count || 0} ex-dividend events</span>
              </div>
              <StockDividendChart dividends={dividends} currency={currency} />
              <div className="stock-forward-note">
                Latest payment {formatCurrency(stats.latest_dividend, currency)}
                {stats.latest_ex_date ? ` ex ${formatMarketDate(stats.latest_ex_date)}` : ""} x{" "}
                {stats.payments_per_year || 0} payments = {formatCurrency(stats.annual_forward_dividend, currency)} AFD.
              </div>
              <div className="stock-dividend-list">
                {recentDividends.map((dividend) => (
                  <div key={dividend.ex_date} className="stock-dividend-row">
                    <span>{formatMarketDate(dividend.ex_date)}</span>
                    <strong>{formatCurrency(dividend.dividend_per_share, currency)}</strong>
                  </div>
                ))}
              </div>
            </section>
          </section>

          <section className="section-panel">
            <div className="panel-heading">
              <h2>Held In Accounts</h2>
              <span>
                {number.format(stock.holdings?.total_quantity || 0)} shares -{" "}
                {formatMoney(stock.holdings?.total_annual_forward_income || 0)} annual forward income
              </span>
            </div>
            {accounts.length ? (
              <div className="table-wrap">
                <table className="stock-holdings-table">
                  <thead>
                    <tr>
                      <th>Account</th>
                      <th className="numeric">Shares</th>
                      <th className="numeric">Avg Cost</th>
                      <th className="numeric">Value</th>
                      <th className="numeric">Annual Income</th>
                      {dividendIncomeForecastDates.map(([label]) => (
                        <th key={label} className="numeric">
                          Forecast {label}
                        </th>
                      ))}
                      <th className="numeric">Gain</th>
                    </tr>
                  </thead>
                  <tbody>
                    {accounts.map((account) => (
                      <tr key={account.account_name}>
                        <td>{shortAccountName(account.account_name)}</td>
                        <td className="numeric">{number.format(account.quantity || 0)}</td>
                        <td className="numeric">{formatCurrency(account.average_cost, currency)}</td>
                        <td className="numeric">{formatMoney(account.current_value)}</td>
                        <td className="numeric">
                          {formatMoney(account.annual_forward_income)}
                          <div className="row-sub">{formatPercent(account.yield_on_cost_pct)} on cost</div>
                        </td>
                        {dividendIncomeForecastDates.map(([label, targetDate]) => {
                          const forecastIncome = forecastDividendIncome(
                            account.annual_forward_income,
                            dividendGrowthPct,
                            forecastStartDate,
                            targetDate,
                          );
                          return (
                            <td key={label} className="numeric">
                              {forecastIncome === null ? "n/a" : formatMoney(forecastIncome)}
                            </td>
                          );
                        })}
                        <td className={`numeric ${toneClass(account.gain_loss)}`}>
                          {formatMoney(account.gain_loss)}
                          <div className="row-sub">{formatPercent(account.gain_loss_pct)}</div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="empty-state">No current holdings found for this symbol.</div>
            )}
          </section>

          <div className="price-update-footer">
            {stock.fetched_at ? `Stock analytics updated ${formatDate(stock.fetched_at)}` : "Stock analytics pending"}
          </div>
        </>
      ) : null}
    </section>
  );
}

function AccountsSetupPage({ accounts, editingAccount, onSubmit, onEdit, onCancelEdit }) {
  const orderedAccounts = sortAccountsForDisplay(accounts);
  const cashCadDefaultValue = cashInputDefault(editingAccount, "CAD");
  const cashUsdDefaultValue = cashInputDefault(editingAccount, "USD");

  return (
    <section className="view active">
      <section className="setup-layout">
        <form key={editingAccount?.id || "new-account"} className="section-panel account-setup-form" onSubmit={onSubmit}>
          <div className="panel-heading">
            <h2>{editingAccount ? "Edit Account" : "New Account"}</h2>
          </div>
          {editingAccount ? <input name="id" type="hidden" value={editingAccount.id} /> : null}
          <label>
            <span>Account name</span>
            <input name="name" type="text" autoComplete="off" defaultValue={editingAccount?.name || ""} required />
          </label>
          <label>
            <span>Owner</span>
            <input name="owner" type="text" autoComplete="off" defaultValue={editingAccount?.owner || ""} />
          </label>
          <label>
            <span>Entity</span>
            <select name="account_entity" defaultValue={accountEntity(editingAccount)}>
              <option value="Personal">Personal</option>
              <option value="Corporate">Corporate</option>
            </select>
          </label>
          <label>
            <span>Account type</span>
            <select name="account_type" defaultValue={editingAccount?.account_type || "RRSP"}>
              <option value="RRSP">RRSP</option>
              <option value="RESP">RESP</option>
              <option value="TFSA">TFSA</option>
              <option value="Taxable">Taxable</option>
              <option value="Corporate Taxable">Corporate Taxable</option>
              <option value="Private Fund">Private Fund</option>
              <option value="Cash">Cash</option>
              <option value="Investment">Investment</option>
            </select>
          </label>
          <label>
            <span>Base currency</span>
            <select name="base_currency" defaultValue={editingAccount?.base_currency || "CAD"}>
              <option value="CAD">CAD</option>
              <option value="USD">USD</option>
            </select>
          </label>
          <div className="cash-edit-fields">
            <label>
              <span>CAD cash</span>
              <input
                name="cash_cad"
                type="number"
                step="0.01"
                placeholder="Optional"
                defaultValue={cashCadDefaultValue}
              />
            </label>
            <label>
              <span>USD cash</span>
              <input
                name="cash_usd"
                type="number"
                step="0.01"
                placeholder="Optional"
                defaultValue={cashUsdDefaultValue}
              />
            </label>
          </div>
          <label>
            <span>Notes</span>
            <textarea name="notes" rows="4" defaultValue={editingAccount?.notes || ""} />
          </label>
          <div className="form-actions">
            <button type="submit">{editingAccount ? "Update Account" : "Save Account"}</button>
            {editingAccount ? (
              <button className="secondary-button" type="button" onClick={onCancelEdit}>
                Cancel
              </button>
            ) : null}
          </div>
        </form>

        <section className="section-panel">
          <div className="panel-heading">
            <h2>Accounts</h2>
          </div>
          <div className="setup-account-list">
            {orderedAccounts.length ? (
              orderedAccounts.map((account) => (
                <div key={account.id} className="setup-account-row">
                  <div className="row-top">
                    <span className="row-title">{account.name}</span>
                    <span className="numeric">{account.base_currency || "CAD"}</span>
                  </div>
                  <div className="row-top row-sub">
                    <span>{account.account_type || "Investment"}</span>
                    <span>{accountEntity(account)}</span>
                  </div>
                  {account.owner ? <div className="row-sub">{account.owner}</div> : null}
                  <div className="row-top row-sub">
                    <span>{account.has_import ? `${formatMoney(account.total_closing_value)} latest value` : "No import yet"}</span>
                    {Number(account.cash_balance || 0) ? (
                      <span>
                        Cash {formatCashBalances(account)}
                      </span>
                    ) : (
                      <span />
                    )}
                  </div>
                  {account.notes ? <div className="row-sub">{account.notes}</div> : null}
                  <div className="row-actions">
                    <button className="quiet-button" type="button" onClick={() => onEdit(account)}>
                      Edit
                    </button>
                  </div>
                </div>
              ))
            ) : (
              <div className="empty-state">No accounts set up.</div>
            )}
          </div>
        </section>
      </section>
    </section>
  );
}

function TransactionsPage({ data, accounts, onSubmit, onDelete, onRefresh }) {
  const orderedAccounts = sortAccountsForDisplay(accounts);
  const transactions = data?.transactions || [];
  const firstAccountId = orderedAccounts[0]?.id || "";

  return (
    <section className="view active">
      <section className="summary-grid transaction-layout">
        <form className="section-panel account-setup-form transaction-form" onSubmit={onSubmit}>
          <div className="panel-heading">
            <h2>New Transaction</h2>
          </div>
          <label>
            <span>Account</span>
            <select name="account_id" defaultValue={firstAccountId} required>
              {orderedAccounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {shortAccountName(account.name)}
                </option>
              ))}
            </select>
          </label>
          <div className="transaction-form-grid">
            <label>
              <span>Date</span>
              <input name="transaction_date" type="date" defaultValue={todayInputValue()} required />
            </label>
            <label>
              <span>Type</span>
              <select name="transaction_type" defaultValue="DIVIDEND">
                {transactionTypes.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="transaction-form-grid">
            <label>
              <span>Ticker</span>
              <input name="symbol" type="text" autoComplete="off" />
            </label>
            <label>
              <span>Market</span>
              <select name="market" defaultValue="CDN">
                <option value="">None</option>
                <option value="CDN">CDN</option>
                <option value="US">US</option>
                <option value="TSX">TSX</option>
                <option value="TSXV">TSXV</option>
                <option value="MANUAL">Manual</option>
              </select>
            </label>
          </div>
          <label>
            <span>Description</span>
            <input name="description" type="text" autoComplete="off" />
          </label>
          <div className="transaction-form-grid">
            <label>
              <span>Currency</span>
              <select name="currency" defaultValue="CAD">
                <option value="CAD">CAD</option>
                <option value="USD">USD</option>
              </select>
            </label>
            <label>
              <span>Qty</span>
              <input name="quantity" type="number" step="0.0001" />
            </label>
          </div>
          <div className="transaction-form-grid">
            <label>
              <span>Price</span>
              <input name="price" type="number" step="0.0001" />
            </label>
            <label>
              <span>Dividend/share</span>
              <input name="dividend_per_share" type="number" step="0.0001" />
            </label>
          </div>
          <div className="transaction-form-grid">
            <label>
              <span>Gross</span>
              <input name="gross_amount" type="number" step="0.01" />
            </label>
            <label>
              <span>Net cash</span>
              <input name="net_amount" type="number" step="0.01" />
            </label>
          </div>
          <div className="transaction-form-grid">
            <label>
              <span>Fees</span>
              <input name="fees" type="number" step="0.01" />
            </label>
            <label>
              <span>Tax</span>
              <input name="tax" type="number" step="0.01" />
            </label>
          </div>
          <label>
            <span>Notes</span>
            <textarea name="notes" rows="3" />
          </label>
          <button type="submit" disabled={!orderedAccounts.length}>
            Save Transaction
          </button>
        </form>

        <section className="section-panel">
          <div className="panel-heading transactions-heading">
            <h2>Transactions</h2>
            <button className="quiet-button" type="button" onClick={onRefresh}>
              Refresh
            </button>
          </div>
          <div className="table-wrap transaction-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Account</th>
                  <th>Type</th>
                  <th>Description</th>
                  <th>Ticker</th>
                  <th>Currency</th>
                  <th className="numeric">Qty</th>
                  <th className="numeric">Price</th>
                  <th className="numeric">Gross</th>
                  <th className="numeric">Fees</th>
                  <th className="numeric">Tax</th>
                  <th className="numeric">Net Cash</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {!data ? (
                  <tr>
                    <td className="empty-state" colSpan="13">
                      Loading transactions...
                    </td>
                  </tr>
                ) : transactions.length ? (
                  transactions.map((transaction) => (
                    <tr key={transaction.id}>
                      <td>{formatMarketDate(transaction.transaction_date)}</td>
                      <td>{shortAccountName(transaction.account_name)}</td>
                      <td>{transactionTypeLabel(transaction.transaction_type)}</td>
                      <td>
                        <div className="description" title={transaction.notes || transaction.description || ""}>
                          {transaction.description || transaction.notes || ""}
                        </div>
                      </td>
                      <td>{transaction.symbol ? tickerLabel(transaction) : ""}</td>
                      <td>{transaction.currency}</td>
                      <td className="numeric">{transaction.quantity ? number.format(transaction.quantity) : ""}</td>
                      <td className="numeric">{transaction.price ? formatCurrency(transaction.price, transaction.currency) : ""}</td>
                      <td className="numeric">{formatCurrency(transaction.gross_amount, transaction.currency)}</td>
                      <td className="numeric">{transaction.fees ? formatCurrency(transaction.fees, transaction.currency) : ""}</td>
                      <td className="numeric">{transaction.tax ? formatCurrency(transaction.tax, transaction.currency) : ""}</td>
                      <td className={`numeric ${toneClass(transaction.net_amount)}`}>
                        {formatCurrency(transaction.net_amount, transaction.currency)}
                      </td>
                      <td className="numeric">
                        <button className="quiet-button compact-button" type="button" onClick={() => onDelete(transaction.id)}>
                          Remove
                        </button>
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td className="empty-state" colSpan="13">
                      No transactions recorded yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </section>
    </section>
  );
}

function UsersAdminPage({ data, onSubmit, onRefresh }) {
  const users = data?.users || [];
  const events = data?.login_events || [];

  return (
    <section className="view active">
      <section className="summary-grid admin-grid">
        <form className="section-panel account-setup-form" onSubmit={onSubmit}>
          <div className="panel-heading">
            <h2>New User</h2>
          </div>
          <label>
            <span>Username</span>
            <input name="username" type="text" autoComplete="off" required />
          </label>
          <label>
            <span>Password</span>
            <input name="password" type="password" autoComplete="new-password" minLength="8" required />
          </label>
          <label className="checkbox-row">
            <input name="is_admin" type="checkbox" />
            <span>Admin user</span>
          </label>
          <label className="checkbox-row">
            <input name="active" type="checkbox" defaultChecked />
            <span>Active</span>
          </label>
          <button type="submit">Create User</button>
        </form>

        <section className="section-panel">
          <div className="panel-heading">
            <h2>Users</h2>
            <button className="quiet-button" type="button" onClick={onRefresh}>
              Refresh
            </button>
          </div>
          <div className="table-wrap admin-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Username</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Last Login</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {!data ? (
                  <tr>
                    <td className="empty-state" colSpan="5">
                      Loading users...
                    </td>
                  </tr>
                ) : users.length ? (
                  users.map((user) => (
                    <tr key={user.id}>
                      <td>{user.username}</td>
                      <td>{user.is_admin ? "Admin" : "User"}</td>
                      <td>{user.active ? "Active" : "Disabled"}</td>
                      <td>{formatDate(user.last_login_at)}</td>
                      <td>{formatDate(user.created_at)}</td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td className="empty-state" colSpan="5">
                      No users yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </section>

      <section className="section-panel">
        <div className="panel-heading">
          <h2>Login Audit</h2>
          <span>{events.length} recent events</span>
        </div>
        <div className="table-wrap admin-table-wrap">
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Username</th>
                <th>Result</th>
                <th>IP</th>
                <th>User Agent</th>
              </tr>
            </thead>
            <tbody>
              {!data ? (
                <tr>
                  <td className="empty-state" colSpan="5">
                    Loading login audit...
                  </td>
                </tr>
              ) : events.length ? (
                events.map((event) => (
                  <tr key={event.id}>
                    <td>{formatDate(event.created_at)}</td>
                    <td>{event.username}</td>
                    <td className={event.success ? "positive" : "negative"}>{event.success ? "Success" : "Failed"}</td>
                    <td>{event.ip_address || ""}</td>
                    <td className="user-agent-cell" title={event.user_agent || ""}>
                      {event.user_agent || ""}
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td className="empty-state" colSpan="5">
                    No login events yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </section>
  );
}

function ImportsPage({ imports }) {
  return (
    <section className="view active">
      <section className="section-panel">
        <div className="panel-heading">
          <h2>Import History</h2>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Imported</th>
                <th>Account</th>
                <th>Report Date</th>
                <th className="numeric">Rows</th>
                <th className="numeric">Value</th>
                <th>Raw File</th>
              </tr>
            </thead>
            <tbody>
              {imports.length ? (
                imports.map((item) => (
                  <tr key={item.id}>
                    <td>{formatDate(item.imported_at)}</td>
                    <td>{item.account_name}</td>
                    <td>{item.report_timestamp || ""}</td>
                    <td className="numeric">{item.row_count}</td>
                    <td className="numeric">{formatMoney(item.total_closing_value)}</td>
                    <td>{item.stored_path}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td className="empty-state" colSpan="6">
                    No imports yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </section>
  );
}

function HistoryPage({ data, accounts, fileLabel, onFileChange, onUpload, onUpdate }) {
  const snapshots = data?.snapshots || [];
  const latest = snapshots[0];

  return (
    <section className="view active">
      <section className="section-panel">
        <div className="panel-heading history-heading">
          <div>
            <h2>Balance History</h2>
            {snapshots.length ? (
              <p>
                {snapshots.length} closes from {formatMarketDate(snapshots[snapshots.length - 1].market_date)} to{" "}
                {formatMarketDate(latest.market_date)}
              </p>
            ) : null}
          </div>
          <form className="history-upload-form" onSubmit={onUpload}>
            <label className="file-picker">
              <input name="file" type="file" accept=".csv,text/csv" onChange={onFileChange} />
              <span>{fileLabel}</span>
            </label>
            <button type="submit">Upload History</button>
          </form>
        </div>

        {!data ? <div className="empty-state">Loading history...</div> : null}
        {data && !snapshots.length ? (
          <div className="empty-state">No saved closes yet.</div>
        ) : null}
      </section>

      {snapshots.length ? (
        <>
          <Metrics
            metrics={[
              ["Latest Close", latest.total_value],
              ["Book Value", latest.book_value],
              ["Day Change", latest.day_change, "tone"],
              ["Return", latest.gain_loss_pct, "percentTone"],
            ]}
          />

          <section className="summary-grid">
            <section className="section-panel">
              <div className="panel-heading">
                <h2>Total Value</h2>
              </div>
              <HistoryChart snapshots={snapshots} />
            </section>

            <section className="section-panel">
              <div className="panel-heading">
                <h2>Latest Accounts</h2>
              </div>
              <HistoryAccountList accounts={latest.accounts || []} totalValue={latest.total_value} />
            </section>
          </section>

          <section className="section-panel">
            <div className="panel-heading">
              <h2>Saved Closes</h2>
            </div>
            <HistoryTable snapshots={snapshots} accounts={accounts} onUpdate={onUpdate} />
          </section>
        </>
      ) : null}
    </section>
  );
}

function HistoryChart({ snapshots }) {
  const points = [...snapshots].reverse().filter((snapshot) => Number(snapshot.total_value || 0) > 0);
  if (points.length < 2) {
    return <div className="empty-state">At least two saved closes are needed for a chart.</div>;
  }

  const width = 900;
  const height = 260;
  const pad = { top: 18, right: 24, bottom: 28, left: 116 };
  const values = points.map((point) => Number(point.total_value || 0));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  function x(index) {
    return pad.left + (index / (points.length - 1)) * (width - pad.left - pad.right);
  }

  function y(value) {
    return height - pad.bottom - ((Number(value || 0) - min) / range) * (height - pad.top - pad.bottom);
  }

  const line = points.map((point, index) => `${x(index).toFixed(1)},${y(point.total_value).toFixed(1)}`).join(" ");

  return (
    <div className="history-chart-wrap">
      <svg className="history-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Saved balance history chart">
        {[0, 0.5, 1].map((step) => {
          const value = min + range * step;
          const lineY = y(value);
          return (
            <g key={step}>
              <line x1={pad.left} x2={width - pad.right} y1={lineY} y2={lineY} />
              <text x={pad.left - 10} y={lineY + 4}>
                {formatMoney(value)}
              </text>
            </g>
          );
        })}
        <polyline points={line} />
      </svg>
    </div>
  );
}

function HistoryAccountList({ accounts, totalValue }) {
  if (!accounts.length) {
    return <div className="empty-state">No account rows saved for this close.</div>;
  }

  return (
    <div className="account-list">
      {accounts.map((account) => (
        <div key={account.id} className="account-row">
          <div className="row-top">
            <span className="row-title">{account.account_name}</span>
            <span className="numeric">{formatMoney(account.value)}</span>
          </div>
          <div className="row-top row-sub">
            <span>{account.account_type || "Investment"}</span>
            <span>{formatPercent(totalValue ? (Number(account.value || 0) / totalValue) * 100 : 0)}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

function historyAccountColumns(snapshots, accounts) {
  const columns = new Map();
  const currentAccounts = new Map(accounts.map((account) => [Number(account.id), account]));
  snapshots.forEach((snapshot) => {
    (snapshot.accounts || []).forEach((account) => {
      const accountId = Number(account.account_id);
      const currentAccount = currentAccounts.get(accountId);
      columns.set(accountId, {
        accountId,
        accountName: currentAccount?.name || account.account_name,
        account_entity: currentAccount?.account_entity || account.account_entity || "Personal",
      });
    });
  });
  return sortAccountsForDisplay(
    Array.from(columns.values()).map((account) => ({
      id: account.accountId,
      name: account.accountName,
      account_entity: account.account_entity,
    }))
  ).map((account) => ({
    accountId: account.id,
    accountName: account.name,
  }));
}

function EditableHistoryNumber({ value, step = "0.01", className = "", onSave }) {
  const initial = value === null || value === undefined ? "" : Number(value).toFixed(step === "0.0001" ? 4 : 2);

  function saveIfChanged(input) {
    const raw = String(input.value || "").trim();
    if (raw === "" && initial === "") {
      return;
    }
    if (raw === "") {
      input.value = initial;
      return;
    }
    const original = initial === "" ? "" : String(Number(initial));
    const next = raw === "" ? "" : String(Number(raw));
    if (next === original || Number.isNaN(Number(raw))) {
      input.value = initial;
      return;
    }
    onSave(Number(raw));
  }

  return (
    <input
      className={`history-edit-input ${className}`}
      type="number"
      step={step}
      defaultValue={initial}
      onFocus={(event) => event.currentTarget.select()}
      onBlur={(event) => saveIfChanged(event.currentTarget)}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.currentTarget.blur();
        }
        if (event.key === "Escape") {
          event.currentTarget.value = initial;
          event.currentTarget.blur();
        }
      }}
    />
  );
}

function HistoryTable({ snapshots, accounts, onUpdate }) {
  const accountColumns = historyAccountColumns(snapshots, accounts);

  return (
    <div className="table-wrap history-table-wrap">
      <table className="history-table">
        <thead>
          <tr>
            <th>Date</th>
            {accountColumns.map((account) => (
              <th key={account.accountId} className="numeric">
                {shortAccountName(account.accountName)}
              </th>
            ))}
            <th className="numeric">Total</th>
            <th className="numeric">Day Change</th>
            <th>Saved</th>
          </tr>
        </thead>
        <tbody>
          {snapshots.map((snapshot) => {
            const valuesByAccount = new Map((snapshot.accounts || []).map((account) => [account.account_id, account.value]));
            return (
              <tr key={snapshot.market_date}>
                <td>{formatMarketDate(snapshot.market_date)}</td>
                {accountColumns.map((account) => (
                  <td key={`${snapshot.market_date}-${account.accountId}`} className="numeric">
                    <EditableHistoryNumber
                      value={valuesByAccount.has(account.accountId) ? valuesByAccount.get(account.accountId) : ""}
                      onSave={(value) =>
                        onUpdate(snapshot.market_date, {
                          account_values: [{ account_id: account.accountId, value }],
                        })
                      }
                    />
                  </td>
                ))}
                <td className="numeric">
                  <EditableHistoryNumber
                    value={snapshot.total_value}
                    onSave={(value) => onUpdate(snapshot.market_date, { total_value: value })}
                  />
                </td>
                <td className={`numeric ${toneClass(snapshot.day_change)}`}>
                  <EditableHistoryNumber
                    value={snapshot.day_change}
                    className={toneClass(snapshot.day_change)}
                    onSave={(value) => onUpdate(snapshot.market_date, { day_change: value })}
                  />
                  <div className="row-sub">
                    <EditableHistoryNumber
                      value={snapshot.day_change_pct}
                      step="0.0001"
                      className={toneClass(snapshot.day_change_pct)}
                      onSave={(value) => onUpdate(snapshot.market_date, { day_change_pct: value })}
                    />
                  </div>
                </td>
                <td>{formatDate(snapshot.updated_at)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Metrics({ metrics }) {
  return (
    <section className="metrics">
      {metrics.map(([label, value, tone]) => (
        <article className="metric" key={label}>
          <span>{label}</span>
          <strong className={tone === "raw" ? "" : tone ? toneClass(value) : ""}>
            {tone === "percentTone" ? formatPercent(value) : tone === "raw" ? value : formatMoney(value)}
          </strong>
        </article>
      ))}
    </section>
  );
}

function AccountSummaryTable({ accounts, incomeByAccount, onActivate }) {
  if (!accounts.length) {
    return <div className="empty-state">No accounts imported.</div>;
  }

  const orderedAccounts = sortAccountsForDisplay(accounts);
  const entityTotals = entityTotalsFromAccounts(orderedAccounts, incomeByAccount);
  const accountGroups = ["Personal", "Corporate"]
    .map((entity) => ({
      entity,
      accounts: orderedAccounts.filter((account) => accountEntity(account) === entity),
      totals: entityTotals[entity],
    }))
    .filter((group) => group.accounts.length);
  const totals = orderedAccounts.reduce(
    (sum, account) => {
      const balance = Number(account.current_total_value ?? account.total_closing_value ?? 0);
      const dayChange = Number(account.day_change || 0);
      sum.balance += balance;
      sum.cash += Number(account.cash_balance || 0);
      sum.income += Number(incomeByAccount.get(account.name) || 0);
      sum.dayChange += dayChange;
      return sum;
    },
    { balance: 0, cash: 0, income: 0, dayChange: 0 }
  );
  const previousBalance = totals.balance - totals.dayChange;
  const totalDayChangePct = previousBalance ? (totals.dayChange / previousBalance) * 100 : null;

  return (
    <div className="table-wrap summary-account-wrap">
      <table className="summary-account-table">
        <thead>
          <tr>
            <th />
            <th className="numeric">Balance</th>
            <th className="numeric">Cash</th>
            <th className="numeric">Income</th>
            <th className="numeric">DoD</th>
            <th className="numeric">DoD$</th>
          </tr>
        </thead>
        <tbody>
          {accountGroups.map((group) => {
            const previousBalance = group.totals.balance - group.totals.dayChange;
            const dayChangePct = previousBalance ? (group.totals.dayChange / previousBalance) * 100 : null;
            return (
              <React.Fragment key={group.entity}>
                <tr className="summary-account-group-row">
                  <td>{group.entity}</td>
                  <td className="numeric">{formatMoney(group.totals.balance)}</td>
                  <td className="numeric">{formatMoney(group.totals.cash)}</td>
                  <td className="numeric">{formatMoney(group.totals.income)}</td>
                  <td className={`numeric ${toneClass(dayChangePct)}`}>{formatPercent(dayChangePct)}</td>
                  <td className={`numeric ${toneClass(group.totals.dayChange)}`}>{formatMoney(group.totals.dayChange)}</td>
                </tr>
                {group.accounts.map((account) => (
                  <tr key={account.id} onClick={() => onActivate("account", account.id)}>
                    <td>{shortAccountName(account.name)}</td>
                    <td className="numeric">{formatMoney(account.current_total_value ?? account.total_closing_value)}</td>
                    <td className="numeric">{formatMoney(account.cash_balance || 0)}</td>
                    <td className="numeric">{formatMoney(incomeByAccount.get(account.name) || 0)}</td>
                    <td className={`numeric ${toneClass(account.day_change_pct)}`}>{formatPercent(account.day_change_pct)}</td>
                    <td className={`numeric ${toneClass(account.day_change)}`}>{formatMoney(account.day_change)}</td>
                  </tr>
                ))}
              </React.Fragment>
            );
          })}
        </tbody>
        <tfoot>
          <tr>
            <td>Total</td>
            <td className="numeric">{formatMoney(totals.balance)}</td>
            <td className="numeric">{formatMoney(totals.cash)}</td>
            <td className="numeric">{formatMoney(totals.income)}</td>
            <td className={`numeric ${toneClass(totalDayChangePct)}`}>{formatPercent(totalDayChangePct)}</td>
            <td className={`numeric ${toneClass(totals.dayChange)}`}>{formatMoney(totals.dayChange)}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

function CurrencyList({ currencies }) {
  const visible = currencies.filter((currency) => Number(currency.closing_value || 0) || Number(currency.cash_value || 0));
  if (!visible.length) {
    return <div className="empty-state">No currency summaries.</div>;
  }

  return (
    <div className="currency-list">
      {visible.map((currency) => (
        <div key={`${currency.account_name}-${currency.currency}`} className="currency-row">
          <div className="row-top">
            <span className="row-title">
              {currency.currency} - {shortAccountName(currency.account_name)}
            </span>
            <span className="numeric">{formatMoney(currency.closing_value)}</span>
          </div>
          <div className="row-top row-sub">
            <span>
              Securities {formatMoney(currency.securities_value)}
              {Number(currency.cash_value || 0) ? ` - Cash ${formatMoney(currency.cash_value)}` : ""}
            </span>
            <span className={toneClass(currency.gain_loss)}>{formatPercent(currency.gain_loss_pct)}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

function Allocation({ allocation }) {
  const visible = allocation.filter((item) => Number(item.closing_value || 0) > 0);
  if (!visible.length) {
    return <div className="empty-state">No allocation data.</div>;
  }

  return (
    <>
      <div className="allocation-bar">
        {visible.map((item, index) => (
          <div
            key={`${item.account_name}-${item.symbol}-${index}`}
            className="allocation-segment"
            title={`${item.symbol} ${formatPercent(item.portfolio_pct)}`}
            style={{
              width: `${Math.max(0.2, Number(item.portfolio_pct || 0))}%`,
              background: colors[index % colors.length],
            }}
          />
        ))}
      </div>
      <div className="allocation-list">
        {visible.map((item, index) => (
          <div key={`${item.account_name}-${item.symbol}-${index}`} className="allocation-row">
            <div className="row-top">
              <div className="allocation-name">
                <span className="swatch" style={{ background: colors[index % colors.length] }} />
                <div>
                  <div className="row-title">{item.symbol}</div>
                  <div className="row-sub">
                    {item.description} - {shortAccountName(item.account_name)}
                  </div>
                </div>
              </div>
              <div className="numeric">
                {formatPercent(item.portfolio_pct)}
                <div className="row-sub">{formatMoney(item.closing_value)}</div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

function CombinedAllocation({ allocation }) {
  const visible = allocation.filter((item) => Number(item.closing_value || 0) > 0);
  if (!visible.length) {
    return <div className="empty-state">No allocation data.</div>;
  }

  return (
    <>
      <div className="allocation-bar">
        {visible.map((item, index) => (
          <div
            key={item.key}
            className="allocation-segment"
            title={`${item.symbol} ${formatPercent(item.portfolio_pct)}`}
            style={{
              width: `${Math.max(0.2, Number(item.portfolio_pct || 0))}%`,
              background: colors[index % colors.length],
            }}
          />
        ))}
      </div>
      <div className="allocation-list">
        {visible.map((item, index) => (
          <div key={item.key} className="allocation-row allocation-group">
            <div className="row-top">
              <div className="allocation-name">
                <span className="swatch" style={{ background: colors[index % colors.length] }} />
                <div>
                  <div className="row-title">{item.symbol}</div>
                  <div className="row-sub">{item.description}</div>
                </div>
              </div>
              <div className="numeric">
                {formatPercent(item.portfolio_pct)}
                <div className="row-sub">{formatMoney(item.closing_value)}</div>
              </div>
            </div>
            <div className="allocation-breakdown">
              {item.accounts.map((account) => (
                <div className="allocation-breakdown-row" key={`${item.key}-${account.account_name}`}>
                  <span>
                    {shortAccountName(account.account_name)}
                    {account.average_cost ? <span className="breakdown-share"> Avg {formatMoney(account.average_cost)}</span> : null}
                  </span>
                  <span className="numeric">
                    {formatMoney(account.closing_value)}
                    <span className="breakdown-share"> {formatPercent(account.category_pct)}</span>
                  </span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

function TableToolbar({ value, onChange, count, placeholder, children }) {
  return (
    <div className="table-toolbar">
      <input type="search" value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} />
      <div className="table-toolbar-meta">
        {children}
        <span>{count} holdings</span>
      </div>
    </div>
  );
}

function HoldingsTable({ holdings, accountName, sort, weightKey, showAccount, showDerisk, onSort, onOpenStock, onOpenTrade }) {
  const baseHeaders = [
    ["trade", "", "action-column", false],
    ["description", "Company"],
    ["account_weight", "Alloc", "numeric"],
    ["symbol", "Ticker"],
    ["fx_to_cad", "FX", "numeric"],
    ["quantity", "Qty", "numeric"],
    ["average_cost", "Avg", "numeric"],
    ["current_price", "Current", "numeric"],
    ["book_value", "Orig", "numeric"],
    ["previous_value", "Yest", "numeric"],
    ["current_value", "Total", "numeric"],
    ["current_gain_loss_pct", "Gain/Loss", "numeric"],
    ["day_change_pct", "Day", "numeric"],
    ["day_value_change", "DoD", "numeric"],
    ["annual_forward_income", "Income", "numeric"],
    ...dividendIncomeForecastDates.map(([label]) => [forecastIncomeField(label), `Forecast ${label}`, "numeric"]),
  ];
  const deriskHeaders = [
    ["derisk_shares", "De-risk", "numeric derisk-column", false],
    ["derisk_sell_pct", "Sell %", "numeric derisk-column", false],
    ["derisk_recover", "Recover", "numeric derisk-column", false],
    ["derisk_left_shares", "Left", "numeric derisk-column", false],
    ["derisk_left_value", "Left $", "numeric derisk-column", false],
    ["derisk_income_lost", "Income Lost", "numeric derisk-column", false],
  ];
  const headers = showDerisk ? [...baseHeaders, ...deriskHeaders] : baseHeaders;
  const totals = holdings.reduce(
    (sum, holding) => {
      const derisk = deriskForHolding(holding);
      sum.currentValue += Number((holding.current_value ?? holding.closing_value) || 0);
      sum.dayValueChange += Number(holding.day_value_change || 0);
      sum.annualIncome += Number(holding.annual_forward_income || 0);
      dividendIncomeForecastDates.forEach(([label]) => {
        const forecastValue = holding[forecastIncomeField(label)];
        if (forecastValue === null || forecastValue === undefined || Number.isNaN(Number(forecastValue))) {
          if (Number(holding.annual_forward_income || 0) > 0) {
            sum.forecastIncomeMissing[label] = true;
          }
        } else {
          sum.forecastIncome[label] += Number(forecastValue);
        }
      });
      if (derisk.possible) {
        sum.deriskRecover += derisk.proceeds;
        sum.deriskValueLeft += derisk.valueLeft;
        sum.deriskIncomeLost += derisk.incomeLost;
      }
      return sum;
    },
    {
      currentValue: 0,
      dayValueChange: 0,
      annualIncome: 0,
      forecastIncome: Object.fromEntries(dividendIncomeForecastDates.map(([label]) => [label, 0])),
      forecastIncomeMissing: Object.fromEntries(dividendIncomeForecastDates.map(([label]) => [label, false])),
      deriskRecover: 0,
      deriskValueLeft: 0,
      deriskIncomeLost: 0,
    }
  );

  return (
    <div className="table-wrap account-holdings-wrap">
      <table className={`account-holdings-table ${showDerisk ? "show-derisk" : ""}`}>
        <thead>
          {accountName ? (
            <tr className="account-holdings-title-row">
              <th />
              <th colSpan={headers.length - 1}>{shortAccountName(accountName)}</th>
            </tr>
          ) : null}
          <tr>
            {headers.map(([key, label, className, sortable = true]) => (
              <th
                key={key}
                className={`${className || ""}${sortable ? " sortable-header" : ""}`}
                onClick={sortable ? () => onSort(key) : undefined}
              >
                {label}
                {sortable && sort.key === key ? (sort.direction === "asc" ? " ^" : " v") : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {holdings.length ? (
            holdings.map((holding) => {
              const derisk = deriskForHolding(holding);
              return (
                <tr key={`${holding.id}-${holding.account_name}`}>
                  <td className="action-cell">
                    {onOpenTrade && canTradeHolding(holding) ? (
                      <button
                        className="icon-button trade-button"
                        type="button"
                        title={`Trade ${tickerLabel(holding)}`}
                        aria-label={`Trade ${tickerLabel(holding)}`}
                        onClick={() => onOpenTrade(holding)}
                      >
                        <TradeIcon />
                      </button>
                    ) : null}
                  </td>
                  <td>
                    <div className="description account-company" title={holding.description}>
                      {holding.description}
                    </div>
                    {showAccount ? <div className="row-sub">{shortAccountName(holding.account_name)}</div> : null}
                  </td>
                  <td className="numeric">{formatPercent(holding.account_weight)}</td>
                  <td>
                    {onOpenStock && canOpenStock(holding) ? (
                      <button className="symbol-link" type="button" onClick={() => onOpenStock(holding)}>
                        {tickerLabel(holding)}
                      </button>
                    ) : (
                      <div className="symbol">{tickerLabel(holding)}</div>
                    )}
                  </td>
                  <td className="numeric">{formatFx(holding.fx_to_cad)}</td>
                  <td className="numeric">{number.format(holding.quantity || 0)}</td>
                  <td className="numeric">{formatMoney(holding.average_cost)}</td>
                  <td className="numeric current-price-cell">{formatMoney(holding.current_price ?? holding.closing_price)}</td>
                  <td className="numeric">{formatWholeMoney(holding.book_value)}</td>
                  <td className="numeric">{formatWholeMoney(holding.previous_value)}</td>
                  <td className="numeric">{formatWholeMoney(holding.current_value ?? holding.closing_value)}</td>
                  <td className={`numeric ${toneClass(holding.current_gain_loss ?? holding.gain_loss)}`}>
                    {formatPercent(holding.current_gain_loss_pct ?? holding.gain_loss_pct)}
                  </td>
                  <td className={`numeric account-day-pct ${toneClass(holding.day_change_pct)}`}>
                    {formatPercent(holding.day_change_pct)}
                  </td>
                  <td className={`numeric ${toneClass(holding.day_value_change)}`}>
                    {holding.day_value_change === null || holding.day_value_change === undefined
                      ? ""
                      : formatWholeMoney(holding.day_value_change)}
                  </td>
                  <td className="numeric income-cell">{formatWholeMoney(holding.annual_forward_income)}</td>
                  {dividendIncomeForecastDates.map(([label]) => {
                    const key = forecastIncomeField(label);
                    return (
                      <td key={key} className="numeric income-cell">
                        {formatForecastIncome(holding[key])}
                      </td>
                    );
                  })}
                  {showDerisk ? (
                    derisk.possible ? (
                      <>
                        <td className="numeric derisk-column">{number.format(derisk.sharesToSell)}</td>
                        <td className="numeric derisk-column">{formatPercent(derisk.sellPct)}</td>
                        <td className="numeric derisk-column">{formatWholeMoney(derisk.proceeds)}</td>
                        <td className="numeric derisk-column">{number.format(derisk.sharesLeft)}</td>
                        <td className="numeric derisk-column">{formatWholeMoney(derisk.valueLeft)}</td>
                        <td className="numeric derisk-column">{formatWholeMoney(derisk.incomeLost)}</td>
                      </>
                    ) : (
                      <>
                        <td className="numeric derisk-column derisk-na">
                          {derisk.status === "not_possible" ? "Not possible" : "n/a"}
                        </td>
                        <td className="numeric derisk-column" />
                        <td className="numeric derisk-column" />
                        <td className="numeric derisk-column" />
                        <td className="numeric derisk-column" />
                        <td className="numeric derisk-column" />
                      </>
                    )
                  ) : null}
                </tr>
              );
            })
          ) : (
            <tr>
              <td className="empty-state" colSpan={headers.length}>
                No holdings match the current filter.
              </td>
            </tr>
          )}
        </tbody>
        {holdings.length ? (
          <tfoot>
            <tr>
              <td />
              <td>Total</td>
              <td />
              <td />
              <td />
              <td />
              <td />
              <td />
              <td />
              <td />
              <td className="numeric">{formatWholeMoney(totals.currentValue)}</td>
              <td />
              <td />
              <td className={`numeric ${toneClass(totals.dayValueChange)}`}>
                {formatWholeMoney(totals.dayValueChange)}
              </td>
              <td className="numeric income-cell">{formatWholeMoney(totals.annualIncome)}</td>
              {dividendIncomeForecastDates.map(([label]) => (
                <td key={label} className="numeric income-cell">
                  {totals.forecastIncomeMissing[label] ? "n/a" : formatWholeMoney(totals.forecastIncome[label])}
                </td>
              ))}
              {showDerisk ? (
                <>
                  <td />
                  <td />
                  <td className="numeric derisk-column">{formatWholeMoney(totals.deriskRecover)}</td>
                  <td />
                  <td className="numeric derisk-column">{formatWholeMoney(totals.deriskValueLeft)}</td>
                  <td className="numeric derisk-column">{formatWholeMoney(totals.deriskIncomeLost)}</td>
                </>
              ) : null}
            </tr>
          </tfoot>
        ) : null}
      </table>
    </div>
  );
}

function sortedHoldings(holdings, queryText, sort) {
  const query = queryText.trim().toLowerCase();
  const filtered = holdings.filter((holding) => {
    if (!query) return true;
    return [holding.symbol, holding.description, holding.asset_type, holding.market, holding.account_name]
      .filter(Boolean)
      .join(" ")
      .toLowerCase()
      .includes(query);
  });

  return [...filtered].sort((a, b) => {
    const aValue = a[sort.key];
    const bValue = b[sort.key];
    const direction = sort.direction === "asc" ? 1 : -1;

    if (typeof aValue === "number" || typeof bValue === "number") {
      return ((Number(aValue) || 0) - (Number(bValue) || 0)) * direction;
    }

    return String(aValue || "").localeCompare(String(bValue || "")) * direction;
  });
}

function groupCombinedAllocation(holdings, totalValue) {
  const groups = new Map();

  holdings
    .filter((holding) => Number(holding.closing_value || 0) > 0)
    .forEach((holding) => {
      const key = `${holding.symbol || holding.description}-${holding.currency || ""}`;
      if (!groups.has(key)) {
        groups.set(key, {
          key,
          symbol: holding.symbol,
          description: holding.description,
          currency: holding.currency,
          closing_value: 0,
          quantity: 0,
          book_value: 0,
          accounts: new Map(),
        });
      }

      const group = groups.get(key);
      const value = Number((holding.current_value ?? holding.closing_value) || 0);
      group.closing_value += value;
      group.quantity += Number(holding.quantity || 0);
      group.book_value += Number(holding.book_value || 0);

      const account = group.accounts.get(holding.account_name) || {
        account_name: holding.account_name,
        closing_value: 0,
        quantity: 0,
        book_value: 0,
      };
      account.closing_value += value;
      account.quantity += Number(holding.quantity || 0);
      account.book_value += Number(holding.book_value || 0);
      group.accounts.set(holding.account_name, account);
    });

  return Array.from(groups.values())
    .map((group) => ({
      ...group,
      average_cost: group.quantity ? group.book_value / group.quantity : null,
      portfolio_pct: totalValue ? (group.closing_value / totalValue) * 100 : 0,
      accounts: Array.from(group.accounts.values())
        .map((account) => ({
          ...account,
          average_cost: account.quantity ? account.book_value / account.quantity : null,
          category_pct: group.closing_value ? (account.closing_value / group.closing_value) * 100 : 0,
          portfolio_pct: totalValue ? (account.closing_value / totalValue) * 100 : 0,
        }))
        .sort((a, b) => Number(b.closing_value || 0) - Number(a.closing_value || 0)),
    }))
    .sort((a, b) => Number(b.closing_value || 0) - Number(a.closing_value || 0));
}

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
