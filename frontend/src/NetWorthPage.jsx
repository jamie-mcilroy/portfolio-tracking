import React, { useEffect, useState } from 'react';
import { categories, netWorthTotals } from './netWorth.js';
import NetWorthHistory from './NetWorthHistory.jsx';

const money = new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', maximumFractionDigits: 0 });
const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Edmonton', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
const emptyItem = () => ({ name: '', category: 'property', value: '', mortgage: '0', valuation_date: today(), notes: '' });

export default function NetWorthPage({ data, request, parseResponse, onAuthError }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [reportBusy, setReportBusy] = useState(false);
  const [form, setForm] = useState(null);
  const [message, setMessage] = useState('');
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true); setError('');
    (async () => {
      try {
        const response = await request('/api/net-worth');
        if (response.status === 401) onAuthError();
        const payload = await parseResponse(response);
        if (active) { setItems(payload.items); setLoaded(true); }
      } catch (error) { if (active) setError(error.message); }
      finally { if (active) setLoading(false); }
    })();
    return () => { active = false; };
  }, [request, parseResponse, reload]);
  async function mutate(path, options) {
    const response = await request(path, options);
    if (response.status === 401) onAuthError();
    return parseResponse(response);
  }
  async function save(event) {
    event.preventDefault(); setBusy(true); setError(''); setMessage('');
    try {
      const payload = { ...form, value: Number(form.value), mortgage: form.category === 'property' ? Number(form.mortgage) : 0 };
      const result = await mutate(`/api/net-worth${form.id ? `/${form.id}` : ''}`, {
        method: form.id ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      setItems((current) => [...current.filter((item) => item.id !== result.item.id), result.item]);
      setForm(null); setMessage('Saved. Net worth totals updated.');
    } catch (error) { setError(error.message); }
    finally { setBusy(false); }
  }
  async function remove(item) {
    if (!window.confirm(`Delete “${item.name}” from Net Worth?`)) return;
    setBusy(true); setError(''); setMessage('');
    try {
      await mutate(`/api/net-worth/${item.id}`, { method: 'DELETE' });
      setItems((current) => current.filter((row) => row.id !== item.id));
      if (form?.id === item.id) setForm(null);
      setMessage('Item deleted.');
    } catch (error) { setError(error.message); }
    finally { setBusy(false); }
  }
  async function generateReport() {
    setReportBusy(true); setError(''); setMessage('');
    try {
      const response = await request('/api/net-worth/report', { cache: 'no-store' });
      if (response.status === 401) onAuthError();
      if (!response.ok) { await parseResponse(response); return; }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url; link.download = `net-worth-${today()}.pdf`;
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      setMessage('Report downloaded. Ready to share with Michelle.');
    } catch (error) { setError(error.message); }
    finally { setReportBusy(false); }
  }
  const totals = netWorthTotals(data.accounts || [], items);
  const change = (key) => (event) => setForm((current) => ({ ...current, [key]: event.target.value }));
  return <section className="view active" aria-label="Net Worth"><section className="section-panel net-worth-panel">
    <div className="panel-heading"><h2>Net worth</h2><div className="net-worth-actions"><span>All values in CAD</span><button type="button" disabled={!loaded || loading || busy || reportBusy || form !== null || !!error} onClick={generateReport}>{reportBusy ? 'Generating…' : 'Generate Report'}</button></div></div>
    <p>Investment accounts update with your portfolio. Add your other assets at their estimated resale value, and keep each property’s mortgage attached to it.</p>
    {error && <p className="message error" role="alert">{error} <button type="button" disabled={busy || loading} onClick={() => setReload((value) => value + 1)}>Refresh list</button></p>}
    {message && <p role="status">{message}</p>}
    {loading ? <p role="status">Loading net worth…</p> : !loaded ? <p>Net worth totals are unavailable until saved assets and debts can be loaded.</p> : <>
      <div className="net-worth-totals">
        {[['Total assets', totals.assets], ['Total debts', totals.debts], ['Net worth', totals.netWorth], ['Investment accounts', totals.investments]].map(([label, value]) => <div key={label}><span>{label}</span><strong>{money.format(value)}</strong></div>)}
      </div>
      <p className="row-sub">All investment accounts are included, with their cash already counted. Property equity = market value − outstanding mortgage. Mortgages are included in total debts once; enter only additional liabilities under Other debts. Add only assets that are outside the tracked investment accounts.</p>
      <div className="table-wrap"><table><caption>Net worth by category</caption><thead><tr><th>Category</th><th>Asset value</th><th>Debt</th><th>Net value / equity</th></tr></thead>
        <tbody><tr><th scope="row">Investment accounts</th><td className="numeric">{money.format(totals.investments)}</td><td className="numeric">—</td><td className="numeric">{money.format(totals.investments)}</td></tr>
          {totals.breakdown.map((row) => <tr key={row.category}><th scope="row">{categories[row.category]}</th>{[row.value, row.debt, row.net].map((value, i) => <td className="numeric" key={i}>{money.format(value)}</td>)}</tr>)}
        </tbody></table></div>
      <div className="panel-heading"><h3>Other assets & debts</h3><button type="button" disabled={busy || form !== null || !!error} onClick={() => { setForm(emptyItem()); setMessage(''); }}>Add item</button></div>
      {form && <form onSubmit={save} className="net-worth-form"><fieldset disabled={busy}>
        <legend>{form.id ? 'Edit item' : 'New item'}</legend>
        <div className="retirement-holdings-inputs">
          <label><span>Name</span><input required maxLength={200} value={form.name} onChange={change('name')} placeholder="e.g. Family home" /></label>
          <label><span>Category</span><select value={form.category} onChange={(event) => setForm((current) => ({ ...current, category: event.target.value, mortgage: '0' }))}>{Object.entries(categories).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
          <label><span>{form.category === 'debt' ? 'Outstanding debt (CAD)' : 'Estimated market value (CAD)'}</span><input type="number" min="0" max="1000000000000000" step="0.01" required value={form.value} onChange={change('value')} /></label>
          {form.category === 'property' && <label><span>Outstanding mortgage (CAD)</span><input type="number" min="0" max="1000000000000000" step="0.01" required value={form.mortgage} onChange={change('mortgage')} /></label>}
          <label><span>Valuation date</span><input type="date" required value={form.valuation_date} onChange={change('valuation_date')} /></label>
        </div>
        {form.category === 'property' && <p className="drawdown-result">Equity: {form.value === '' || form.mortgage === '' ? 'Enter value and mortgage' : money.format(Number(form.value) - Number(form.mortgage))}</p>}
        <label className="net-worth-notes">Notes<textarea maxLength={2000} value={form.notes} onChange={change('notes')} /></label>
        <div className="net-worth-actions"><button type="submit">{busy ? 'Saving…' : 'Save item'}</button><button type="button" onClick={() => setForm(null)}>Cancel</button></div>
      </fieldset></form>}
      <div className="table-wrap"><table><caption>Saved manual valuations. Investments are pulled from Portfolio separately.</caption>
        <thead><tr>{['Name', 'Category', 'Value', 'Mortgage', 'Net value / equity', 'Valued on', 'Actions'].map((label) => <th scope="col" key={label}>{label}</th>)}</tr></thead>
        <tbody>{items.length === 0 ? <tr><td colSpan="7">No manual assets or debts yet. Add your first property or other item above.</td></tr> : [...items].sort((a, b) => a.category.localeCompare(b.category) || a.name.localeCompare(b.name)).map((item) => <tr key={item.id}>
          <th scope="row">{item.name}{item.notes && <span className="row-sub net-worth-item-note">{item.notes}</span>}</th><td>{categories[item.category]}</td>
          <td className="numeric">{money.format(item.value)}</td><td className="numeric">{item.category === 'property' ? money.format(item.mortgage) : '—'}</td>
          <td className="numeric">{money.format(item.category === 'debt' ? -item.value : item.value - item.mortgage)}</td><td>{item.valuation_date}</td>
          <td><div className="net-worth-actions"><button type="button" aria-label={`Edit ${item.name}`} disabled={busy || form !== null} onClick={() => { setForm({ ...item }); setMessage(''); }}>Edit</button><button type="button" aria-label={`Delete ${item.name}`} disabled={busy || form !== null} onClick={() => remove(item)}>Delete</button></div></td>
        </tr>)}</tbody></table></div>
      <NetWorthHistory request={request} parseResponse={parseResponse} onAuthError={onAuthError} editing={form !== null || busy} />
    </>}
  </section></section>;
}
