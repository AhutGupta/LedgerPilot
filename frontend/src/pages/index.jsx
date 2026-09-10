import { useEffect, useMemo, useState } from "react";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "/api/v1";

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
              <th key={column.key}>{column.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row.id || row.lot_id || row.symbol || "row"}-${index}`}>
              {columns.map((column) => (
                <td key={column.key}>{row[column.key] ?? "—"}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
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

  return (
    <main className="page">
      <section className="hero">
        <div>
          <p className="eyebrow">✨ LedgerPilot MVP</p>
          <h1>Household portfolio dashboard</h1>
          <p>
            Household analytics stay derived and tenant-scoped while uploads, accounts, and
            transaction ownership can now be attributed to specific people inside each household.
          </p>
        </div>
        {profile ? (
          <div className="profileCard">
            <strong>{profile.full_name}</strong>
            <span>{profile.email}</span>
            <button type="button" onClick={clearSession}>
              Sign out
            </button>
          </div>
        ) : null}
      </section>

      {!profile ? (
        <section className="card authCard">
          <div className="tabRow">
            <button type="button" className={mode === "login" ? "active" : ""} onClick={() => setMode("login")}>
              Login
            </button>
            <button type="button" className={mode === "register" ? "active" : ""} onClick={() => setMode("register")}>
              Register
            </button>
          </div>
          <form className="stack" onSubmit={handleAuth}>
            <label>
              Email
              <input
                type="email"
                value={authForm.email}
                onChange={(event) => setAuthForm({ ...authForm, email: event.target.value })}
                required
              />
            </label>
            {mode === "register" ? (
              <>
                <label>
                  Full name
                  <input
                    value={authForm.full_name}
                    onChange={(event) => setAuthForm({ ...authForm, full_name: event.target.value })}
                    required
                  />
                </label>
                <label>
                  Household name
                  <input
                    value={authForm.household_name}
                    onChange={(event) => setAuthForm({ ...authForm, household_name: event.target.value })}
                    required
                  />
                </label>
              </>
            ) : null}
            <label>
              Password
              <input
                type="password"
                value={authForm.password}
                onChange={(event) => setAuthForm({ ...authForm, password: event.target.value })}
                minLength={12}
                required
              />
            </label>
            <button type="submit" disabled={isLoading}>
              {mode === "login" ? "Sign in" : "Create profile"}
            </button>
          </form>
        </section>
      ) : (
        <>
          <section className="grid twoCol threeColOnWide">
            <section className="card stack">
              <div className="row between">
                <h2>Households</h2>
                <span>{households.length} visible</span>
              </div>
              <div className="pillRow">
                {households.map((household) => (
                  <button
                    key={household.id}
                    type="button"
                    className={household.id === selectedHouseholdId ? "pill active" : "pill"}
                    onClick={() => setSelectedHouseholdId(household.id)}
                  >
                    {household.name}
                  </button>
                ))}
              </div>
              <form className="inlineForm" onSubmit={handleCreateHousehold}>
                <input
                  placeholder="Create another household"
                  value={newHouseholdName}
                  onChange={(event) => setNewHouseholdName(event.target.value)}
                />
                <button type="submit" disabled={isLoading}>
                  Add
                </button>
              </form>
            </section>

            <section className="card stack">
              <div className="row between">
                <h2>People</h2>
                <span>{people.length || 0} in household</span>
              </div>
              <div className="pillRow">
                {people.map((person) => (
                  <button
                    key={person.id}
                    type="button"
                    className={person.id === selectedPersonId ? "pill active" : "pill"}
                    onClick={() => setSelectedPersonId(person.id)}
                  >
                    {person.full_name}
                    {person.is_linked_profile ? " (you)" : ""}
                  </button>
                ))}
              </div>
              <form className="inlineForm" onSubmit={handleCreatePerson}>
                <input
                  placeholder="Add a household person"
                  value={newPersonName}
                  onChange={(event) => setNewPersonName(event.target.value)}
                />
                <button type="submit" disabled={isLoading || !selectedHouseholdId}>
                  Add
                </button>
              </form>
            </section>

            <section className="card stack">
              <h2>Upload connector export</h2>
              <form className="stack" onSubmit={handleUpload}>
                <label>
                  Connector
                  <select value={uploadConnector} onChange={(event) => setUploadConnector(event.target.value)}>
                    {connectors.map((connector) => (
                      <option key={connector.name} value={connector.name}>
                        {connector.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  CSV export
                  <input type="file" accept=".csv,text/csv" onChange={(event) => setUploadFile(event.target.files?.[0] || null)} />
                </label>
                <button type="submit" disabled={isLoading || !selectedHouseholdId || !selectedPersonId}>
                  Upload to {selectedPerson?.full_name || "person"}
                </button>
              </form>
            </section>
          </section>

          <section className="card statusCard">
            <div>
              <strong>Status</strong>
              <p>{statusMessage}</p>
            </div>
            {dashboard ? (
              <dl>
                <div>
                  <dt>Source</dt>
                  <dd>{dashboard.source}</dd>
                </div>
                <div>
                  <dt>Freshness</dt>
                  <dd>{dashboard.freshness}</dd>
                </div>
                <div>
                  <dt>Sync</dt>
                  <dd>{dashboard.sync_status}</dd>
                </div>
                <div>
                  <dt>As of</dt>
                  <dd>{formatDateTime(dashboard.as_of)}</dd>
                </div>
                <div>
                  <dt>Selected person</dt>
                  <dd>{dashboard.selected_person?.full_name || "—"}</dd>
                </div>
                <div>
                  <dt>Household people</dt>
                  <dd>{dashboard.people?.length || 0}</dd>
                </div>
              </dl>
            ) : null}
          </section>

          {dashboard ? (
            <section className="grid twoCol">
              <section className="card stack">
                <div className="row between">
                  <h2>Household summary</h2>
                  <span>{dashboard.policy?.name || "No policy"}</span>
                </div>
                <dl>
                  <div>
                    <dt>Market value</dt>
                    <dd>{dashboard.summary?.total_market_value || "0"}</dd>
                  </div>
                  <div>
                    <dt>Cost basis</dt>
                    <dd>{dashboard.summary?.total_cost_basis || "0"}</dd>
                  </div>
                  <div>
                    <dt>Unrealized gain</dt>
                    <dd>{dashboard.summary?.total_unrealized_gain || "0"}</dd>
                  </div>
                  <div>
                    <dt>Cash-like weight</dt>
                    <dd>{dashboard.summary?.cash_like_weight_pct || "0"}%</dd>
                  </div>
                </dl>
              </section>
              <section className="card stack">
                <div className="row between">
                  <h2>Selected person portfolio</h2>
                  <span>{selectedPerson?.full_name || "No person selected"}</span>
                </div>
                <dl>
                  <div>
                    <dt>Market value</dt>
                    <dd>{selectedPersonDashboard?.summary?.total_market_value || "0"}</dd>
                  </div>
                  <div>
                    <dt>Accounts</dt>
                    <dd>{selectedPersonDashboard?.summary?.accounts || "0"}</dd>
                  </div>
                  <div>
                    <dt>Positions</dt>
                    <dd>{selectedPersonDashboard?.summary?.positions || "0"}</dd>
                  </div>
                  <div>
                    <dt>Cash-like weight</dt>
                    <dd>{selectedPersonDashboard?.summary?.cash_like_weight_pct || "0"}%</dd>
                  </div>
                </dl>
              </section>
            </section>
          ) : null}

          <section className="grid twoCol">
            <section className="card stack">
              <h2>People portfolio overview</h2>
              <Table
                columns={[
                  { key: "person_name", label: "Person" },
                  { key: "account_count", label: "Accounts" },
                  { key: "import_count", label: "Imports" },
                  { key: "market_value", label: "Market value" },
                  { key: "latest_imported_at", label: "Latest import" },
                ]}
                rows={(dashboard?.person_portfolios || []).map((entry) => ({
                  id: entry.person.id,
                  person_name: `${entry.person.full_name}${entry.person.is_linked_profile ? " (you)" : ""}`,
                  account_count: entry.account_count,
                  import_count: entry.import_count,
                  market_value: entry.summary.total_market_value,
                  latest_imported_at: formatDateTime(entry.latest_imported_at),
                }))}
                emptyLabel="No people have been created yet."
              />
            </section>
            <section className="card stack">
              <h2>Warnings</h2>
              {dashboard?.warnings?.length ? (
                <ul>
                  {dashboard.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              ) : (
                <p className="empty">No freshness or policy warnings.</p>
              )}
            </section>
          </section>

          <section className="grid">
            <section className="card stack">
              <h2>Selected person accounts</h2>
              <Table
                columns={[
                  { key: "display_name", label: "Account" },
                  { key: "connector", label: "Connector" },
                  { key: "created_at", label: "Created" },
                ]}
                rows={(selectedPersonDashboard?.accounts || []).map((account) => ({
                  ...account,
                  created_at: formatDateTime(account.created_at),
                }))}
                emptyLabel={selectedPersonId ? "No accounts for the selected person yet." : "Select a person."}
              />
            </section>
            <section className="card stack">
              <h2>Selected person import history</h2>
              <Table
                columns={[
                  { key: "connector", label: "Connector" },
                  { key: "row_count", label: "New rows" },
                  { key: "imported_at", label: "Imported at" },
                ]}
                rows={(selectedPersonDashboard?.imports || []).map((item) => ({
                  ...item,
                  imported_at: formatDateTime(item.imported_at),
                }))}
                emptyLabel="No CSV exports have been imported for the selected person."
              />
            </section>
            <section className="card stack">
              <h2>Selected person transactions</h2>
              <Table
                columns={[
                  { key: "transaction_date", label: "Date" },
                  { key: "account_id", label: "Account" },
                  { key: "symbol", label: "Symbol" },
                  { key: "transaction_type", label: "Type" },
                  { key: "quantity", label: "Quantity" },
                  { key: "price", label: "Price" },
                ]}
                rows={(selectedPersonDashboard?.recent_transactions || []).map((item) => ({
                  ...item,
                  transaction_date: item.transaction_date,
                }))}
                emptyLabel="No transactions for the selected person yet."
              />
            </section>
            <section className="card stack">
              <h2>Household holdings</h2>
              <Table
                columns={[
                  { key: "account_id", label: "Account" },
                  { key: "symbol", label: "Symbol" },
                  { key: "quantity", label: "Quantity" },
                ]}
                rows={dashboard?.holdings || []}
                emptyLabel={selectedHouseholdId ? "No holdings yet." : "Select a household."}
              />
            </section>
            <section className="card stack">
              <h2>Tax lots</h2>
              <Table
                columns={[
                  { key: "account_id", label: "Account" },
                  { key: "symbol", label: "Symbol" },
                  { key: "quantity", label: "Quantity" },
                  { key: "cost_basis", label: "Cost basis" },
                  { key: "unrealized_gain", label: "Unrealized gain" },
                  { key: "holding_period", label: "Holding period" },
                ]}
                rows={dashboard?.tax_lots || []}
                emptyLabel={selectedHouseholdId ? "No open tax lots yet." : "Select a household."}
              />
            </section>
            <section className="card stack">
              <h2>Rebalance recommendations</h2>
              <Table
                columns={[
                  { key: "symbol", label: "Symbol" },
                  { key: "current_weight_pct", label: "Current %" },
                  { key: "target_weight_pct", label: "Target %" },
                  { key: "action", label: "Action" },
                  { key: "estimated_trade_value", label: "Trade value" },
                ]}
                rows={dashboard?.recommendations || []}
                emptyLabel="No policy-driven rebalance actions yet."
              />
            </section>
            <section className="card stack">
              <h2>Household import history</h2>
              <Table
                columns={[
                  { key: "person_name", label: "Person" },
                  { key: "connector", label: "Connector" },
                  { key: "row_count", label: "New rows" },
                  { key: "imported_at", label: "Imported at" },
                ]}
                rows={(dashboard?.imports || []).map((item) => ({
                  ...item,
                  imported_at: formatDateTime(item.imported_at),
                }))}
                emptyLabel="No CSV exports have been imported."
              />
            </section>
            <section className="card stack">
              <h2>Recent syncs</h2>
              <Table
                columns={[
                  { key: "person_name", label: "Person" },
                  { key: "trigger", label: "Trigger" },
                  { key: "status", label: "Status" },
                  { key: "summary", label: "Summary" },
                ]}
                rows={dashboard?.recent_syncs || []}
                emptyLabel="No sync runs recorded yet."
              />
            </section>
          </section>
        </>
      )}
    </main>
  );
}
