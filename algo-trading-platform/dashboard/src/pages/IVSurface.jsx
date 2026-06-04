import { useState, useMemo } from 'react';
import {
  Activity,
  Flame,
  Grid3X3,
  TrendingUp,
  TrendingDown,
  BarChart3,
  Eye,
  Layers,
  ArrowUpDown,
  Info,
} from 'lucide-react';
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  ReferenceLine,
  AreaChart,
  Area,
  ComposedChart,
  Bar,
} from 'recharts';
import { useIVSurface } from '../hooks/useApi';

/* ─── Color helpers ─────────────────────────────────────────── */
function ivToColor(iv, minIV, maxIV) {
  const t = maxIV === minIV ? 0.5 : (iv - minIV) / (maxIV - minIV);
  // Deep blue → cyan → green → yellow → orange → red
  if (t < 0.2) {
    const s = t / 0.2;
    return `rgb(${Math.round(20 + 10 * s)}, ${Math.round(40 + 120 * s)}, ${Math.round(140 + 60 * s)})`;
  } else if (t < 0.4) {
    const s = (t - 0.2) / 0.2;
    return `rgb(${Math.round(30 + 20 * s)}, ${Math.round(160 + 40 * s)}, ${Math.round(200 - 80 * s)})`;
  } else if (t < 0.6) {
    const s = (t - 0.4) / 0.2;
    return `rgb(${Math.round(50 + 150 * s)}, ${Math.round(200 - 20 * s)}, ${Math.round(120 - 80 * s)})`;
  } else if (t < 0.8) {
    const s = (t - 0.6) / 0.2;
    return `rgb(${Math.round(200 + 45 * s)}, ${Math.round(180 - 80 * s)}, ${Math.round(40 - 10 * s)})`;
  } else {
    const s = (t - 0.8) / 0.2;
    return `rgb(${Math.round(245 - 20 * s)}, ${Math.round(100 - 70 * s)}, ${Math.round(30 + 10 * s)})`;
  }
}

function ivToTextColor(iv, minIV, maxIV) {
  const t = maxIV === minIV ? 0.5 : (iv - minIV) / (maxIV - minIV);
  return t > 0.55 ? '#fff' : 'rgba(255,255,255,0.9)';
}

/* ─── Empty fallback (no mock data) ───────────────────────── */
const EMPTY_IV_DATA = { symbol: 'NIFTY', spot: 0, surface: [], strikes: [], timestamp: new Date().toISOString() };

/* ─── Stat card ────────────────────────────────────────────── */
function StatCard({ icon: Icon, label, value, sub, color = 'text-accent' }) {
  return (
    <div className="glass-card p-4 flex items-center gap-3">
      <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${
        color === 'text-accent' ? 'bg-accent/10' :
        color === 'text-profit' ? 'bg-profit/10' :
        color === 'text-loss' ? 'bg-loss/10' :
        'bg-blue-500/10'
      }`}>
        <Icon className={`w-5 h-5 ${color}`} />
      </div>
      <div className="min-w-0">
        <div className="text-xs text-slate-400 uppercase tracking-wider">{label}</div>
        <div className="text-lg font-bold text-white">{value}</div>
        {sub && <div className="text-xs text-slate-500">{sub}</div>}
      </div>
    </div>
  );
}

/* ─── Custom Tooltip for Recharts ──────────────────────────── */
function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-terminal-card/95 border border-terminal-border rounded-lg px-3 py-2 text-xs shadow-xl"
      style={{ backdropFilter: 'blur(12px)' }}>
      <div className="text-slate-400 mb-1">Strike: {label}</div>
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full" style={{ background: p.color }} />
          <span className="text-slate-300">{p.name}:</span>
          <span className="font-semibold text-white">{p.value?.toFixed(2)}%</span>
        </div>
      ))}
    </div>
  );
}

function TermTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-terminal-card/95 border border-terminal-border rounded-lg px-3 py-2 text-xs shadow-xl"
      style={{ backdropFilter: 'blur(12px)' }}>
      <div className="text-slate-400 mb-1">{label} DTE</div>
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full" style={{ background: p.color }} />
          <span className="text-slate-300">{p.name}:</span>
          <span className="font-semibold text-white">{p.value?.toFixed(2)}%</span>
        </div>
      ))}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   IV Surface Page
   ═══════════════════════════════════════════════════════════════ */
export default function IVSurface() {
  const [symbol, setSymbol] = useState('NIFTY');
  const [ivType, setIvType] = useState('call'); // 'call' | 'put'
  const [selectedDte, setSelectedDte] = useState(null);
  const [hoveredCell, setHoveredCell] = useState(null);
  const [view, setView] = useState('heatmap'); // 'heatmap' | 'smile' | 'term'

  const { data: apiData } = useIVSurface(symbol);

  /* Use API data or empty fallback (no mock data) */
  const data = useMemo(() => {
    if (apiData?.surface?.length) return apiData;
    return EMPTY_IV_DATA;
  }, [apiData]);

  const { surface } = data;
  const spot = data.spot ?? data.spot_price ?? 0;

  /* Build grid structures */
  const { dtes, strikes, gridMap, allIVs, minIV, maxIV } = useMemo(() => {
    const dteSet = new Set();
    const strikeSet = new Set();
    const map = {};
    const ivs = [];

    surface.forEach((p) => {
      dteSet.add(p.dte);
      strikeSet.add(p.strike);
      const key = `${p.dte}-${p.strike}`;
      map[key] = p;
      ivs.push(ivType === 'call' ? p.iv_call : p.iv_put);
    });

    const sortedDtes = [...dteSet].sort((a, b) => a - b);
    const sortedStrikes = [...strikeSet].sort((a, b) => a - b);
    return {
      dtes: sortedDtes,
      strikes: sortedStrikes,
      gridMap: map,
      allIVs: ivs,
      minIV: Math.min(...ivs),
      maxIV: Math.max(...ivs),
    };
  }, [surface, ivType]);

  /* Smile data — IV vs strike for each DTE */
  const smileData = useMemo(() => {
    return strikes.map((s) => {
      const row = { strike: s };
      dtes.forEach((d) => {
        const p = gridMap[`${d}-${s}`];
        if (p) {
          row[`dte_${d}_call`] = p.iv_call;
          row[`dte_${d}_put`] = p.iv_put;
        }
      });
      return row;
    });
  }, [strikes, dtes, gridMap]);

  /* Term structure data — IV vs DTE for ATM strike */
  const termData = useMemo(() => {
    const atmStrike = strikes.reduce((best, s) =>
      Math.abs(s - spot) < Math.abs(best - spot) ? s : best, strikes[0]);
    return dtes.map((d) => {
      const p = gridMap[`${d}-${atmStrike}`];
      return {
        dte: d,
        label: `${d}D`,
        iv_call: p?.iv_call ?? 0,
        iv_put: p?.iv_put ?? 0,
      };
    });
  }, [dtes, strikes, spot, gridMap]);

  /* Skew data — difference between OTM put and OTM call IV */
  const skewStats = useMemo(() => {
    const atmStrike = strikes.reduce((best, s) =>
      Math.abs(s - spot) < Math.abs(best - spot) ? s : best, strikes[0]);
    const atmIdx = strikes.indexOf(atmStrike);
    const putIdx = Math.max(0, atmIdx - 2);
    const callIdx = Math.min(strikes.length - 1, atmIdx + 2);

    const stats = dtes.map((d) => {
      const atm = gridMap[`${d}-${atmStrike}`];
      const otmPut = gridMap[`${d}-${strikes[putIdx]}`];
      const otmCall = gridMap[`${d}-${strikes[callIdx]}`];
      const skew = otmPut && otmCall ? (otmPut.iv_put - otmCall.iv_call).toFixed(2) : '—';
      return {
        dte: d,
        atmIV: atm ? (ivType === 'call' ? atm.iv_call : atm.iv_put).toFixed(2) : '—',
        skew,
      };
    });
    return stats;
  }, [dtes, strikes, spot, gridMap, ivType]);

  const atmStrike = useMemo(() =>
    strikes.reduce((best, s) => Math.abs(s - spot) < Math.abs(best - spot) ? s : best, strikes[0]),
    [strikes, spot]);

  const smileColors = ['#7c3aed', '#3b82f6', '#06b6d4', '#22c55e', '#f59e0b', '#ef4444'];

  return (
    <div className="space-y-4 animate-fade-in">
      {/* ── Header Row ──────────────────────────────────────── */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-white flex items-center gap-2">
            <Flame className="w-6 h-6 text-orange-400" />
            IV Surface Heatmap
          </h1>
          <p className="text-sm text-slate-400 mt-0.5">
            Implied volatility across strikes &amp; expiries
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {/* Symbol selector */}
          <select
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            className="bg-slate-800 border border-terminal-border rounded-lg px-3 py-1.5 text-sm text-slate-200 focus:outline-none focus:ring-1 focus:ring-accent"
          >
            <option value="NIFTY">NIFTY</option>
            <option value="BANKNIFTY">BANKNIFTY</option>
            <option value="FINNIFTY">FINNIFTY</option>
          </select>

          {/* Call/Put toggle */}
          <div className="flex bg-slate-800 rounded-lg border border-terminal-border p-0.5">
            <button
              onClick={() => setIvType('call')}
              className={`px-3 py-1 text-xs font-semibold rounded-md transition-all ${
                ivType === 'call'
                  ? 'bg-blue-600 text-white shadow-sm'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              Call IV
            </button>
            <button
              onClick={() => setIvType('put')}
              className={`px-3 py-1 text-xs font-semibold rounded-md transition-all ${
                ivType === 'put'
                  ? 'bg-purple-600 text-white shadow-sm'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              Put IV
            </button>
          </div>

          {/* View toggle */}
          <div className="flex bg-slate-800 rounded-lg border border-terminal-border p-0.5">
            {[
              { id: 'heatmap', icon: Grid3X3, label: 'Heatmap' },
              { id: 'smile', icon: TrendingUp, label: 'Smile' },
              { id: 'term', icon: Layers, label: 'Term' },
            ].map(({ id, icon: VIcon, label }) => (
              <button
                key={id}
                onClick={() => setView(id)}
                className={`flex items-center gap-1 px-3 py-1 text-xs font-semibold rounded-md transition-all ${
                  view === id
                    ? 'bg-accent text-white shadow-sm'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                <VIcon className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">{label}</span>
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* ── Stat Cards ─────────────────────────────────────── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard
          icon={Activity}
          label="ATM IV"
          value={`${skewStats.find(s => s.dte === (selectedDte || dtes[2]))?.atmIV ?? '—'}%`}
          sub={`${selectedDte || dtes[2]}D expiry`}
          color="text-accent"
        />
        <StatCard
          icon={ArrowUpDown}
          label="IV Skew"
          value={`${skewStats.find(s => s.dte === (selectedDte || dtes[2]))?.skew ?? '—'}%`}
          sub="OTM Put − OTM Call"
          color="text-blue-400"
        />
        <StatCard
          icon={TrendingDown}
          label="Min IV"
          value={`${minIV.toFixed(2)}%`}
          sub="Across surface"
          color="text-profit"
        />
        <StatCard
          icon={TrendingUp}
          label="Max IV"
          value={`${maxIV.toFixed(2)}%`}
          sub="Across surface"
          color="text-loss"
        />
      </div>

      {/* ── Spot Price Banner ──────────────────────────────── */}
      <div className="glass-card p-3 flex flex-wrap items-center gap-4 text-sm">
        <div className="flex items-center gap-2">
          <Eye className="w-4 h-4 text-accent" />
          <span className="text-slate-400">Spot:</span>
          <span className="font-bold text-white">₹{spot?.toFixed(2)}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-slate-400">ATM Strike:</span>
          <span className="font-semibold text-accent">{atmStrike}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-slate-400">Strikes:</span>
          <span className="text-slate-200">{strikes.length}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-slate-400">Expiries:</span>
          <span className="text-slate-200">{dtes.length} ({dtes.join(', ')} DTE)</span>
        </div>
        {hoveredCell && (
          <div className="ml-auto flex items-center gap-2 text-xs bg-slate-800/50 px-3 py-1 rounded-lg border border-terminal-border">
            <Info className="w-3.5 h-3.5 text-blue-400" />
            <span className="text-slate-400">Strike {hoveredCell.strike}</span>
            <span className="text-slate-500">|</span>
            <span className="text-slate-400">{hoveredCell.dte}D</span>
            <span className="text-slate-500">|</span>
            <span className="font-semibold text-white">{hoveredCell.iv?.toFixed(2)}%</span>
          </div>
        )}
      </div>

      {/* ═══ HEATMAP VIEW ═══════════════════════════════════ */}
      {view === 'heatmap' && (
        <div className="glass-card p-4">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
              <Grid3X3 className="w-4 h-4 text-accent" />
              {ivType === 'call' ? 'Call' : 'Put'} IV Surface — {symbol}
            </h2>
            {/* Color legend */}
            <div className="flex items-center gap-2 text-xs text-slate-400">
              <span>Low IV</span>
              <div className="flex h-3 rounded-sm overflow-hidden" style={{ width: 100 }}>
                {Array.from({ length: 20 }).map((_, i) => (
                  <div
                    key={i}
                    className="flex-1"
                    style={{ background: ivToColor(minIV + (maxIV - minIV) * (i / 19), minIV, maxIV) }}
                  />
                ))}
              </div>
              <span>High IV</span>
            </div>
          </div>

          {/* Scrollable heatmap grid */}
          <div className="overflow-x-auto -mx-4 px-4">
            <table className="w-full border-collapse" style={{ minWidth: strikes.length * 64 + 80 }}>
              <thead>
                <tr>
                  <th className="sticky left-0 z-10 bg-slate-900/90 text-xs font-semibold text-slate-400 uppercase tracking-wider px-3 py-2.5 text-left"
                    style={{ backdropFilter: 'blur(8px)', minWidth: 70 }}>
                    DTE \ Strike
                  </th>
                  {strikes.map((s) => (
                    <th
                      key={s}
                      className={`text-xs font-medium px-1 py-2.5 text-center whitespace-nowrap transition-colors ${
                        s === atmStrike ? 'text-accent font-bold' : 'text-slate-400'
                      }`}
                    >
                      {s}
                      {s === atmStrike && (
                        <div className="text-[9px] text-accent/70 font-normal">ATM</div>
                      )}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {dtes.map((d) => (
                  <tr
                    key={d}
                    className={`cursor-pointer transition-all ${
                      selectedDte === d ? 'ring-1 ring-accent/40' : ''
                    }`}
                    onClick={() => setSelectedDte(selectedDte === d ? null : d)}
                  >
                    <td className="sticky left-0 z-10 bg-slate-900/90 text-xs font-semibold text-slate-300 px-3 py-0"
                      style={{ backdropFilter: 'blur(8px)' }}>
                      <div className="flex items-center gap-1.5">
                        <span className={`w-1.5 h-1.5 rounded-full ${
                          d <= 3 ? 'bg-red-400' : d <= 14 ? 'bg-yellow-400' : 'bg-green-400'
                        }`} />
                        {d}D
                      </div>
                    </td>
                    {strikes.map((s) => {
                      const p = gridMap[`${d}-${s}`];
                      const iv = p ? (ivType === 'call' ? p.iv_call : p.iv_put) : null;
                      const isAtm = s === atmStrike;
                      return (
                        <td
                          key={s}
                          className="px-0 py-0 text-center relative"
                          onMouseEnter={() => setHoveredCell(iv != null ? { strike: s, dte: d, iv } : null)}
                          onMouseLeave={() => setHoveredCell(null)}
                        >
                          {iv != null ? (
                            <div
                              className={`mx-0.5 my-0.5 rounded-md py-2 px-1 text-xs font-semibold transition-all hover:scale-105 hover:shadow-lg ${
                                isAtm ? 'ring-1 ring-accent/50' : ''
                              }`}
                              style={{
                                background: ivToColor(iv, minIV, maxIV),
                                color: ivToTextColor(iv, minIV, maxIV),
                                fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
                              }}
                            >
                              {iv.toFixed(1)}
                            </div>
                          ) : (
                            <div className="mx-0.5 my-0.5 rounded-md py-2 px-1 text-xs text-slate-600">—</div>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Heatmap row info */}
          <div className="mt-3 flex flex-wrap gap-3 text-xs text-slate-500">
            <div className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-red-400" />
              Near-term (1–3D)
            </div>
            <div className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-yellow-400" />
              Mid-term (7–14D)
            </div>
            <div className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-green-400" />
              Far-term (30–60D)
            </div>
            <div className="ml-auto">Click a row to select expiry</div>
          </div>
        </div>
      )}

      {/* ═══ SMILE VIEW ═════════════════════════════════════ */}
      {view === 'smile' && (
        <div className="glass-card p-4">
          <h2 className="text-sm font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2 mb-4">
            <TrendingUp className="w-4 h-4 text-accent" />
            Volatility Smile — {ivType === 'call' ? 'Call' : 'Put'} IV by Strike
          </h2>

          {/* DTE selector chips */}
          <div className="flex flex-wrap gap-2 mb-4">
            {dtes.map((d, i) => (
              <button
                key={d}
                onClick={() => setSelectedDte(selectedDte === d ? null : d)}
                className={`px-3 py-1 rounded-full text-xs font-semibold transition-all border ${
                  selectedDte === d || selectedDte === null
                    ? 'border-accent/30 text-white'
                    : 'border-terminal-border text-slate-500 opacity-40'
                }`}
                style={{
                  backgroundColor: selectedDte === d ? smileColors[i] + '22' : 'transparent',
                  borderColor: selectedDte === d ? smileColors[i] : undefined,
                }}
              >
                <span className="inline-block w-2 h-2 rounded-full mr-1.5" style={{ background: smileColors[i] }} />
                {d}D
              </button>
            ))}
          </div>

          <div style={{ height: 380 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={smileData} margin={{ top: 10, right: 20, bottom: 10, left: 10 }}>
                <CartesianGrid stroke="rgba(255,255,255,0.04)" strokeDasharray="3 3" />
                <XAxis
                  dataKey="strike"
                  tick={{ fill: '#64748b', fontSize: 11 }}
                  axisLine={{ stroke: 'rgba(255,255,255,0.06)' }}
                />
                <YAxis
                  tick={{ fill: '#64748b', fontSize: 11 }}
                  axisLine={{ stroke: 'rgba(255,255,255,0.06)' }}
                  domain={['auto', 'auto']}
                  tickFormatter={(v) => `${v}%`}
                />
                <Tooltip content={<ChartTooltip />} />
                <ReferenceLine
                  x={atmStrike}
                  stroke="rgba(124, 58, 237, 0.5)"
                  strokeDasharray="5 5"
                  label={{ value: 'ATM', fill: '#7c3aed', fontSize: 10, position: 'top' }}
                />
                {dtes.map((d, i) => {
                  if (selectedDte != null && selectedDte !== d) return null;
                  const key = ivType === 'call' ? `dte_${d}_call` : `dte_${d}_put`;
                  return (
                    <Line
                      key={d}
                      type="monotone"
                      dataKey={key}
                      name={`${d}D`}
                      stroke={smileColors[i]}
                      strokeWidth={selectedDte === d ? 3 : 2}
                      dot={selectedDte === d ? { r: 3, fill: smileColors[i] } : false}
                      activeDot={{ r: 5 }}
                      opacity={selectedDte != null && selectedDte !== d ? 0.2 : 1}
                    />
                  );
                })}
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* ═══ TERM STRUCTURE VIEW ═══════════════════════════ */}
      {view === 'term' && (
        <div className="glass-card p-4">
          <h2 className="text-sm font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2 mb-4">
            <Layers className="w-4 h-4 text-accent" />
            IV Term Structure — ATM Strike ({atmStrike})
          </h2>

          <div style={{ height: 380 }}>
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={termData} margin={{ top: 10, right: 20, bottom: 10, left: 10 }}>
                <CartesianGrid stroke="rgba(255,255,255,0.04)" strokeDasharray="3 3" />
                <XAxis
                  dataKey="label"
                  tick={{ fill: '#64748b', fontSize: 11 }}
                  axisLine={{ stroke: 'rgba(255,255,255,0.06)' }}
                />
                <YAxis
                  tick={{ fill: '#64748b', fontSize: 11 }}
                  axisLine={{ stroke: 'rgba(255,255,255,0.06)' }}
                  domain={['auto', 'auto']}
                  tickFormatter={(v) => `${v}%`}
                />
                <Tooltip content={<TermTooltip />} />
                <Bar dataKey="iv_call" name="Call IV" fill="rgba(59, 130, 246, 0.25)" radius={[4, 4, 0, 0]} barSize={32} />
                <Bar dataKey="iv_put" name="Put IV" fill="rgba(168, 85, 247, 0.25)" radius={[4, 4, 0, 0]} barSize={32} />
                <Line type="monotone" dataKey="iv_call" name="Call IV" stroke="#3b82f6" strokeWidth={2.5} dot={{ r: 4, fill: '#3b82f6' }} />
                <Line type="monotone" dataKey="iv_put" name="Put IV" stroke="#a855f7" strokeWidth={2.5} dot={{ r: 4, fill: '#a855f7' }} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>

          {/* Term structure table */}
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-terminal-border">
                  <th className="text-left text-slate-400 uppercase tracking-wider py-2 px-3">DTE</th>
                  <th className="text-right text-slate-400 uppercase tracking-wider py-2 px-3">Call IV</th>
                  <th className="text-right text-slate-400 uppercase tracking-wider py-2 px-3">Put IV</th>
                  <th className="text-right text-slate-400 uppercase tracking-wider py-2 px-3">Spread</th>
                  <th className="text-right text-slate-400 uppercase tracking-wider py-2 px-3">Skew</th>
                </tr>
              </thead>
              <tbody>
                {termData.map((row, i) => {
                  const spread = (row.iv_put - row.iv_call).toFixed(2);
                  const prevCall = i > 0 ? termData[i - 1].iv_call : row.iv_call;
                  const change = (row.iv_call - prevCall).toFixed(2);
                  return (
                    <tr key={row.dte} className="border-b border-terminal-border/50 hover:bg-white/[0.02] transition-colors">
                      <td className="py-2 px-3 font-semibold text-slate-300">
                        <div className="flex items-center gap-1.5">
                          <span className={`w-1.5 h-1.5 rounded-full ${
                            row.dte <= 3 ? 'bg-red-400' : row.dte <= 14 ? 'bg-yellow-400' : 'bg-green-400'
                          }`} />
                          {row.dte}D
                        </div>
                      </td>
                      <td className="py-2 px-3 text-right text-blue-400 font-medium">{row.iv_call.toFixed(2)}%</td>
                      <td className="py-2 px-3 text-right text-purple-400 font-medium">{row.iv_put.toFixed(2)}%</td>
                      <td className="py-2 px-3 text-right text-slate-300">{spread}%</td>
                      <td className={`py-2 px-3 text-right font-medium ${
                        Number(change) > 0 ? 'text-profit' : Number(change) < 0 ? 'text-loss' : 'text-slate-400'
                      }`}>
                        {Number(change) > 0 ? '+' : ''}{change}%
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ═══ SKEW TABLE (shown below heatmap) ════════════════ */}
      {view === 'heatmap' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* Skew summary */}
          <div className="glass-card p-4">
            <h2 className="text-sm font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2 mb-3">
              <BarChart3 className="w-4 h-4 text-blue-400" />
              IV Skew by Expiry
            </h2>
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-terminal-border">
                  <th className="text-left text-slate-400 uppercase tracking-wider py-2 px-3">DTE</th>
                  <th className="text-right text-slate-400 uppercase tracking-wider py-2 px-3">ATM IV</th>
                  <th className="text-right text-slate-400 uppercase tracking-wider py-2 px-3">Skew (P−C)</th>
                </tr>
              </thead>
              <tbody>
                {skewStats.map((s) => (
                  <tr
                    key={s.dte}
                    className={`border-b border-terminal-border/50 hover:bg-white/[0.02] transition-colors cursor-pointer ${
                      selectedDte === s.dte ? 'bg-accent/5' : ''
                    }`}
                    onClick={() => setSelectedDte(selectedDte === s.dte ? null : s.dte)}
                  >
                    <td className="py-2.5 px-3 font-semibold text-slate-300">
                      <div className="flex items-center gap-1.5">
                        <span className={`w-1.5 h-1.5 rounded-full ${
                          s.dte <= 3 ? 'bg-red-400' : s.dte <= 14 ? 'bg-yellow-400' : 'bg-green-400'
                        }`} />
                        {s.dte}D
                      </div>
                    </td>
                    <td className="py-2.5 px-3 text-right text-accent font-medium">{s.atmIV}%</td>
                    <td className={`py-2.5 px-3 text-right font-medium ${
                      Number(s.skew) > 0 ? 'text-loss' : 'text-profit'
                    }`}>
                      {Number(s.skew) > 0 ? '+' : ''}{s.skew}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Mini smile chart for selected/default DTE */}
          <div className="glass-card p-4">
            <h2 className="text-sm font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2 mb-3">
              <TrendingUp className="w-4 h-4 text-orange-400" />
              IV Smile — {selectedDte || dtes[2]}D Expiry
            </h2>
            <div style={{ height: 220 }}>
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart
                  data={smileData}
                  margin={{ top: 5, right: 10, bottom: 5, left: 5 }}
                >
                  <CartesianGrid stroke="rgba(255,255,255,0.04)" strokeDasharray="3 3" />
                  <XAxis
                    dataKey="strike"
                    tick={{ fill: '#64748b', fontSize: 10 }}
                    axisLine={{ stroke: 'rgba(255,255,255,0.06)' }}
                  />
                  <YAxis
                    tick={{ fill: '#64748b', fontSize: 10 }}
                    axisLine={{ stroke: 'rgba(255,255,255,0.06)' }}
                    domain={['auto', 'auto']}
                    tickFormatter={(v) => `${v}%`}
                  />
                  <Tooltip content={<ChartTooltip />} />
                  <ReferenceLine
                    x={atmStrike}
                    stroke="rgba(124, 58, 237, 0.4)"
                    strokeDasharray="4 4"
                  />
                  <Area
                    type="monotone"
                    dataKey={`dte_${selectedDte || dtes[2]}_call`}
                    name="Call IV"
                    stroke="#3b82f6"
                    fill="rgba(59, 130, 246, 0.1)"
                    strokeWidth={2}
                    dot={{ r: 2.5, fill: '#3b82f6' }}
                  />
                  <Area
                    type="monotone"
                    dataKey={`dte_${selectedDte || dtes[2]}_put`}
                    name="Put IV"
                    stroke="#a855f7"
                    fill="rgba(168, 85, 247, 0.08)"
                    strokeWidth={2}
                    dot={{ r: 2.5, fill: '#a855f7' }}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
