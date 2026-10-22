import React, { useMemo, useState } from 'react';
import { ageOn, drawdownAccounts } from './drawdown.js';
import { projectDieWithZero } from './dieWithZero.js';
import { DrawdownChart } from './Drawdown.jsx';

const money = new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', maximumFractionDigits: 0 });

export default function DieWithZero({ data }) {
  const [inputs, setInputs] = useState({ retirementAge: '', endAge: '', priceGrowth: '2' });
  const startDate = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Edmonton', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
  const currentAge = ageOn(startDate);
  const accounts = useMemo(() => drawdownAccounts(data), [data]);
  const ready = Object.values(inputs).every((value) => value !== '');
  const result = useMemo(() => {
    if (!ready) return null;
    try { return projectDieWithZero({ accounts, startDate, ...Object.fromEntries(Object.entries(inputs).map(([key, value]) => [key, Number(value)])) }); }
    catch (error) { return { error: error.message }; }
  }, [accounts, startDate, inputs, ready]);
  const field = (key, label, min, max, step = 1) => <label><span>{label}</span><input type="number" required min={min} max={max} step={step}
    value={inputs[key]} onChange={(event) => setInputs((current) => ({ ...current, [key]: event.target.value }))} /></label>;
  const missing = accounts.reduce((sum, account) => sum + account.missingIncome, 0);
  return <section className="section-panel drawdown-panel">
    <div className="panel-heading"><h2>Die With Zero</h2><span>Jamie’s age today: {currentAge}</span></div>
    <p>Find the steady yearly income that uses your portfolio by your chosen end-of-life age. Both ages use Jamie’s birthday.</p>
    <div className="retirement-holdings-inputs">
      {field('retirementAge', 'At what age will you retire?', currentAge, 119)}
      {field('endAge', 'Expected end-of-life age (Jamie’s age)', currentAge + 1, 120)}
      {field('priceGrowth', 'Annual share-price increase (%)', -99.9, 100, 0.1)}
    </div>
    <p className="row-sub">Included: {accounts.map((account) => account.name).join(' · ') || 'No matching accounts available'}. Current balance: {money.format(accounts.reduce((sum, account) => sum + account.balance, 0))}.</p>
    <p className="row-sub">Uses the same growth, dividend and withdrawal assumptions as Drawdown. West Bragg funds withdrawals first, followed by Jamie and Michelle’s RSPs.</p>
    {missing > 0 && <p className="row-sub">Dividend estimates are missing for {missing} holding(s); their balances are included with zero dividend income.</p>}
    {!ready ? <p className="empty-state" role="status">Enter your retirement age, expected end-of-life age and stock growth to calculate your yearly income.</p>
      : result.error ? <p className="empty-state" role="alert">{result.error}</p> : <>
        <p className="drawdown-result" role="status">{money.format(result.annualWithdrawal)} per year before tax · {money.format(result.annualWithdrawal / 12)} per month on average · {money.format(result.rows.at(-1).ending)} remaining at age {inputs.endAge}</p>
        <p className="row-sub">Income stays fixed in future CAD. Growth generally makes the balance fall along a curve; a straight-line balance decline would require changing the income each year. Partial years receive prorated income.</p>
        <DrawdownChart result={result} />
        <div className="table-wrap"><table>
          <caption>Annual totals in CAD. Income includes dividends, cash and investment sales. Age and balances are at the end of each period; first and final years may be partial.</caption>
          <thead><tr>{['Year', 'Jamie’s age', 'Opening balance', 'Investment gain / loss', 'Yearly income', 'West Bragg income', 'RSP income', 'Remaining balance'].map((label) => <th scope="col" key={label}>{label}</th>)}</tr></thead>
          <tbody>{result.rows.map((row) => <tr key={row.year}><th scope="row">{row.year}</th><td className="numeric">{ageOn(row.date)}</td>
            {[row.opening, row.investmentGain, row.westWithdrawal + row.rspWithdrawal, row.westWithdrawal, row.rspWithdrawal, row.ending].map((value, index) => <td className="numeric" key={index}>{money.format(value)}</td>)}
          </tr>)}</tbody>
        </table></div>
      </>}
    <details className="retirement-holdings-method"><summary>How this projection works</summary>
      <p>The calculation solves for a fixed annual withdrawal that leaves approximately zero at the selected end-of-life birthday, without running out earlier. Retirement begins on Jamie’s December 2 birthday, or today when you enter his current age. Until retirement, dividends are reinvested and no income is withdrawn. Withdrawals are modelled monthly and prorated by calendar year.</p>
      <p>Starts with current account balances and cached dividend estimates on {startDate}. Your growth input applies to share prices; dividends are additional, with dividend per unit held flat. Reinvesting buys units and selling reduces future dividends. Cash earns no growth. Exchange rates stay fixed. No contributions, inflation adjustments, taxes, CPP/OAS or mandatory RSP withdrawals are included. The zero balance depends on the assumed returns and lifespan; actual results will vary.</p>
    </details>
  </section>;
}
