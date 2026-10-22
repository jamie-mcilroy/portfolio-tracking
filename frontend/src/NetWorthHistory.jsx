import React, { useEffect, useState } from 'react';

const money = new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', maximumFractionDigits: 0 });
const savedTime = (value) => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Edmonton', dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));
const signed = (value) => `${value > 0 ? '+' : ''}${money.format(value)}`;

export default function NetWorthHistory({ request, parseResponse, onAuthError, editing }) {
  const [snapshots, setSnapshots] = useState([]);
  const [selection, setSelection] = useState({ before: '', after: '' });
  const [comparison, setComparison] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true); setError('');
    (async () => {
      try {
        const response = await request('/api/net-worth/snapshots', { cache: 'no-store' });
        if (response.status === 401) onAuthError();
        const payload = await parseResponse(response);
        if (!active) return;
        setSnapshots(payload.snapshots);
        setSelection({ before: String(payload.snapshots[1]?.id || ''), after: String(payload.snapshots[0]?.id || '') });
      } catch (error) { if (active) setError(error.message); }
      finally { if (active) setLoading(false); }
    })();
    return () => { active = false; };
  }, [request, parseResponse, revision]);
  useEffect(() => {
    let active = true;
    setComparison(null);
    if (!selection.before || !selection.after) return;
    (async () => {
      try {
        const response = await request(`/api/net-worth/changes?${new URLSearchParams(selection)}`, { cache: 'no-store' });
        if (response.status === 401) onAuthError();
        const payload = await parseResponse(response);
        if (active) { setComparison(payload); setError(''); }
      } catch (error) { if (active) setError(error.message); }
    })();
    return () => { active = false; };
  }, [request, parseResponse, selection]);
  async function saveSnapshot() {
    setBusy(true); setError(''); setMessage('');
    try {
      const response = await request('/api/net-worth/snapshots', { method: 'POST' });
      if (response.status === 401) onAuthError();
      await parseResponse(response);
      setMessage('Snapshot saved.'); setRevision((value) => value + 1);
    } catch (error) { setError(error.message); }
    finally { setBusy(false); }
  }
  async function download() {
    setBusy(true); setError('');
    try {
      const response = await request(`/api/net-worth/changes/report?${new URLSearchParams(selection)}`, { cache: 'no-store' });
      if (response.status === 401) onAuthError();
      if (!response.ok) { await parseResponse(response); return; }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url; link.download = 'net-worth-change.pdf';
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      setMessage('Change report downloaded.');
    } catch (error) { setError(error.message); }
    finally { setBusy(false); }
  }
  const current = comparison && String(comparison.before.id) === selection.before && String(comparison.after.id) === selection.after ? comparison : null;
  const table = (title, rows) => <div className="table-wrap"><table><caption>{title}</caption><thead><tr><th scope="col">Name</th><th scope="col">Earlier value</th><th scope="col">Later value</th><th scope="col">Change</th></tr></thead>
    <tbody>{rows.map((row) => <tr key={row.key}><th scope="row">{row.name}</th><td className="numeric">{money.format(row.before)}</td><td className="numeric">{money.format(row.after)}</td><td className={`numeric ${row.change < 0 ? 'negative' : row.change > 0 ? 'positive' : ''}`}>{signed(row.change)}</td></tr>)}</tbody></table></div>;
  return <section className="net-worth-history" aria-label="Net worth history">
    <div className="panel-heading"><h3>History & change reports</h3><div className="net-worth-actions">
      <button type="button" disabled={loading || busy || editing} onClick={saveSnapshot}>Save snapshot now</button>
      <button type="button" disabled={loading || busy} onClick={() => setRevision((value) => value + 1)}>Refresh history</button>
    </div></div>
    <p className="row-sub">Automatic snapshots: Fridays at 5 p.m. Edmonton time, while the server is running. An initial baseline is saved automatically. Saved values stay fixed even if you edit or delete an asset later. If the server restarts later in the same week, it saves at the actual restart time; missed past weeks are not reconstructed.</p>
    {error && <p className="message error" role="alert">{error}</p>}
    {message && <p role="status">{message}</p>}
    {loading ? <p role="status">Loading snapshots…</p> : snapshots.length < 2 ? <p>{snapshots.length ? `First snapshot saved ${savedTime(snapshots[0].captured_at)}. ` : ''}You need two snapshots to run a change report. Save one now or wait for the next weekly save.</p> : <>
      <div className="retirement-holdings-inputs">
        {[['before', 'Earlier snapshot'], ['after', 'Later snapshot']].map(([key, label]) => <label key={key}><span>{label}</span><select disabled={busy} value={selection[key]} onChange={(event) => setSelection((current) => ({ ...current, [key]: event.target.value }))}>
          {snapshots.map((snapshot) => <option key={snapshot.id} value={snapshot.id}>{savedTime(snapshot.captured_at)} · {snapshot.source} · #{snapshot.id}</option>)}
        </select></label>)}
        <button type="button" disabled={!current || busy} onClick={download}>{busy ? 'Working…' : 'Download Change Report'}</button>
      </div>
      {current && <>
        <p className="drawdown-result">Net worth: {money.format(current.before.totals.netWorth)} → {money.format(current.after.totals.netWorth)} · Change: {signed(current.totals.netWorth)}</p>
        {table('Changes by investment account', current.accounts)}
        {table('Changes by category (net values, including property equity)', current.categories)}
        <p className="row-sub">Changes include trades, transfers, debt payments and valuation edits; they are not investment returns. Added and removed accounts are compared with zero on the missing side.</p>
      </>}
    </>}
  </section>;
}
