import { useState } from 'react';
import axios from 'axios';
import {
  Chart as ChartJS, CategoryScale, LinearScale, BarElement,
  ArcElement, Tooltip, Legend,
} from 'chart.js';
import { Bar, Doughnut } from 'react-chartjs-2';
import { fetchTenantVehicles, reanalyzeVehicle, fetchUnknownFaults, fetchKnowledgeBase } from './services/api.js';

ChartJS.register(CategoryScale, LinearScale, BarElement, ArcElement, Tooltip, Legend);

// ── Helpers ───────────────────────────────────────────────────────────────────

const fmt = (iso) => {
  if (!iso) return '-';
  const d = new Date(iso);
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
};

const SEVERITY_COLOR = { Low: '#00DFD8', Medium: '#FFB432', High: '#FF7832', Critical: '#FF4D4D' };
const URGENCY_COLOR  = { Ignore: '#3D5A73', Monitor: '#00DFD8', 'Schedule Maintenance': '#FFB432', 'Immediate Action': '#FF4D4D' };

const severityPill = (s) => {
  const c = SEVERITY_COLOR[s] || '#3D5A73';
  return <span style={{ display:'inline-block', padding:'2px 10px', borderRadius:20, fontSize:10, fontWeight:700, background:`${c}20`, color:c, border:`1px solid ${c}44` }}>{s || '-'}</span>;
};

const urgencyPill = (u) => {
  const c = URGENCY_COLOR[u] || '#3D5A73';
  return <span style={{ display:'inline-block', padding:'2px 10px', borderRadius:20, fontSize:10, fontWeight:700, background:`${c}20`, color:c, border:`1px solid ${c}44` }}>{u || '-'}</span>;
};

const CHART_OPTS = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: {
    legend: { labels: { color: '#7A9BB5', font: { family: 'DM Sans', size: 11 } } },
    tooltip: { backgroundColor: '#0C1525', borderColor: 'rgba(0,223,216,0.3)', borderWidth: 1, titleColor: '#00DFD8', bodyColor: '#E8F4F8' },
  },
  scales: {
    x: { ticks: { color: '#3D5A73', font: { size: 9 }, maxTicksLimit: 8 }, grid: { color: 'rgba(0,223,216,0.05)' } },
    y: { ticks: { color: '#3D5A73', font: { size: 10 } }, grid: { color: 'rgba(0,223,216,0.05)' } },
  },
};

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  const [tenantId, setTenantId]           = useState('');
  const [activeTab, setActiveTab]         = useState('diagnostics');
  const [vehicles, setVehicles]           = useState([]);
  const [selectedVehicle, setSelectedVehicle] = useState(null);
  const [unknownFaults, setUnknownFaults] = useState([]);
  const [kbEntries, setKbEntries]         = useState([]);
  const [loading, setLoading]             = useState(false);
  const [reanalyzing, setReanalyzing]     = useState(false);
  const [error, setError]                 = useState('');
  const [kbSearch, setKbSearch]           = useState('');
  const [ufPage, setUfPage]               = useState(1);
  const [kbPage, setKbPage]               = useState(1);
  const PAGE_SIZE = 10;

  const handleFetch = async () => {
    if (!tenantId.trim()) { setError('Tenant ID is required.'); return; }
    setError(''); setLoading(true); setSelectedVehicle(null);
    try {
      const [fleet, uf, kb] = await Promise.all([
        fetchTenantVehicles(tenantId.trim()),
        fetchUnknownFaults(),
        fetchKnowledgeBase(),
      ]);
      setVehicles(fleet.vehicles || []);
      setUnknownFaults(uf.faults || []);
      setKbEntries(kb.entries || []);
      setUfPage(1); setKbPage(1);
    } catch (err) {
      setError('Failed to fetch data. Check Tenant ID and ensure the API is running.');
    }
    setLoading(false);
  };

  const handleVehicleClick = (v) => {
    setSelectedVehicle(v);
    setActiveTab('diagnostics');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const handleReanalyze = async () => {
    if (!selectedVehicle) return;
    setReanalyzing(true);
    try {
      const res = await reanalyzeVehicle(selectedVehicle.vehicleId);
      setSelectedVehicle(sv => ({ ...sv, diagnostics: res.diagnostics }));
      setVehicles(vs => vs.map(v => v.vehicleId === res.vehicleId ? { ...v, diagnostics: res.diagnostics } : v));
    } catch (err) {
      setError('Reanalysis failed.');
    }
    setReanalyzing(false);
  };

  // ── Severity breakdown chart ──────────────────────────────────────────────

  const diags = selectedVehicle?.diagnostics || [];

  const severityBreakdown = (() => {
    const counts = { Low: 0, Medium: 0, High: 0, Critical: 0 };
    diags.forEach(d => { if (counts[d.severity] !== undefined) counts[d.severity]++; });
    return {
      labels: Object.keys(counts),
      datasets: [{
        label: 'Faults',
        data: Object.values(counts),
        backgroundColor: Object.keys(counts).map(k => `${SEVERITY_COLOR[k]}99`),
        borderColor: Object.keys(counts).map(k => SEVERITY_COLOR[k]),
        borderWidth: 1, borderRadius: 6,
      }],
    };
  })();

  const urgencyBreakdown = (() => {
    const counts = { Ignore: 0, Monitor: 0, 'Schedule Maintenance': 0, 'Immediate Action': 0 };
    diags.forEach(d => { if (counts[d.urgency] !== undefined) counts[d.urgency]++; });
    const labels = Object.keys(counts);
    return {
      labels,
      datasets: [{
        data: Object.values(counts),
        backgroundColor: labels.map(k => `${URGENCY_COLOR[k]}99`),
        borderWidth: 0,
      }],
    };
  })();

  // ── Fleet summary stats ───────────────────────────────────────────────────

  const criticalCount = vehicles.reduce((n, v) =>
    n + (v.diagnostics || []).filter(d => d.severity === 'Critical').length, 0);
  const highCount = vehicles.reduce((n, v) =>
    n + (v.diagnostics || []).filter(d => d.severity === 'High').length, 0);
  const unknownCount = vehicles.reduce((n, v) =>
    n + (v.diagnostics || []).filter(d => d.is_unknown).length, 0);

  // ── KB filter ─────────────────────────────────────────────────────────────

  const filteredKb = kbEntries.filter(e =>
    !kbSearch || e.code?.toLowerCase().includes(kbSearch.toLowerCase()) ||
    e.meaning?.toLowerCase().includes(kbSearch.toLowerCase())
  );
  const kbTotal = Math.ceil(filteredKb.length / PAGE_SIZE);
  const kbPage_ = Math.min(kbPage, kbTotal || 1);
  const kbRows  = filteredKb.slice((kbPage_ - 1) * PAGE_SIZE, kbPage_ * PAGE_SIZE);

  const ufTotal = Math.ceil(unknownFaults.length / PAGE_SIZE);
  const ufRows  = unknownFaults.slice((ufPage - 1) * PAGE_SIZE, ufPage * PAGE_SIZE);

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;400;500;600;700&display=swap');
        * { box-sizing:border-box; margin:0; padding:0; }
        :root {
          --bg-deep:#050A14; --bg-panel:#0C1525; --bg-card:#111D2E; --bg-card-hover:#152236;
          --border:rgba(0,223,216,0.1); --border-strong:rgba(0,223,216,0.28);
          --cyan:#00DFD8; --cyan-dim:rgba(0,223,216,0.12); --blue:#007CF0;
          --red:#FF4D4D; --amber:#FFB432; --orange:#FF7832;
          --text-primary:#E8F4F8; --text-secondary:#7A9BB5; --text-muted:#3D5A73;
          --gradient:linear-gradient(135deg,#007CF0 0%,#00DFD8 100%);
        }
        body { font-family:'DM Sans',sans-serif; background:var(--bg-deep); color:var(--text-primary); }
        ::-webkit-scrollbar { width:4px; height:4px; }
        ::-webkit-scrollbar-track { background:var(--bg-deep); }
        ::-webkit-scrollbar-thumb { background:var(--border-strong); border-radius:4px; }
        .shell { display:flex; min-height:100vh; }
        .sidebar {
          width:260px; flex-shrink:0; background:var(--bg-panel);
          border-right:1px solid var(--border); display:flex; flex-direction:column;
          padding:28px 18px; gap:24px; position:sticky; top:0; height:100vh; overflow-y:auto;
        }
        .logo-icon { width:40px; height:40px; border-radius:10px; background:var(--gradient); display:flex; align-items:center; justify-content:center; font-size:18px; box-shadow:0 0 20px rgba(0,223,216,0.25); flex-shrink:0; }
        .logo-text { font-family:'Space Mono',monospace; font-size:13px; font-weight:700; }
        .logo-sub { font-size:10px; color:var(--text-muted); letter-spacing:1.5px; text-transform:uppercase; margin-top:2px; }
        .divider { height:1px; background:var(--border); flex-shrink:0; }
        .nav-label { font-size:10px; letter-spacing:2px; text-transform:uppercase; color:var(--text-muted); font-weight:600; }
        .nav-item { border-radius:12px; padding:13px 16px; cursor:pointer; transition:opacity 0.15s; border:1px solid var(--border); }
        .nav-item-title { font-size:13px; font-weight:600; }
        .nav-item-sub { font-size:11px; margin-top:3px; }
        .status-bar { margin-top:auto; background:var(--bg-card); border:1px solid var(--border); border-radius:12px; padding:13px 15px; display:flex; align-items:center; gap:10px; flex-shrink:0; }
        .status-dot { width:8px; height:8px; border-radius:50%; background:var(--cyan); box-shadow:0 0 8px var(--cyan); flex-shrink:0; animation:pulse 2s infinite; }
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }
        .main { flex:1; padding:28px; display:flex; flex-direction:column; gap:20px; overflow-x:hidden; min-width:0; }
        .card { background:var(--bg-card); border:1px solid var(--border); border-radius:16px; padding:22px 24px; }
        .card-title { font-size:11px; font-weight:700; letter-spacing:1.5px; text-transform:uppercase; color:var(--text-muted); margin-bottom:16px; display:flex; align-items:center; gap:8px; }
        .card-title::before { content:''; width:3px; height:12px; border-radius:2px; background:var(--gradient); display:block; }
        .page-title { font-family:'Space Mono',monospace; font-size:18px; font-weight:700; background:var(--gradient); -webkit-background-clip:text; -webkit-text-fill-color:transparent; background-clip:text; }
        .badge { background:var(--cyan-dim); border:1px solid var(--border-strong); color:var(--cyan); font-size:10px; font-weight:700; letter-spacing:1.5px; text-transform:uppercase; padding:4px 12px; border-radius:20px; }
        .input { width:100%; background:var(--bg-deep); border:1px solid var(--border); border-radius:10px; padding:12px 15px; font-family:'Space Mono',monospace; font-size:13px; color:var(--text-primary); outline:none; transition:border-color 0.2s, box-shadow 0.2s; }
        .input::placeholder { color:var(--text-muted); font-family:'DM Sans',sans-serif; }
        .input:focus { border-color:var(--cyan); box-shadow:0 0 0 3px rgba(0,223,216,0.08); }
        .input-label { font-size:10px; font-weight:700; letter-spacing:1px; text-transform:uppercase; color:var(--text-muted); margin-bottom:7px; display:block; }
        .btn { background:var(--gradient); border:none; color:#fff; font-family:'DM Sans',sans-serif; font-size:14px; font-weight:600; padding:12px 28px; border-radius:10px; cursor:pointer; transition:transform 0.15s, box-shadow 0.15s; box-shadow:0 4px 18px rgba(0,124,240,0.28); }
        .btn:hover { transform:translateY(-2px); box-shadow:0 8px 26px rgba(0,124,240,0.42); }
        .btn:active { transform:translateY(0); }
        .btn:disabled { opacity:0.5; cursor:not-allowed; transform:none; }
        .btn-sm { padding:8px 18px; font-size:12px; border-radius:8px; }
        .error { color:var(--red); font-size:12px; margin-top:8px; }
        .stat-row { display:grid; grid-template-columns:repeat(4,1fr); gap:14px; }
        .stat-card { background:var(--bg-deep); border:1px solid var(--border); border-radius:14px; padding:18px 16px; display:flex; flex-direction:column; gap:6px; }
        .stat-label { font-size:10px; font-weight:600; letter-spacing:1px; text-transform:uppercase; color:var(--text-muted); }
        .stat-value { font-family:'Space Mono',monospace; font-size:24px; font-weight:700; line-height:1; }
        .stat-sub { font-size:11px; color:var(--text-muted); }
        .two-col { display:grid; grid-template-columns:1fr 1fr; gap:20px; }
        .chart-wrap { height:260px; }
        .tab-bar { display:flex; gap:4px; background:var(--bg-deep); border:1px solid var(--border); border-radius:12px; padding:4px; }
        .tab-btn { flex:1; padding:9px 16px; border:none; border-radius:9px; font-family:'DM Sans',sans-serif; font-size:13px; font-weight:600; cursor:pointer; transition:background 0.15s, color 0.15s; background:transparent; color:var(--text-muted); }
        .tab-btn.active { background:var(--gradient); color:#fff; box-shadow:0 2px 12px rgba(0,124,240,0.25); }
        .tab-btn:not(.active):hover { color:var(--text-secondary); background:rgba(255,255,255,0.03); }
        .tbl { width:100%; border-collapse:collapse; }
        .tbl th { padding:10px 14px; text-align:left; font-size:10px; letter-spacing:1.5px; text-transform:uppercase; color:var(--text-muted); font-weight:600; border-bottom:1px solid var(--border-strong); }
        .tbl td { padding:10px 14px; font-size:12px; color:var(--text-primary); border-bottom:1px solid var(--border); vertical-align:top; }
        .tbl tr:last-child td { border-bottom:none; }
        .tbl tbody tr:hover td { background:var(--bg-card-hover); }
        .vehicle-link { color:var(--cyan); cursor:pointer; text-decoration:underline; text-underline-offset:3px; text-decoration-color:rgba(0,223,216,0.3); }
        .vehicle-link:hover { color:#fff; text-decoration-color:var(--cyan); }
        .mono { font-family:'Space Mono',monospace; }
        .empty { color:var(--text-muted); font-size:13px; text-align:center; padding:40px 0; font-style:italic; }
        .pagination-row { display:flex; align-items:center; gap:12px; justify-content:center; margin-top:16px; }
        .page-btn { background:var(--bg-card); border:1px solid var(--border); color:var(--text-secondary); padding:6px 16px; border-radius:6px; cursor:pointer; font-size:12px; }
        .page-btn:disabled { opacity:0.35; cursor:default; }
        .page-btn:not(:disabled):hover { border-color:var(--cyan); color:var(--cyan); }
        .page-info { font-size:12px; color:var(--text-muted); }
        .fault-card { background:var(--bg-deep); border:1px solid var(--border); border-radius:14px; padding:18px 20px; display:flex; flex-direction:column; gap:10px; }
        .fault-code { font-family:'Space Mono',monospace; font-size:15px; font-weight:700; color:var(--cyan); }
        .fault-ecu { font-size:11px; color:var(--text-muted); margin-top:2px; }
        .fault-issue { font-size:13px; color:var(--text-secondary); line-height:1.5; }
        .fault-explain { font-size:12px; color:var(--text-muted); line-height:1.6; border-top:1px solid var(--border); padding-top:10px; margin-top:2px; }
        .steps-list { margin:0; padding-left:18px; font-size:12px; color:var(--text-muted); line-height:1.8; }
        .fault-meta-row { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
        .fault-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(340px, 1fr)); gap:16px; }
        .confidence-bar-track { height:4px; background:rgba(255,255,255,0.05); border-radius:2px; overflow:hidden; margin-top:4px; width:100px; display:inline-block; }
        .confidence-bar-fill { height:100%; border-radius:2px; background:var(--gradient); }
        .kb-source { display:inline-block; padding:2px 8px; border-radius:20px; font-size:9px; font-weight:700; letter-spacing:0.5px; text-transform:uppercase; }
      `}</style>

      <div className="shell">

        {/* Sidebar */}
        <div className="sidebar">
          <div style={{ display:'flex', alignItems:'center', gap:12 }}>
            <div className="logo-icon">🔧</div>
            <div>
              <div className="logo-text">DriverBook</div>
              <div className="logo-sub">Diagnostics AI</div>
            </div>
          </div>

          <div className="divider" />

          <div style={{ display:'flex', flexDirection:'column', gap:8 }}>
            <div className="nav-label">Modules</div>
            {[
              { key:'diagnostics', icon:'🛠', title:'Fault Diagnostics', sub:'Per-vehicle analysis' },
              { key:'fleet',       icon:'🚛', title:'Fleet Overview',    sub:'All vehicles' },
              { key:'unknown',     icon:'❓', title:'Unknown Faults',    sub:'Review queue' },
              { key:'kb',          icon:'📚', title:'Knowledge Base',    sub:'Code definitions' },
            ].map(t => (
              <div
                key={t.key}
                className="nav-item"
                onClick={() => setActiveTab(t.key)}
                style={{
                  background: activeTab === t.key ? 'var(--gradient)' : 'var(--bg-card)',
                  opacity: activeTab === t.key ? 1 : 0.65,
                }}
              >
                <div className="nav-item-title" style={{ color: activeTab === t.key ? '#fff' : 'var(--text-secondary)' }}>{t.icon} {t.title}</div>
                <div className="nav-item-sub" style={{ color: activeTab === t.key ? 'rgba(255,255,255,0.65)' : 'var(--text-muted)' }}>{t.sub}</div>
              </div>
            ))}
          </div>

          {selectedVehicle && (
            <>
              <div className="divider" />
              <div style={{ display:'flex', flexDirection:'column', gap:8 }}>
                <div className="nav-label">Active Vehicle</div>
                <div style={{ fontSize:11, color:'var(--text-secondary)', fontFamily:"'Space Mono',monospace", wordBreak:'break-all', lineHeight:1.6 }}>
                  {selectedVehicle.vehicleId?.slice(-12)}
                </div>
                <div style={{ fontSize:11, color:'var(--text-muted)' }}>
                  Faults: <span style={{ color:'var(--text-secondary)' }}>{selectedVehicle.diagnostics?.length ?? 0}</span>
                </div>
                <div style={{ fontSize:11, color:'var(--text-muted)' }}>
                  Staged: <span style={{ color:'var(--text-secondary)' }}>{fmt(selectedVehicle.staged_at)}</span>
                </div>
              </div>
            </>
          )}

          {unknownFaults.length > 0 && (
            <div style={{ background:'rgba(255,77,77,0.08)', border:'1px solid rgba(255,77,77,0.2)', borderRadius:12, padding:'12px 14px' }}>
              <div style={{ fontSize:10, fontWeight:700, letterSpacing:'1px', textTransform:'uppercase', color:'#FF4D4D', marginBottom:6 }}>⚠ Unknown Codes</div>
              <div style={{ fontFamily:"'Space Mono',monospace", fontSize:22, fontWeight:700, color:'#FF4D4D', lineHeight:1 }}>{unknownFaults.length}</div>
              <div style={{ fontSize:11, color:'var(--text-muted)', marginTop:4 }}>awaiting review</div>
            </div>
          )}

          <div className="status-bar">
            <div className="status-dot" />
            <div>
              <div style={{ fontSize:12, fontWeight:600, color:'var(--cyan)' }}>API Connected</div>
              <div style={{ fontSize:11, color:'var(--text-muted)' }}>localhost:8000</div>
            </div>
          </div>
        </div>

        {/* Main */}
        <div className="main">

          {/* Header */}
          <div className="card" style={{ padding:'18px 24px' }}>
            <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between' }}>
              <div className="page-title">Diagnostics Dashboard</div>
              {vehicles.length > 0 && <span className="badge">{vehicles.length} vehicles loaded</span>}
            </div>
          </div>

          {/* Query Input */}
          <div className="card">
            <div className="card-title">Query</div>
            <div style={{ maxWidth:480 }}>
              <label className="input-label">Tenant ID</label>
              <input
                className="input"
                placeholder="Enter Tenant ID (MongoDB ObjectId)"
                value={tenantId}
                onChange={e => setTenantId(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleFetch()}
              />
            </div>
            <button className="btn" onClick={handleFetch} disabled={loading} style={{ marginTop:14 }}>
              {loading ? 'Fetching…' : '↗ Fetch Fleet Data'}
            </button>
            {error && <div className="error">{error}</div>}
          </div>

          {/* Tab bar — only after data loads */}
          {vehicles.length > 0 && (
            <div className="tab-bar">
              <button className={`tab-btn ${activeTab === 'diagnostics' ? 'active' : ''}`} onClick={() => setActiveTab('diagnostics')}>🛠 Diagnostics</button>
              <button className={`tab-btn ${activeTab === 'fleet' ? 'active' : ''}`} onClick={() => setActiveTab('fleet')}>🚛 Fleet</button>
              <button className={`tab-btn ${activeTab === 'unknown' ? 'active' : ''}`} onClick={() => setActiveTab('unknown')}>❓ Unknown ({unknownFaults.length})</button>
              <button className={`tab-btn ${activeTab === 'kb' ? 'active' : ''}`} onClick={() => setActiveTab('kb')}>📚 KB ({kbEntries.length})</button>
            </div>
          )}

          {/* ── DIAGNOSTICS TAB ────────────────────────────────────────────── */}
          {activeTab === 'diagnostics' && vehicles.length > 0 && (
            <>
              {/* Fleet summary stats */}
              <div className="stat-row">
                <div className="stat-card">
                  <div className="stat-label">Total Vehicles</div>
                  <div className="stat-value" style={{ color:'var(--cyan)' }}>{vehicles.length}</div>
                  <div className="stat-sub">in tenant</div>
                </div>
                <div className="stat-card">
                  <div className="stat-label">Critical Faults</div>
                  <div className="stat-value" style={{ color:'#FF4D4D' }}>{criticalCount}</div>
                  <div className="stat-sub">across fleet</div>
                </div>
                <div className="stat-card">
                  <div className="stat-label">High Faults</div>
                  <div className="stat-value" style={{ color:'#FF7832' }}>{highCount}</div>
                  <div className="stat-sub">across fleet</div>
                </div>
                <div className="stat-card">
                  <div className="stat-label">Unknown Codes</div>
                  <div className="stat-value" style={{ color:'var(--amber)' }}>{unknownCount}</div>
                  <div className="stat-sub">not in KB</div>
                </div>
              </div>

              {/* Vehicle picker */}
              {!selectedVehicle && (
                <div className="card">
                  <div className="card-title">Select a Vehicle</div>
                  <table className="tbl">
                    <thead>
                      <tr>
                        <th>Vehicle ID</th>
                        <th>Fault Count</th>
                        <th>Critical</th>
                        <th>High</th>
                        <th>Unknown</th>
                        <th>Staged At</th>
                      </tr>
                    </thead>
                    <tbody>
                      {vehicles.map(v => {
                        const crit = (v.diagnostics || []).filter(d => d.severity === 'Critical').length;
                        const high = (v.diagnostics || []).filter(d => d.severity === 'High').length;
                        const unk  = (v.diagnostics || []).filter(d => d.is_unknown).length;
                        return (
                          <tr key={v.vehicleId} onClick={() => handleVehicleClick(v)} style={{ cursor:'pointer' }}>
                            <td><span className="vehicle-link mono">{v.vehicleId?.slice(-14)}</span></td>
                            <td><span className="mono">{v.fault_count ?? (v.diagnostics?.length ?? '-')}</span></td>
                            <td>{crit > 0 ? <span style={{ color:'#FF4D4D', fontWeight:700 }}>▲ {crit}</span> : <span style={{ color:'var(--text-muted)' }}>—</span>}</td>
                            <td>{high > 0 ? <span style={{ color:'#FF7832', fontWeight:700 }}>▲ {high}</span> : <span style={{ color:'var(--text-muted)' }}>—</span>}</td>
                            <td>{unk  > 0 ? <span style={{ color:'var(--amber)', fontWeight:700 }}>{unk}</span> : <span style={{ color:'var(--text-muted)' }}>—</span>}</td>
                            <td style={{ color:'var(--text-muted)', fontSize:11 }}>{fmt(v.staged_at)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}

              {/* Vehicle detail */}
              {selectedVehicle && (
                <>
                  {/* Vehicle header */}
                  <div className="card" style={{ padding:'16px 24px' }}>
                    <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', flexWrap:'wrap', gap:12 }}>
                      <div>
                        <div style={{ fontSize:11, color:'var(--text-muted)', marginBottom:4 }}>VEHICLE</div>
                        <div className="mono" style={{ fontSize:14, color:'var(--text-primary)', wordBreak:'break-all' }}>{selectedVehicle.vehicleId}</div>
                      </div>
                      <div style={{ display:'flex', gap:10 }}>
                        <button className="btn btn-sm" onClick={() => setSelectedVehicle(null)} style={{ background:'var(--bg-deep)', border:'1px solid var(--border)', color:'var(--text-secondary)', boxShadow:'none' }}>
                          ← Back to Fleet
                        </button>
                        <button className="btn btn-sm" onClick={handleReanalyze} disabled={reanalyzing}>
                          {reanalyzing ? 'Reanalyzing…' : '⟳ Reanalyze'}
                        </button>
                      </div>
                    </div>
                  </div>

                  {/* Charts */}
                  {diags.length > 0 && (
                    <div className="two-col">
                      <div className="card">
                        <div className="card-title">Severity Breakdown</div>
                        <div className="chart-wrap">
                          <Bar data={severityBreakdown} options={{ ...CHART_OPTS, plugins: { ...CHART_OPTS.plugins, legend: { display: false } } }} />
                        </div>
                      </div>
                      <div className="card">
                        <div className="card-title">Urgency Distribution</div>
                        <div className="chart-wrap">
                          <Doughnut data={urgencyBreakdown} options={{ responsive:true, maintainAspectRatio:false, plugins: { legend: { position:'right', labels: { color:'#7A9BB5', font:{ size:11 } } }, tooltip: CHART_OPTS.plugins.tooltip } }} />
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Fault cards */}
                  {diags.length === 0
                    ? <div className="card"><div className="empty">No diagnostics available for this vehicle.</div></div>
                    : (
                      <div className="card">
                        <div className="card-title">{diags.length} Fault{diags.length !== 1 ? 's' : ''} Diagnosed</div>
                        <div className="fault-grid">
                          {diags.map((d, i) => (
                            <div key={i} className="fault-card">
                              <div>
                                <div className="fault-code">{d.code || '—'}</div>
                                <div className="fault-ecu">{d.ecu || ''}{d.fmi != null ? `  ·  FMI ${d.fmi}` : ''}</div>
                              </div>
                              <div className="fault-meta-row">
                                {severityPill(d.severity)}
                                {urgencyPill(d.urgency)}
                                {d.is_unknown && <span style={{ display:'inline-block', padding:'2px 10px', borderRadius:20, fontSize:10, fontWeight:700, background:'rgba(255,180,50,0.12)', color:'#FFB432', border:'1px solid rgba(255,180,50,0.25)' }}>UNKNOWN</span>}
                                {d.confidence != null && (
                                  <span style={{ fontSize:10, color:'var(--text-muted)', display:'flex', alignItems:'center', gap:6 }}>
                                    {d.confidence}%
                                    <span className="confidence-bar-track"><span className="confidence-bar-fill" style={{ width:`${d.confidence}%` }} /></span>
                                  </span>
                                )}
                              </div>
                              {d.issue && <div className="fault-issue">{d.issue}</div>}
                              {d.impact && <div style={{ fontSize:12, color:'var(--text-muted)' }}>Impact: {d.impact}</div>}
                              {(d.explanation || d.resolution_steps?.length > 0) && (
                                <div className="fault-explain">
                                  {d.explanation && <div style={{ marginBottom:8 }}>{d.explanation}</div>}
                                  {d.resolution_steps?.length > 0 && (
                                    <>
                                      <div style={{ fontSize:10, fontWeight:700, letterSpacing:'1px', textTransform:'uppercase', color:'var(--text-muted)', marginBottom:6 }}>Resolution Steps</div>
                                      <ol className="steps-list">
                                        {d.resolution_steps.map((s, j) => <li key={j}>{s}</li>)}
                                      </ol>
                                    </>
                                  )}
                                  {d.who_can_fix && <div style={{ marginTop:8, fontSize:11, color:'var(--text-muted)' }}>Who can fix: <span style={{ color:'var(--text-secondary)' }}>{d.who_can_fix}</span></div>}
                                </div>
                              )}
                              {d.error && <div style={{ fontSize:11, color:'#FF4D4D' }}>Error: {d.error}</div>}
                            </div>
                          ))}
                        </div>
                      </div>
                    )
                  }
                </>
              )}
            </>
          )}

          {/* ── FLEET TAB ─────────────────────────────────────────────────── */}
          {activeTab === 'fleet' && vehicles.length > 0 && (
            <div className="card">
              <div className="card-title">All Vehicles — {vehicles.length} total</div>
              <div style={{ overflowX:'auto' }}>
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>Vehicle ID</th>
                      <th>Total Faults</th>
                      <th>Critical</th>
                      <th>High</th>
                      <th>Medium</th>
                      <th>Low</th>
                      <th>Unknown</th>
                      <th>Docs</th>
                      <th>Staged At</th>
                    </tr>
                  </thead>
                  <tbody>
                    {vehicles.map(v => {
                      const ds = v.diagnostics || [];
                      const bySev = (s) => ds.filter(d => d.severity === s).length;
                      const unk = ds.filter(d => d.is_unknown).length;
                      return (
                        <tr key={v.vehicleId} onClick={() => { handleVehicleClick(v); setActiveTab('diagnostics'); }} style={{ cursor:'pointer' }}>
                          <td><span className="vehicle-link mono">{v.vehicleId?.slice(-14)}</span></td>
                          <td className="mono">{ds.length}</td>
                          <td>{bySev('Critical') > 0 ? <span style={{ color:'#FF4D4D', fontWeight:700 }}>▲ {bySev('Critical')}</span> : '—'}</td>
                          <td>{bySev('High') > 0 ? <span style={{ color:'#FF7832', fontWeight:700 }}>▲ {bySev('High')}</span> : '—'}</td>
                          <td>{bySev('Medium') > 0 ? <span style={{ color:'#FFB432' }}>{bySev('Medium')}</span> : '—'}</td>
                          <td>{bySev('Low') > 0 ? <span style={{ color:'#00DFD8' }}>{bySev('Low')}</span> : '—'}</td>
                          <td>{unk > 0 ? <span style={{ color:'var(--amber)', fontWeight:700 }}>{unk}</span> : '—'}</td>
                          <td className="mono" style={{ color:'var(--text-muted)' }}>{v.doc_count ?? 1}</td>
                          <td style={{ color:'var(--text-muted)', fontSize:11 }}>{fmt(v.staged_at)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* ── UNKNOWN FAULTS TAB ─────────────────────────────────────────── */}
          {activeTab === 'unknown' && (
            <div className="card">
              <div className="card-title">Unknown Fault Codes — Review Queue ({unknownFaults.length})</div>
              {unknownFaults.length === 0
                ? <div className="empty">No unknown faults captured yet.</div>
                : (
                  <>
                    <div style={{ overflowX:'auto' }}>
                      <table className="tbl">
                        <thead>
                          <tr>
                            <th>Code</th>
                            <th>ECU</th>
                            <th>FMI</th>
                            <th>Occurrences</th>
                            <th>First Seen</th>
                            <th>Last Seen</th>
                            <th>Status</th>
                            <th>Raw Description</th>
                          </tr>
                        </thead>
                        <tbody>
                          {ufRows.map((f, i) => (
                            <tr key={i}>
                              <td><span className="mono" style={{ color:'var(--cyan)', fontWeight:700 }}>{f.code}</span></td>
                              <td style={{ color:'var(--text-secondary)', fontSize:11 }}>{f.ecu || '—'}</td>
                              <td className="mono" style={{ color:'var(--text-muted)' }}>{f.fmi ?? '—'}</td>
                              <td><span style={{ color:'#FF4D4D', fontWeight:700, fontFamily:"'Space Mono',monospace" }}>▲ {f.occurrence_count}</span></td>
                              <td style={{ color:'var(--text-muted)', fontSize:11 }}>{fmt(f.first_seen)}</td>
                              <td style={{ color:'var(--text-muted)', fontSize:11 }}>{fmt(f.last_seen)}</td>
                              <td><span style={{ display:'inline-block', padding:'2px 8px', borderRadius:20, fontSize:9, fontWeight:700, background:'rgba(255,180,50,0.12)', color:'#FFB432', border:'1px solid rgba(255,180,50,0.25)', textTransform:'uppercase' }}>{f.status}</span></td>
                              <td style={{ color:'var(--text-muted)', maxWidth:200, fontSize:11 }}>{f.raw_description || '—'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    {ufTotal > 1 && (
                      <div className="pagination-row">
                        <button className="page-btn" disabled={ufPage === 1} onClick={() => setUfPage(p => p - 1)}>← Prev</button>
                        <span className="page-info">Page {ufPage} of {ufTotal}</span>
                        <button className="page-btn" disabled={ufPage === ufTotal} onClick={() => setUfPage(p => p + 1)}>Next →</button>
                      </div>
                    )}
                  </>
                )
              }
            </div>
          )}

          {/* ── KNOWLEDGE BASE TAB ─────────────────────────────────────────── */}
          {activeTab === 'kb' && (
            <div className="card">
              <div className="card-title">Knowledge Base — {kbEntries.length} entries</div>
              <div style={{ maxWidth:360, marginBottom:16 }}>
                <input className="input" placeholder="Search by code or meaning…" value={kbSearch} onChange={e => { setKbSearch(e.target.value); setKbPage(1); }} />
              </div>
              {filteredKb.length === 0
                ? <div className="empty">No entries match your search.</div>
                : (
                  <>
                    <div style={{ overflowX:'auto' }}>
                      <table className="tbl">
                        <thead>
                          <tr>
                            <th>Code</th>
                            <th>System</th>
                            <th>Meaning</th>
                            <th>Severity</th>
                            <th>Urgency</th>
                            <th>Occurrences</th>
                            <th>Source</th>
                          </tr>
                        </thead>
                        <tbody>
                          {kbRows.map((e, i) => {
                            const srcColor = e.source === 'seed' ? '#007CF0' : e.source === 'auto_learned' ? '#00DFD8' : '#FFB432';
                            return (
                              <tr key={i}>
                                <td><span className="mono" style={{ color:'var(--cyan)', fontWeight:700 }}>{e.code}</span></td>
                                <td style={{ color:'var(--text-muted)', fontSize:11 }}>{e.system || '—'}</td>
                                <td style={{ color:'var(--text-secondary)', maxWidth:260 }}>{e.meaning || '—'}</td>
                                <td>{severityPill(e.severity)}</td>
                                <td>{urgencyPill(e.urgency)}</td>
                                <td className="mono" style={{ color:'var(--text-muted)' }}>{e.occurrence_count ?? 0}</td>
                                <td><span className="kb-source" style={{ background:`${srcColor}18`, color:srcColor, border:`1px solid ${srcColor}40` }}>{e.source}</span></td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                    {kbTotal > 1 && (
                      <div className="pagination-row">
                        <button className="page-btn" disabled={kbPage_ === 1} onClick={() => setKbPage(p => p - 1)}>← Prev</button>
                        <span className="page-info">Page {kbPage_} of {kbTotal}</span>
                        <button className="page-btn" disabled={kbPage_ === kbTotal} onClick={() => setKbPage(p => p + 1)}>Next →</button>
                      </div>
                    )}
                  </>
                )
              }
            </div>
          )}

          {/* Empty state */}
          {vehicles.length === 0 && !loading && (
            <div className="card" style={{ textAlign:'center', padding:'60px 24px' }}>
              <div style={{ fontSize:40, marginBottom:16 }}>🔧</div>
              <div style={{ fontSize:16, fontWeight:600, color:'var(--text-secondary)', marginBottom:8 }}>No fleet data loaded</div>
              <div style={{ fontSize:13, color:'var(--text-muted)' }}>Enter a Tenant ID above and hit Fetch Fleet Data.</div>
            </div>
          )}

        </div>
      </div>
    </>
  );
}
