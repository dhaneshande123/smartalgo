import { useState, useMemo, useCallback } from 'react';
import {
  ComposedChart, Bar, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
  ReferenceLine, CartesianGrid, Area, Cell,
} from 'recharts';
import {
  BarChart3, Clock, TrendingUp, TrendingDown, Activity, Layers,
  ChevronDown, Eye, EyeOff,
} from 'lucide-react';
import { useCandles, useIndices } from '../hooks/useApi';

/* ════════════════════════════════════════════════════════════
   Constants
   ════════════════════════════════════════════════════════════ */

const SYMBOLS = [
  { value: 'NIFTY', label: 'NIFTY 50' },
  { value: 'BANKNIFTY', label: 'BANK NIFTY' },
  { value: 'FINNIFTY', label: 'FIN NIFTY' },
  { value: 'MIDCPNIFTY', label: 'MIDCAP NIFTY' },
];

const TIMEFRAMES = [
  { value: 'M1', label: '1m', count: 120 },
  { value: 'M5', label: '5m', count: 100 },
  { value: 'M15', label: '15m', count: 80 },
  { value: 'M30', label: '30m', count: 60 },
  { value: 'H1', label: '1H', count: 50 },
  { value: 'D1', label: '1D', count: 60 },
];

const INDICATOR_PRESETS = {
  sma20: { label: 'SMA 20', color: '#f59e0b', period: 20, type: 'sma' },
  sma50: { label: 'SMA 50', color: '#3b82f6', period: 50, type: 'sma' },
  ema9: { label: 'EMA 9', color: '#a855f7', period: 9, type: 'ema' },
  ema21: { label: 'EMA 21', color: '#ec4899', period: 21, type: 'ema' },
  bb: { label: 'Bollinger', color: '#6366f1', period: 20, type: 'bb' },
};

/* ════════════════════════════════════════════════════════════
   Technical Indicator Calculations
   ════════════════════════════════════════════════════════════ */

function calcSMA(data, period, key = 'close') {
  return data.map((d, i) => {
    if (i < period - 1) return null;
    const slice = data.slice(i - period + 1, i + 1);
    return slice.reduce((sum, c) => sum + c[key], 0) / period;
  });
}

function calcEMA(data, period, key = 'close') {
  const k = 2 / (period + 1);
  const result = [];
  let prev = null;
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) { result.push(null); continue; }
    if (prev === null) {
      prev = data.slice(0, period).reduce((s, c) => s + c[key], 0) / period;
    } else {
      prev = data[i][key] * k + prev * (1 - k);
    }
    result.push(prev);
  }
  return result;
}

function calcBollingerBands(data, period = 20, stdDev = 2) {
  const sma = calcSMA(data, period);
  return data.map((d, i) => {
    if (sma[i] === null) return { upper: null, middle: null, lower: null };
    const slice = data.slice(i - period + 1, i + 1);
    const mean = sma[i];
    const variance = slice.reduce((s, c) => s + Math.pow(c.close - mean, 2), 0) / period;
    const sd = Math.sqrt(variance);
    return { upper: mean + stdDev * sd, middle: mean, lower: mean - stdDev * sd };
  });
}

function calcRSI(data, period = 14) {
  const result = [];
  let avgGain = 0, avgLoss = 0;
  for (let i = 0; i < data.length; i++) {
    if (i === 0) { result.push(null); continue; }
    const change = data[i].close - data[i - 1].close;
    const gain = Math.max(change, 0);
    const loss = Math.abs(Math.min(change, 0));
    if (i <= period) {
      avgGain += gain; avgLoss += loss;
      if (i === period) { avgGain /= period; avgLoss /= period; }
      result.push(i < period ? null : (100 - 100 / (1 + avgGain / (avgLoss || 0.001))));
    } else {
      avgGain = (avgGain * (period - 1) + gain) / period;
      avgLoss = (avgLoss * (period - 1) + loss) / period;
      result.push(100 - 100 / (1 + avgGain / (avgLoss || 0.001)));
    }
  }
  return result;
}

function calcMACD(data) {
  const ema12 = calcEMA(data, 12);
  const ema26 = calcEMA(data, 26);
  const macdLine = ema12.map((v, i) => (v !== null && ema26[i] !== null) ? v - ema26[i] : null);
  // Signal line: EMA 9 of MACD
  const macdData = macdLine.map(v => ({ close: v || 0 }));
  const signal = calcEMA(macdData, 9);
  return macdLine.map((v, i) => ({
    macd: v, signal: signal[i],
    histogram: (v !== null && signal[i] !== null) ? v - signal[i] : null,
  }));
}

/* ════════════════════════════════════════════════════════════
   Custom Candlestick Bar Shape
   ════════════════════════════════════════════════════════════ */

function CandlestickShape(props) {
  const { x, width, payload, background } = props;
  if (!payload || !background) return null;

  const { open, close, high, low, _domainMin, _domainRange } = payload;
  if (_domainRange == null || _domainRange === 0) return null;

  const bullish = close >= open;
  const color = bullish ? '#22c55e' : '#ef4444';

  // Convert price to Y pixel using background plot area
  const plotY = background.y;
  const plotH = background.height;
  const toY = (price) => plotY + plotH - ((price - _domainMin) / _domainRange) * plotH;

  const oY = toY(open);
  const cY = toY(close);
  const hY = toY(high);
  const lY = toY(low);

  const bodyTop = Math.min(oY, cY);
  const bodyH = Math.max(Math.abs(cY - oY), 1);
  const barW = Math.min(Math.max(width * 0.65, 2), 14);
  const cx = x + width / 2;

  return (
    <g>
      <line x1={cx} y1={hY} x2={cx} y2={bodyTop} stroke={color} strokeWidth={1} />
      <line x1={cx} y1={bodyTop + bodyH} x2={cx} y2={lY} stroke={color} strokeWidth={1} />
      <rect
        x={cx - barW / 2} y={bodyTop} width={barW} height={bodyH}
        fill={color} fillOpacity={bullish ? 0.25 : 0.85}
        stroke={color} strokeWidth={1} rx={1}
      />
    </g>
  );
}

/* ════════════════════════════════════════════════════════════
   Custom Tooltip
   ════════════════════════════════════════════════════════════ */

function ChartTooltip({ active, payload }) {
  if (!active || !payload?.[0]?.payload) return null;
  const d = payload[0].payload;
  const bullish = d.close >= d.open;
  const change = d.close - d.open;
  const changePct = d.open ? ((change / d.open) * 100).toFixed(2) : '0.00';

  return (
    <div className="bg-terminal-card/95 backdrop-blur border border-terminal-border rounded-lg px-3 py-2 shadow-xl text-xs">
      <div className="text-slate-400 mb-1.5 font-medium">{d.time}</div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
        <span className="text-slate-500">Open</span>
        <span className="font-mono text-white text-right">{d.open?.toFixed(2)}</span>
        <span className="text-slate-500">High</span>
        <span className="font-mono text-profit text-right">{d.high?.toFixed(2)}</span>
        <span className="text-slate-500">Low</span>
        <span className="font-mono text-loss text-right">{d.low?.toFixed(2)}</span>
        <span className="text-slate-500">Close</span>
        <span className={`font-mono text-right ${bullish ? 'text-profit' : 'text-loss'}`}>{d.close?.toFixed(2)}</span>
        <span className="text-slate-500">Change</span>
        <span className={`font-mono text-right ${bullish ? 'text-profit' : 'text-loss'}`}>
          {change >= 0 ? '+' : ''}{change.toFixed(2)} ({changePct}%)
        </span>
        <span className="text-slate-500">Volume</span>
        <span className="font-mono text-slate-300 text-right">{(d.volume / 1000).toFixed(0)}K</span>
      </div>
    </div>
  );
}

function SubTooltip({ active, payload, type }) {
  if (!active || !payload?.[0]?.payload) return null;
  const d = payload[0].payload;
  return (
    <div className="bg-terminal-card/95 backdrop-blur border border-terminal-border rounded-lg px-2.5 py-1.5 shadow-xl text-[10px]">
      {type === 'rsi' && d.rsi != null && (
        <span className="font-mono text-yellow-400">RSI: {d.rsi.toFixed(1)}</span>
      )}
      {type === 'macd' && (
        <div className="space-y-0.5">
          {d.macd != null && <div><span className="text-slate-500">MACD:</span> <span className="font-mono text-blue-400">{d.macd.toFixed(2)}</span></div>}
          {d.signal != null && <div><span className="text-slate-500">Signal:</span> <span className="font-mono text-orange-400">{d.signal.toFixed(2)}</span></div>}
          {d.histogram != null && <div><span className="text-slate-500">Hist:</span> <span className={`font-mono ${d.histogram >= 0 ? 'text-profit' : 'text-loss'}`}>{d.histogram.toFixed(2)}</span></div>}
        </div>
      )}
    </div>
  );
}

/* ════════════════════════════════════════════════════════════
   Stat Badge
   ════════════════════════════════════════════════════════════ */

function StatBadge({ label, value, positive, icon: Icon }) {
  return (
    <div className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-white/[0.03] border border-terminal-border">
      {Icon && <Icon className="w-3.5 h-3.5 text-slate-500" />}
      <span className="text-[10px] text-slate-500 uppercase">{label}</span>
      <span className={`text-xs font-mono font-semibold ${positive === true ? 'text-profit' : positive === false ? 'text-loss' : 'text-white'}`}>
        {value}
      </span>
    </div>
  );
}

/* ════════════════════════════════════════════════════════════
   Main Component
   ════════════════════════════════════════════════════════════ */

export default function Charts() {
  const [symbol, setSymbol] = useState('NIFTY');
  const [timeframe, setTimeframe] = useState('M5');
  const [activeIndicators, setActiveIndicators] = useState(['sma20', 'ema9']);
  const [showVolume, setShowVolume] = useState(true);
  const [subChart, setSubChart] = useState('rsi'); // 'rsi' | 'macd' | 'none'
  const [indicatorMenu, setIndicatorMenu] = useState(false);

  const tfConfig = TIMEFRAMES.find(t => t.value === timeframe) || TIMEFRAMES[1];
  const { data: candleData } = useCandles(symbol, timeframe, tfConfig.count);
  const { data: indicesData } = useIndices();

  // Parse candle data
  const rawCandles = useMemo(() => {
    const candles = candleData?.candles || candleData;
    if (!Array.isArray(candles)) return [];
    return candles.map(c => ({
      timestamp: c.timestamp,
      time: new Date(c.timestamp).toLocaleTimeString('en-IN', {
        hour: '2-digit', minute: '2-digit',
        ...(timeframe === 'D1' ? { hour: undefined, minute: undefined } : {}),
      }),
      date: new Date(c.timestamp).toLocaleDateString('en-IN', { day: '2-digit', month: 'short' }),
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
      volume: c.volume,
    }));
  }, [candleData, timeframe]);

  // Calculate indicators
  const chartData = useMemo(() => {
    if (!rawCandles.length) return [];

    const indicators = {};
    activeIndicators.forEach(key => {
      const cfg = INDICATOR_PRESETS[key];
      if (!cfg) return;
      if (cfg.type === 'sma') indicators[key] = calcSMA(rawCandles, cfg.period);
      else if (cfg.type === 'ema') indicators[key] = calcEMA(rawCandles, cfg.period);
      else if (cfg.type === 'bb') indicators[`${key}_bb`] = calcBollingerBands(rawCandles, cfg.period);
    });

    const rsi = calcRSI(rawCandles);
    const macd = calcMACD(rawCandles);

    return rawCandles.map((c, i) => {
      const entry = { ...c, rsi: rsi[i], ...macd[i] };
      Object.keys(indicators).forEach(key => {
        if (key.endsWith('_bb')) {
          const bb = indicators[key][i];
          entry.bb_upper = bb.upper;
          entry.bb_middle = bb.middle;
          entry.bb_lower = bb.lower;
        } else {
          entry[key] = indicators[key][i];
        }
      });
      return entry;
    });
  }, [rawCandles, activeIndicators]);

  // Price domain for Y axis
  const priceDomain = useMemo(() => {
    if (!chartData.length) return [0, 100];
    let min = Infinity, max = -Infinity;
    chartData.forEach(d => {
      if (d.low < min) min = d.low;
      if (d.high > max) max = d.high;
    });
    const pad = (max - min) * 0.08;
    return [Math.floor(min - pad), Math.ceil(max + pad)];
  }, [chartData]);

  // Embed domain info into each data point for the candlestick shape
  const candleChartData = useMemo(() => {
    const [dMin, dMax] = priceDomain;
    const dRange = dMax - dMin;
    return chartData.map(d => ({ ...d, _domainMin: dMin, _domainRange: dRange }));
  }, [chartData, priceDomain]);

  // Current spot info from indices
  const spotInfo = useMemo(() => {
    const rawIndices = indicesData?.indices || indicesData;
    if (!Array.isArray(rawIndices)) return null;
    return rawIndices.find(i => (i.symbol || '').toUpperCase().includes(symbol));
  }, [indicesData, symbol]);

  // Last candle stats
  const lastCandle = chartData[chartData.length - 1];
  const prevCandle = chartData[chartData.length - 2];
  const dayChange = lastCandle && prevCandle ? lastCandle.close - prevCandle.close : 0;
  const dayChangePct = prevCandle?.close ? ((dayChange / prevCandle.close) * 100).toFixed(2) : '0.00';

  const toggleIndicator = useCallback((key) => {
    setActiveIndicators(prev =>
      prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key]
    );
  }, []);

  const maxVol = useMemo(() => {
    return Math.max(...chartData.map(d => d.volume || 0), 1);
  }, [chartData]);

  return (
    <div className="space-y-4 animate-fade-in">
      {/* ── Top Bar ── */}
      <div className="glass-card !p-3">
        <div className="flex flex-wrap items-center gap-3">
          {/* Symbol Selector */}
          <div className="flex items-center gap-2">
            <BarChart3 className="w-4 h-4 text-accent" />
            <select
              value={symbol}
              onChange={e => setSymbol(e.target.value)}
              className="bg-terminal-bg border border-terminal-border rounded-lg px-3 py-1.5 text-sm font-semibold text-white focus:border-accent focus:outline-none"
            >
              {SYMBOLS.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
          </div>

          {/* Timeframe Pills */}
          <div className="flex items-center gap-1 bg-terminal-bg rounded-lg p-0.5 border border-terminal-border">
            {TIMEFRAMES.map(tf => (
              <button
                key={tf.value}
                onClick={() => setTimeframe(tf.value)}
                className={`px-2.5 py-1 text-xs font-medium rounded-md transition-all ${
                  timeframe === tf.value
                    ? 'bg-accent text-white shadow-sm'
                    : 'text-slate-400 hover:text-white hover:bg-white/[0.05]'
                }`}
              >
                {tf.label}
              </button>
            ))}
          </div>

          {/* Indicators Toggle */}
          <div className="relative">
            <button
              onClick={() => setIndicatorMenu(!indicatorMenu)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-slate-300 bg-terminal-bg border border-terminal-border rounded-lg hover:border-accent/50 transition-colors"
            >
              <Layers className="w-3.5 h-3.5" />
              Indicators ({activeIndicators.length})
              <ChevronDown className={`w-3 h-3 transition-transform ${indicatorMenu ? 'rotate-180' : ''}`} />
            </button>
            {indicatorMenu && (
              <div className="absolute top-full left-0 mt-1 z-50 bg-terminal-card border border-terminal-border rounded-lg shadow-xl py-1 min-w-[180px]">
                {Object.entries(INDICATOR_PRESETS).map(([key, cfg]) => (
                  <button
                    key={key}
                    onClick={() => toggleIndicator(key)}
                    className="flex items-center gap-2 w-full px-3 py-1.5 text-xs hover:bg-white/[0.04] transition-colors"
                  >
                    <div className="w-3 h-3 rounded-sm border" style={{
                      borderColor: cfg.color,
                      backgroundColor: activeIndicators.includes(key) ? cfg.color : 'transparent',
                    }} />
                    <span className={activeIndicators.includes(key) ? 'text-white' : 'text-slate-400'}>{cfg.label}</span>
                    <span className="ml-auto text-[10px] text-slate-600">{cfg.period}</span>
                  </button>
                ))}
                <div className="border-t border-terminal-border my-1" />
                <div className="px-3 py-1 text-[10px] text-slate-500 font-medium uppercase">Sub Chart</div>
                {[
                  { key: 'rsi', label: 'RSI (14)' },
                  { key: 'macd', label: 'MACD (12,26,9)' },
                  { key: 'none', label: 'None' },
                ].map(opt => (
                  <button
                    key={opt.key}
                    onClick={() => setSubChart(opt.key)}
                    className="flex items-center gap-2 w-full px-3 py-1.5 text-xs hover:bg-white/[0.04] transition-colors"
                  >
                    <div className={`w-2 h-2 rounded-full ${subChart === opt.key ? 'bg-accent' : 'bg-slate-600'}`} />
                    <span className={subChart === opt.key ? 'text-white' : 'text-slate-400'}>{opt.label}</span>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Volume Toggle */}
          <button
            onClick={() => setShowVolume(!showVolume)}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border transition-colors ${
              showVolume
                ? 'text-blue-400 border-blue-500/30 bg-blue-500/10'
                : 'text-slate-500 border-terminal-border bg-terminal-bg'
            }`}
          >
            {showVolume ? <Eye className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5" />}
            Vol
          </button>

          {/* Spot Price */}
          <div className="ml-auto flex items-center gap-3">
            {lastCandle && (
              <>
                <span className="text-lg font-bold font-mono text-white">
                  {lastCandle.close?.toLocaleString('en-IN', { minimumFractionDigits: 2 })}
                </span>
                <span className={`flex items-center gap-0.5 text-sm font-mono font-semibold ${dayChange >= 0 ? 'text-profit' : 'text-loss'}`}>
                  {dayChange >= 0 ? <TrendingUp className="w-3.5 h-3.5" /> : <TrendingDown className="w-3.5 h-3.5" />}
                  {dayChange >= 0 ? '+' : ''}{dayChange.toFixed(2)} ({dayChangePct}%)
                </span>
              </>
            )}
          </div>
        </div>
      </div>

      {/* ── Stats Row ── */}
      {lastCandle && (
        <div className="flex flex-wrap gap-2">
          <StatBadge label="Open" value={lastCandle.open?.toFixed(2)} icon={Clock} />
          <StatBadge label="High" value={lastCandle.high?.toFixed(2)} positive={true} icon={TrendingUp} />
          <StatBadge label="Low" value={lastCandle.low?.toFixed(2)} positive={false} icon={TrendingDown} />
          <StatBadge label="Close" value={lastCandle.close?.toFixed(2)} positive={dayChange >= 0} icon={Activity} />
          <StatBadge label="Volume" value={`${(lastCandle.volume / 1000).toFixed(0)}K`} icon={BarChart3} />
          {lastCandle.rsi != null && (
            <StatBadge
              label="RSI"
              value={lastCandle.rsi.toFixed(1)}
              positive={lastCandle.rsi > 30 && lastCandle.rsi < 70 ? undefined : lastCandle.rsi <= 30}
            />
          )}
        </div>
      )}

      {/* ── Main Candlestick Chart ── */}
      <div className="glass-card !p-3">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
              {symbol} {tfConfig.label} Chart
            </h3>
            {activeIndicators.map(key => {
              const cfg = INDICATOR_PRESETS[key];
              if (!cfg) return null;
              return (
                <span key={key} className="flex items-center gap-1 text-[10px]">
                  <span className="w-2 h-0.5 rounded" style={{ backgroundColor: cfg.color }} />
                  <span style={{ color: cfg.color }}>{cfg.label}</span>
                </span>
              );
            })}
          </div>
        </div>

        <div style={{ height: subChart !== 'none' ? 380 : 460 }}>
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={candleChartData} margin={{ top: 10, right: 10, bottom: 0, left: 10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e2433" vertical={false} />
              <XAxis
                dataKey={timeframe === 'D1' ? 'date' : 'time'}
                tick={{ fontSize: 10, fill: '#64748b' }}
                axisLine={{ stroke: '#1e2433' }}
                tickLine={false}
                interval="preserveStartEnd"
                minTickGap={40}
              />
              <YAxis
                domain={priceDomain}
                tick={{ fontSize: 10, fill: '#64748b' }}
                axisLine={false}
                tickLine={false}
                tickFormatter={v => v.toLocaleString('en-IN')}
                width={65}
              />
              {showVolume && (
                <YAxis
                  yAxisId="volume"
                  orientation="right"
                  domain={[0, maxVol * 5]}
                  hide
                />
              )}
              <Tooltip content={<ChartTooltip />} />

              {/* Volume Bars (background) */}
              {showVolume && (
                <Bar yAxisId="volume" dataKey="volume" opacity={0.15} radius={[1, 1, 0, 0]} barSize={8}>
                  {chartData.map((d, i) => (
                    <Cell key={i} fill={d.close >= d.open ? '#22c55e' : '#ef4444'} />
                  ))}
                </Bar>
              )}

              {/* Bollinger Bands */}
              {activeIndicators.includes('bb') && (
                <>
                  <Area dataKey="bb_upper" stroke="#6366f1" strokeWidth={1} fill="none" dot={false} strokeDasharray="4 2" connectNulls />
                  <Area dataKey="bb_lower" stroke="#6366f1" strokeWidth={1} fill="none" dot={false} strokeDasharray="4 2" connectNulls />
                  <Line dataKey="bb_middle" stroke="#6366f1" strokeWidth={1} dot={false} strokeDasharray="2 2" connectNulls opacity={0.5} />
                </>
              )}

              {/* Moving Averages */}
              {activeIndicators.filter(k => k !== 'bb').map(key => {
                const cfg = INDICATOR_PRESETS[key];
                if (!cfg) return null;
                return (
                  <Line
                    key={key}
                    dataKey={key}
                    stroke={cfg.color}
                    strokeWidth={1.5}
                    dot={false}
                    connectNulls
                  />
                );
              })}

              {/* Candlestick bars with custom shape */}
              <Bar
                dataKey="close"
                shape={<CandlestickShape />}
                barSize={12}
                isAnimationActive={false}
                background={{ fill: 'transparent' }}
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* ── Sub Chart: RSI ── */}
      {subChart === 'rsi' && (
        <div className="glass-card !p-3">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2">RSI (14)</h3>
          <div style={{ height: 120 }}>
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={chartData} margin={{ top: 5, right: 10, bottom: 0, left: 10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e2433" vertical={false} />
                <XAxis dataKey={timeframe === 'D1' ? 'date' : 'time'} tick={{ fontSize: 9, fill: '#475569' }} axisLine={false} tickLine={false} minTickGap={60} />
                <YAxis domain={[0, 100]} ticks={[20, 30, 50, 70, 80]} tick={{ fontSize: 9, fill: '#475569' }} axisLine={false} tickLine={false} width={30} />
                <Tooltip content={<SubTooltip type="rsi" />} />
                <ReferenceLine y={70} stroke="#ef4444" strokeDasharray="3 3" strokeWidth={0.8} />
                <ReferenceLine y={30} stroke="#22c55e" strokeDasharray="3 3" strokeWidth={0.8} />
                <ReferenceLine y={50} stroke="#334155" strokeDasharray="2 2" strokeWidth={0.5} />
                <Area
                  dataKey="rsi"
                  stroke="#eab308"
                  strokeWidth={1.5}
                  fill="url(#rsiGrad)"
                  dot={false}
                  connectNulls
                />
                <defs>
                  <linearGradient id="rsiGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#eab308" stopOpacity={0.15} />
                    <stop offset="100%" stopColor="#eab308" stopOpacity={0} />
                  </linearGradient>
                </defs>
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* ── Sub Chart: MACD ── */}
      {subChart === 'macd' && (
        <div className="glass-card !p-3">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2">MACD (12, 26, 9)</h3>
          <div style={{ height: 120 }}>
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={chartData} margin={{ top: 5, right: 10, bottom: 0, left: 10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e2433" vertical={false} />
                <XAxis dataKey={timeframe === 'D1' ? 'date' : 'time'} tick={{ fontSize: 9, fill: '#475569' }} axisLine={false} tickLine={false} minTickGap={60} />
                <YAxis tick={{ fontSize: 9, fill: '#475569' }} axisLine={false} tickLine={false} width={40} />
                <Tooltip content={<SubTooltip type="macd" />} />
                <ReferenceLine y={0} stroke="#334155" strokeWidth={0.5} />
                <Bar dataKey="histogram" barSize={4}>
                  {chartData.map((d, i) => (
                    <Cell key={i} fill={d.histogram >= 0 ? '#22c55e' : '#ef4444'} opacity={0.6} />
                  ))}
                </Bar>
                <Line dataKey="macd" stroke="#3b82f6" strokeWidth={1.5} dot={false} connectNulls />
                <Line dataKey="signal" stroke="#f97316" strokeWidth={1.5} dot={false} connectNulls />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </div>
  );
}
