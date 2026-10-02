import { useEffect, useMemo, useState } from "react";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "/api/v1";
const PLAID_LINK_SCRIPT_URL = "https://cdn.plaid.com/link/v2/stable/link-initialize.js";

async function apiRequest(path, { token, method = "GET", body, headers = {} } = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers: {
      ...(token ? { Authorization: ["Bearer", token].join(" ") } : {}),
      ...headers,
    },
    body,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || "Request failed");
  }
  return payload;
}

function loadPlaidLink() {
  if (window.Plaid) {
    return Promise.resolve(window.Plaid);
  }
  return new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[src="${PLAID_LINK_SCRIPT_URL}"]`);
    if (existing) {
      existing.addEventListener("load", () => resolve(window.Plaid), { once: true });
      existing.addEventListener("error", () => reject(new Error("Could not load Plaid Link.")), { once: true });
      return;
    }
    const script = document.createElement("script");
    script.src = PLAID_LINK_SCRIPT_URL;
    script.async = true;
    script.onload = () => resolve(window.Plaid);
    script.onerror = () => reject(new Error("Could not load Plaid Link."));
    document.head.appendChild(script);
  });
}

function storageKeyForPerson(householdId) {
  return `ledgerpilot-person-id:${householdId}`;
}

function formatDateTime(value) {
  if (!value) {
    return "—";
  }
  return new Date(value).toLocaleString();
}


const SECTION_ACCENTS = {
  holdings: "blue",
  transactions: "violet",
  taxlots: "amber",
  accounts: "teal",
  imports: "slate",
  syncs: "rose",
  recs: "green",
};
const DONUT_COLORS = ["#2563eb", "#10b981", "#f59e0b", "#8b5cf6", "#ef4444", "#14b8a6", "#ec4899", "#64748b"];

function decodeEntities(value) {
  if (typeof value !== "string") {
    return value;
  }
  return value.replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&#39;|&apos;/g, "'").replace(/&quot;/g, '"');
}

function toNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

const currencyFormat = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
const quantityFormat = new Intl.NumberFormat("en-US", { maximumFractionDigits: 6 });

function money(value) {
  return currencyFormat.format(toNumber(value));
}

function signedMoney(value) {
  const number = toNumber(value);
  return `${number > 0 ? "+" : ""}${currencyFormat.format(number)}`;
}

function pct(value) {
  return `${toNumber(value).toFixed(1)}%`;
}

function qty(value) {
  return quantityFormat.format(toNumber(value));
}

function shortId(value) {
  if (!value) {
    return "—";
  }
  return value.length > 10 ? `…${value.slice(-6)}` : value;
}

function initials(name) {
  return (name || "?")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("");
}

function Icon({ name }) {
  const paths = {
    gear: "M12 15a3 3 0 100-6 3 3 0 000 6zm7.4-3a7.4 7.4 0 00-.1-1.2l2-1.5-2-3.4-2.3.9a7 7 0 00-2-1.2L14.5 3h-4l-.4 2.6a7 7 0 00-2 1.2l-2.3-.9-2 3.4 2 1.5a7.4 7.4 0 000 2.4l-2 1.5 2 3.4 2.3-.9a7 7 0 002 1.2l.4 2.6h4l.4-2.6a7 7 0 002-1.2l2.3.9 2-3.4-2-1.5c.1-.4.1-.8.1-1.2z",
    chevron: "M6 9l6 6 6-6",
    upload: "M12 16V4m0 0l-4 4m4-4l4 4M4 20h16",
    check: "M5 13l4 4L19 7",
    plus: "M12 5v14M5 12h14",
  };
  return (
    <svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={paths[name]} />
    </svg>
  );
}

function StatTile({ label, value, tone, hint }) {
  return (
    <div className="stat">
      <span className="statLabel">{label}</span>
      <strong className={`statValue ${tone || ""}`}>{value}</strong>
      {hint ? <span className="statHint">{hint}</span> : null}
    </div>
  );
}

function Donut({ allocation }) {
  const slices = (allocation || []).filter((item) => toNumber(item.weight_pct) > 0).sort((a, b) => toNumber(b.weight_pct) - toNumber(a.weight_pct));
  if (!slices.length) {
    return <div className="donut empty" aria-label="No allocation" />;
  }
  const top = slices.slice(0, 5);
  const restWeight = slices.slice(5).reduce((sum, item) => sum + toNumber(item.weight_pct), 0);
  const parts = top.map((item) => ({ label: decodeEntities(item.symbol), weight: toNumber(item.weight_pct) }));
  if (restWeight > 0) {
    parts.push({ label: "Other", weight: restWeight });
  }
  let cursor = 0;
  const stops = parts.map((part, index) => {
    const start = cursor;
    cursor += part.weight;
    return `${DONUT_COLORS[index % DONUT_COLORS.length]} ${start}% ${cursor}%`;
  });
  return (
    <div className="donutWrap">
      <div className="donut" style={{ background: `conic-gradient(${stops.join(", ")})` }} role="img" aria-label="Allocation chart" />
      <ul className="legend">
        {parts.map((part, index) => (
          <li key={part.label}>
            <span className="dot" style={{ background: DONUT_COLORS[index % DONUT_COLORS.length] }} />
            <span className="legendLabel" title={part.label}>{part.label}</span>
            <span className="legendValue">{pct(part.weight)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function SummaryCard({ title, subtitle, summary, allocation, accent, meta }) {
  const gain = toNumber(summary?.total_unrealized_gain);
  const cost = toNumber(summary?.total_cost_basis);
  const gainPct = cost ? (gain / cost) * 100 : 0;
  return (
    <section className={`card summaryCard ${accent}`}>
      <header className="cardHead">
        <div>
          <p className="kicker">{subtitle}</p>
          <h2>{title}</h2>
        </div>
        {meta ? <span className="badge">{meta}</span> : null}
      </header>
      <div className="bigNumber">{money(summary?.total_market_value)}</div>
      <div className={`delta ${gain >= 0 ? "up" : "down"}`}>
        {gain >= 0 ? "▲" : "▼"} {signedMoney(gain)} <span>({gainPct.toFixed(2)}%) unrealized</span>
      </div>
      <div className="statRow">
        <StatTile label="Cost basis" value={money(summary?.total_cost_basis)} />
        <StatTile label="Positions" value={summary?.positions ?? 0} />
        <StatTile label="Cash-like" value={pct(summary?.cash_like_weight_pct)} />
      </div>
      <Donut allocation={allocation} />
    </section>
  );
}

function Table({ columns, rows, emptyLabel }) {
  if (!rows.length) {
    return <p className="empty">{emptyLabel}</p>;
  }
  return (
    <div className="tableWrap">
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} className={column.numeric ? "num" : ""}>{column.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row.id || row.lot_id || row.symbol || "row"}-${index}`}>
              {columns.map((column) => {
                const raw = column.render ? column.render(row) : row[column.key];
                return (
                  <td key={column.key} className={`${column.numeric ? "num" : ""} ${column.className ? column.className(row) : ""}`}>
                    {raw ?? "—"}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SectionCard({ title, accent, scope, onScope, count, children, noScope }) {
  return (
    <section className={`card section ${accent}`}>
      <header className="cardHead">
        <h2>
          <span className="accentBar" />
          {title}
          {count !== undefined ? <span className="count">{count}</span> : null}
        </h2>
        {noScope ? null : (
          <div className="segmented" role="tablist">
            {["household", "personal"].map((option) => (
              <button
                key={option}
                type="button"
                role="tab"
                aria-selected={scope === option}
                className={scope === option ? "active" : ""}
                onClick={() => onScope(option)}
              >
                {option === "household" ? "Household" : "Personal"}
              </button>
            ))}
          </div>
        )}
      </header>
      {children}
    </section>
  );
}

const gainClass = (row) => (toNumber(row.unrealized_gain) > 0 ? "pos" : toNumber(row.unrealized_gain) < 0 ? "neg" : "");

export default function HomePage() {
  const [mode, setMode] = useState("login");
  const [token, setToken] = useState("");
  const [profile, setProfile] = useState(null);
  const [households, setHouseholds] = useState([]);
  const [people, setPeople] = useState([]);
  const [selectedHouseholdId, setSelectedHouseholdId] = useState("");
  const [selectedPersonId, setSelectedPersonId] = useState("");
  const [dashboard, setDashboard] = useState(null);
  const [connectors, setConnectors] = useState([]);
  const [statusMessage, setStatusMessage] = useState("Create a profile or sign in to load a household dashboard.");
  const [isLoading, setIsLoading] = useState(false);
  const [authForm, setAuthForm] = useState({
    email: "",
    full_name: "",
    password: "",
    household_name: "",
  });
  const [newHouseholdName, setNewHouseholdName] = useState("");
  const [newPersonName, setNewPersonName] = useState("");
  const [uploadConnector, setUploadConnector] = useState("ibkr");
  const [uploadFile, setUploadFile] = useState(null);
  const [isPlaidConnecting, setIsPlaidConnecting] = useState(false);

  const selectedHousehold = useMemo(
    () => households.find((household) => household.id === selectedHouseholdId) || null,
    [households, selectedHouseholdId],
  );
  const selectedPerson = useMemo(
    () => people.find((person) => person.id === selectedPersonId) || dashboard?.selected_person || null,
    [people, selectedPersonId, dashboard],
  );
  const selectedPersonDashboard = dashboard?.selected_person_dashboard || null;

  useEffect(() => {
    const savedToken = window.localStorage.getItem("ledgerpilot-token");
    const savedHouseholdId = window.localStorage.getItem("ledgerpilot-household-id");
    if (savedToken) {
      setToken(savedToken);
      refreshSession(savedToken, savedHouseholdId);
    }
    apiRequest("/connectors")
      .then((data) => {
        setConnectors(data.connectors || []);
        if (data.connectors?.length) {
          setUploadConnector(data.connectors[0].name);
        }
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!selectedHouseholdId) {
      setSelectedPersonId("");
      setPeople([]);
      return;
    }
    const savedPersonId = window.localStorage.getItem(storageKeyForPerson(selectedHouseholdId));
    const fallbackPersonId = savedPersonId || selectedHousehold?.linked_person_id || "";
    setSelectedPersonId(fallbackPersonId);
  }, [selectedHouseholdId, selectedHousehold?.linked_person_id]);

  useEffect(() => {
    if (!token || !selectedHouseholdId) {
      return;
    }
    window.localStorage.setItem("ledgerpilot-household-id", selectedHouseholdId);
    loadDashboard(token, selectedHouseholdId, selectedPersonId || selectedHousehold?.linked_person_id || "");
  }, [token, selectedHouseholdId, selectedPersonId, selectedHousehold?.linked_person_id]);

  async function refreshSession(currentToken, preferredHouseholdId) {
    try {
      const data = await apiRequest("/me", { token: currentToken });
      setProfile(data.profile);
      setHouseholds(data.households || []);
      const fallbackHouseholdId = preferredHouseholdId || data.households?.[0]?.id || "";
      setSelectedHouseholdId(fallbackHouseholdId);
      if (!fallbackHouseholdId) {
        setDashboard(null);
        setPeople([]);
      }
    } catch (error) {
      clearSession();
      setStatusMessage(error.message);
    }
  }

  async function loadDashboard(currentToken, householdId, personId) {
    try {
      setIsLoading(true);
      const query = personId ? `?person_id=${encodeURIComponent(personId)}` : "";
      const data = await apiRequest(`/households/${householdId}/dashboard${query}`, { token: currentToken });
      setDashboard(data);
      setPeople(data.people || []);
      if (data.selected_person?.id) {
        window.localStorage.setItem(storageKeyForPerson(householdId), data.selected_person.id);
        if (data.selected_person.id !== selectedPersonId) {
          setSelectedPersonId(data.selected_person.id);
        }
      }
      setStatusMessage(
        data.selected_person?.full_name
          ? `Loaded ${selectedHousehold?.name || "household"} with ${data.selected_person.full_name} selected.`
          : `Loaded ${selectedHousehold?.name || "household"} dashboard.`,
      );
    } catch (error) {
      setDashboard(null);
      setPeople([]);
      setStatusMessage(error.message);
    } finally {
      setIsLoading(false);
    }
  }

  async function handleAuth(event) {
    event.preventDefault();
    setIsLoading(true);
    setStatusMessage(mode === "login" ? "Signing in…" : "Creating profile…");
    try {
      const data = await apiRequest(mode === "login" ? "/auth/login" : "/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(authForm),
      });
      window.localStorage.setItem("ledgerpilot-token", data.access_token);
      setToken(data.access_token);
      setProfile(data.profile);
      setHouseholds(data.households || []);
      const initialHouseholdId = data.households?.[0]?.id || "";
      setSelectedHouseholdId(initialHouseholdId);
      setSelectedPersonId(data.households?.[0]?.linked_person_id || "");
      setStatusMessage(mode === "login" ? "Signed in." : "Profile created.");
      setAuthForm({ email: authForm.email, full_name: "", password: "", household_name: "" });
    } catch (error) {
      setStatusMessage(error.message);
    } finally {
      setIsLoading(false);
    }
  }

  async function handleCreateHousehold(event) {
    event.preventDefault();
    if (!newHouseholdName.trim()) {
      setStatusMessage("Household name is required.");
      return;
    }
    try {
      setIsLoading(true);
      const data = await apiRequest("/households", {
        token,
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newHouseholdName }),
      });
      const nextHouseholds = [...households, data.household];
      setHouseholds(nextHouseholds);
      setSelectedHouseholdId(data.household.id);
      setSelectedPersonId(data.household.linked_person_id || "");
      setNewHouseholdName("");
      setStatusMessage(`Created ${data.household.name}.`);
    } catch (error) {
      setStatusMessage(error.message);
    } finally {
      setIsLoading(false);
    }
  }

  async function handleCreatePerson(event) {
    event.preventDefault();
    if (!newPersonName.trim() || !selectedHouseholdId) {
      setStatusMessage("Choose a household and enter a person name.");
      return;
    }
    try {
      setIsLoading(true);
      const data = await apiRequest(`/households/${selectedHouseholdId}/people`, {
        token,
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ full_name: newPersonName }),
      });
      setNewPersonName("");
      setSelectedPersonId(data.person.id);
      await loadDashboard(token, selectedHouseholdId, data.person.id);
      setStatusMessage(`Created ${data.person.full_name}.`);
    } catch (error) {
      setStatusMessage(error.message);
    } finally {
      setIsLoading(false);
    }
  }

  async function handleUpload(event) {
    event.preventDefault();
    if (!uploadFile || !selectedHouseholdId || !selectedPersonId) {
      setStatusMessage("Choose a household, person, and CSV file first.");
      return;
    }
    try {
      setIsLoading(true);
      const body = await uploadFile.text();
      const result = await apiRequest(
        `/households/${selectedHouseholdId}/people/${selectedPersonId}/imports/${uploadConnector}`,
        {
          token,
          method: "POST",
          headers: { "Content-Type": "text/csv" },
          body,
        },
      );
      await loadDashboard(token, selectedHouseholdId, selectedPersonId);
      setStatusMessage(
        result.idempotent
          ? `Import already existed for ${result.person_name} (${result.batch_id}).`
          : `Imported ${result.row_count} normalized row(s) for ${result.person_name} into batch ${result.batch_id}.`,
      );
    } catch (error) {
      setStatusMessage(error.message);
    } finally {
      setIsLoading(false);
    }
  }

  async function handlePlaidConnect() {
    if (!selectedHouseholdId || !selectedPersonId) {
      setStatusMessage("Choose a household and person before connecting an investment account.");
      return;
    }
    try {
      setIsPlaidConnecting(true);
      setStatusMessage("Preparing secure Plaid connection…");
      const { link_token: linkToken } = await apiRequest(
        `/households/${selectedHouseholdId}/people/${selectedPersonId}/plaid/link-token`,
        { token, method: "POST" },
      );
      const Plaid = await loadPlaidLink();
      if (!Plaid) {
        throw new Error("Plaid Link did not initialize.");
      }
      const handler = Plaid.create({
        token: linkToken,
        onSuccess: async (publicToken) => {
          try {
            setStatusMessage("Importing your current investment positions…");
            const result = await apiRequest(
              `/households/${selectedHouseholdId}/people/${selectedPersonId}/plaid/exchange`,
              {
                token,
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ public_token: publicToken }),
              },
            );
            await loadDashboard(token, selectedHouseholdId, selectedPersonId);
            setStatusMessage(`Imported ${result.row_count} current investment position(s) for ${result.person.full_name}.`);
          } catch (error) {
            setStatusMessage(error.message);
          } finally {
            setIsPlaidConnecting(false);
          }
        },
        onExit: (_, error) => {
          if (error) {
            setStatusMessage("Plaid connection was not completed. You can try again.");
          }
          setIsPlaidConnecting(false);
        },
      });
      handler.open();
    } catch (error) {
      setStatusMessage(error.message);
      setIsPlaidConnecting(false);
    }
  }

  function clearSession() {
    window.localStorage.removeItem("ledgerpilot-token");
    window.localStorage.removeItem("ledgerpilot-household-id");
    setToken("");
    setProfile(null);
    setHouseholds([]);
    setPeople([]);
    setSelectedHouseholdId("");
    setSelectedPersonId("");
    setDashboard(null);
  }

  const [menuOpen, setMenuOpen] = useState(false);
  const [scopes, setScopes] = useState({});
  const [showCsv, setShowCsv] = useState(false);
  const [householdFormOpen, setHouseholdFormOpen] = useState(false);
  const [personFormOpen, setPersonFormOpen] = useState(false);

  useEffect(() => {
    if (!menuOpen) {
      return undefined;
    }
    const onClick = (event) => {
      if (!event.target.closest?.(".menuWrap")) {
        setMenuOpen(false);
      }
    };
    const onKey = (event) => {
      if (event.key === "Escape") {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  const scopeOf = (key) => scopes[key] || "household";
  const setScope = (key) => (value) => setScopes((current) => ({ ...current, [key]: value }));
  const viewFor = (key) => (scopeOf(key) === "personal" ? selectedPersonDashboard : dashboard);
  const personName = selectedPerson?.full_name || "Select a person";

  const holdingRows = (key) => {
    const view = viewFor(key);
    const values = {};
    (view?.tax_lots || []).forEach((lot) => {
      const id = `${lot.account_id}|${lot.symbol}`;
      values[id] = (values[id] || 0) + toNumber(lot.market_value);
    });
    return (view?.holdings || [])
      .map((item) => ({
        ...item,
        market_value: values[`${item.account_id}|${item.symbol}`] || 0,
      }))
      .sort((a, b) => b.market_value - a.market_value);
  };

  const latestImport = (dashboard?.imports || []).map((item) => item.imported_at).sort().pop();
  const personLatestImport = (selectedPersonDashboard?.imports || []).map((item) => item.imported_at).sort().pop();
  const tone = /fail|error|required|not |could/i.test(statusMessage) ? "error" : "ok";

  function chooseHousehold(id) {
    setSelectedHouseholdId(id);
    setSelectedPersonId("");
  }

  if (!profile) {
    return (
      <main className="authPage">
        <section className="authHero">
          <div className="logo"><span className="logoMark">◆</span> LedgerPilot</div>
          <h1>Your household's investments, in one calm view.</h1>
          <p>Read-only and advisory. Connect accounts with Plaid or upload a CSV, then see holdings, tax lots and allocation for everyone in your household.</p>
          <ul className="featureList">
            <li>Household and per-person portfolios</li>
            <li>Tax lots with unrealized gains</li>
            <li>No trading, no money movement</li>
          </ul>
        </section>
        <section className="card authCard">
          <div className="segmented wide">
            <button type="button" className={mode === "login" ? "active" : ""} onClick={() => setMode("login")}>Sign in</button>
            <button type="button" className={mode === "register" ? "active" : ""} onClick={() => setMode("register")}>Create account</button>
          </div>
          <form className="stack" onSubmit={handleAuth}>
            <label>
              Email
              <input type="email" value={authForm.email} onChange={(event) => setAuthForm({ ...authForm, email: event.target.value })} required />
            </label>
            {mode === "register" ? (
              <>
                <label>
                  Full name
                  <input value={authForm.full_name} onChange={(event) => setAuthForm({ ...authForm, full_name: event.target.value })} required />
                </label>
                <label>
                  Household name
                  <input value={authForm.household_name} onChange={(event) => setAuthForm({ ...authForm, household_name: event.target.value })} required />
                </label>
              </>
            ) : null}
            <label>
              Password
              <input type="password" value={authForm.password} onChange={(event) => setAuthForm({ ...authForm, password: event.target.value })} minLength={8} required />
            </label>
            <button type="submit" className="primary" disabled={isLoading}>{mode === "login" ? "Sign in" : "Create profile"}</button>
          </form>
          <p className={`formStatus ${tone}`}>{statusMessage}</p>
        </section>
      </main>
    );
  }

  const holdingColumns = [
    { key: "symbol", label: "Holding", render: (row) => <strong>{decodeEntities(row.symbol)}</strong> },
    { key: "account_id", label: "Account", render: (row) => <span className="muted" title={row.account_id}>{shortId(row.account_id)}</span> },
    { key: "quantity", label: "Quantity", numeric: true, render: (row) => qty(row.quantity) },
    { key: "market_value", label: "Market value", numeric: true, render: (row) => money(row.market_value) },
  ];

  return (
    <div className="shell">
      <header className="topbar">
        <div className="logo"><span className="logoMark">◆</span> LedgerPilot</div>
        <div className="topRight">
          <div className="contextChip" title="Current view">
            <span>{selectedHousehold?.name || "No household"}</span>
            <span className="sep">/</span>
            <strong>{personName}</strong>
          </div>
          <div className="menuWrap">
            <button type="button" className="avatarButton" aria-haspopup="menu" aria-expanded={menuOpen} onClick={() => setMenuOpen((open) => !open)}>
              <span className="avatar">{initials(profile.full_name)}</span>
              <Icon name="gear" />
              <Icon name="chevron" />
            </button>
            {menuOpen ? (
              <div className="menu" role="menu">
                <div className="menuProfile">
                  <span className="avatar large">{initials(profile.full_name)}</span>
                  <div>
                    <strong>{profile.full_name}</strong>
                    <span>{profile.email}</span>
                  </div>
                </div>

                <p className="menuLabel">Households</p>
                {households.map((household) => (
                  <button key={household.id} type="button" role="menuitemradio" aria-checked={household.id === selectedHouseholdId} className={`menuItem ${household.id === selectedHouseholdId ? "selected" : ""}`} onClick={() => chooseHousehold(household.id)}>
                    <span>{household.name}</span>
                    {household.id === selectedHouseholdId ? <Icon name="check" /> : null}
                  </button>
                ))}
                {householdFormOpen ? (
                  <form className="inlineForm" onSubmit={async (event) => { await handleCreateHousehold(event); setHouseholdFormOpen(false); }}>
                    <input autoFocus placeholder="Household name" value={newHouseholdName} onChange={(event) => setNewHouseholdName(event.target.value)} />
                    <button type="submit" className="primary small" disabled={isLoading}>Add</button>
                  </form>
                ) : (
                  <button type="button" className="menuItem add" onClick={() => setHouseholdFormOpen(true)}><Icon name="plus" /> New household</button>
                )}

                <p className="menuLabel">People in {selectedHousehold?.name || "household"}</p>
                {people.map((person) => (
                  <button key={person.id} type="button" role="menuitemradio" aria-checked={person.id === selectedPersonId} className={`menuItem ${person.id === selectedPersonId ? "selected" : ""}`} onClick={() => setSelectedPersonId(person.id)}>
                    <span><span className="avatar tiny">{initials(person.full_name)}</span>{person.full_name}{person.is_linked_profile ? " (you)" : ""}</span>
                    {person.id === selectedPersonId ? <Icon name="check" /> : null}
                  </button>
                ))}
                {personFormOpen ? (
                  <form className="inlineForm" onSubmit={async (event) => { await handleCreatePerson(event); setPersonFormOpen(false); }}>
                    <input autoFocus placeholder="Person name" value={newPersonName} onChange={(event) => setNewPersonName(event.target.value)} />
                    <button type="submit" className="primary small" disabled={isLoading || !selectedHouseholdId}>Add</button>
                  </form>
                ) : (
                  <button type="button" className="menuItem add" disabled={!selectedHouseholdId} onClick={() => setPersonFormOpen(true)}><Icon name="plus" /> Add person</button>
                )}

                <div className="menuDivider" />
                <button type="button" className="menuItem danger" onClick={clearSession}>Sign out</button>
              </div>
            ) : null}
          </div>
        </div>
      </header>

      <main className="page">
        <section className="welcome">
          <div className="welcomeMain">
            <span className="avatar xl">{initials(selectedPerson?.full_name)}</span>
            <div>
              <p className="kicker">{selectedHousehold?.name || "Household"} household</p>
              <h1>{personName}{selectedPerson?.is_linked_profile ? " (you)" : ""}</h1>
              <p className="muted">
                {selectedPersonDashboard?.accounts?.length || 0} account{(selectedPersonDashboard?.accounts?.length || 0) === 1 ? "" : "s"} ·
                last imported {formatDateTime(personLatestImport)}
              </p>
            </div>
          </div>
          <div className="chips">
            {(selectedPersonDashboard?.accounts || []).map((account) => (
              <span key={account.id || account.display_name} className="chip" title={account.display_name}>
                {account.connector} · {shortId(account.display_name)}
              </span>
            ))}
          </div>
        </section>

        {dashboard?.warnings?.length ? (
          <div className="banner" role="status">
            <strong>Heads up:</strong> {dashboard.warnings.join(" ")}
          </div>
        ) : null}

        <section className="topGrid">
          <SummaryCard
            title={selectedHousehold?.name || "Household"}
            subtitle="Household portfolio"
            accent="blue"
            meta={`${dashboard?.people?.length || 0} people`}
            summary={dashboard?.summary}
            allocation={dashboard?.allocation}
          />
          <SummaryCard
            title={personName}
            subtitle="Personal portfolio"
            accent="green"
            meta={`${selectedPersonDashboard?.summary?.accounts || 0} accounts`}
            summary={selectedPersonDashboard?.summary}
            allocation={selectedPersonDashboard?.allocation}
          />

          <section className="card connectCard">
            <header className="cardHead">
              <div>
                <p className="kicker">Add data for {personName}</p>
                <h2>Connect &amp; import</h2>
              </div>
              <span className={`pillStatus ${dashboard?.sync_status === "succeeded" ? "ok" : ""}`}>{dashboard?.sync_status || "idle"}</span>
            </header>
            <p className="muted small">Import current positions once with Plaid. Automatic refresh and tax-lot history are deferred for this MVP.</p>
            <button type="button" className="primary block" onClick={handlePlaidConnect} disabled={isLoading || isPlaidConnecting || !selectedHouseholdId || !selectedPersonId}>
              <Icon name="upload" /> {isPlaidConnecting ? "Connecting…" : "Connect with Plaid"}
            </button>
            <button type="button" className="ghost block" onClick={() => setShowCsv((open) => !open)} aria-expanded={showCsv}>
              {showCsv ? "Hide CSV upload" : "Or upload a CSV export"}
            </button>
            {showCsv ? (
              <form className="stack csvForm" onSubmit={handleUpload}>
                <label>
                  Connector
                  <select value={uploadConnector} onChange={(event) => setUploadConnector(event.target.value)}>
                    {connectors.map((connector) => (
                      <option key={connector.name} value={connector.name}>{connector.name}</option>
                    ))}
                  </select>
                </label>
                <label>
                  CSV export
                  <input type="file" accept=".csv,text/csv" onChange={(event) => setUploadFile(event.target.files?.[0] || null)} />
                </label>
                <details className="csvHelp">
                  <summary>CSV format help</summary>
                  <p>Upload a transaction table containing account, symbol, date, quantity, price or amount, and buy/sell type. Common labels such as <code>Account Number</code>, <code>Security Symbol</code>, <code>Trade Date</code>, <code>Shares</code>, and <code>Net Amount</code> work too.</p>
                  <p>Interactive Brokers Activity Statement CSVs beginning with <code>Statement,Header,Field Name,Field Value</code> are supported directly.</p>
                </details>
                <button type="submit" className="primary block" disabled={isLoading || !selectedHouseholdId || !selectedPersonId}>Upload to {personName}</button>
              </form>
            ) : null}
            <div className={`statusBox ${tone}`} role="status">
              <span className="statusDot" />
              <span>{isLoading ? "Working…" : statusMessage}</span>
            </div>
            <dl className="metaList">
              <div><dt>Source</dt><dd>{dashboard?.source || "—"}</dd></div>
              <div><dt>Freshness</dt><dd>{dashboard?.freshness || "—"}</dd></div>
              <div><dt>As of</dt><dd>{formatDateTime(dashboard?.as_of)}</dd></div>
              <div><dt>Last import</dt><dd>{formatDateTime(latestImport)}</dd></div>
            </dl>
          </section>
        </section>

        <section className="card section slate peopleOverview">
          <header className="cardHead"><h2><span className="accentBar" />People in household</h2></header>
          <div className="peopleGrid">
            {(dashboard?.person_portfolios || []).map((entry) => (
              <button key={entry.person.id} type="button" className={`personTile ${entry.person.id === selectedPersonId ? "active" : ""}`} onClick={() => setSelectedPersonId(entry.person.id)}>
                <span className="avatar">{initials(entry.person.full_name)}</span>
                <span className="personInfo">
                  <strong>{entry.person.full_name}{entry.person.is_linked_profile ? " (you)" : ""}</strong>
                  <span className="muted">{entry.account_count} accounts · {entry.import_count} imports</span>
                </span>
                <span className="personValue">{money(entry.summary.total_market_value)}</span>
              </button>
            ))}
            {!dashboard?.person_portfolios?.length ? <p className="empty">No people yet.</p> : null}
          </div>
        </section>

        <section className="sectionGrid">
          <SectionCard title="Holdings" accent={SECTION_ACCENTS.holdings} scope={scopeOf("holdings")} onScope={setScope("holdings")} count={holdingRows("holdings").length}>
            <Table columns={holdingColumns} rows={holdingRows("holdings")} emptyLabel="No holdings yet." />
          </SectionCard>

          <SectionCard title="Transactions" accent={SECTION_ACCENTS.transactions} scope={scopeOf("transactions")} onScope={setScope("transactions")}>
            <Table
              columns={[
                { key: "transaction_date", label: "Date" },
                { key: "symbol", label: "Symbol", render: (row) => <strong>{decodeEntities(row.symbol)}</strong> },
                { key: "transaction_type", label: "Type", render: (row) => <span className={`tag ${String(row.transaction_type).toLowerCase()}`}>{String(row.transaction_type).replace("_", " ")}</span> },
                { key: "quantity", label: "Qty", numeric: true, render: (row) => qty(row.quantity) },
                { key: "price", label: "Price", numeric: true, render: (row) => money(row.price) },
              ]}
              rows={viewFor("transactions")?.recent_transactions || []}
              emptyLabel="No transactions yet."
            />
          </SectionCard>

          <SectionCard title="Tax lots" accent={SECTION_ACCENTS.taxlots} scope={scopeOf("taxlots")} onScope={setScope("taxlots")} count={(viewFor("taxlots")?.tax_lots || []).length}>
            <Table
              columns={[
                { key: "symbol", label: "Holding", render: (row) => <strong>{decodeEntities(row.symbol)}</strong> },
                { key: "quantity", label: "Qty", numeric: true, render: (row) => qty(row.quantity) },
                { key: "cost_basis", label: "Cost basis", numeric: true, render: (row) => money(row.cost_basis) },
                { key: "unrealized_gain", label: "Gain", numeric: true, render: (row) => signedMoney(row.unrealized_gain), className: gainClass },
                { key: "holding_period", label: "Period", render: (row) => <span className="tag">{String(row.holding_period || "").replace("_", " ")}</span> },
              ]}
              rows={viewFor("taxlots")?.tax_lots || []}
              emptyLabel="No open tax lots yet."
            />
          </SectionCard>

          <SectionCard title="Accounts" accent={SECTION_ACCENTS.accounts} scope={scopeOf("accounts")} onScope={setScope("accounts")}>
            <Table
              columns={[
                { key: "display_name", label: "Account", render: (row) => <span title={row.display_name}>{shortId(row.display_name)}</span> },
                { key: "connector", label: "Connector", render: (row) => <span className="tag">{row.connector}</span> },
                { key: "created_at", label: "Created", render: (row) => formatDateTime(row.created_at) },
              ]}
              rows={viewFor("accounts")?.accounts || []}
              emptyLabel="No accounts yet."
            />
          </SectionCard>

          <SectionCard title="Import history" accent={SECTION_ACCENTS.imports} scope={scopeOf("imports")} onScope={setScope("imports")}>
            <Table
              columns={[
                { key: "person_name", label: "Person" },
                { key: "connector", label: "Connector", render: (row) => <span className="tag">{row.connector}</span> },
                { key: "row_count", label: "New rows", numeric: true },
                { key: "imported_at", label: "Imported", render: (row) => formatDateTime(row.imported_at) },
              ]}
              rows={(viewFor("imports")?.imports || []).map((item) => ({ ...item, person_name: item.person_name || personName }))}
              emptyLabel="Nothing imported yet."
            />
          </SectionCard>

          <SectionCard title="Recent syncs" accent={SECTION_ACCENTS.syncs} scope={scopeOf("syncs")} onScope={setScope("syncs")}>
            <Table
              columns={[
                { key: "person_name", label: "Person" },
                { key: "trigger", label: "Trigger" },
                { key: "status", label: "Status", render: (row) => <span className={`tag ${row.status}`}>{row.status}</span> },
                { key: "summary", label: "Summary" },
              ]}
              rows={(dashboard?.recent_syncs || []).filter((item) => scopeOf("syncs") === "household" || item.person_name === selectedPerson?.full_name)}
              emptyLabel="No sync runs recorded yet."
            />
          </SectionCard>

          <SectionCard title="Rebalance recommendations" accent={SECTION_ACCENTS.recs} scope={scopeOf("recs")} onScope={setScope("recs")}>
            <Table
              columns={[
                { key: "symbol", label: "Symbol" },
                { key: "current_weight_pct", label: "Current %", numeric: true },
                { key: "target_weight_pct", label: "Target %", numeric: true },
                { key: "action", label: "Action", render: (row) => <span className={`tag ${String(row.action).toLowerCase()}`}>{row.action}</span> },
                { key: "estimated_trade_value", label: "Trade value", numeric: true, render: (row) => money(row.estimated_trade_value) },
              ]}
              rows={viewFor("recs")?.recommendations || []}
              emptyLabel="No policy-driven rebalance actions yet. Add a household policy to enable them."
            />
          </SectionCard>
        </section>
      </main>
    </div>
  );
}
