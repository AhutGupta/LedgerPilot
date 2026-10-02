import { useCallback, useEffect, useState } from "react";

const TABS = [
  { id: "policy", label: "Policy" },
  { id: "simulate", label: "Sale simulator" },
  { id: "reports", label: "Reports" },
  { id: "memory", label: "Memory" },
  { id: "audit", label: "Audit log" },
];
const MEMORY_TYPES = ["goal", "constraint", "preference", "reconciliation_note"];

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const num = (value) => (Number.isFinite(Number(value)) ? Number(value) : 0);
const money = (value) => usd.format(num(value));
const clean = (value) => (typeof value === "string" ? value.replace(/&amp;/g, "&") : value);
const when = (value) => (value ? new Date(value).toLocaleString() : "—");

function Msg({ state }) {
  if (!state) {
    return null;
  }
  return <p className={`formStatus ${state.error ? "error" : "okText"}`}>{state.text}</p>;
}

function PolicyTab({ householdId, request, allocation, policy, onSaved }) {
  const [form, setForm] = useState({ name: "", threshold: "5", cash: "0", maxPos: "", notes: "" });
  const [targets, setTargets] = useState([]);
  const [msg, setMsg] = useState(null);

  useEffect(() => {
    const saved = policy?.target_allocations || {};
    const symbols = Array.from(new Set([...(allocation || []).filter((a) => a.asset_class !== "cash_like").map((a) => a.symbol), ...Object.keys(saved)]));
    setTargets(symbols.map((symbol) => ({ symbol, value: saved[symbol] ?? saved[String(symbol).toUpperCase()] ?? "" })));
    setForm({
      name: policy?.name || "Household policy",
      threshold: policy?.rebalance_threshold_pct ?? "5",
      cash: policy?.cash_reserve_target_pct ?? "0",
      maxPos: policy?.max_single_position_pct ?? "",
      notes: policy?.notes || "",
    });
  }, [policy, allocation]);

  const total = targets.reduce((sum, item) => sum + num(item.value), 0);
  const current = Object.fromEntries((allocation || []).map((a) => [a.symbol, a.weight_pct]));

  async function save(event) {
    event.preventDefault();
    const target_allocations = {};
    targets.forEach((item) => {
      if (item.value !== "" && item.symbol.trim()) {
        target_allocations[item.symbol.trim()] = item.value;
      }
    });
    try {
      await request(`/households/${householdId}/policies`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: form.name,
          target_allocations,
          rebalance_threshold_pct: form.threshold,
          cash_reserve_target_pct: form.cash,
          max_single_position_pct: form.maxPos === "" ? null : form.maxPos,
          notes: form.notes || null,
        }),
      });
      setMsg({ text: "Policy saved. Recommendations updated." });
      onSaved();
    } catch (error) {
      setMsg({ error: true, text: error.message });
    }
  }

  return (
    <form className="stack" onSubmit={save}>
      <p className="muted small">
        A policy is your household's target mix. Enter the % you want each holding to be; any holding that drifts from its target by more than the threshold
        shows up under Rebalance recommendations. Saving creates a new version.
      </p>
      <div className="formGrid">
        <label>Policy name<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required /></label>
        <label>Rebalance threshold (%)<input type="number" step="0.1" min="0" max="100" value={form.threshold} onChange={(e) => setForm({ ...form, threshold: e.target.value })} /></label>
        <label>Cash reserve target (%)<input type="number" step="0.1" min="0" max="100" value={form.cash} onChange={(e) => setForm({ ...form, cash: e.target.value })} /></label>
        <label>Max single position (%)<input type="number" step="0.1" min="0" max="100" value={form.maxPos} placeholder="optional" onChange={(e) => setForm({ ...form, maxPos: e.target.value })} /></label>
      </div>
      <div className="tableWrap">
        <table>
          <thead><tr><th>Holding</th><th className="num">Current %</th><th className="num">Target %</th></tr></thead>
          <tbody>
            {targets.map((item, index) => (
              <tr key={item.symbol}>
                <td>{clean(item.symbol)}</td>
                <td className="num">{current[item.symbol] ? `${num(current[item.symbol]).toFixed(1)}%` : "—"}</td>
                <td className="num">
                  <input className="cellInput" type="number" step="0.1" min="0" max="100" value={item.value} onChange={(e) => setTargets(targets.map((t, i) => (i === index ? { ...t, value: e.target.value } : t)))} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!targets.length ? <p className="empty">Import holdings first, then set targets.</p> : null}
      </div>
      <p className={`small ${total > 100 ? "neg" : "muted"}`}>Targets total {total.toFixed(1)}% (cash is tracked separately)</p>
      <label>Notes<input value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></label>
      <button type="submit" className="primary" disabled={!householdId || total > 100}>Save policy</button>
      <Msg state={msg} />
    </form>
  );
}

function SimulateTab({ householdId, request, holdings }) {
  const [form, setForm] = useState({ key: "", quantity: "", price: "" });
  const [result, setResult] = useState(null);
  const [msg, setMsg] = useState(null);
  const options = holdings || [];

  async function run(event) {
    event.preventDefault();
    const holding = options.find((item) => `${item.account_id}|${item.symbol}` === form.key);
    if (!holding) {
      setMsg({ error: true, text: "Choose a holding." });
      return;
    }
    try {
      setMsg(null);
      setResult(await request(`/households/${householdId}/simulate-sale`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol: holding.symbol, account_id: holding.account_id, quantity: form.quantity, sale_price: form.price }),
      }));
    } catch (error) {
      setResult(null);
      setMsg({ error: true, text: error.message });
    }
  }

  return (
    <div className="stack">
      <p className="muted small">What-if only: nothing is sold or recorded. See realized gain and which tax lots would be used.</p>
      <form className="formGrid" onSubmit={run}>
        <label>Holding
          <select value={form.key} onChange={(e) => setForm({ ...form, key: e.target.value })}>
            <option value="">Select…</option>
            {options.map((item) => (
              <option key={`${item.account_id}|${item.symbol}`} value={`${item.account_id}|${item.symbol}`}>{clean(item.symbol)} ({item.quantity})</option>
            ))}
          </select>
        </label>
        <label>Quantity<input type="number" step="any" min="0" value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} required /></label>
        <label>Sale price<input type="number" step="any" min="0" value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} required /></label>
        <button type="submit" className="primary">Simulate</button>
      </form>
      <Msg state={msg} />
      {result ? (
        <div className="resultBox">
          <div className="statRow">
            <div className="stat"><span className="statLabel">Proceeds</span><strong>{money(result.proceeds)}</strong></div>
            <div className="stat"><span className="statLabel">Cost basis</span><strong>{money(result.cost_basis)}</strong></div>
            <div className="stat"><span className="statLabel">Realized gain</span><strong className={num(result.realized_gain) >= 0 ? "pos" : "neg"}>{money(result.realized_gain)}</strong></div>
          </div>
          <div className="tableWrap">
            <table>
              <thead><tr><th>Lot</th><th className="num">Qty</th><th className="num">Gain</th><th>Period</th></tr></thead>
              <tbody>
                {(result.lots || []).map((lot, index) => (
                  <tr key={`${lot.lot_id}-${index}`}><td>{clean(lot.lot_id)}</td><td className="num">{lot.quantity}</td><td className={`num ${num(lot.realized_gain) >= 0 ? "pos" : "neg"}`}>{money(lot.realized_gain)}</td><td><span className="tag">{String(lot.holding_period).replace("_", " ")}</span></td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function ReportsTab({ householdId, request }) {
  const [report, setReport] = useState(null);
  const [kind, setKind] = useState("");
  const [msg, setMsg] = useState(null);

  async function load(path, label) {
    try {
      setMsg(null);
      setKind(label);
      setReport(await request(`/households/${householdId}/reports/${path}`));
    } catch (error) {
      setReport(null);
      setMsg({ error: true, text: error.message });
    }
  }

  const realized = kind === "ytd" ? report : report?.realized_gains;
  return (
    <div className="stack">
      <div className="btnRow">
        <button type="button" className="ghost" onClick={() => load("quarterly-review", "quarterly")}>Quarterly review</button>
        <button type="button" className="ghost" onClick={() => load("ytd-realized-gains", "ytd")}>YTD realized gains</button>
      </div>
      <Msg state={msg} />
      {report ? (
        <div className="resultBox">
          <p className="muted small">As of {report.as_of}</p>
          {report.summary ? (
            <div className="statRow">
              <div className="stat"><span className="statLabel">Market value</span><strong>{money(report.summary.total_market_value)}</strong></div>
              <div className="stat"><span className="statLabel">Cost basis</span><strong>{money(report.summary.total_cost_basis)}</strong></div>
              <div className="stat"><span className="statLabel">Unrealized</span><strong>{money(report.summary.total_unrealized_gain)}</strong></div>
            </div>
          ) : null}
          {realized ? (
            <div className="statRow">
              {Object.entries(realized).filter(([, v]) => typeof v !== "object").map(([key, value]) => (
                <div className="stat" key={key}><span className="statLabel">{key.replace(/_/g, " ")}</span><strong>{Number.isFinite(Number(value)) ? money(value) : String(value)}</strong></div>
              ))}
            </div>
          ) : null}
          {report.risks?.length ? <ul className="plainList">{report.risks.map((risk) => <li key={risk}>{clean(risk)}</li>)}</ul> : null}
          {report.recommendations?.length ? (
            <ul className="plainList">{report.recommendations.map((r) => <li key={r.symbol}>{r.action} {clean(r.symbol)} ~{money(r.estimated_trade_value)}</li>)}</ul>
          ) : null}
        </div>
      ) : <p className="empty">Pick a report to generate it from the current ledger.</p>}
    </div>
  );
}

function MemoryTab({ householdId, request }) {
  const [entries, setEntries] = useState([]);
  const [form, setForm] = useState({ entry_type: "goal", content: "", labels: "", importance: "medium" });
  const [msg, setMsg] = useState(null);

  const load = useCallback(async () => {
    if (!householdId) {
      return;
    }
    try {
      setEntries((await request(`/households/${householdId}/memory`)).entries || []);
    } catch (error) {
      setMsg({ error: true, text: error.message });
    }
  }, [householdId, request]);
  useEffect(() => { load(); }, [load]);

  async function add(event) {
    event.preventDefault();
    try {
      await request(`/households/${householdId}/memory`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...form, labels: form.labels.split(",").map((l) => l.trim()).filter(Boolean) }),
      });
      setForm({ ...form, content: "", labels: "" });
      setMsg(null);
      load();
    } catch (error) {
      setMsg({ error: true, text: error.message });
    }
  }

  return (
    <div className="stack">
      <p className="muted small">Goals, constraints and preferences the household wants advice to respect (e.g. "Saving for a house in 2029").</p>
      <form className="stack" onSubmit={add}>
        <div className="formGrid">
          <label>Type<select value={form.entry_type} onChange={(e) => setForm({ ...form, entry_type: e.target.value })}>{MEMORY_TYPES.map((t) => <option key={t} value={t}>{t.replace("_", " ")}</option>)}</select></label>
          <label>Importance<select value={form.importance} onChange={(e) => setForm({ ...form, importance: e.target.value })}>{["low", "medium", "high"].map((t) => <option key={t}>{t}</option>)}</select></label>
          <label>Labels (comma separated)<input value={form.labels} onChange={(e) => setForm({ ...form, labels: e.target.value })} /></label>
        </div>
        <label>Note<input value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })} required /></label>
        <button type="submit" className="primary" disabled={!householdId}>Add memory</button>
      </form>
      <Msg state={msg} />
      <ul className="feed">
        {entries.map((entry) => (
          <li key={entry.id}>
            <div><span className="tag">{entry.entry_type.replace("_", " ")}</span> <span className={`tag imp-${entry.importance}`}>{entry.importance}</span></div>
            <p>{entry.content}</p>
            <span className="muted small">{when(entry.created_at)}{entry.labels?.length ? ` · ${entry.labels.join(", ")}` : ""}</span>
          </li>
        ))}
      </ul>
      {!entries.length ? <p className="empty">No memory entries yet.</p> : null}
    </div>
  );
}

function AuditTab({ householdId, request }) {
  const [events, setEvents] = useState([]);
  const [msg, setMsg] = useState(null);
  useEffect(() => {
    if (!householdId) {
      return;
    }
    request(`/households/${householdId}/audit`).then((data) => setEvents(data.events || [])).catch((error) => setMsg({ error: true, text: error.message }));
  }, [householdId, request]);
  return (
    <div className="stack">
      <Msg state={msg} />
      <ul className="feed">
        {events.map((event) => (
          <li key={event.id}>
            <div><span className="tag">{event.event_type}</span> <span className="muted small">{event.entity_type}</span></div>
            <span className="muted small">{when(event.created_at)}</span>
          </li>
        ))}
      </ul>
      {!events.length ? <p className="empty">No audit events yet.</p> : null}
    </div>
  );
}

export default function PlanningPanel({ householdId, request, policy, allocation, holdings, onPolicySaved }) {
  const [tab, setTab] = useState("policy");
  return (
    <section className="card section violet planning">
      <header className="cardHead">
        <h2>Planning &amp; tools</h2>
        <div className="segmented" role="tablist">
          {TABS.map((item) => (
            <button key={item.id} type="button" role="tab" aria-selected={tab === item.id} className={tab === item.id ? "active" : ""} onClick={() => setTab(item.id)}>{item.label}</button>
          ))}
        </div>
      </header>
      {tab === "policy" ? <PolicyTab householdId={householdId} request={request} allocation={allocation} policy={policy} onSaved={onPolicySaved} /> : null}
      {tab === "simulate" ? <SimulateTab householdId={householdId} request={request} holdings={holdings} /> : null}
      {tab === "reports" ? <ReportsTab householdId={householdId} request={request} /> : null}
      {tab === "memory" ? <MemoryTab householdId={householdId} request={request} /> : null}
      {tab === "audit" ? <AuditTab householdId={householdId} request={request} /> : null}
    </section>
  );
}
