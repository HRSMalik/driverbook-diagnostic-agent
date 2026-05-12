import os

import requests
import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000")

st.set_page_config(page_title="DriverBook Diagnostics", layout="wide")
st.title("DriverBook Diagnostics")
st.caption(f"API: {API_URL}")

with st.sidebar:
    st.header("Settings")
    show_reanalyze = st.toggle(
        "Show reanalyze controls",
        value=False,
        help="Admin / dev only. Forces the LLM graph to re-run for one vehicle. Use after editing seed_kb.json or when cached diagnostics look wrong.",
    )


def _render_diagnostics(diagnostics: list[dict]) -> None:
    if not diagnostics:
        st.info("No diagnostics yet for this vehicle.")
        return
    for diag in diagnostics:
        title = f"{diag.get('code', 'N/A')} — {diag.get('severity', 'N/A')}"
        with st.expander(title):
            st.write(f"**ECU:** {diag.get('ecu', 'N/A')}")
            st.write(f"**Severity:** {diag.get('severity', 'N/A')} | **Urgency:** {diag.get('urgency', 'N/A')}")
            st.write(f"**Confidence:** {diag.get('confidence', 'N/A')} | **From KB:** {diag.get('from_kb', False)}")
            if diag.get("issue"):
                st.write(f"**Issue:** {diag['issue']}")
            if diag.get("explanation"):
                st.write(f"**Explanation:** {diag['explanation']}")
            steps = diag.get("resolution_steps") or []
            if steps:
                st.write("**Resolution steps:**")
                for step in steps:
                    st.write(f"- {step}")
            if diag.get("parts_likely_needed"):
                st.write(f"**Parts:** {', '.join(diag['parts_likely_needed'])}")
            if diag.get("estimated_downtime"):
                st.write(f"**Estimated downtime:** {diag['estimated_downtime']}")


# ── State ────────────────────────────────────────────────────────────────────
st.session_state.setdefault("vehicles", None)
st.session_state.setdefault("tenant_id", "")


# ── Step 1 — Tenant lookup ───────────────────────────────────────────────────
st.subheader("Look up vehicles by tenant")
with st.form("tenant"):
    tenant_id = st.text_input(
        "Tenant ID",
        value=st.session_state["tenant_id"],
        placeholder="e.g. 68a90f46e73919af2fccdd77",
    )
    fetched = st.form_submit_button("Fetch vehicles + diagnostics")

if fetched:
    if not tenant_id.strip():
        st.error("Please enter a tenantId.")
        st.stop()
    with st.spinner("Loading (running graph for any unanalyzed vehicles)..."):
        response = requests.get(
            f"{API_URL}/tenants/{tenant_id.strip()}/vehicles",
            timeout=600,
        )
    if response.status_code != 200:
        st.error(f"{response.status_code}: {response.text}")
        st.stop()
    body = response.json()
    st.session_state["tenant_id"] = tenant_id.strip()
    st.session_state["vehicles"] = body["vehicles"]


# ── Step 2 — Render vehicles + their diagnostics inline ──────────────────────
vehicles = st.session_state.get("vehicles")
if vehicles is not None:
    st.subheader(f"Vehicles for tenant `{st.session_state['tenant_id']}` ({len(vehicles)} found)")
    if not vehicles:
        st.info("No staged vehicles for this tenant. Run the batch scan first: `python -m core.datascanpipeline`.")
    else:
        for v in vehicles:
            header = f"🚚 {v['vehicleId']}  —  {v['fault_count']} fault(s)"
            with st.expander(header, expanded=False):
                st.caption(f"source_id: `{v['source_id']}` · staged: {v.get('staged_at')}")

                if show_reanalyze:
                    if st.button("🔄 Reanalyze", key=f"reanalyze-{v['vehicleId']}"):
                        with st.spinner("Re-running graph..."):
                            r = requests.post(
                                f"{API_URL}/vehicles/{v['vehicleId']}/reanalyze",
                                timeout=600,
                            )
                        if r.status_code != 200:
                            st.error(f"{r.status_code}: {r.text}")
                        else:
                            v["diagnostics"] = r.json()["diagnostics"]
                            st.success("Reanalyzed.")

                _render_diagnostics(v.get("diagnostics", []))
