import React, { useMemo, useState } from "react";
import { buildPortfolioPerformanceSeries } from "./performance.js";
import { projectRetirement } from "./retirement.js";

const money = new Intl.NumberFormat("en-CA", { style: "currency", currency: "CAD", maximumFractionDigits: 0 });
const format = (value) => money.format(value);
const dateLabel = (value) => new Date(`${value}T00:00:00`).toLocaleDateString("en-CA", { year: "numeric", month: "short", day: "numeric" });

export default function RetirementProjection({ accounts, excludedAccounts, history }) {
  const series = buildPortfolioPerformanceSeries(history?.snapshots || [],
    accounts.filter((account) => !excludedAccounts.includes(String(account.id))).map((account) => account.id));
  const latest = series.at(-1);
  return (
    <section className="section-panel retirement-projection">
      <div className="panel-heading"><h2>Growth & Withdrawals</h2></div>
      {latest ? <ProjectionInputs latest={latest} /> : (
        <div className="empty-state">Select accounts with saved balances to build a projection.</div>
      )}
    </section>
  );
}

function ProjectionInputs({ latest }) {
  const startYear = Number(latest.date.slice(0, 4));
  const [assumptions, setAssumptions] = useState(() => ({
    retirementDate: latest.date, endYear: startYear + 30, annualReturn: 5, inflation: 2,
    monthlyContribution: 0, monthlySpending: 0, monthlyIncome: 0, taxRate: 0,
    oneTimeExpense: 0, expenseYear: startYear + 1,
  }));
  const [scenario, setScenario] = useState("base");
  const [realDollars, setRealDollars] = useState(false);
  const offset = { lower: -2, base: 0, higher: 2 }[scenario];
  const calculated = useMemo(() => {
    try {
      const input = Object.fromEntries(Object.entries(assumptions).map(([key, value]) =>
        [key, key === "retirementDate" ? value : value === "" ? NaN : Number(value)]));
      // Validate the entered base assumptions before applying scenario adjustments.
      const base = projectRetirement({ ...input, startingBalance: latest.value, startDate: latest.date });
      const result = offset === 0 ? base : projectRetirement({ ...input,
        annualReturn: input.annualReturn + offset, startingBalance: latest.value, startDate: latest.date });
      return { result, effectiveReturn: input.annualReturn + offset };
    } catch (error) { return { error: error.message }; }
  }, [assumptions, latest.value, latest.date, offset]);
  function field(key, label, options = {}) {
    return <label key={key}><span>{label}</span><input type="number" min="0" step="any" {...options}
      value={assumptions[key]} onChange={(event) => setAssumptions((current) => ({ ...current, [key]: event.target.value }))} /></label>;
  }
  const result = calculated.result;
  const last = result?.points.at(-1);
  const amount = (value, factor = 1) => format(value / (realDollars ? factor : 1));
  return <>
    <p className="row-sub">Starting balance: <strong>{format(latest.value)}</strong> · {dateLabel(latest.date)} · Uses the accounts checked above.</p>
    <div className="projection-inputs">
      <label><span>Retirement start date</span><input type="date" min={latest.date}
        value={assumptions.retirementDate} onChange={(event) => setAssumptions((current) => ({ ...current, retirementDate: event.target.value }))} /></label>
      {field("endYear", "Projection end year", { step: 1, min: startYear + 1, max: startYear + 60 })}
      {field("annualReturn", "Annual return after fees (%)", { min: -97, max: 98, step: 0.1 })}
      {field("inflation", "Annual inflation (%)", { max: 25, step: 0.1 })}
      {field("monthlyContribution", "Monthly contribution before retirement (CAD)")}
      {field("monthlySpending", "Monthly retirement spending, after tax (CAD today)")}
      {field("monthlyIncome", "Other monthly retirement income, after tax (CAD today)")}
      {field("taxRate", "Tax allowance on portfolio withdrawals (%)", { max: 99, step: 0.1 })}
      {field("oneTimeExpense", "One-time expense, after tax (CAD today)")}
      {field("expenseYear", "One-time expense year", { step: 1, min: startYear, max: startYear + 60 })}
    </div>
    <p className="row-sub">Illustrative starting assumptions: 5% return and 2% inflation. Enter your own spending, income and tax allowance. Other income offsets spending and is not added to the portfolio.</p>
    <div className="projection-controls">
      <div className="performance-range" role="group" aria-label="Projection return scenario">
        {[["lower", "Lower (−2 pts)"], ["base", "Base"], ["higher", "Higher (+2 pts)"]].map(([key, label]) =>
          <button type="button" key={key} className={scenario === key ? "active" : ""}
            aria-pressed={scenario === key} onClick={() => setScenario(key)}>{label}</button>)}
      </div>
      <label className="projection-toggle"><input type="checkbox" checked={realDollars}
        onChange={(event) => setRealDollars(event.target.checked)} /> Show in today’s dollars</label>
    </div>
    {calculated.error ? <p role="alert" className="projection-error">{calculated.error}</p> : <>
      <div className="projection-metrics" aria-live="polite">
        <div><span>Ending balance with withdrawals</span><strong>{amount(last.balance, last.factor)}</strong></div>
        <div><span>Ending balance, growth only</span><strong>{amount(last.growthOnly, last.factor)}</strong></div>
        <div><span>Balance reaches zero</span><strong>{result.depletionDate ? dateLabel(result.depletionDate) : "Not within projection"}</strong></div>
        <div><span>First spending shortfall</span><strong>{result.shortfallDate ? dateLabel(result.shortfallDate) : "None projected"}</strong></div>
      </div>
      <p className="row-sub">{calculated.effectiveReturn}% annual return · {realDollars ? `Balances expressed in ${startYear} purchasing power` : "Future dollars"} · CAD</p>
      <ProjectionChart points={result.points} realDollars={realDollars} />
      <details className="projection-table-details">
        <summary>Annual breakdown</summary>
        <div className="table-wrap"><table>
          <caption>Annual cash flows in future CAD; partial first and last years follow the saved balance date.</caption>
          <thead><tr>{["Year", "Opening balance", "Growth", "Contributions", "Gross withdrawals", "After-tax spending funded", "Spending shortfall", "Ending balance"].map((label) =>
            <th key={label} className={label === "Year" ? "" : "numeric"}>{label}</th>)}</tr></thead>
          <tbody>{result.rows.map((row) => <tr key={row.year}><th>{row.year}</th>
            {[row.opening, row.growth, row.contributions, row.withdrawals, row.spending, row.shortfall, row.ending].map((value, index) =>
              <td className={`numeric ${index === 5 && value > 0 ? "negative" : ""}`} key={index}>{format(value)}</td>)}
          </tr>)}</tbody>
        </table></div>
      </details>
    </>}
    <p className="row-sub projection-method">Monthly model: growth first, then contributions or withdrawals. Contributions stay fixed and stop at retirement. Spending and other income rise with inflation from the starting date. Withdrawals begin in the first monthly period starting on or after retirement. The one-time expense is paid in the first period starting in its chosen year. The growth-only line includes the same contributions and no withdrawals. The annual table always shows future dollars.</p>
    <p className="row-sub">The tax allowance is a single estimate across the selected RRSP and corporate accounts. Account-specific tax rules, mandatory withdrawals and variable market returns are not modelled.</p>
  </>;
}

function ProjectionChart({ points, realDollars }) {
  const [hovered, setHovered] = useState(null);
  const width = 960, height = 310;
  const pad = { left: 112, right: 22, top: 20, bottom: 32 };
  const value = (point, key) => point[key] / (realDollars ? point.factor : 1);
  const maximum = Math.max(1, ...points.flatMap((point) => [value(point, "balance"), value(point, "growthOnly")])) * 1.05;
  const x = (index) => pad.left + index / (points.length - 1 || 1) * (width - pad.left - pad.right);
  const y = (amount) => height - pad.bottom - amount / maximum * (height - pad.top - pad.bottom);
  const activeIndex = hovered === null ? null : Math.min(hovered, points.length - 1);
  const point = activeIndex === null ? null : points[activeIndex];
  const line = (key) => points.map((item, index) => `${x(index)},${y(value(item, key))}`).join(" ");
  return <div>
    <div className="projection-legend"><span className="projection-growth">Growth only</span><span className="projection-withdrawals">With withdrawals</span></div>
    <svg className="performance-chart projection-chart" role="img" aria-label="Projected retirement balances with and without withdrawals"
      viewBox={`0 0 ${width} ${height}`} onMouseLeave={() => setHovered(null)}
      onMouseMove={(event) => {
        const bounds = event.currentTarget.getBoundingClientRect();
        const pointer = (event.clientX - bounds.left) / bounds.width * width;
        setHovered(Math.max(0, Math.min(points.length - 1, Math.round((pointer - pad.left) / (width - pad.left - pad.right) * (points.length - 1)))));
      }}>
      {[0, 0.25, 0.5, 0.75, 1].map((fraction) => <g key={fraction}>
        <line x1={pad.left} x2={width - pad.right} y1={y(maximum * fraction)} y2={y(maximum * fraction)} />
        <text x={pad.left - 10} y={y(maximum * fraction) + 4}>{format(maximum * fraction)}</text>
      </g>)}
      <polyline className="projection-growth-line" points={line("growthOnly")} />
      <polyline className="projection-withdrawal-line" points={line("balance")} />
      {[0, Math.floor((points.length - 1) / 2), points.length - 1].map((index) =>
        <text key={index} className="projection-year" x={x(index)} y={height - 8}>{points[index].date.slice(0, 4)}</text>)}
      {point && <line className="performance-hover-line" x1={x(activeIndex)} x2={x(activeIndex)} y1={pad.top} y2={height - pad.bottom} />}
    </svg>
    <div className="projection-hover" aria-live="polite">{point ? <>
      {dateLabel(point.date)} · Growth only: <strong>{format(value(point, "growthOnly"))}</strong> · With withdrawals: <strong>{format(value(point, "balance"))}</strong>
    </> : "Hover over the chart for monthly balances. See the annual breakdown for growth and cash flows."}</div>
  </div>;
}
