import { useMemo, useState } from "react";

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const num = (value) => (Number.isFinite(Number(value)) ? Number(value) : 0);
const money = (value) => usd.format(num(value));
const clean = (value) => (typeof value === "string" ? value.replace(/&amp;/g, "&") : value);

function Msg({ state }) {
  return state ? <p className={`formStatus ${state.error ? "error" : "okText"}`}>{state.text}</p> : null;
}

function EstimatorTab({ sales }) {
  const years = useMemo(() => {
    const found = new Set((sales || []).map((sale) => String(sale.sale_date).slice(0, 4)));
    found.add(String(new Date().getFullYear()));
    return Array.from(found).sort().reverse();
  }, [sales]);
  const [year, setYear] = useState(String(new Date().getFullYear()));
  const [rates, setRates] = useState({ shortRate: "32", longRate: "15" });

  const totals = useMemo(() => {
    const result = { proceeds: 0, basis: 0, st: 0, lt: 0, count: 0 };
    (sales || []).filter((sale) => String(sale.sale_date).startsWith(year)).forEach((sale) => {
      result.count += 1;
      result.proceeds += num(sale.proceeds);
      result.basis += num(sale.cost_basis);
      if (sale.holding_period === "long_term") {
        result.lt += num(sale.realized_gain);
      } else {
        result.st += num(sale.realized_gain);
      }
    });
    return result;
  }, [sales, year]);

  // Net losses in one bucket offset gains in the other; a net loss yields no tax here.
  let st = totals.st;
  let lt = totals.lt;
  if (st < 0 && lt > 0) { lt = Math.max(0, lt + st); st = 0; }
  else if (lt < 0 && st > 0) { st = Math.max(0, st + lt); lt = 0; }
  const tax = Math.max(0, st) * (num(rates.shortRate) / 100) + Math.max(0, lt) * (num(rates.longRate) / 100);
  const net = totals.st + totals.lt;

  return (
    <div className="stack">
      <p className="muted small">
        Realized gains from sales in the selected tax year, split short-term vs long-term, with a rough tax estimate for planning quarterly payments.
        An estimate only, not tax advice.
      </p>
      <div className="formGrid">
        <label>Tax year<select value={year} onChange={(e) => setYear(e.target.value)}>{years.map((y) => <option key={y}>{y}</option>)}</select></label>
        <label>Short-term rate (%)<input type="number" min="0" max="100" step="0.5" value={rates.shortRate} onChange={(e) => setRates({ ...rates, shortRate: e.target.value })} /></label>
        <label>Long-term rate (%)<input type="number" min="0" max="100" step="0.5" value={rates.longRate} onChange={(e) => setRates({ ...rates, longRate: e.target.value })} /></label>
      </div>
      <div className="resultBox">
        <div className="statRow">
          <div className="stat"><span className="statLabel">Short-term gain</span><strong className={totals.st >= 0 ? "pos" : "neg"}>{money(totals.st)}</strong></div>
          <div className="stat"><span className="statLabel">Long-term gain</span><strong className={totals.lt >= 0 ? "pos" : "neg"}>{money(totals.lt)}</strong></div>
          <div className="stat"><span className="statLabel">Net realized</span><strong className={net >= 0 ? "pos" : "neg"}>{money(net)}</strong></div>
        </div>
        <div className="statRow">
          <div className="stat"><span className="statLabel">Sales</span><strong>{totals.count}</strong></div>
          <div className="stat"><span className="statLabel">Proceeds</span><strong>{money(totals.proceeds)}</strong></div>
          <div className="stat"><span className="statLabel">Estimated tax</span><strong>{money(tax)}</strong></div>
        </div>
      </div>
      {!totals.count ? <p className="empty">No sales recorded in {year}. Import a CSV with SELL transactions to see realized gains.</p> : null}
    </div>
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
      <p className="muted small">What-if only: see the realized gain and which tax lots a sale would use before you sell. Nothing is recorded.</p>
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

export default function TaxPanel({ householdId, request, sales, holdings, scope, onScope }) {
  const [tab, setTab] = useState("estimate");
  return (
    <section className="card section amber planning">
      <header className="cardHead">
        <h2>Tax planning</h2>
        <div className="row">
          <div className="segmented" role="tablist">
            {[["estimate", "Gain estimator"], ["simulate", "Sale simulator"]].map(([id, label]) => (
              <button key={id} type="button" role="tab" aria-selected={tab === id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{label}</button>
            ))}
          </div>
          <div className="segmented" role="tablist">
            {["household", "personal"].map((option) => (
              <button key={option} type="button" role="tab" aria-selected={scope === option} className={scope === option ? "active" : ""} onClick={() => onScope(option)}>
                {option === "household" ? "Household" : "Personal"}
              </button>
            ))}
          </div>
        </div>
      </header>
      {tab === "estimate" ? <EstimatorTab sales={sales} /> : <SimulateTab householdId={householdId} request={request} holdings={holdings} />}
    </section>
  );
}
