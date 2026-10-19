import React, { useMemo, useState } from "react";
import { projectDrip } from "./dripProjection.js";

const cad = new Intl.NumberFormat("en-CA", { style: "currency", currency: "CAD", maximumFractionDigits: 0 });
const compact = new Intl.NumberFormat("en-CA", { style: "currency", currency: "CAD", notation: "compact", maximumFractionDigits: 1 });
const money = (value) => cad.format(value);
const dateLabel = (date) => new Date(`${date}T00:00:00`).toLocaleDateString("en-CA", { year: "numeric", month: "short", day: "numeric" });
function ageAt(date) {
  const [year, month, day] = date.split("-").map(Number);
  return year - 1972 - (month < 12 || (month === 12 && day < 2) ? 1 : 0);
}

export default function DripProjectionChart({ balance, annualIncome, capitalGrowth, dividendGrowth, inflation, desiredIncome, retirementDate, endDate, accounts }) {
  const [selectedIndex, setSelectedIndex] = useState(null);
  const [todayDollars, setTodayDollars] = useState(true);
  const startDate = new Intl.DateTimeFormat("en-CA", { timeZone: "America/Edmonton", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  const projection = useMemo(() => {
    try {
      const number = (value) => value === "" ? NaN : Number(value);
      return { points: projectDrip({ balance, annualIncome, startDate, retirementDate, endDate, accounts,
        capitalGrowth: number(capitalGrowth), dividendGrowth: number(dividendGrowth),
        inflation: number(inflation), desiredIncome: desiredIncome === "" ? 0 : Number(desiredIncome) }) };
    } catch (error) { return { error: error.message }; }
  }, [balance, annualIncome, capitalGrowth, dividendGrowth, inflation, desiredIncome, retirementDate, endDate, startDate, accounts]);
  const points = projection.points;
  if (!points) return <section className="section-panel"><h2>Your retirement picture</h2><p className="empty-state" role="status">{projection.error}</p></section>;
  const last = points.at(-1);
  const retirementIndex = Math.max(0, points.findIndex((point) => point.date === retirementDate));
  const atRetirement = points[retirementIndex];
  const firstShortfall = points.find((point) => point.shortfall > .01);
  const hasTarget = Number(desiredIncome) > 0;
  const hasBalanceEstimates = (accounts || []).some((account) => account.retirementBalance != null && account.retirementBalance !== "");
  const selected = Math.max(0, Math.min(selectedIndex ?? retirementIndex, points.length - 1));
  const shown = points[selected];
  const amount = (point, key) => (point[key] || 0) / (todayDollars ? point.factor : 1);
  const formatAmount = (point, key) => money(amount(point, key));
  const funding = shown.retired ? shown : points[Math.min(retirementIndex + 1, points.length - 1)];
  const dividendPart = amount(funding, "dividendFunding");
  const capitalPart = amount(funding, "capitalFunding");
  const gapPart = amount(funding, "incomeShortfall");
  const fundingTotal = dividendPart + capitalPart + gapPart || 1;
  const width = 1000, height = 310;
  const pad = { left: 80, right: 28, top: 30, bottom: 40 };
  const baseline = height - pad.bottom;
  const maximum = Math.max(1, ...points.map((point) => amount(point, "balance"))) * 1.1;
  const firstTime = Date.parse(points[0].date);
  const duration = Date.parse(last.date) - firstTime || 1;
  const xTime = (time) => pad.left + (time - firstTime) / duration * (width - pad.left - pad.right);
  const x = (point) => xTime(Date.parse(point.date));
  const y = (value) => baseline - value / maximum * (baseline - pad.top);
  const line = points.map((point) => `${x(point)},${y(amount(point, "balance"))}`).join(" ");
  const ticks = [{ date: startDate, label: `Now · ${ageAt(startDate)}` }];
  for (let age = Math.ceil((ageAt(startDate) + 1) / 5) * 5; age < ageAt(last.date); age += 5) {
    const date = `${1972 + age}-12-02`;
    if (Date.parse(date) < Date.parse(last.date) - duration * .065) ticks.push({ date, label: String(age) });
  }
  if (last.date !== startDate) ticks.push({ date: last.date, label: `Age ${ageAt(last.date)}` });
  const retirementX = x(atRetirement);
  const markerTextX = Math.max(pad.left + 8, Math.min(retirementX + 8, width - pad.right - 150));
  const status = !hasTarget ? "neutral" : firstShortfall ? "shortfall" : "funded";
  return <section className="section-panel retirement-story">
    <div className="panel-heading"><h2>Your retirement picture</h2>
      <label className="story-dollar-toggle"><input type="checkbox" checked={todayDollars} onChange={(event) => setTodayDollars(event.target.checked)} /> Today’s dollars</label>
    </div>
    <div className={`story-result story-result-${status}`} role="status">
      <span className="story-eyebrow">Will our money last?</span>
      <h3>{!hasTarget ? "Enter an income target to see how your plan holds up."
        : firstShortfall ? `The income plan falls short at age ${ageAt(firstShortfall.date)}.`
          : `Your income plan is funded through age ${ageAt(last.date)}.`}</h3>
      <p>{!hasTarget ? "The chart currently shows growth with all dividends reinvested."
        : firstShortfall ? `Savings cannot cover the full planned income from ${dateLabel(firstShortfall.date)} under these assumptions.`
          : `${formatAmount(last, "balance")} remains at age ${ageAt(last.date)} under these assumptions.`}</p>
    </div>
    <p className="story-units">{todayDollars ? "All amounts below are in today’s CAD purchasing power, before tax." : "All amounts below are in future CAD, before tax; income rises with inflation."}</p>
    <div className="story-milestones">
      <div><span>1 · Today</span><strong>{formatAmount(points[0], "balance")}</strong><p>Our starting savings</p></div>
      <div><span>2 · Retire at {ageAt(atRetirement.date)}</span><strong>{formatAmount(atRetirement, "balance")}</strong><p>{formatAmount(atRetirement, "annualIncome")} / year in dividends{hasBalanceEstimates ? " · Uses your balance estimates" : ""}</p></div>
      <div><span>3 · At age {ageAt(last.date)}</span><strong>{formatAmount(last, "balance")}</strong><p>Savings remaining after planned income</p></div>
    </div>
    <div className="story-chart-heading"><h3>Our savings over time</h3><span>Age is Jamie’s age</span></div>
    <div className="story-chart-scroll">
      <svg className="story-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Projected savings by Jamie’s age, with retirement marked"
        onPointerMove={(event) => {
          if (event.pointerType === "touch") return;
          const bounds = event.currentTarget.getBoundingClientRect();
          const pointer = (event.clientX - bounds.left) / bounds.width * width;
          let index = 0;
          points.forEach((point, candidate) => { if (Math.abs(x(point) - pointer) < Math.abs(x(points[index]) - pointer)) index = candidate; });
          setSelectedIndex(index);
        }}>
        <rect className="story-before-retirement" x={pad.left} y={pad.top} width={Math.max(0, retirementX - pad.left)} height={baseline - pad.top} />
        {[0, .25, .5, .75, 1].map((fraction) => <g key={fraction}>
          <line className="story-grid" x1={pad.left} x2={width - pad.right} y1={y(maximum * fraction)} y2={y(maximum * fraction)} />
          <text className="story-value-tick" x={pad.left - 12} y={y(maximum * fraction) + 4}>{compact.format(maximum * fraction)}</text>
        </g>)}
        <polygon className="story-balance-area" points={`${pad.left},${baseline} ${line} ${x(last)},${baseline}`} />
        <polyline className="story-balance-line" points={line} />
        <line className="story-retirement-line" x1={retirementX} x2={retirementX} y1={pad.top} y2={baseline} />
        <text className="story-retirement-label" x={markerTextX} y={pad.top - 10}>Retire at {ageAt(atRetirement.date)}</text>
        {ticks.map((tick) => <text className="story-age-tick" key={tick.date} x={xTime(Date.parse(tick.date))} y={height - 12}>{tick.label}</text>)}
        <line className="story-selected-line" x1={x(shown)} x2={x(shown)} y1={pad.top} y2={baseline} />
        <circle className="story-selected-dot" cx={x(shown)} cy={y(amount(shown, "balance"))} r="5" />
      </svg>
    </div>
    <div className="story-phases"><span>Before retirement: dividends buy more investments</span><span>After retirement: dividends + savings pay our income</span></div>
    {hasBalanceEstimates && <p className="row-sub story-estimate-note">Your estimated balances replace the calculated amounts at retirement. A step in the chart reflects that change; future deposits are not scheduled individually. Estimates are entered in retirement-year dollars{todayDollars ? " and shown here in today’s purchasing power" : ""}.</p>}
    <label className="story-age-slider"><span>Explore the plan · Age {ageAt(shown.date)} · {dateLabel(shown.date)}</span>
      <input type="range" min="0" max={points.length - 1} step="1" value={selected}
        aria-label="Explore projected savings by age" aria-valuetext={`Age ${ageAt(shown.date)}, savings ${formatAmount(shown, "balance")}`}
        onChange={(event) => setSelectedIndex(Number(event.target.value))} />
    </label>
    <div className="story-selected-summary"><strong>{formatAmount(shown, "balance")}</strong> in savings · <strong>{formatAmount(shown, "annualIncome")}</strong> annual dividends</div>
    {hasTarget && funding.retired && <div className="story-income">
      <div className="story-chart-heading"><h3>Where our retirement income comes from</h3><span>Age {ageAt(funding.date)} · {dateLabel(funding.date)}</span></div>
      <p>{money(amount(funding, "targetIncome"))} annual income target · {money(amount(funding, "targetIncome") / 12)} / month on average</p>
      <div className="story-income-bar" role="img" aria-label={`Annual income: dividends ${money(dividendPart)}, savings ${money(capitalPart)}, unfunded ${money(gapPart)}`}>
        {[[dividendPart, "dividends"], [capitalPart, "capital"], [gapPart, "gap"]].map(([value, key]) => value > 0 && <div key={key} className={`story-income-${key}`} style={{ width: `${value / fundingTotal * 100}%` }} />)}
      </div>
      <div className="story-income-breakdown">
        <div className="story-income-dividend-label"><span>From dividends</span><strong>{money(dividendPart)} / year</strong></div>
        <div className="story-income-capital-label"><span>From selling investments</span><strong>{money(capitalPart)} / year</strong></div>
        {gapPart > .01 && <div className="story-income-gap-label"><span>Income we cannot fund</span><strong>{money(gapPart)} / year</strong></div>}
      </div>
      <p className="row-sub">Annual pace for the selected monthly period. {shown.date <= retirementDate ? "Before retirement, this panel previews the first retirement period." : "Move the age slider to see how the income mix changes."}</p>
      {capitalPart > 0 && <p className="row-sub">Selling from: {(funding.accounts || []).filter((account) => account.capitalFunding > .01).map((account) => account.name).join(", ")}.</p>}
    </div>}
    {accounts && <details className="story-assumptions"><summary>Account balances at age {ageAt(shown.date)}</summary>
      <div className="table-wrap"><table><thead><tr><th>Account</th><th className="numeric">Balance</th><th className="numeric">Annual dividends</th></tr></thead>
        <tbody>{(shown.accounts || []).map((account) => <tr key={account.id}><td>{account.name}</td>
          <td className="numeric">{money(account.balance / (todayDollars ? shown.factor : 1))}</td>
          <td className="numeric">{money(account.annualIncome / (todayDollars ? shown.factor : 1))}</td></tr>)}</tbody>
      </table></div>
    </details>}
    <details className="story-assumptions"><summary>How this projection works</summary>
      <p>Each account reinvests its own dividends before retirement. Optional retirement balance estimates replace calculated balances on the retirement date, including any expected future deposits. Dividend income scales at that account’s projected yield; non-dividend accounts retain zero dividend income. Once retired, dividends pay income first; investments are sold in your chosen account order to cover the rest. Each account is used up before selling from the next. Surplus dividends are reinvested in their source accounts. Selling dividend-paying investments reduces future dividends; selling an account with no dividends leaves other accounts’ dividends intact. The same capital and dividend growth rates apply to each account. Desired income increases with inflation. Taxes, individually scheduled contributions and changing market returns are not included. This is an illustration based on your inputs.</p>
    </details>
  </section>;
}
