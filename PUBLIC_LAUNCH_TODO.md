# Before making LedgerPilot public

LedgerPilot is an MVP for private, self-hosted use. Complete these items before publishing it or
handling anyone else's financial data.

- [ ] Require a stronger password policy again (at least 12 characters) and add reset, email
  verification, rate limiting, and account lockout flows. The MVP deliberately allows 8 characters
  to reduce setup friction.
- [ ] Replace development Docker secrets and PostgreSQL credentials with a managed secret store and
  separate production credentials.
- [ ] Configure an HTTPS reverse proxy, secure cookies/session strategy, CSP, security headers, and
  deployment-specific CORS origins.
- [ ] Complete Plaid production access, institution testing, consent/disclosure copy, disconnect and
  data-deletion workflows, error monitoring, and encrypted-token key rotation.
- [ ] Add scheduled/incremental Plaid synchronization and reconciliation before describing account
  data as current. The MVP imports current investment positions once only.
- [ ] Add historical transactions and tax-lot reconciliation before presenting tax calculations as
  authoritative for Plaid-connected accounts.
- [ ] Choose an LLM provider and implement a chat UI with explicit consent, tenant-scoped tool
  execution, provider data-retention controls, prompt-injection defenses, rate limits, and audit
  review. The current AI layer is a safe tool API, not a chatbot.
- [ ] Perform privacy, threat-model, dependency, backup/restore, accessibility, and load testing
  reviews; publish a privacy policy and support process.
