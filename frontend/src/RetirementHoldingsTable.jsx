import React, { useMemo, useState } from "react";
import { isRspAccount } from "./performance.js";
import { projectHoldingIncome, aggregateHoldingIncomeRows } from "./holdingIncomeProjection.js";

const money = new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', maximumFractionDigits: 0 });
const dividendMoney = new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', minimumFractionDigits: 2, maximumFractionDigits: 2 });
const shares = new Intl.NumberFormat('en-CA', { maximumFractionDigits: 0 });
const display = (value, formatter = money) => value == null ? 'n/a' : formatter.format(value);

export default function RetirementHoldingsTable({ data }) {
  const [years, setYears] = useState(10);
  const [priceGrowth, setPriceGrowth] = useState(4);
  const [sgovPriceGrowth, setSgovPriceGrowth] = useState(0);
  const [sgovDividendGrowth, setSgovDividendGrowth] = useState(0);
  const asOf = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Edmonton', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
  const accounts = (data.accounts || data.all_accounts || []).filter(isRspAccount);
  const hasSgov = (data.holdings || []).some((holding) => accounts.some((account) => String(account.id) === String(holding.account_id))
    && [holding.symbol, holding.yahoo_symbol].some((symbol) => String(symbol || '').trim().toUpperCase() === 'SGOV'));
  const result = useMemo(() => {
    try {
      const number = (value) => value === '' ? NaN : Number(value);
      const options = { asOf, years: number(years), priceGrowth: number(priceGrowth),
        sgovPriceGrowth: number(sgovPriceGrowth), sgovDividendGrowth: number(sgovDividendGrowth) };
      // Validate options even when there are no holdings.
      projectHoldingIncome({ quantity: 0, current_price: 0, annual_forward_dividend: 0 }, options);
      const ids = new Set(accounts.map((account) => String(account.id)));
      const holdings = (data.holdings || []).filter((holding) => ids.has(String(holding.account_id)));
      return { rows: aggregateHoldingIncomeRows(holdings.map((holding, index) => ({ holding, key: `${holding.account_id}-${holding.holding_order_key || holding.symbol}-${index}`,
        ...projectHoldingIncome(holding, options) }))), years: Array.from({ length: options.years }, (_, index) => Number(asOf.slice(0, 4)) + index + 1) };
    } catch (error) { return { error: error.message }; }
  }, [data, asOf, years, priceGrowth, sgovPriceGrowth, sgovDividendGrowth]);
  const rows = result.rows || [];
  const sum = (list) => list.filter((value) => value !== null).reduce((total, value) => total + value, 0);
  const hasUnavailable = rows.some((row) => row.currentIncome === null || row.projections.some((point) => point.income === null));
  return <section className="view active" aria-label="Dividend Growth">
    <section className="section-panel retirement-holdings">
      <div className="panel-heading"><h2>Retirement holdings & dividend income</h2><span>{accounts.length} accounts · {rows.length} holdings</span></div>
      <div className="retirement-holdings-inputs">
        <label><span>Years to show</span><input type="number" min="1" max="30" step="1" value={years} onChange={(event) => setYears(event.target.value)} /></label>
        <label><span>Annual share-price growth (%)</span><input type="number" min="-99.9" max="100" step="0.1" value={priceGrowth} onChange={(event) => setPriceGrowth(event.target.value)} /></label>
        {hasSgov && <>
          <label><span>SGOV share-price growth (%)</span><input type="number" min="-99.9" max="100" step="0.1" value={sgovPriceGrowth} onChange={(event) => setSgovPriceGrowth(event.target.value)} /></label>
          <label><span>SGOV dividend growth (%)</span><input type="number" min="-99.9" max="100" step="0.1" value={sgovDividendGrowth} onChange={(event) => setSgovDividendGrowth(event.target.value)} /></label>
        </>}
      </div>
      <p className="row-sub">Jamie and Michelle RSP holdings only.</p>
      {hasSgov && <p className="row-sub">SGOV uses its own assumptions above: 0% price growth and 0% dividend growth by default. DRIP remains enabled; historical payout growth is not used for its projection.</p>}
      <p className="row-sub">Current figures use the latest available holdings and prices. Projection begins {asOf}. Each year shows annual income above January 1 shares after DRIP. Hover for price, dividend and income increase details. All dollar amounts are CAD.</p>
      {result.error ? <p className="empty-state" role="alert">{result.error}</p> : !rows.length ? <p className="empty-state">No holdings in the retirement accounts yet.</p> : <>
        <div className="table-wrap retirement-holdings-scroll"><table className="retirement-income-table" style={{ width: `${392 + result.years.length * 82}px` }}>
          <caption>Year cells: annual income / starting shares. Rounded display; full-precision DRIP calculations.</caption>
          <colgroup>
            {[74, 64, 54, 58, 78, 64].map((width, index) => <col key={`current-${index}`} style={{ width: `${width}px` }} />)}
            {result.years.map((year) => <col key={year} style={{ width: '82px' }} />)}
          </colgroup>
          <thead><tr>
            <th className="retirement-holding-name" scope="col">Symbol</th>
            <th className="numeric" scope="col">Shares</th>
            <th className="numeric" scope="col">Price</th>
            <th className="numeric" scope="col" title="Dividend per share per payment; hover amounts for payment frequency">Div.</th>
            <th className="numeric" scope="col">Income</th>
            <th className="numeric" scope="col" title="Annual dividend growth used in the projection">Growth</th>
            {result.years.map((year) => <th key={year} scope="col" className="numeric retirement-year-start" title={`January 1, ${year}: annual income and starting shares`}>{year}</th>)}
          </tr></thead>
          <tbody>
              {rows.map((row) => <tr key={row.key}>
                <th scope="row" className="retirement-holding-name" title={[row.holding.market, row.holding.description, ...row.notes].filter(Boolean).join(" · ")}>{row.holding.symbol || row.holding.description}</th>
                <td className="numeric">{display(row.quantity, shares)}</td><td className="numeric">{display(row.price)}</td>
                <td className="numeric" title={`${row.frequency || 0} payments per year · Annual dividend per share: ${display(row.annualDividend, dividendMoney)}`}>{row.annualDividend === 0 ? display(0, dividendMoney) : display(row.frequency ? row.annualDividend / row.frequency : null, dividendMoney)}</td>
                <td className="numeric">{display(row.currentIncome)}</td>
                <td className="numeric" title={row.notes.join(' ')}>{row.growthMixed ? 'Mixed' : row.historicGrowth === null && !row.sgov ? (row.annualDividend > 0 ? 'n/a (flat)' : 'n/a') : `${row.appliedDividendGrowth.toFixed(0)}%`}</td>
                {row.projections.map((point) => <td key={point.year} className="numeric retirement-year-start retirement-year-cell"
                  title={`January 1, ${point.year} · Starting shares: ${display(point.shares, shares)} · Price: ${display(point.price)} · Annual dividend per share: ${display(point.annualDividend, dividendMoney)} · Annual income: ${display(point.income)} · Income increase: ${display(point.increase)}`}>
                  <strong>{display(point.income)}</strong>
                  <span>{display(point.shares, shares)} sh</span>
                </td>)}
              </tr>)}
          </tbody>
          <tfoot><tr><th scope="row" className="retirement-holding-name">Total</th><td colSpan="3" /><td className="numeric">{display(sum(rows.map((row) => row.currentIncome)))}</td><td />
            {result.years.map((year, index) => <td key={year} className="numeric retirement-year-start" title={`Income increase: ${display(sum(rows.map((row) => row.projections[index].increase)))}`}>{display(sum(rows.map((row) => row.projections[index].income)))}</td>)}
          </tr></tfoot>
        </table></div>
        {hasUnavailable && <p className="row-sub">Totals exclude unavailable values. Review holdings marked n/a for missing dividend or price data.</p>}
      </>}
      <details className="retirement-holdings-method"><summary>Growth and DRIP assumptions</summary><p>Uses each holding’s cached historical dividend growth rate, calculated over up to five years of available history. Dividends grow at that annual rate and all payments buy fractional shares of the same holding. Payments are spaced evenly from today using the recorded payment frequency, with monthly payments assumed if unavailable. Share prices grow at the rate above. New shares earn subsequent dividends. January 1 balances include payments through that date; the first projected year includes only DRIP from today onward. Missing growth history uses a clearly labelled flat dividend assumption. Cash receives no dividend or price growth. Exchange rates stay fixed. No deposits, withdrawals or taxes are modelled.</p></details>
    </section>
  </section>;
}
