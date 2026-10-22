import React, { useMemo, useState } from 'react';
import { ageOn, drawdownAccounts, projectDrawdown } from './drawdown.js';

const money = new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', maximumFractionDigits: 0 });
const percent = new Intl.NumberFormat('en-CA', { style: 'percent', minimumFractionDigits: 2, maximumFractionDigits: 2 });
const compact = new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', notation: 'compact', maximumFractionDigits: 1 });
const series = [
  { key: 'west', label: 'West Bragg', color: '#a26921' },
  { key: 'rsp', label: 'Jamie + Michelle RSP', color: '#2764a5' },
  { key: 'total', label: 'Total remaining', color: '#227a55' },
];
function balances(point) {
  const west = point.accounts.filter((a) => a.westBragg).reduce((sum, a) => sum + a.balance, 0);
  const rsp = point.accounts.filter((a) => !a.westBragg).reduce((sum, a) => sum + a.balance, 0);
  return { ...point, west, rsp, total: west + rsp };
}

export default function Drawdown({ data }) {
  const [inputs, setInputs] = useState({ retirementAge: '', endAge: '', annualWithdrawal: '', priceGrowth: '2' });
  const startDate = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Edmonton', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
  const currentAge = ageOn(startDate);
  const accounts = useMemo(() => drawdownAccounts(data), [data]);
  const ready = Object.values(inputs).every((value) => value !== '');
  const result = useMemo(() => {
    if (!ready) return null;
    try { return projectDrawdown({ accounts, startDate, ...Object.fromEntries(Object.entries(inputs).map(([key, value]) => [key, Number(value)])) }); }
    catch (error) { return { error: error.message }; }
  }, [accounts, startDate, inputs, ready]);
  const field = (key, label, min, max, step = 1) => <label><span>{label}</span><input type="number" required min={min} max={max} step={step}
    value={inputs[key]} onChange={(event) => setInputs((current) => ({ ...current, [key]: event.target.value }))} /></label>;
  const missing = accounts.reduce((sum, a) => sum + a.missingIncome, 0);
  return <section className="section-panel drawdown-panel">
    <div className="panel-heading"><h2>Retirement drawdown</h2><span>Jamie’s age today: {currentAge}</span></div>
    <div className="retirement-holdings-inputs">
      {field('retirementAge', 'At what age will you retire?', currentAge, 119)}
      {field('endAge', 'Expected end-of-life age', currentAge + 1, 120)}
      {field('annualWithdrawal', 'Expected annual withdrawal (CAD)', 0, undefined, 1000)}
      {field('priceGrowth', 'Annual share-price increase (%)', -99.9, 100, 0.1)}
    </div>
    <p className="row-sub">West Bragg accounts are drawn down first, then Jamie and Michelle’s RSPs. Withdrawals include dividends and sales, before tax, and stay fixed each year.</p>
    <p className="row-sub">Included: {accounts.map((account) => account.name).join(' · ') || 'No matching accounts available'}.</p>
    {missing > 0 && <p className="row-sub">Dividend estimates are missing for {missing} holding(s); their balances are included with zero dividend income.</p>}
    {!ready ? <p className="empty-state" role="status">Enter your retirement age, expected end-of-life age and annual withdrawal to see the year-by-year chart.</p>
      : result.error ? <p className="empty-state" role="alert">{result.error}</p> : <>
        <p className="drawdown-result" role="status">{result.firstShortfall
          ? `The first withdrawal shortfall starts at age ${ageOn(result.firstShortfall)} (${result.firstShortfall}).`
          : `Withdrawals are covered through age ${inputs.endAge}, with ${money.format(result.rows.at(-1).ending)} remaining.`}</p>
        <DrawdownChart result={result} />
        <div className="table-wrap"><table>
          <caption>Annual totals in CAD. ROI includes price gains and dividends, with withdrawals added back, divided by the opening balance. First and final years may be partial; ROI is not annualized. Age and balances are at the end of each period.</caption>
          <thead><tr><th scope="col">Year</th><th scope="col">Age</th><th scope="col" className="numeric" title="(Ending balance + funded withdrawals − opening balance) ÷ opening balance. Includes price gains and dividends; DRIP is counted once. Partial-year returns are not annualized.">ROI (%)</th>{['Dividends earned', 'Reinvested (DRIP)', 'West Bragg withdrawal', 'RSP withdrawal', 'West Bragg remaining', 'RSP remaining', 'Total remaining', 'Unfunded withdrawal'].map((label) => <th scope="col" key={label}>{label}</th>)}</tr></thead>
          <tbody>{result.rows.map(balances).map((row) => <tr key={row.year}>
            <th scope="row">{row.year}</th><td className="numeric">{ageOn(row.date)}</td>
            <td className="numeric" title={`Investment gain: ${money.format(row.investmentGain)} · Opening balance: ${money.format(row.opening)}`}>{row.roi === null ? 'n/a' : percent.format(row.roi)}</td>
            {[row.dividends, row.drip, row.westWithdrawal, row.rspWithdrawal, row.west, row.rsp, row.total, row.shortfall].map((value, index) => <td className="numeric" key={index}>{money.format(value)}</td>)}
          </tr>)}</tbody>
        </table></div>
      </>}
    <details className="retirement-holdings-method"><summary>How this projection works</summary>
      <p>Starts with the latest account balances and cached annual dividend estimates on {startDate}. Retirement begins on Jamie’s December 2 birthday, or today if you enter his current age. The projection stops at the selected end-of-life birthday. Withdrawals are prorated for partial years. Before retirement, dividends are reinvested. After retirement, each account’s dividends, cash and investments fund withdrawals in the listed order; accounts later in the order keep reinvesting until needed. Each account is modelled as a blended investment: fractional DRIP purchases use prices increasing at your chosen rate, and selling units reduces future dividend income. Dividend per unit stays flat. Cash earns no growth. Calculations run monthly with additional boundaries at retirement and year end. Exchange rates stay fixed. No new contributions, inflation, taxes, CPP/OAS or mandatory RSP withdrawals are included.</p>
    </details>
  </section>;
}

export function DrawdownChart({ result }) {
  const points = [result.initial, ...result.rows].map(balances);
  const width = 1000, height = 320, left = 90, right = 25, top = 25, bottom = 55;
  const start = Date.parse(points[0].date), end = Date.parse(points.at(-1).date);
  const max = Math.max(1, ...points.map((point) => point.total)) * 1.05;
  const x = (date) => left + (Date.parse(date) - start) / (end - start) * (width - left - right);
  const y = (value) => height - bottom - value / max * (height - top - bottom);
  const tickStep = Math.max(1, Math.ceil(points.length / 9));
  return <>
    <div className="drawdown-legend">{series.map((item) => <span key={item.key}><i style={{ background: item.color }} />{item.label}</span>)}</div>
    <div className="drawdown-chart-scroll"><svg viewBox={`0 0 ${width} ${height}`} className="drawdown-chart" role="img" aria-label="Year-by-year retirement balances for West Bragg, Jamie and Michelle RSPs, and total remaining">
      {[0, .25, .5, .75, 1].map((fraction) => <g key={fraction}>
        <line x1={left} x2={width - right} y1={y(max * fraction)} y2={y(max * fraction)} stroke="#dfe5e9" />
        <text x={left - 10} y={y(max * fraction) + 4} textAnchor="end">{compact.format(max * fraction)}</text>
      </g>)}
      <line x1={x(result.retirementDate)} x2={x(result.retirementDate)} y1={top} y2={height - bottom} stroke="#59656d" strokeDasharray="5 4" />
      <text x={Math.min(x(result.retirementDate) + 6, width - 170)} y={top - 7}>Retire at {ageOn(result.retirementDate)}</text>
      {series.map((item) => <polyline key={item.key} points={points.map((point) => `${x(point.date)},${y(point[item.key])}`).join(' ')} fill="none" stroke={item.color} strokeWidth={item.key === 'total' ? 3 : 2} />)}
      {points.map((point, index) => <g key={point.date}>
        <circle cx={x(point.date)} cy={y(point.total)} r="4" fill="#227a55"><title>{point.date} · Age {ageOn(point.date)} · West Bragg {money.format(point.west)} · RSP {money.format(point.rsp)} · Total {money.format(point.total)}</title></circle>
        {(index % tickStep === 0 || index === points.length - 1) && <text x={x(point.date)} y={height - bottom + 23} textAnchor={index === points.length - 1 ? 'end' : 'middle'}>{point === points[0] ? 'Now' : point.year}</text>}
      </g>)}
      <text x={width / 2} y={height - 5} textAnchor="middle">Year · balances in future CAD</text>
    </svg></div>
  </>;
}
