# Workdown — DriverBook Diagnostics Agent

Daily status log. One bullet per task, newest first.

---

- **2026-05-14** — Added tenant name display: company names pulled from source DB companies collection, stored locally, shown in tenant picker chips, stat cards, and vehicle detail header instead of raw ObjectIds
- **2026-05-14** — Full pipeline scan completed: 4 new tenants discovered (24 total), 13 new documents staged, 14 fault codes KB-enriched via LLM (156 KB entries total, 578 diagnostics stored)
- **2026-05-14** — Removed all hardcoded sensitive values from source code: source DB name, collection name, and CORS origins now read from .env; added VITE_API_URL to frontend .env; updated .env.example to match real values
- **2026-05-14** — Fixed telemetry threshold labels: replaced generic "above safe threshold" with signal-specific labels (overheating, critically low, refill required, low fuel); added two-tier fuel alert (amber < 10%, red < 5%)
- **2026-05-14** — Fixed coolant temperature display bug: source data mixed Kelvin/Celsius units; added `_to_celsius()` conversion at ingestion (`datascanpipeline.py`) and at render time (`App.jsx`) so all values show correctly in °C
- **2026-05-14** — Added live telemetry panel to vehicle detail view: API now returns `telemetry` from `fault_vehicles.raw_input`; dashboard shows all 6 signals (coolant, oil, DEF, speed, fuel, RPM) with threshold alerts and color coding
- **2026-05-14** — POC: wired telemetry escalation back into Flow 1 `diagnose_node`; KB severity is now adjusted live using vehicle signals (coolant > 105°C, oil < 20 PSI, DEF < 5%); escalated faults show badge and callout in dashboard fault cards
- **2026-05-14** — Audit backlog (B-1 to B-12) and future features (F-1 to F-7) added to `PLAN.md` with concrete implementation paths aligned to two-flow architecture
- **2026-05-14** — Performance test added (`tests/perf_test.py`): tenant endpoint 5–11ms avg, other endpoints 2–5ms avg across all key routes
- **2026-05-14** — Fixed N+1 query in tenant endpoint: replaced per-vehicle diagnostics loop with single batched `$in` query + in-memory grouping; added indexes on `source_id` and `tenantId`
- **2026-05-14** — Added CORS middleware to `api.py` fixing dashboard "Failed to fetch" error (browser blocked localhost:5173 → localhost:8000)
- **2026-05-14** — Added `GET /tenants` endpoint and tenant picker UI (clickable chips) so fleet managers can select tenants without typing IDs
- **2026-05-14** — Full pipeline run across all 20 tenants: 154 documents analyzed, 44 unknown codes enriched via LLM and saved to KB permanently
- **2026-05-14** — `README.md`, `PLAN.md`, `PRD.md`, and `PRD.docx` updated to reflect current two-flow KB-first architecture
