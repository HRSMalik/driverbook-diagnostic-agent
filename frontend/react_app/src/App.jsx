import { useState, useEffect } from 'react';
import axios from 'axios';
import {
  Chart as ChartJS, CategoryScale, LinearScale, BarElement,
  ArcElement, Tooltip, Legend,
} from 'chart.js';
import { Bar, Doughnut } from 'react-chartjs-2';
import { fetchTenantVehicles, reanalyzeVehicle, fetchKnowledgeBase, fetchTenants, triggerFullScan } from './services/api.js';

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
  const [tenantId, setTenantId]               = useState('');
  const [activeTab, setActiveTab]             = useState('diagnostics');
  const [vehicles, setVehicles]               = useState([]);
  const [selectedVehicle, setSelectedVehicle] = useState(null);
  const [kbEntries, setKbEntries]             = useState([]);
  const [loading, setLoading]                 = useState(false);
  const [reanalyzing, setReanalyzing]         = useState(false);
  const [error, setError]                     = useState('');
  const [kbSearch, setKbSearch]               = useState('');
  const [kbPage, setKbPage]                   = useState(1);
  const [tenants, setTenants]                 = useState([]);
  const [activeTenantId, setActiveTenantId]   = useState('');
  const [scanStatus, setScanStatus]           = useState('');
  const [scanning, setScanning]               = useState(false);
  const PAGE_SIZE = 10;

  useEffect(() => {
    fetchTenants().then(d => setTenants(d.tenants || [])).catch(() => {});
  }, []);

  const tenantName = (tid) => (tenants.find(t => t.tenantId === tid) || {}).name || tid;

  const handleFetch = async () => {
    if (!tenantId.trim()) { setError('Tenant ID is required.'); return; }
    setError(''); setLoading(true); setSelectedVehicle(null);
    try {
      const [fleet, kb, tenantsData] = await Promise.all([
        fetchTenantVehicles(tenantId.trim()),
        fetchKnowledgeBase(),
        fetchTenants(),
      ]);
      const filtered = (fleet.vehicles || []).filter(v =>
        (v.diagnostics || []).some(d => !d.is_unknown)
      );
      setVehicles(filtered);
      setKbEntries(kb.entries || []);
      setTenants(tenantsData.tenants || []);
      setActiveTenantId(tenantId.trim());
      setKbPage(1);
    } catch (err) {
      setError('Failed to fetch data. Check Tenant ID and ensure the API is running.');
    }
    setLoading(false);
  };

  const handleScanAll = async () => {
    setScanning(true); setScanStatus('');
    try {
      const res = await triggerFullScan({});
      setScanStatus(res.message || 'Scan started in background.');
    } catch (err) {
      setScanStatus('Failed to start scan. Is the API running?');
    }
    setScanning(false);
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

  // Only show known (non-unknown) diagnostics in the detail view
  const diags = (selectedVehicle?.diagnostics || []).filter(d => !d.is_unknown);

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

  const criticalCount = vehicles.reduce((n, v) =>
    n + (v.diagnostics || []).filter(d => !d.is_unknown && d.severity === 'Critical').length, 0);
  const highCount = vehicles.reduce((n, v) =>
    n + (v.diagnostics || []).filter(d => !d.is_unknown && d.severity === 'High').length, 0);
  const unknownCount = vehicles.reduce((n, v) =>
    n + (v.diagnostics || []).filter(d => d.is_unknown).length, 0);

  const filteredKb = kbEntries.filter(e =>
    !kbSearch || e.code?.toLowerCase().includes(kbSearch.toLowerCase()) ||
    e.meaning?.toLowerCase().includes(kbSearch.toLowerCase())
  );
  const kbTotal = Math.ceil(filteredKb.length / PAGE_SIZE);
  const kbPage_ = Math.min(kbPage, kbTotal || 1);
  const kbRows  = filteredKb.slice((kbPage_ - 1) * PAGE_SIZE, kbPage_ * PAGE_SIZE);

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
                  Faults: <span style={{ color:'var(--text-secondary)' }}>{diags.length}</span>
                </div>
                <div style={{ fontSize:11, color:'var(--text-muted)' }}>
                  Staged: <span style={{ color:'var(--text-secondary)' }}>{fmt(selectedVehicle.staged_at)}</span>
                </div>
              </div>
            </>
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
              {vehicles.length > 0 && <span className="badge">{vehicles.length} vehicles with faults</span>}
            </div>
          </div>

          {/* Query Input */}
          <div className="card">
            <div style={{ display:'flex', gap:20, flexWrap:'wrap', alignItems:'flex-end' }}>
              <div style={{ flex:'1 1 340px' }}>
                <div className="card-title" style={{ marginBottom:12 }}>Query by Tenant</div>
                <label className="input-label">Tenant ID</label>
                <input
                  className="input"
                  placeholder="Enter Tenant ID (MongoDB ObjectId)"
                  value={tenantId}
                  onChange={e => setTenantId(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && handleFetch()}
                />
                <button className="btn" onClick={handleFetch} disabled={loading} style={{ marginTop:12 }}>
                  {loading ? 'Fetching…' : '↗ Fetch Fleet Data'}
                </button>
                {error && <div className="error">{error}</div>}
              </div>

            </div>

            {tenants.length > 0 && (
              <div style={{ marginTop:18, borderTop:'1px solid var(--border)', paddingTop:16 }}>
                <div style={{ fontSize:10, fontWeight:700, letterSpacing:'1px', textTransform:'uppercase', color:'var(--text-muted)', marginBottom:10 }}>
                  Known Tenants ({tenants.length})
                </div>
                <div style={{ display:'flex', flexWrap:'wrap', gap:8 }}>
                  {tenants.map(t => (
                    <span
                      key={t.tenantId}
                      onClick={() => { setTenantId(t.tenantId); }}
                      style={{
                        fontSize:11,
                        padding:'5px 12px', borderRadius:20, cursor:'pointer',
                        background: tenantId === t.tenantId ? 'var(--cyan-dim)' : 'var(--bg-deep)',
                        border: tenantId === t.tenantId ? '1px solid var(--cyan)' : '1px solid var(--border)',
                        color: tenantId === t.tenantId ? 'var(--cyan)' : 'var(--text-muted)',
                        transition:'all 0.15s',
                      }}
                    >
                      {t.name}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Tenant title */}
          {vehicles.length > 0 && activeTenantId && (
            <div style={{ padding:'10px 4px 0' }}>
              <div className="page-title" style={{ fontSize:20 }}>
                {tenantName(activeTenantId)}
              </div>
              <div style={{ fontSize:11, color:'var(--text-muted)', marginTop:3, fontFamily:"'Space Mono',monospace" }}>{activeTenantId}</div>
            </div>
          )}

          {/* ── DIAGNOSTICS TAB ────────────────────────────────────────────── */}
          {activeTab === 'diagnostics' && vehicles.length > 0 && (
            <>
              <div className="stat-row">
                <div className="stat-card">
                  <div className="stat-label">Vehicles with Faults</div>
                  <div className="stat-value" style={{ color:'var(--cyan)' }}>{vehicles.length}</div>
                  <div className="stat-sub">{tenantName(activeTenantId)}</div>
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
                  <div className="stat-sub">pending review</div>
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
                        <th>Medium</th>
                        <th>Unknown</th>
                        <th>Staged At</th>
                      </tr>
                    </thead>
                    <tbody>
                      {vehicles.map(v => {
                        const ds = (v.diagnostics || []).filter(d => !d.is_unknown);
                        const crit = ds.filter(d => d.severity === 'Critical').length;
                        const high = ds.filter(d => d.severity === 'High').length;
                        const med  = ds.filter(d => d.severity === 'Medium').length;
                        const unk  = (v.diagnostics || []).filter(d => d.is_unknown).length;
                        return (
                          <tr key={v.vehicleId} onClick={() => handleVehicleClick(v)} style={{ cursor:'pointer' }}>
                            <td><span className="vehicle-link mono">{v.vehicleId?.slice(-14)}</span></td>
                            <td><span className="mono">{ds.length}</span></td>
                            <td>{crit > 0 ? <span style={{ color:'#FF4D4D', fontWeight:700 }}>▲ {crit}</span> : <span style={{ color:'var(--text-muted)' }}>—</span>}</td>
                            <td>{high > 0 ? <span style={{ color:'#FF7832', fontWeight:700 }}>▲ {high}</span> : <span style={{ color:'var(--text-muted)' }}>—</span>}</td>
                            <td>{med  > 0 ? <span style={{ color:'#FFB432' }}>{med}</span> : <span style={{ color:'var(--text-muted)' }}>—</span>}</td>
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
                  <div className="card" style={{ padding:'16px 24px' }}>
                    <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', flexWrap:'wrap', gap:12 }}>
                      <div>
                        <div style={{ fontSize:11, color:'var(--text-muted)', marginBottom:2 }}>{tenantName(activeTenantId)}</div>
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

                  {/* Telemetry panel */}
                  {selectedVehicle.telemetry && Object.keys(selectedVehicle.telemetry).length > 0 && (() => {
                    const t = selectedVehicle.telemetry;
                    const num = (v) => { const n = parseFloat(v); return isNaN(n) ? null : n; };
                    const toCelsius = (v) => v !== null && v > 150 ? v - 273.15 : v;
                    const coolant = toCelsius(num(t.engineCoolantTemperature));
                    const oil     = num(t.engineOilPressure);
                    const def     = num(t.defLevel);
                    const speed   = num(t.speed);
                    const fuel    = num(t.fuelLevel);
                    const rpm     = num(t.engineSpeed);
                    const signals = [
                      { label: 'Coolant Temp', value: coolant, unit: '°C',   warn: coolant !== null && coolant > 105, critical: false,                          warnLabel: '▲ overheating' },
                      { label: 'Oil Pressure', value: oil,     unit: ' PSI', warn: oil !== null && oil < 20,          critical: oil !== null && oil < 20,        warnLabel: '▼ critically low' },
                      { label: 'DEF Level',    value: def,     unit: '%',    warn: def !== null && def < 5,            critical: false,                          warnLabel: '▼ refill required' },
                      { label: 'Speed',        value: speed,   unit: ' km/h', warn: false,                            critical: false,                          warnLabel: '' },
                      { label: 'Fuel Level',   value: fuel,    unit: '%',    warn: fuel !== null && fuel < 10,         critical: fuel !== null && fuel < 5,      warnLabel: fuel !== null && fuel < 5 ? '▼ critical — refuel now' : '▼ low fuel' },
                      { label: 'Engine RPM',   value: rpm,     unit: ' RPM', warn: false,                             critical: false,                          warnLabel: '' },
                    ].filter(s => s.value !== null);
                    if (!signals.length) return null;
                    const anyAlert = signals.some(s => s.warn);
                    return (
                      <div className="card">
                        <div className="card-title" style={{ marginBottom: 14 }}>
                          Live Telemetry Snapshot
                          {anyAlert && <span style={{ marginLeft:8, fontSize:10, color:'#FF7832', background:'#FF783220', border:'1px solid #FF783240', borderRadius:20, padding:'2px 8px', fontWeight:700 }}>⚠ threshold exceeded</span>}
                        </div>
                        <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(160px, 1fr))', gap:10 }}>
                          {signals.map(s => {
                            const color = s.critical ? '#FF4D4D' : s.warn ? '#FF7832' : '#00DFD8';
                            return (
                              <div key={s.label} style={{ background:'var(--bg-deep)', border:`1px solid ${s.warn ? color + '44' : 'var(--border)'}`, borderRadius:10, padding:'12px 14px' }}>
                                <div style={{ fontSize:10, color:'var(--text-muted)', letterSpacing:'0.8px', textTransform:'uppercase', marginBottom:6 }}>{s.label}</div>
                                <div style={{ fontSize:20, fontWeight:700, fontFamily:'Space Mono,monospace', color, lineHeight:1 }}>
                                  {s.value.toFixed(1)}<span style={{ fontSize:11, fontWeight:400, color:'var(--text-muted)', marginLeft:2 }}>{s.unit}</span>
                                </div>
                                {s.warn && <div style={{ fontSize:10, color, marginTop:6 }}>{s.warnLabel}</div>}
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    );
                  })()}

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

                  {diags.length === 0
                    ? <div className="card"><div className="empty">No diagnosed faults available for this vehicle.</div></div>
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
                                {d.severity_escalated && (
                                  <span style={{ display:'inline-flex', alignItems:'center', gap:4, padding:'2px 8px', borderRadius:20, fontSize:10, fontWeight:700, background:'#FF783220', color:'#FF7832', border:'1px solid #FF783244' }}>
                                    ↑ escalated from {d.base_severity}
                                  </span>
                                )}
                                {urgencyPill(d.urgency)}
                                {d.confidence != null && (
                                  <span style={{ fontSize:10, color:'var(--text-muted)', display:'flex', alignItems:'center', gap:6 }}>
                                    {d.confidence}%
                                    <span className="confidence-bar-track"><span className="confidence-bar-fill" style={{ width:`${d.confidence}%` }} /></span>
                                  </span>
                                )}
                              </div>
                              {d.severity_escalated && (
                                <div style={{ fontSize:11, color:'#FF7832', background:'#FF783210', border:'1px solid #FF783230', borderRadius:8, padding:'6px 10px', display:'flex', alignItems:'center', gap:6 }}>
                                  <span>⚠</span>
                                  <span>Live vehicle conditions exceeded safe thresholds — severity escalated from <strong>{d.base_severity}</strong></span>
                                </div>
                              )}
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
              <div className="card-title">All Vehicles — {vehicles.length} with fault codes</div>
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
                      <th>Staged At</th>
                    </tr>
                  </thead>
                  <tbody>
                    {vehicles.map(v => {
                      const ds = (v.diagnostics || []).filter(d => !d.is_unknown);
                      const bySev = (s) => ds.filter(d => d.severity === s).length;
                      const unk = (v.diagnostics || []).filter(d => d.is_unknown).length;
                      return (
                        <tr key={v.vehicleId} onClick={() => { handleVehicleClick(v); setActiveTab('diagnostics'); }} style={{ cursor:'pointer' }}>
                          <td><span className="vehicle-link mono">{v.vehicleId?.slice(-14)}</span></td>
                          <td className="mono">{ds.length}</td>
                          <td>{bySev('Critical') > 0 ? <span style={{ color:'#FF4D4D', fontWeight:700 }}>▲ {bySev('Critical')}</span> : '—'}</td>
                          <td>{bySev('High') > 0 ? <span style={{ color:'#FF7832', fontWeight:700 }}>▲ {bySev('High')}</span> : '—'}</td>
                          <td>{bySev('Medium') > 0 ? <span style={{ color:'#FFB432' }}>{bySev('Medium')}</span> : '—'}</td>
                          <td>{bySev('Low') > 0 ? <span style={{ color:'#00DFD8' }}>{bySev('Low')}</span> : '—'}</td>
                          <td>{unk > 0 ? <span style={{ color:'var(--amber)', fontWeight:700 }}>{unk}</span> : '—'}</td>
                          <td style={{ color:'var(--text-muted)', fontSize:11 }}>{fmt(v.staged_at)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
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
                          </tr>
                        </thead>
                        <tbody>
                          {kbRows.map((e, i) => (
                            <tr key={i}>
                              <td><span className="mono" style={{ color:'var(--cyan)', fontWeight:700 }}>{e.code}</span></td>
                              <td style={{ color:'var(--text-muted)', fontSize:11 }}>{e.system || '—'}</td>
                              <td style={{ color:'var(--text-secondary)', maxWidth:260 }}>{e.meaning || '—'}</td>
                              <td>{severityPill(e.severity)}</td>
                              <td>{urgencyPill(e.urgency)}</td>
                              <td className="mono" style={{ color:'var(--text-muted)' }}>{e.occurrence_count ?? 0}</td>
                            </tr>
                          ))}
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
