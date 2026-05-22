const state = {
  data: null,
  activeView: "summary",
  activeAccountId: null,
  summaryQuery: "",
  accountQuery: "",
  sortKey: "closing_value",
  sortDirection: "desc",
};

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

const money = new Intl.NumberFormat("en-CA", {
  style: "currency",
  currency: "CAD",
  maximumFractionDigits: 2,
});

const number = new Intl.NumberFormat("en-CA", {
  maximumFractionDigits: 4,
});

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

function setTone(element, value) {
  element.classList.remove("positive", "negative");
  if (Number(value) > 0) element.classList.add("positive");
  if (Number(value) < 0) element.classList.add("negative");
}

function showMessage(text, isError = false) {
  const message = document.querySelector("#message");
  message.textContent = text;
  message.hidden = false;
  message.classList.toggle("error", isError);
  window.clearTimeout(showMessage.timer);
  showMessage.timer = window.setTimeout(() => {
    message.hidden = true;
  }, 4500);
}

async function loadSummary() {
  const response = await fetch("/api/summary");
  if (!response.ok) {
    throw new Error("Could not load portfolio summary.");
  }
  state.data = await response.json();

  if (state.activeView === "account" && !activeAccount()) {
    state.activeView = "summary";
    state.activeAccountId = null;
  }

  render();
}

function render() {
  renderTabs();
  renderImportAccountOptions();
  renderActiveView();
  renderImports();
  renderAsOfLine();
}

function renderTabs() {
  const target = document.querySelector("#portfolioTabs");
  const accountTabs = state.data.accounts
    .map(
      (account) => `
        <button
          class="tab ${state.activeView === "account" && state.activeAccountId === account.id ? "active" : ""}"
          data-view="account"
          data-account-id="${account.id}"
          type="button"
        >
          ${escapeHtml(shortAccountName(account.name))}
        </button>
      `
    )
    .join("");

  target.innerHTML = `
    <button class="tab ${state.activeView === "summary" ? "active" : ""}" data-view="summary" type="button">
      Summary
    </button>
    ${accountTabs}
    <button class="tab ${state.activeView === "accounts" ? "active" : ""}" data-view="accounts" type="button">
      Accounts
    </button>
    <button class="tab ${state.activeView === "imports" ? "active" : ""}" data-view="imports" type="button">
      Imports
    </button>
  `;
}

function activateView(view, accountId = null) {
  state.activeView = view;
  state.activeAccountId = accountId ? Number(accountId) : null;
  if (view !== "account" && state.sortKey === "account_weight") {
    state.sortKey = "closing_value";
    state.sortDirection = "desc";
  }
  render();
}

function renderActiveView() {
  document.querySelectorAll(".view").forEach((view) => view.classList.remove("active"));

  if (state.activeView === "imports") {
    document.querySelector("#importsView").classList.add("active");
    return;
  }

  if (state.activeView === "accounts") {
    document.querySelector("#accountsSetupView").classList.add("active");
    renderAccountSetupPage();
    return;
  }

  if (state.activeView === "account") {
    document.querySelector("#accountView").classList.add("active");
    renderAccountPage();
    return;
  }

  document.querySelector("#summaryView").classList.add("active");
  renderSummaryPage();
}

function renderAsOfLine() {
  const latestReport = state.data.accounts
    .map((account) => account.report_timestamp)
    .filter(Boolean)
    .sort()
    .at(-1);
  document.querySelector("#asOfLine").textContent = latestReport
    ? `Latest holdings: ${latestReport}`
    : "Local holdings snapshots";
}

function renderImportAccountOptions() {
  const target = document.querySelector("#accountOptions");
  const accounts = state.data.all_accounts || state.data.accounts;
  target.innerHTML = accounts
    .map((account) => `<option value="${escapeHtml(account.name)}"></option>`)
    .join("");
}

function renderSummaryPage() {
  renderSummaryTotals();
  renderAccounts();
  renderCurrencies();
  renderAllocation(
    state.data.allocation,
    document.querySelector("#summaryAllocationBar"),
    document.querySelector("#summaryAllocationList")
  );
  renderHoldingsTable({
    holdings: sortedHoldings(state.data.holdings, state.summaryQuery),
    body: document.querySelector("#summaryHoldingsBody"),
    count: document.querySelector("#summaryHoldingCount"),
    countLabel: "holdings",
    weightKey: "combined_portfolio_pct",
    emptyText: "No holdings match the current filter.",
    showAccount: true,
  });
}

function renderAccountSetupPage() {
  const target = document.querySelector("#setupAccountList");
  const accounts = state.data.all_accounts || [];

  if (!accounts.length) {
    target.innerHTML = `<div class="empty-state">No accounts set up.</div>`;
    return;
  }

  target.innerHTML = accounts
    .map((account) => {
      const status = account.has_import ? `${formatMoney(account.total_closing_value)} latest value` : "No import yet";
      const cash = Number(account.cash_balance || 0) ? `<span>Cash ${formatMoney(account.cash_balance)}</span>` : "";
      return `
        <button
          class="setup-account-row button-row"
          ${account.has_import ? `data-view="account" data-account-id="${account.id}"` : ""}
          type="button"
        >
          <span class="row-top">
            <span class="row-title">${escapeHtml(account.name)}</span>
            <span class="numeric">${escapeHtml(account.base_currency || "CAD")}</span>
          </span>
          <span class="row-top row-sub">
            <span>${escapeHtml(account.account_type || "Investment")}</span>
            <span>${escapeHtml(account.owner || "")}</span>
          </span>
          <span class="row-top row-sub">
            <span>${status}</span>
            ${cash}
          </span>
          ${account.notes ? `<span class="row-sub">${escapeHtml(account.notes)}</span>` : ""}
        </button>
      `;
    })
    .join("");
}

function renderSummaryTotals() {
  const totals = state.data.totals;
  document.querySelector("#totalValue").textContent = formatMoney(totals.closing_value);
  document.querySelector("#bookValue").textContent = formatMoney(totals.book_value);

  const gainLoss = document.querySelector("#gainLoss");
  gainLoss.textContent = formatMoney(totals.gain_loss);
  setTone(gainLoss, totals.gain_loss);

  const gainLossPct = document.querySelector("#gainLossPct");
  gainLossPct.textContent = formatPercent(totals.gain_loss_pct);
  setTone(gainLossPct, totals.gain_loss_pct);
}

function renderAccounts() {
  const target = document.querySelector("#accountList");
  if (!state.data.accounts.length) {
    target.innerHTML = `<div class="empty-state">No accounts imported.</div>`;
    return;
  }

  target.innerHTML = state.data.accounts
    .map(
      (account) => `
        <button class="account-row button-row" data-view="account" data-account-id="${account.id}" type="button">
          <span class="row-top">
            <span class="row-title">${escapeHtml(account.name)}</span>
            <span class="numeric">${formatMoney(account.total_closing_value)}</span>
          </span>
          <span class="row-top row-sub">
            <span>${escapeHtml(account.report_timestamp || "")}</span>
            <span>${account.row_count} securities</span>
          </span>
          ${
            Number(account.cash_balance || 0)
              ? `<span class="row-sub">Cash ${formatMoney(account.cash_balance)}</span>`
              : ""
          }
        </button>
      `
    )
    .join("");
}

function renderCurrencies() {
  const target = document.querySelector("#currencyList");
  if (!state.data.currency_summaries.length) {
    target.innerHTML = `<div class="empty-state">No currency summaries.</div>`;
    return;
  }

  target.innerHTML = state.data.currency_summaries
    .filter((currency) => Number(currency.closing_value || 0) || Number(currency.cash_value || 0))
    .map(
      (currency) => `
        <div class="currency-row">
          <div class="row-top">
            <span class="row-title">${escapeHtml(currency.currency)} - ${shortAccountName(currency.account_name)}</span>
            <span class="numeric">${formatMoney(currency.closing_value)}</span>
          </div>
          <div class="row-top row-sub">
            <span>
              Securities ${formatMoney(currency.securities_value)}
              ${Number(currency.cash_value || 0) ? ` - Cash ${formatMoney(currency.cash_value)}` : ""}
            </span>
            <span class="${Number(currency.gain_loss || 0) >= 0 ? "positive" : "negative"}">
              ${formatPercent(currency.gain_loss_pct)}
            </span>
          </div>
        </div>
      `
    )
    .join("");
}

function renderAccountPage() {
  const account = activeAccount();
  if (!account) return;

  const holdings = state.data.holdings
    .filter((holding) => holding.account_name === account.name)
    .map((holding) => ({
      ...holding,
      account_weight: account.total_closing_value
        ? (Number(holding.closing_value || 0) / account.total_closing_value) * 100
        : 0,
    }));

  const allocation = holdings
    .filter((holding) => Number(holding.closing_value || 0) > 0)
    .sort((a, b) => Number(b.closing_value || 0) - Number(a.closing_value || 0))
    .map((holding) => ({
      symbol: holding.symbol,
      description: holding.description,
      closing_value: holding.closing_value,
      currency: holding.currency,
      account_name: holding.account_name,
      portfolio_pct: holding.account_weight,
    }));

  document.querySelector("#accountHeader").innerHTML = `
    <div>
      <h2>${escapeHtml(account.name)}</h2>
      <p>${escapeHtml(account.report_timestamp || "")}</p>
    </div>
    <button class="quiet-button" data-view="summary" type="button">Summary</button>
  `;

  renderAccountTotals(account);
  renderAllocation(
    allocation,
    document.querySelector("#accountAllocationBar"),
    document.querySelector("#accountAllocationList")
  );
  renderHoldingsTable({
    holdings: sortedHoldings(holdings, state.accountQuery),
    body: document.querySelector("#accountHoldingsBody"),
    count: document.querySelector("#accountHoldingCount"),
    countLabel: "holdings",
    weightKey: "account_weight",
    emptyText: "No account holdings match the current filter.",
    showAccount: false,
  });
}

function renderAccountTotals(account) {
  const metrics = [
    ["Account Value", account.total_closing_value],
    ["Book Value", account.total_book_value],
    ["Unrealized Gain", account.total_gain_loss, "tone"],
    ["Return", account.total_gain_loss_pct, "percentTone"],
    ["Cash", account.cash_balance || 0],
  ];

  document.querySelector("#accountMetrics").innerHTML = metrics
    .map(([label, value, tone]) => {
      const display = tone === "percentTone" ? formatPercent(value) : formatMoney(value);
      const toneClass = tone ? (Number(value || 0) >= 0 ? "positive" : "negative") : "";
      return `
        <article class="metric">
          <span>${label}</span>
          <strong class="${toneClass}">${display}</strong>
        </article>
      `;
    })
    .join("");
}

function renderAllocation(allocationRows, bar, list) {
  const allocation = allocationRows.filter((item) => Number(item.closing_value || 0) > 0);

  if (!allocation.length) {
    bar.innerHTML = "";
    list.innerHTML = `<div class="empty-state">No allocation data.</div>`;
    return;
  }

  bar.innerHTML = allocation
    .map((item, index) => {
      const width = Math.max(0.2, Number(item.portfolio_pct || 0));
      return `
        <div
          class="allocation-segment"
          title="${escapeHtml(item.symbol)} ${formatPercent(item.portfolio_pct)}"
          style="width: ${width}%; background: ${colors[index % colors.length]}"
        ></div>
      `;
    })
    .join("");

  list.innerHTML = allocation
    .map(
      (item, index) => `
        <div class="allocation-row">
          <div class="row-top">
            <div class="allocation-name">
              <span class="swatch" style="background: ${colors[index % colors.length]}"></span>
              <div>
                <div class="row-title">${escapeHtml(item.symbol)}</div>
                <div class="row-sub">${escapeHtml(item.description)} - ${shortAccountName(item.account_name)}</div>
              </div>
            </div>
            <div class="numeric">
              ${formatPercent(item.portfolio_pct)}
              <div class="row-sub">${formatMoney(item.closing_value)}</div>
            </div>
          </div>
        </div>
      `
    )
    .join("");
}

function sortedHoldings(holdings, queryText) {
  const query = queryText.trim().toLowerCase();
  const filtered = holdings.filter((holding) => {
    if (!query) return true;
    return [holding.symbol, holding.description, holding.asset_type, holding.market, holding.account_name]
      .filter(Boolean)
      .join(" ")
      .toLowerCase()
      .includes(query);
  });

  return filtered.sort((a, b) => {
    const aValue = a[state.sortKey];
    const bValue = b[state.sortKey];
    const direction = state.sortDirection === "asc" ? 1 : -1;

    if (typeof aValue === "number" || typeof bValue === "number") {
      return ((Number(aValue) || 0) - (Number(bValue) || 0)) * direction;
    }

    return String(aValue || "").localeCompare(String(bValue || "")) * direction;
  });
}

function renderHoldingsTable({ holdings, body, count, countLabel, weightKey, emptyText, showAccount }) {
  count.textContent = `${holdings.length} ${countLabel}`;

  if (!holdings.length) {
    body.innerHTML = `
      <tr>
        <td class="empty-state" colspan="8">${emptyText}</td>
      </tr>
    `;
    return;
  }

  body.innerHTML = holdings
    .map(
      (holding) => `
        <tr>
          <td>
            <div class="symbol">${escapeHtml(holding.symbol)}</div>
            <div class="row-sub">${escapeHtml(holding.currency)} ${escapeHtml(holding.market)}</div>
          </td>
          <td>
            <div class="description" title="${escapeHtml(holding.description)}">${escapeHtml(holding.description)}</div>
            ${showAccount ? `<div class="row-sub">${shortAccountName(holding.account_name)}</div>` : ""}
          </td>
          <td class="numeric">${number.format(holding.quantity || 0)}</td>
          <td class="numeric">${formatMoney(holding.closing_price)}</td>
          <td class="numeric">${formatMoney(holding.closing_value)}</td>
          <td class="numeric">${formatMoney(holding.book_value)}</td>
          <td class="numeric ${Number(holding.gain_loss || 0) >= 0 ? "positive" : "negative"}">
            ${formatMoney(holding.gain_loss)}
            <div class="row-sub">${formatPercent(holding.gain_loss_pct)}</div>
          </td>
          <td class="numeric">${formatPercent(holding[weightKey])}</td>
        </tr>
      `
    )
    .join("");
}

function renderImports() {
  const target = document.querySelector("#importsBody");
  if (!state.data.imports.length) {
    target.innerHTML = `
      <tr>
        <td class="empty-state" colspan="6">No imports yet.</td>
      </tr>
    `;
    return;
  }

  target.innerHTML = state.data.imports
    .map(
      (item) => `
        <tr>
          <td>${formatDate(item.imported_at)}</td>
          <td>${escapeHtml(item.account_name)}</td>
          <td>${escapeHtml(item.report_timestamp || "")}</td>
          <td class="numeric">${item.row_count}</td>
          <td class="numeric">${formatMoney(item.total_closing_value)}</td>
          <td>${escapeHtml(item.stored_path)}</td>
        </tr>
      `
    )
    .join("");
}

function activeAccount() {
  return state.data.accounts.find((account) => account.id === state.activeAccountId);
}

function shortAccountName(name) {
  return String(name || "")
    .replace(/^\d+\s+/, "")
    .replace(/\s+-\s+Combined Holdings$/i, "");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

document.querySelector("#fileInput").addEventListener("change", (event) => {
  const file = event.target.files[0];
  document.querySelector("#fileLabel").textContent = file ? file.name : "Choose CSV";
});

document.querySelector("#uploadForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button");
  const input = document.querySelector("#fileInput");

  if (!input.files.length) {
    showMessage("Choose a CSV file first.", true);
    return;
  }

  button.disabled = true;
  try {
    const response = await fetch("/api/import", {
      method: "POST",
      body: new FormData(form),
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || "Import failed.");
    }
    const cashText = Number(result.cash_balance || 0) ? ` with ${formatMoney(result.cash_balance)} cash` : "";
    showMessage(result.message || `Imported ${result.row_count} holdings for ${result.account_name}${cashText}.`);
    form.reset();
    document.querySelector("#fileLabel").textContent = "Choose CSV";
    await loadSummary();
  } catch (error) {
    showMessage(error.message, true);
  } finally {
    button.disabled = false;
  }
});

document.querySelector("#accountSetupForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button");
  const payload = Object.fromEntries(new FormData(form).entries());

  button.disabled = true;
  try {
    const response = await fetch("/api/accounts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || "Account save failed.");
    }
    showMessage(`${result.created ? "Created" : "Updated"} ${result.account.name}.`);
    form.reset();
    document.querySelector("#setupBaseCurrency").value = "CAD";
    await loadSummary();
    state.activeView = "accounts";
    render();
  } catch (error) {
    showMessage(error.message, true);
  } finally {
    button.disabled = false;
  }
});

document.querySelector("#summarySearchInput").addEventListener("input", (event) => {
  state.summaryQuery = event.target.value;
  renderSummaryPage();
});

document.querySelector("#accountSearchInput").addEventListener("input", (event) => {
  state.accountQuery = event.target.value;
  renderAccountPage();
});

document.addEventListener("click", (event) => {
  const tab = event.target.closest("[data-view]");
  if (!tab) return;

  const view = tab.dataset.view;
  activateView(view, tab.dataset.accountId || null);
});

document.querySelectorAll("th[data-sort]").forEach((header) => {
  header.addEventListener("click", () => {
    const key = header.dataset.sort;
    if (state.sortKey === key) {
      state.sortDirection = state.sortDirection === "asc" ? "desc" : "asc";
    } else {
      state.sortKey = key;
      state.sortDirection = "desc";
    }
    renderActiveView();
  });
});

loadSummary().catch((error) => {
  showMessage(error.message, true);
});
