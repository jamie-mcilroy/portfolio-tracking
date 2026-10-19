import React, { useState } from "react";
import { modelRental } from "./rentalCashFlow.js";

const cad = new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', maximumFractionDigits: 0 });
const preciseCad = new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', minimumFractionDigits: 2, maximumFractionDigits: 2 });
const money = (value) => value == null ? 'Not listed' : cad.format(value);
function initial(listing) {
  const values = { price: listing.price ?? '', rent: '', downPaymentPct: 20, interestRate: 5,
    amortizationYears: 25, condoFee: listing.condo_fee_monthly ?? '', annualTax: listing.property_tax_annual ?? '',
    insurance: 50, utilities: 0, maintenancePct: 5, managementPct: 0, vacancyPct: 5, otherCosts: 0, closingCosts: 0 };
  const { result } = calculate(values);
  if (result) values.rent = Number(result.breakEvenRent.toFixed(2));
  return values;
}

function calculate(values) {
  try {
    const numbers = Object.fromEntries(Object.entries(values).map(([key, value]) =>
      [key, value === '' ? (key === 'rent' ? 0 : NaN) : Number(value)]));
    return { result: modelRental(numbers) };
  } catch (error) { return { error: error.message }; }
}

export default function RealEstatePage({ request, parseResponse, onAuthError }) {
  const [mls, setMls] = useState('');
  const [comparisons, setComparisons] = useState([]);
  const [selectedMls, setSelectedMls] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const selected = comparisons.find((item) => item.listing.mls === selectedMls);
  async function lookup(event) {
    event.preventDefault();
    const normalized = mls.trim().toUpperCase();
    if (!/^[A-Z]\d{7}$/.test(normalized)) { setError('Enter an MLS number such as A2338340.'); return; }
    if (comparisons.some((item) => item.listing.mls === normalized)) {
      setSelectedMls(normalized); setMls(''); setError(''); return;
    }
    setBusy(true); setError('');
    try {
      const response = await request('/api/real-estate/condos', { method: 'POST', cache: 'no-store', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mls: normalized }) });
      if (response.status === 401) onAuthError();
      const data = await parseResponse(response);
      setComparisons((current) => [...current, { listing: data.listing, values: initial(data.listing) }]);
      setMls('');
    } catch (error) { setError(error.message); }
    finally { setBusy(false); }
  }
  function updateSelected(update) {
    setComparisons((current) => current.map((item) => item.listing.mls === selectedMls
      ? { ...item, values: typeof update === 'function' ? update(item.values) : update } : item));
  }
  return <section className="view active" aria-label="Real Estate">
    <div className="tabs" role="tablist" aria-label="Real Estate sections">
      <button className="tab active" type="button" role="tab" id="rental-condo-tab" aria-selected="true" aria-controls="rental-condo-panel">Rental Condo</button>
    </div>
    <section className="section-panel rental-condo-panel" role="tabpanel" id="rental-condo-panel" aria-labelledby="rental-condo-tab">
      <div className="panel-heading"><h2>{selected ? 'Rental condo model' : 'Rental condo comparisons'}</h2><span>{comparisons.length} {comparisons.length === 1 ? 'property' : 'properties'} · Temporary</span></div>
      {selected ? <>
        <button className="rental-back" type="button" onClick={() => setSelectedMls(null)}>← Back to comparisons</button>
        <RentalModel listing={selected.listing} values={selected.values} onValuesChange={updateSelected} />
      </> : <>
        <form className="rental-condo-lookup" onSubmit={lookup}>
          <label htmlFor="condo-mls">MLS number</label>
          <input id="condo-mls" type="text" placeholder="A2338340" value={mls} onChange={(event) => setMls(event.target.value.toUpperCase())}
            maxLength="8" pattern="[A-Za-z][0-9]{7}" required autoComplete="off" disabled={busy} />
          <button type="submit" disabled={busy}>{busy ? 'Looking up…' : 'Add to comparison'}</button>
          {comparisons.length > 0 && <button className="rental-clear" type="button" disabled={busy} onClick={() => { setComparisons([]); setMls(''); setError(''); }}>Clear comparisons</button>}
        </form>
        <p className="row-sub">Add MLS numbers to compare properties in columns, with categories down the rows. Click any property column to model its rent and expenses. Each property keeps its own inputs while you compare. Nothing is saved; leaving Real Estate clears the comparisons.</p>
        {error && <div className="message error" role="alert">{error}</div>}
        <RentalComparisonList comparisons={comparisons} onOpen={setSelectedMls}
          onEdit={(mls, key, value) => setComparisons((current) => current.map((item) => item.listing.mls === mls
            ? { ...item, values: { ...item.values, [key]: value } } : item))}
          onRemove={(mls) => setComparisons((current) => current.filter((item) => item.listing.mls !== mls))} />
      </>}
      <p className="row-sub rental-condo-source">Listing and photo source: CalgaryListings.com. Coverage depends on that source. Missing fees or taxes must be entered before the calculation can be completed.</p>
    </section>
  </section>;
}

export function RentalComparisonList({ comparisons, onOpen, onRemove, onEdit }) {
  if (!comparisons.length) return <div className="empty-state">Add an MLS number above to start your comparison.</div>;
  const columns = comparisons.map((item) => ({ ...item, ...calculate(item.values), hasRent: item.values.rent !== '' }));
  const dollars = (value) => value === '' || value == null ? '—' : money(Number(value));
  const percent = (value) => value === '' || value == null ? '—' : `${Number(value).toFixed(2)}%`;
  const resultMoney = (key, rentRequired = false) => (item) => item.result && (!rentRequired || item.hasRent) ? preciseCad.format(item.result[key]) : '—';
  const resultPercent = (key) => (item) => item.result && item.hasRent ? item.result[key] == null ? 'n/a' : percent(item.result[key]) : '—';
  const editableRows = {
    'Modelled purchase price': { key: 'price', min: .01 },
    'Condo fee / month': { key: 'condoFee' },
    'Property taxes / year': { key: 'annualTax' },
    'Expected rent / month': { key: 'rent', placeholder: 'Enter rent' },
    'Down payment (%)': { key: 'downPaymentPct', max: 100 },
    'Annual mortgage rate': { key: 'interestRate', max: 100, step: .1 },
    'Amortization (years)': { key: 'amortizationYears', min: 1, max: 50, step: 1 },
    'Closing costs': { key: 'closingCosts' },
    'Vacancy allowance': { key: 'vacancyPct', max: 99.9 },
    'Landlord insurance / month': { key: 'insurance' },
    'Owner-paid utilities / month': { key: 'utilities' },
    'Maintenance (% collected rent)': { key: 'maintenancePct', max: 99.9 },
    'Management (% collected rent)': { key: 'managementPct', max: 99.9 },
    'Other costs / reserves per month': { key: 'otherCosts' },
  };
  const groups = [
    ['Property', [
      ['Main photo', (item) => <ListingPhoto listing={item.listing} />, 'photo'],
      ['Address', (item) => item.listing.address, 'address'],
      ['Asking price', (item) => money(item.listing.price)],
      ['Modelled purchase price', (item) => dollars(item.values.price)],
      ['Condo fee / month', (item) => dollars(item.values.condoFee)],
      ['Property taxes / year', (item) => dollars(item.values.annualTax)],
      ['Bedrooms', (item) => item.listing.bedrooms ?? 'Not listed'],
      ['Bathrooms', (item) => item.listing.bathrooms ?? 'Not listed'],
    ]],
    ['Rent & financing', [
      ['Expected rent / month', (item) => item.hasRent ? dollars(item.values.rent) : 'Enter rent'],
      ['Down payment (%)', (item) => percent(item.values.downPaymentPct)],
      ['Down payment (CAD)', resultMoney('downPayment')],
      ['Annual mortgage rate', (item) => percent(item.values.interestRate)],
      ['Amortization (years)', (item) => item.values.amortizationYears || '—'],
      ['Mortgage amount', resultMoney('mortgage')],
      ['Mortgage payment / month', resultMoney('mortgagePayment')],
      ['Closing costs', (item) => dollars(item.values.closingCosts)],
    ]],
    ['Operating assumptions', [
      ['Vacancy allowance', (item) => percent(item.values.vacancyPct)],
      ['Landlord insurance / month', (item) => dollars(item.values.insurance)],
      ['Owner-paid utilities / month', (item) => dollars(item.values.utilities)],
      ['Maintenance (% collected rent)', (item) => percent(item.values.maintenancePct)],
      ['Management (% collected rent)', (item) => percent(item.values.managementPct)],
      ['Other costs / reserves per month', (item) => dollars(item.values.otherCosts)],
    ]],
    ['Results · before income tax', [
      ['Break-even rent / month', resultMoney('breakEvenRent')],
      ['Cash flow / month', resultMoney('cashFlow', true), 'cashFlow'],
      ['Annual cash flow', resultMoney('annualCashFlow', true), 'annualCashFlow'],
      ['Annual return on down payment', resultPercent('downPaymentReturnPct'), 'downPaymentReturnPct'],
      ['Annual return incl. closing costs', resultPercent('cashOnCashReturnPct'), 'cashOnCashReturnPct'],
      ['Initial cash required', resultMoney('initialCash')],
      ['Cash-flow verdict', (item) => item.error || (!item.hasRent ? 'Enter rent to model' : item.result.cashFlow > .005 ? 'Positive' : item.result.cashFlow < -.005 ? 'Negative' : 'Neutral'), 'verdict'],
    ]],
  ];
  return <div className="table-wrap rental-comparison-sheet-wrap">
    <table className="rental-comparison-sheet" style={{ width: `${230 + columns.length * 240}px` }} aria-label="Rental property comparison spreadsheet">
      <caption>Edit the shaded input cells directly; results update immediately. Click an MLS heading to open the full model. All amounts are CAD.</caption>
      <colgroup><col className="rental-sheet-label-col" />{columns.map(({ listing }) => <col key={listing.mls} className="rental-sheet-property-col" />)}</colgroup>
      <thead><tr><th scope="col">Category</th>{columns.map(({ listing }) => <th scope="col" key={listing.mls}>
        <button type="button" onClick={() => onOpen(listing.mls)} aria-label={`Open rental model for ${listing.mls}`}>{listing.mls}<span>Open model →</span></button>
      </th>)}</tr></thead>
      <tbody>{groups.map(([group, rows]) => <React.Fragment key={group}>
        <tr className="rental-sheet-group"><th scope="rowgroup" colSpan={columns.length + 1}>{group}</th></tr>
        {rows.map(([label, render, kind]) => <tr key={label}>
          <th scope="row">{label}</th>
          {columns.map((item) => {
            const editable = editableRows[label];
            const value = item.result?.[kind];
            const tone = value != null && item.hasRent ? value < -.005 ? 'negative' : value > .005 ? 'positive' : '' : '';
            return <td key={item.listing.mls} className={`${editable ? 'rental-sheet-editable' : ''} ${kind === 'photo' ? 'rental-sheet-photo' : ''} ${kind === 'address' || kind === 'verdict' ? 'rental-sheet-text' : ''} ${tone}`}>
              {editable ? <input className="rental-sheet-input" type="number" min={editable.min ?? 0}
                max={editable.max} step={editable.step ?? 'any'} placeholder={editable.placeholder}
                value={item.values[editable.key]} aria-label={`${label} for ${item.listing.mls}`}
                onChange={(event) => onEdit(item.listing.mls, editable.key, event.target.value)} />
                : <button className="rental-sheet-cell" type="button" onClick={() => onOpen(item.listing.mls)} aria-label={`${label} for ${item.listing.mls}; open model`}>{render(item)}</button>}
            </td>;
          })}
        </tr>)}
      </React.Fragment>)}</tbody>
      <tfoot><tr><th scope="row">Actions</th>{columns.map(({ listing }) => <td key={listing.mls}>
        <div className="rental-sheet-actions"><button type="button" onClick={() => onOpen(listing.mls)}>Open model</button>
          <button type="button" onClick={() => onRemove(listing.mls)} aria-label={`Remove ${listing.mls} from comparison`}>Remove</button></div>
      </td>)}</tr></tfoot>
    </table>
  </div>;
}

function ListingPhoto({ listing }) {
  const [failed, setFailed] = useState(false);
  return listing.photo_url && !failed
    ? <img className="rental-photo" src={listing.photo_url} alt={`Main listing photo — ${listing.address}`} referrerPolicy="no-referrer" onError={() => setFailed(true)} />
    : <div className="rental-photo rental-photo-empty">Listing photo unavailable</div>;
}

export function RentalModel({ listing, values, onValuesChange }) {
  const { result, error: calculationError } = calculate(values);
  const hasRent = values.rent !== '';
  const status = !result || !hasRent ? 'pending' : result.cashFlow > .005 ? 'positive' : result.cashFlow < -.005 ? 'negative' : 'neutral';
  function field(key, label, extra = {}) {
    return <label key={key}><span>{label}</span><input type="number" min="0" step="any" {...extra} value={values[key]}
      onChange={(event) => onValuesChange((current) => ({ ...current, [key]: event.target.value }))} /></label>;
  }
  const lines = result ? [
    ['Expected monthly rent', Number(values.rent || 0)], ['Vacancy allowance', -result.vacancy],
    ['Mortgage payment (principal + interest)', -result.mortgagePayment], ['Condo fee', -Number(values.condoFee)],
    ['Property taxes / month', -result.monthlyTax], ['Landlord insurance', -Number(values.insurance)],
    ['Owner-paid utilities', -Number(values.utilities)], ['Maintenance reserve', -result.maintenance],
    ['Property management', -result.management], ['Other monthly costs / reserves', -Number(values.otherCosts)],
  ] : [];
  return <div className="rental-model">
    <div className="rental-property">
      <ListingPhoto listing={listing} />
      <div><a href={listing.source_url} target="_blank" rel="noreferrer">MLS {listing.mls} · View listing</a>
        <h3>{listing.address}</h3>
        <dl className="rental-property-facts">
          <div><dt>Asking price</dt><dd>{money(listing.price)}</dd></div>
          <div><dt>Condo fee / month</dt><dd>{money(listing.condo_fee_monthly)}</dd></div>
          <div><dt>Property taxes / year{listing.tax_year ? ` (${listing.tax_year})` : ''}</dt><dd>{money(listing.property_tax_annual)}</dd></div>
          <div><dt>Bedrooms / bathrooms</dt><dd>{listing.bedrooms ?? 'Not listed'} / {listing.bathrooms ?? 'Not listed'}</dd></div>
        </dl>
      </div>
    </div>
    <div className={`rental-verdict rental-verdict-${status}`} aria-live="polite">
      <span>Monthly cash flow, before income tax</span>
      <strong>{result && hasRent ? `${result.cashFlow >= 0 ? '+' : '−'}${preciseCad.format(Math.abs(result.cashFlow))}` : hasRent ? 'Complete the model inputs' : 'Enter your expected rent'}</strong>
      <p>{calculationError || (status === 'positive' ? 'Cash-flow positive under these assumptions.' : status === 'negative' ? 'The rental needs a monthly cash contribution under these assumptions.' : status === 'neutral' ? 'Cash-flow neutral under these assumptions.' : 'Adjust the inputs below to test whether the condo can cover its costs.')}</p>
    </div>
    <div className="metrics rental-result-metrics">
      <div className="metric"><span>Break-even advertised rent / month</span><strong>{result ? preciseCad.format(result.breakEvenRent) : '—'}</strong><p className="row-sub">Includes your vacancy and expense allowances</p></div>
      <div className="metric"><span>Annual cash surplus / shortfall</span><strong>{result && hasRent ? money(result.annualCashFlow) : '—'}</strong></div>
      <div className="metric"><span>Annual cash return on down payment</span>
        <strong>{result && hasRent ? result.downPaymentReturnPct === null ? 'n/a' : `${result.downPaymentReturnPct.toFixed(2)}%` : '—'}</strong>
        <p className="row-sub">{result ? `Annual cash flow ÷ ${money(result.downPayment)} down payment` : 'Annual cash flow ÷ down payment'}</p>
      </div>
      <div className="metric"><span>Annual cash return including closing costs</span>
        <strong>{result && hasRent ? result.cashOnCashReturnPct === null ? 'n/a' : `${result.cashOnCashReturnPct.toFixed(2)}%` : '—'}</strong>
        <p className="row-sub">Annual cash flow ÷ total initial cash</p>
      </div>
      <div className="metric"><span>Mortgage payment / month</span><strong>{result ? money(result.mortgagePayment) : '—'}</strong><p className="row-sub">{result ? `${money(result.mortgage)} financed` : ''}</p></div>
      <div className="metric"><span>Initial cash required</span><strong>{result ? money(result.initialCash) : '—'}</strong><p className="row-sub">Down payment + entered closing costs</p></div>
    </div>
    <div className="rental-model-columns">
      <div>
        <fieldset className="rental-input-group"><legend>Rent & financing</legend><div className="rental-input-grid">
          {field('price', 'Modelled purchase price (CAD)')}
          {field('rent', 'Expected monthly rent (CAD)', { placeholder: 'Enter expected rent' })}
          {field('downPaymentPct', 'Down payment (%)', { max: 100 })}
          {field('interestRate', 'Annual mortgage rate (%)', { max: 100, step: .1 })}
          {field('amortizationYears', 'Amortization (years)', { min: 1, max: 50, step: 1 })}
          {field('closingCosts', 'Closing costs (CAD)')}
        </div></fieldset>
        <fieldset className="rental-input-group"><legend>Operating costs & allowances</legend><div className="rental-input-grid">
          {field('condoFee', 'Condo fee / month (CAD)')}
          {field('annualTax', 'Property taxes / year (CAD)')}
          {field('insurance', 'Landlord insurance / month (CAD)')}
          {field('utilities', 'Owner-paid utilities / month (CAD)')}
          {field('vacancyPct', 'Vacancy / non-payment (%)', { max: 99.9 })}
          {field('maintenancePct', 'Maintenance reserve (% collected rent)', { max: 99.9 })}
          {field('managementPct', 'Management fee (% collected rent)', { max: 99.9 })}
          {field('otherCosts', 'Other costs / reserves per month (CAD)')}
        </div></fieldset>
        <button className="rental-reset" type="button" onClick={() => onValuesChange(initial(listing))}>Reset assumptions</button>
      </div>
      <div className="rental-breakdown"><h3>Where the rent goes each month</h3>
        {result && hasRent ? <dl>{lines.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{preciseCad.format(value)}</dd></div>)}
          <div className="rental-breakdown-total"><dt>Cash left over</dt><dd>{preciseCad.format(result.cashFlow)}</dd></div>
        </dl> : <p className="row-sub">Complete the inputs to see the monthly breakdown.</p>}
      </div>
    </div>
    <p className="row-sub rental-assumptions">Starting values are illustrative: 20% down, 5% annual mortgage rate, 25-year amortization, 5% vacancy, 5% maintenance and $50/month insurance. Use your own estimates. Set 100% down for an all-cash purchase. Condo fees and taxes come from the listing when available.</p>
    <details className="rental-method"><summary>Calculation assumptions</summary><p>Monthly fixed-rate mortgage payments use Canadian semi-annual compounding. The full mortgage payment, including principal, is a cash outflow. Vacancy reduces collected rent; maintenance and management percentages apply to collected rent. Break-even rent covers these allowances and all entered monthly costs. Closing costs affect initial cash, not recurring cash flow. Annual cash return divides annual cash flow by the down payment, or by down payment plus closing costs. A zero denominator shows n/a. Returns exclude appreciation and equity gained through principal repayment. Cash flow is before income tax and excludes mortgage default insurance, purchase eligibility and unentered special assessments. This is a cash-flow model, not a tax-profit calculation.</p>
      <a href="https://www.bmo.com/en-ca/popups/main/personal/mortgages/terms-and-conditions-en/" target="_blank" rel="noreferrer">Fixed-rate mortgage interest convention</a>
    </details>
  </div>;
}
