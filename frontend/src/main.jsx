import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
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
const dayMs = 24 * 60 * 60 * 1000;

const money = new Intl.NumberFormat("en-CA", {
  style: "currency",
  currency: "CAD",
  maximumFractionDigits: 2,
});

const number = new Intl.NumberFormat("en-CA", {
  maximumFractionDigits: 4,
});

class AuthError extends Error {}

function formatMoney(value) {
  return money.format(Number(value || 0));
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

function shortAccountName(name) {
  return String(name || "")
    .replace(/^\d+\s+/, "")
    .replace(/\s+-\s+Combined Holdings$/i, "");
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
  const [auth, setAuth] = useState({ status: "checking", username: null });
  const [data, setData] = useState(null);
  const [historyData, setHistoryData] = useState(null);
  const [activeView, setActiveView] = useState("summary");
  const [activeAccountId, setActiveAccountId] = useState(null);
  const [accountQuery, setAccountQuery] = useState("");
  const [sort, setSort] = useState({ key: "current_value", direction: "desc" });
  const [message, setMessage] = useState(null);
  const [fileLabel, setFileLabel] = useState("Choose CSV");
  const [historyFileLabel, setHistoryFileLabel] = useState("Choose CSV");
  const [editingAccount, setEditingAccount] = useState(null);

  async function loadSummary() {
    const response = await apiFetch("/api/summary");
    const payload = await parseApiResponse(response);
    setData(payload);
    if (activeView === "account" && !payload.accounts.some((account) => account.id === activeAccountId)) {
      setActiveView("summary");
      setActiveAccountId(null);
    }
  }

  async function loadHistory() {
    const response = await apiFetch("/api/balance-snapshots?limit=5000");
    const payload = await parseApiResponse(response);
    setHistoryData(payload);
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
          setAuth({ status: "anonymous", username: null });
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
      setAuth({ status: "authenticated", username: payload.username });
      await loadSummary();
    } catch (error) {
      if (error instanceof AuthError) {
        setAuth({ status: "anonymous", username: null });
        return;
      }
      setAuth({ status: "anonymous", username: null });
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

    button.disabled = true;
    try {
      const response = await apiFetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await parseApiResponse(response);
      setAuth({ status: "authenticated", username: result.username });
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
    setAuth({ status: "anonymous", username: null });
  }

  function activateView(view, accountId = null) {
    setActiveView(view);
    setActiveAccountId(accountId ? Number(accountId) : null);
    if (view !== "account" && sort.key === "account_weight") {
      setSort({ key: "current_value", direction: "desc" });
    }
    if (view === "history" || (view === "account" && !historyData)) {
      loadHistory().catch((error) => {
        if (error instanceof AuthError) {
          setAuth({ status: "anonymous", username: null });
        } else {
          showMessage(error.message, true);
        }
      });
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
        setAuth({ status: "anonymous", username: null });
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
        setAuth({ status: "anonymous", username: null });
      }
      showMessage(error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  async function handleSaveAccount(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector("button");
    const payload = Object.fromEntries(new FormData(form).entries());
    const accountId = payload.id;
    delete payload.id;
    if ("cash_balance" in payload && String(payload.cash_balance).trim() === "") {
      payload.cash_balance = "0";
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
      form.elements.base_currency.value = "CAD";
      setEditingAccount(null);
      await loadSummary();
      setActiveView("accounts");
    } catch (error) {
      if (error instanceof AuthError) {
        setAuth({ status: "anonymous", username: null });
      }
      showMessage(error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  const activeAccount = data?.accounts.find((account) => account.id === activeAccountId) || null;
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
          <h1>Portfolio Tracker</h1>
          <p>{latestReport ? `Latest holdings: ${latestReport}` : "Local holdings snapshots"}</p>
        </div>
        <ImportForm
          accounts={data.all_accounts || data.accounts}
          fileLabel={fileLabel}
          onFileChange={(event) => setFileLabel(event.target.files[0]?.name || "Choose CSV")}
          onSubmit={handleImport}
          username={auth.username}
          onOpenAccounts={() => activateView("accounts")}
          onOpenImports={() => activateView("imports")}
          onOpenHistory={() => activateView("history")}
          onLogout={handleLogout}
        />
      </header>

      <main>
        <section className="app-panel">
          <Tabs
            accounts={data.accounts}
            activeView={activeView}
            activeAccountId={activeAccountId}
            onActivate={activateView}
          />

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

          {activeView === "imports" && <ImportsPage imports={data.imports} />}

          {activeView === "history" && (
            <HistoryPage
              data={historyData}
              accounts={data.all_accounts || data.accounts || []}
              fileLabel={historyFileLabel}
              onFileChange={(event) => setHistoryFileLabel(event.target.files[0]?.name || "Choose CSV")}
              onUpload={handleHistoryImport}
            />
          )}
        </section>
      </main>
    </>
  );
}

function LoginScreen({ message, onLogin }) {
  return (
    <main className="login-screen">
      <form className="login-panel" onSubmit={onLogin}>
        <div>
          <h1>Portfolio Tracker</h1>
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

function ImportForm({
  accounts,
  fileLabel,
  onFileChange,
  onSubmit,
  username,
  onOpenAccounts,
  onOpenImports,
  onOpenHistory,
  onLogout,
}) {
  const [menuOpen, setMenuOpen] = useState(false);

  function runMenuAction(action) {
    setMenuOpen(false);
    action();
  }

  return (
    <div className="top-actions">
      <form className="upload-form" onSubmit={onSubmit}>
        <label className="file-picker">
          <input name="file" type="file" accept=".csv,text/csv" onChange={onFileChange} />
          <span>{fileLabel}</span>
        </label>
        <input name="account_name" type="text" list="accountOptions" placeholder="Account override" />
        <datalist id="accountOptions">
          {accounts.map((account) => (
            <option key={account.id} value={account.name} />
          ))}
        </datalist>
        <input name="cash_balance" type="number" step="0.01" min="0" placeholder="Cash balance" />
        <select name="cash_currency" aria-label="Cash currency" defaultValue="CAD">
          <option value="CAD">CAD</option>
          <option value="USD">USD</option>
        </select>
        <button type="submit">Import</button>
      </form>
      <div className="session-menu">
        <button className="session-menu-button" type="button" onClick={() => setMenuOpen((open) => !open)}>
          <span>{username}</span>
          <span aria-hidden="true">v</span>
        </button>
        {menuOpen ? (
          <div className="session-menu-panel">
            <button type="button" onClick={() => runMenuAction(onOpenAccounts)}>
              Accounts
            </button>
            <button type="button" onClick={() => runMenuAction(onOpenImports)}>
              Imports
            </button>
            <button type="button" onClick={() => runMenuAction(onOpenHistory)}>
              History
            </button>
            <button type="button" onClick={() => runMenuAction(onLogout)}>
              Sign Out
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function Tabs({ accounts, activeView, activeAccountId, onActivate }) {
  return (
    <div className="tabs" role="tablist" aria-label="Portfolio pages">
      <button className={`tab ${activeView === "summary" ? "active" : ""}`} onClick={() => onActivate("summary")}>
        Summary
      </button>
      {accounts.map((account) => (
        <button
          key={account.id}
          className={`tab ${activeView === "account" && activeAccountId === account.id ? "active" : ""}`}
          onClick={() => onActivate("account", account.id)}
        >
          {shortAccountName(account.name)}
        </button>
      ))}
    </div>
  );
}

function SummaryPage({ data, onActivate }) {
  return (
    <section className="view active">
      <Metrics
        metrics={[
          ["Current Value", data.totals.current_value ?? data.totals.closing_value],
          ["Book Value", data.totals.book_value],
          ["Day Change", data.totals.day_change, "tone"],
          ["Current Gain", data.totals.current_gain_loss ?? data.totals.gain_loss, "tone"],
          ["Current Return", data.totals.current_gain_loss_pct ?? data.totals.gain_loss_pct, "percentTone"],
        ]}
      />
      <PriceRefreshStatus status={data.price_refresh} />
      <BalanceSnapshotStatus snapshot={data.balance_snapshot?.latest} />

      <section className="section-panel">
        <div className="panel-heading">
          <h2>Accounts</h2>
        </div>
        <AccountSummaryTable accounts={data.accounts} onActivate={onActivate} />
      </section>
    </section>
  );
}

function BalanceSnapshotStatus({ snapshot }) {
  if (!snapshot) {
    return null;
  }

  return (
    <div className="price-status">
      <span>Last saved close {snapshot.market_date}</span>
      <span>{formatMoney(snapshot.total_value)}</span>
      {snapshot.updated_at ? <span>Saved {formatDate(snapshot.updated_at)}</span> : null}
    </div>
  );
}

function PriceRefreshStatus({ status }) {
  if (!status) {
    return null;
  }

  const refresh = status.refresh || {};
  const schedule = status.schedule || {};
  const latest = status.latest_fetched_at || refresh.completed_at;
  return (
    <div className={`price-status ${refresh.status === "error" ? "error" : ""}`}>
      <span>{status.price_count || 0} live prices</span>
      {latest ? <span>Last price update {formatDate(latest)}</span> : <span>Waiting for first price refresh</span>}
      {schedule.start && schedule.end ? (
        <span>
          Fetch window {schedule.start}-{schedule.end} {schedule.timezone || ""}
          {schedule.in_window === false ? " (paused)" : ""}
        </span>
      ) : null}
      {status.total_day_change !== null && status.total_day_change !== undefined ? (
        <span className={toneClass(status.total_day_change)}>Daily price change {formatMoney(status.total_day_change)}</span>
      ) : null}
      {refresh.error ? <span>{refresh.error}</span> : null}
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

function AccountPage({ account, holdings, history, query, sort, onQuery, onSort, onActivate }) {
  const accountHoldings = useMemo(
    () =>
      holdings
        .filter((holding) => holding.account_name === account.name)
        .map((holding) => ({
          ...holding,
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

  const allocation = accountHoldings
    .filter((holding) => Number(holding.closing_value || 0) > 0)
    .sort((a, b) => Number(b.closing_value || 0) - Number(a.closing_value || 0))
    .map((holding) => ({
      symbol: holding.symbol,
      description: holding.description,
      closing_value: holding.current_value ?? holding.closing_value,
      currency: holding.currency,
      account_name: holding.account_name,
      portfolio_pct: holding.account_weight,
    }));

  return (
    <section className="view active">
      <div className="account-header">
        <div>
          <h2>{account.name}</h2>
          <p>{account.report_timestamp || ""}</p>
        </div>
        <button className="quiet-button" onClick={() => onActivate("summary")}>
          Summary
        </button>
      </div>

      <Metrics
        metrics={[
          ["Account Value", account.total_closing_value],
          ["Current Value", account.current_total_value ?? account.total_closing_value],
          ["Book Value", account.total_book_value],
          ["Day Change", account.day_change, "tone"],
          ["Current Gain", account.current_total_gain_loss ?? account.total_gain_loss, "tone"],
          ["Current Return", account.current_total_gain_loss_pct ?? account.total_gain_loss_pct, "percentTone"],
          ["Cash", account.cash_balance || 0],
        ]}
      />

      <section className="summary-grid account-insights-grid">
        <section className="section-panel">
          <div className="panel-heading">
            <h2>Account Allocation</h2>
          </div>
          <Allocation allocation={allocation} />
        </section>

        <section className="section-panel">
          <div className="panel-heading">
            <h2>Monthly Change</h2>
          </div>
          <AccountMonthlyPivot pivot={monthlyPivot} loading={!history} accountName={account.name} />
          <AccountPerformanceChart series={performanceSeries} loading={!history} />
        </section>
      </section>

      <section className="section-panel">
        <TableToolbar value={query} onChange={onQuery} count={sortedAccountHoldings.length} placeholder="Filter account holdings" />
        <HoldingsTable
          holdings={sortedAccountHoldings}
          sort={sort}
          weightKey="account_weight"
          showAccount={false}
          onSort={onSort}
        />
      </section>
    </section>
  );
}

function AccountsSetupPage({ accounts, editingAccount, onSubmit, onEdit, onCancelEdit }) {
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
            <span>Account type</span>
            <select name="account_type" defaultValue={editingAccount?.account_type || "RRSP"}>
              <option value="RRSP">RRSP</option>
              <option value="RESP">RESP</option>
              <option value="TFSA">TFSA</option>
              <option value="Taxable">Taxable</option>
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
          {editingAccount?.has_import ? (
            <div className="cash-edit-fields">
              <label>
                <span>Cash balance</span>
                <input
                  name="cash_balance"
                  type="number"
                  step="0.01"
                  defaultValue={editingAccount?.cash_balance ?? 0}
                />
              </label>
              <label>
                <span>Cash currency</span>
                <select name="cash_currency" defaultValue={editingAccount?.cash_currency || editingAccount?.base_currency || "CAD"}>
                  <option value="CAD">CAD</option>
                  <option value="USD">USD</option>
                </select>
              </label>
            </div>
          ) : null}
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
            {accounts.length ? (
              accounts.map((account) => (
                <div key={account.id} className="setup-account-row">
                  <div className="row-top">
                    <span className="row-title">{account.name}</span>
                    <span className="numeric">{account.base_currency || "CAD"}</span>
                  </div>
                  <div className="row-top row-sub">
                    <span>{account.account_type || "Investment"}</span>
                    <span>{account.owner || ""}</span>
                  </div>
                  <div className="row-top row-sub">
                    <span>{account.has_import ? `${formatMoney(account.total_closing_value)} latest value` : "No import yet"}</span>
                    {Number(account.cash_balance || 0) ? (
                      <span>
                        Cash {formatMoney(account.cash_balance)} {account.cash_currency || ""}
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

function HistoryPage({ data, accounts, fileLabel, onFileChange, onUpload }) {
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
            <HistoryTable snapshots={snapshots} accounts={accounts} />
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
  accounts.forEach((account) => {
    columns.set(account.id, {
      accountId: account.id,
      accountName: account.name,
    });
  });
  snapshots.forEach((snapshot) => {
    (snapshot.accounts || []).forEach((account) => {
      if (!columns.has(account.account_id)) {
        columns.set(account.account_id, {
          accountId: account.account_id,
          accountName: account.account_name,
        });
      }
    });
  });
  return Array.from(columns.values()).sort((left, right) => left.accountName.localeCompare(right.accountName));
}

function HistoryTable({ snapshots, accounts }) {
  const accountColumns = historyAccountColumns(snapshots, accounts);

  return (
    <div className="table-wrap">
      <table>
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
                    {valuesByAccount.has(account.accountId) ? formatMoney(valuesByAccount.get(account.accountId)) : ""}
                  </td>
                ))}
                <td className="numeric">{formatMoney(snapshot.total_value)}</td>
                <td className={`numeric ${toneClass(snapshot.day_change)}`}>
                  {snapshot.day_change === null || snapshot.day_change === undefined ? "" : formatMoney(snapshot.day_change)}
                  <div className="row-sub">
                    {snapshot.day_change_pct === null || snapshot.day_change_pct === undefined ? "" : formatPercent(snapshot.day_change_pct)}
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
          <strong className={tone ? toneClass(value) : ""}>
            {tone === "percentTone" ? formatPercent(value) : formatMoney(value)}
          </strong>
        </article>
      ))}
    </section>
  );
}

function summaryAccountRank(account) {
  const name = shortAccountName(account.name).toLowerCase();
  const order = [
    ["resp", 0],
    ["jamie rrsp", 1],
    ["jamie tfsa", 2],
    ["jamie tfsac", 2],
    ["jamie cash", 3],
    ["michelle rrsp", 4],
    ["michelle rsp", 4],
    ["michelle tfsa", 5],
    ["michelle cash", 6],
  ];
  const match = order.find(([label]) => name === label);
  return match ? match[1] : 99;
}

function AccountSummaryTable({ accounts, onActivate }) {
  if (!accounts.length) {
    return <div className="empty-state">No accounts imported.</div>;
  }

  const orderedAccounts = [...accounts].sort((left, right) => {
    const rankDiff = summaryAccountRank(left) - summaryAccountRank(right);
    if (rankDiff) return rankDiff;
    return shortAccountName(left.name).localeCompare(shortAccountName(right.name));
  });

  return (
    <div className="table-wrap summary-account-wrap">
      <table className="summary-account-table">
        <thead>
          <tr>
            <th />
            <th className="numeric">Balance</th>
            <th className="numeric">Cap</th>
            <th className="numeric">Cash</th>
            <th className="numeric">DoD</th>
            <th className="numeric">DoD$</th>
          </tr>
        </thead>
        <tbody>
          {orderedAccounts.map((account) => (
            <tr key={account.id} onClick={() => onActivate("account", account.id)}>
              <td>{shortAccountName(account.name)}</td>
              <td className="numeric">{formatMoney(account.current_total_value ?? account.total_closing_value)}</td>
              <td className="numeric">{formatMoney(account.total_book_value)}</td>
              <td className="numeric">{formatMoney(account.cash_balance || 0)}</td>
              <td className={`numeric ${toneClass(account.day_change_pct)}`}>{formatPercent(account.day_change_pct)}</td>
              <td className={`numeric ${toneClass(account.day_change)}`}>{formatMoney(account.day_change)}</td>
            </tr>
          ))}
        </tbody>
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

function TableToolbar({ value, onChange, count, placeholder }) {
  return (
    <div className="table-toolbar">
      <input type="search" value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} />
      <span>{count} holdings</span>
    </div>
  );
}

function HoldingsTable({ holdings, sort, weightKey, showAccount, onSort }) {
  const headers = [
    ["symbol", "Symbol"],
    ["description", "Description"],
    ["quantity", "Quantity", "numeric"],
    ["average_cost", "Avg Cost", "numeric"],
    ["current_price", "Current Price", "numeric"],
    ["day_change", "Day Change", "numeric"],
    ["current_value", "Current Value", "numeric"],
    ["book_value", "Book", "numeric"],
    ["current_gain_loss", "Gain", "numeric"],
    [weightKey, weightKey === "account_weight" ? "Acct Weight" : "Weight", "numeric"],
  ];

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {headers.map(([key, label, className]) => (
              <th key={key} className={className || ""} onClick={() => onSort(key)}>
                {label}
                {sort.key === key ? (sort.direction === "asc" ? " ^" : " v") : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {holdings.length ? (
            holdings.map((holding) => (
              <tr key={`${holding.id}-${holding.account_name}`}>
                <td>
                  <div className="symbol">{holding.symbol}</div>
                  <div className="row-sub">
                    {holding.currency} {holding.market}
                  </div>
                </td>
                <td>
                  <div className="description" title={holding.description}>
                    {holding.description}
                  </div>
                  {showAccount ? <div className="row-sub">{shortAccountName(holding.account_name)}</div> : null}
                </td>
                <td className="numeric">{number.format(holding.quantity || 0)}</td>
                <td className="numeric">{formatMoney(holding.average_cost)}</td>
                <td className="numeric">
                  {formatMoney(holding.current_price ?? holding.closing_price)}
                  <div className="row-sub">
                    {holding.current_price_source === "yfinance" ? "Live" : "Import"}
                    {holding.price_quote_time ? ` ${formatDate(holding.price_quote_time)}` : ""}
                  </div>
                </td>
                <td className={`numeric ${toneClass(holding.day_value_change)}`}>
                  {holding.day_value_change === null || holding.day_value_change === undefined
                    ? ""
                    : formatMoney(holding.day_value_change)}
                  <div className="row-sub">{formatPercent(holding.day_change_pct)}</div>
                </td>
                <td className="numeric">{formatMoney(holding.current_value ?? holding.closing_value)}</td>
                <td className="numeric">{formatMoney(holding.book_value)}</td>
                <td className={`numeric ${toneClass(holding.current_gain_loss ?? holding.gain_loss)}`}>
                  {formatMoney(holding.current_gain_loss ?? holding.gain_loss)}
                  <div className="row-sub">{formatPercent(holding.current_gain_loss_pct ?? holding.gain_loss_pct)}</div>
                </td>
                <td className="numeric">{formatPercent(holding[weightKey])}</td>
              </tr>
            ))
          ) : (
            <tr>
              <td className="empty-state" colSpan="10">
                No holdings match the current filter.
              </td>
            </tr>
          )}
        </tbody>
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
