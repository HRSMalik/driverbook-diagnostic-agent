# Workdown — DriverBook Diagnostics Agent

Daily status log. One bullet per task, newest first.

---

- **2026-05-22** — Removed MongoDB entirely: deleted db/ folder and pymongo; KB is now file-based (seed_kb.json in-memory dict); auto-learned codes written directly into seed_kb.json; removed MONGO_URI/MONGO_DB from settings and .env.example; ObjectId validation replaced with 24-char hex regex
- **2026-05-22** — Added sliding-window rate limiter middleware: 60 req/min per API key by default, configurable via RATE_LIMIT_PER_MINUTE, returns 429 with Retry-After header, /health and /ready exempt
- **2026-05-22** — Added API key middleware: X-API-Key header required on all endpoints except /health and /ready; added API_KEY to config/settings.py and .env.example; rewrote README with auth flow, current project structure, and correct env vars table
- **2026-05-22** — Removed batch scan pipeline: deleted datascanpipeline.py, dtc_parser.py, fault_vehicles.py, diagnostics_output.py, unknown_faults.py; stripped all batch endpoints from api.py; removed langgraph, langchain, langchain_community from requirements.txt; system is now purely on-click KB-first
- **2026-05-22** — Style compliance: created config/settings.py Settings singleton; removed all scattered os.getenv calls from api.py, llm/llm_client.py, and core/datascanpipeline.py
- **2026-05-22** — Migrated LLM from Ollama (Llama 3.1) to OpenAI (gpt-4o-mini); replaced langchain-ollama with langchain-openai; updated llm_client.py, .env, .env.example, and /ready endpoint to check OpenAI instead of Ollama
- **2026-05-22** — Renamed hf_client.py to llm_client.py; updated all imports in orchestration/diagnostic_graph.py and llm/parsers.py
- **2026-05-22** — Removed redundant code: duplicate _extract_fmi() from datascanpipeline.py (now imported from core.dtc_parser), unused scan_dtc_documents() function, EXPLAIN_* and BATCH_* unused prompt templates, legacy purpose/issue/impact fields from diagnostic output, confidence field from all diagnostic responses
- **2026-05-22** — Cleaned up dead files: frontend/streamlit_app/, tests/reports/, write_prd.py, PRD.docx, CODEBASE_EXPLANATION.md, frontend/react_app/ stub
- **2026-05-22** — Cleaned requirements.txt: removed pandas, streamlit, mongomock, python-docx; added langchain-core and langchain-openai
- **2026-05-22** — Removed estimated_downtime field from all diagnostic responses and KB enrichment prompt schema
- **2026-05-22** — Updated README.md to reflect API-only direction, OpenAI, python venv setup, current endpoint contract with real response examples
- **2026-05-22** — Set up deployment repo tekhqs-driver-book/driverbookv2-ai-diagnostic under MalikHarris-tekh GitHub account; cloned to diagnostic_agent_org/, synced production files, pushed initial commit
- **2026-05-22** — Created /driverbookpush skill: pushes main dev repo under HRSMalik then syncs and pushes deployment org repo under MalikHarris-tekh, mirrors /gitpushall pattern
- **2026-05-22** — Full UAT completed post-cleanup: all endpoints passing, OpenAI enrichment flow confirmed working end-to-end, no legacy fields in responses

- **2026-05-15** — Removed React dashboard (frontend/react_app/) as module shifts to pure API integration
- **2026-05-15** — Added GET /vehicles/{vehicle_id}/faults endpoint: returns fault code list with severity per vehicle, cache-first from diagnostics_output, falls back to staged faults if not yet analyzed
- **2026-05-15** — Added GET /vehicles/faults/diagnose endpoint: on-click KB-first diagnostic for a specific fault code; runs Flow 2 LLM enrichment on KB miss, saves permanently, returns full diagnostic
- **2026-05-15** — Fixed KB enrichment schema bug: auto_learn_from_diagnosis was reading wrong keys (purpose/issue) from LLM output instead of meaning/causes, causing new KB entries to drop rich fields
- **2026-05-15** — Created DriverBook AI Modules Overview PDF: two-module architecture doc covering source collections, queries, intervals, infrastructure, deploy repos, and data storage for fault code and fuel monitoring modules

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
- **2026-05-22** — Seeded KB with 162 historical fault codes retrieved from MongoDB before removal; seed_kb.json now contains full auto-learned history, MongoDB no longer required
