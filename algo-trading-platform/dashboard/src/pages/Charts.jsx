import { useState, useRef, useEffect, useMemo, useCallback } from 'react';
import { createChart, ColorType, CrosshairMode, CandlestickSeries, HistogramSeries, LineSeries } from 'lightweight-charts';
import {
  BarChart3, Clock, TrendingUp, TrendingDown, Activity, Layers,
  ChevronDown, Eye, EyeOff,
} from 'lucide-react';
import { useCandles, useIndices } from '../hooks/useApi';
import { useTheme } from '../context/ThemeContext';

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
  const macdData = macdLine.map(v => ({ close: v || 0 }));
  const signal = calcEMA(macdData, 9);
  return macdLine.map((v, i) => ({
    macd: v, signal: signal[i],
    histogram: (v !== null && signal[i] !== null) ? v - signal[i] : null,
  }));
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
  const { theme } = useTheme();
  const isDark = theme === 'dark';

  const [symbol, setSymbol] = useState('NIFTY');
  const [timeframe, setTimeframe] = useState('M5');
  const [activeIndicators, setActiveIndicators] = useState(['sma20', 'ema9']);
  const [showVolume, setShowVolume] = useState(true);
  const [subChart, setSubChart] = useState('rsi');
  const [indicatorMenu, setIndicatorMenu] = useState(false);

  const tfConfig = TIMEFRAMES.find(t => t.value === timeframe) || TIMEFRAMES[1];
  const { data: candleData } = useCandles(symbol, timeframe, tfConfig.count);
  const { data: indicesData } = useIndices();

  /* ── Refs for lightweight-charts ── */
  const chartContainerRef = useRef(null);
  const chartRef = useRef(null);
  const candleSeriesRef = useRef(null);
  const volumeSeriesRef = useRef(null);
  const indicatorSeriesRef = useRef({}); // keyed by indicator name

  /* ── Sub-chart refs (RSI / MACD) ── */
  const rsiContainerRef = useRef(null);
  const rsiChartRef = useRef(null);
  const rsiSeriesRef = useRef(null);

  const macdContainerRef = useRef(null);
  const macdChartRef = useRef(null);
  const macdLineSeriesRef = useRef(null);
  const macdSignalSeriesRef = useRef(null);
  const macdHistSeriesRef = useRef(null);

  // Parse candle data
  const rawCandles = useMemo(() => {
    const candles = candleData?.candles || candleData;
    if (!Array.isArray(candles)) return [];
    return candles.map(c => ({
      timestamp: c.timestamp,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
      volume: c.volume,
    }));
  }, [candleData]);

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

  /* ════════════════════════════════════════════════════════════
     Helper: convert timestamp to lightweight-charts time (UTC seconds)
     ════════════════════════════════════════════════════════════ */
  const toChartTime = useCallback((ts) => {
    return Math.floor(new Date(ts).getTime() / 1000);
  }, []);

  /* ════════════════════════════════════════════════════════════
     Create / destroy the main chart on mount / unmount
     ════════════════════════════════════════════════════════════ */
  useEffect(() => {
    const container = chartContainerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: isDark ? '#0b0e14' : '#ffffff' },
        textColor: isDark ? '#94a3b8' : '#475569',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: isDark ? 'rgba(100,116,139,0.08)' : 'rgba(0,0,0,0.04)' },
        horzLines: { color: isDark ? 'rgba(100,116,139,0.08)' : 'rgba(0,0,0,0.04)' },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: {
        borderColor: isDark ? 'rgba(100,116,139,0.15)' : 'rgba(0,0,0,0.08)',
        scaleMargins: { top: 0.1, bottom: 0.2 },
      },
      timeScale: {
        borderColor: isDark ? 'rgba(100,116,139,0.15)' : 'rgba(0,0,0,0.08)',
        timeVisible: true,
        secondsVisible: false,
      },
      handleScroll: { vertTouchDrag: false },
      width: container.clientWidth,
      height: 500,
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderUpColor: '#22c55e',
      borderDownColor: '#ef4444',
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
    });

    const volumeSeries = chart.addSeries(HistogramSeries, {
      color: '#7c3aed',
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    }, 1);

    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.1, bottom: 0 },
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;

    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({ width: chartContainerRef.current.clientWidth });
      }
    };
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      indicatorSeriesRef.current = {};
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* ════════════════════════════════════════════════════════════
     Update theme on dark/light change
     ════════════════════════════════════════════════════════════ */
  useEffect(() => {
    if (!chartRef.current) return;
    chartRef.current.applyOptions({
      layout: {
        background: { type: ColorType.Solid, color: isDark ? '#0b0e14' : '#ffffff' },
        textColor: isDark ? '#94a3b8' : '#475569',
      },
      grid: {
        vertLines: { color: isDark ? 'rgba(100,116,139,0.08)' : 'rgba(0,0,0,0.04)' },
        horzLines: { color: isDark ? 'rgba(100,116,139,0.08)' : 'rgba(0,0,0,0.04)' },
      },
      rightPriceScale: {
        borderColor: isDark ? 'rgba(100,116,139,0.15)' : 'rgba(0,0,0,0.08)',
      },
      timeScale: {
        borderColor: isDark ? 'rgba(100,116,139,0.15)' : 'rgba(0,0,0,0.08)',
      },
    });
    // Also update sub-charts
    [rsiChartRef, macdChartRef].forEach(ref => {
      if (!ref.current) return;
      ref.current.applyOptions({
        layout: {
          background: { type: ColorType.Solid, color: isDark ? '#0b0e14' : '#ffffff' },
          textColor: isDark ? '#94a3b8' : '#475569',
        },
        grid: {
          vertLines: { color: isDark ? 'rgba(100,116,139,0.08)' : 'rgba(0,0,0,0.04)' },
          horzLines: { color: isDark ? 'rgba(100,116,139,0.08)' : 'rgba(0,0,0,0.04)' },
        },
      });
    });
  }, [isDark]);

  /* ════════════════════════════════════════════════════════════
     Update candle + volume + indicator data when chartData changes
     ════════════════════════════════════════════════════════════ */
  useEffect(() => {
    const chart = chartRef.current;
    const candleSeries = candleSeriesRef.current;
    const volumeSeries = volumeSeriesRef.current;
    if (!chart || !candleSeries) return;
    if (!chartData.length) return;

    // Candle data
    const candleFormatted = chartData.map(c => ({
      time: toChartTime(c.timestamp),
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));
    candleSeries.setData(candleFormatted);

    // Volume data
    if (volumeSeries) {
      if (showVolume) {
        const volumeFormatted = chartData.map(c => ({
          time: toChartTime(c.timestamp),
          value: c.volume,
          color: c.close >= c.open
            ? 'rgba(34,197,94,0.25)'
            : 'rgba(239,68,68,0.25)',
        }));
        volumeSeries.setData(volumeFormatted);
      } else {
        volumeSeries.setData([]);
      }
    }

    // Remove old indicator series
    Object.values(indicatorSeriesRef.current).forEach(s => {
      try { chart.removeSeries(s); } catch (e) { /* already removed */ }
    });
    indicatorSeriesRef.current = {};

    // Add indicator overlays
    activeIndicators.forEach(key => {
      const cfg = INDICATOR_PRESETS[key];
      if (!cfg) return;

      if (cfg.type === 'bb') {
        // Bollinger Bands: upper, middle, lower
        const bbUpper = chart.addSeries(LineSeries, {
          color: cfg.color,
          lineWidth: 1,
          lineStyle: 2, // Dashed
          title: '',
          priceLineVisible: false,
          lastValueVisible: false,
        });
        const bbMiddle = chart.addSeries(LineSeries, {
          color: cfg.color,
          lineWidth: 1,
          lineStyle: 1, // Dotted
          title: 'BB',
          priceLineVisible: false,
          lastValueVisible: false,
        });
        const bbLower = chart.addSeries(LineSeries, {
          color: cfg.color,
          lineWidth: 1,
          lineStyle: 2, // Dashed
          title: '',
          priceLineVisible: false,
          lastValueVisible: false,
        });

        const upperData = [];
        const middleData = [];
        const lowerData = [];
        chartData.forEach(c => {
          const t = toChartTime(c.timestamp);
          if (c.bb_upper != null) upperData.push({ time: t, value: c.bb_upper });
          if (c.bb_middle != null) middleData.push({ time: t, value: c.bb_middle });
          if (c.bb_lower != null) lowerData.push({ time: t, value: c.bb_lower });
        });
        bbUpper.setData(upperData);
        bbMiddle.setData(middleData);
        bbLower.setData(lowerData);

        indicatorSeriesRef.current[`${key}_upper`] = bbUpper;
        indicatorSeriesRef.current[`${key}_middle`] = bbMiddle;
        indicatorSeriesRef.current[`${key}_lower`] = bbLower;
      } else {
        // SMA / EMA
        const series = chart.addSeries(LineSeries, {
          color: cfg.color,
          lineWidth: 1.5,
          title: cfg.label,
          priceLineVisible: false,
          lastValueVisible: false,
        });
        const lineData = [];
        chartData.forEach(c => {
          const t = toChartTime(c.timestamp);
          if (c[key] != null) lineData.push({ time: t, value: c[key] });
        });
        series.setData(lineData);
        indicatorSeriesRef.current[key] = series;
      }
    });

    // Fit content
    chart.timeScale().fitContent();
  }, [chartData, activeIndicators, showVolume, toChartTime]);

  /* ════════════════════════════════════════════════════════════
     RSI Sub-Chart
     ════════════════════════════════════════════════════════════ */
  useEffect(() => {
    // Clean up previous RSI chart
    if (rsiChartRef.current) {
      rsiChartRef.current.remove();
      rsiChartRef.current = null;
      rsiSeriesRef.current = null;
    }

    if (subChart !== 'rsi') return;
    const container = rsiContainerRef.current;
    if (!container) return;

    const rsiChart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: isDark ? '#0b0e14' : '#ffffff' },
        textColor: isDark ? '#94a3b8' : '#475569',
        fontSize: 10,
      },
      grid: {
        vertLines: { color: isDark ? 'rgba(100,116,139,0.08)' : 'rgba(0,0,0,0.04)' },
        horzLines: { color: isDark ? 'rgba(100,116,139,0.08)' : 'rgba(0,0,0,0.04)' },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: {
        borderColor: isDark ? 'rgba(100,116,139,0.15)' : 'rgba(0,0,0,0.08)',
        scaleMargins: { top: 0.05, bottom: 0.05 },
      },
      timeScale: {
        borderColor: isDark ? 'rgba(100,116,139,0.15)' : 'rgba(0,0,0,0.08)',
        timeVisible: true,
        secondsVisible: false,
      },
      handleScroll: { vertTouchDrag: false },
      width: container.clientWidth,
      height: 120,
    });

    const rsiSeries = rsiChart.addSeries(LineSeries, {
      color: '#eab308',
      lineWidth: 1.5,
      title: 'RSI',
      priceLineVisible: false,
      lastValueVisible: true,
    });

    rsiChartRef.current = rsiChart;
    rsiSeriesRef.current = rsiSeries;

    // Set data
    if (chartData.length) {
      const rsiData = [];
      chartData.forEach(c => {
        if (c.rsi != null) {
          rsiData.push({ time: toChartTime(c.timestamp), value: c.rsi });
        }
      });
      rsiSeries.setData(rsiData);
      rsiChart.timeScale().fitContent();
    }

    const handleResize = () => {
      if (rsiContainerRef.current) {
        rsiChart.applyOptions({ width: rsiContainerRef.current.clientWidth });
      }
    };
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      rsiChart.remove();
      rsiChartRef.current = null;
      rsiSeriesRef.current = null;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subChart, chartData, isDark]);

  /* ════════════════════════════════════════════════════════════
     MACD Sub-Chart
     ════════════════════════════════════════════════════════════ */
  useEffect(() => {
    // Clean up previous MACD chart
    if (macdChartRef.current) {
      macdChartRef.current.remove();
      macdChartRef.current = null;
      macdLineSeriesRef.current = null;
      macdSignalSeriesRef.current = null;
      macdHistSeriesRef.current = null;
    }

    if (subChart !== 'macd') return;
    const container = macdContainerRef.current;
    if (!container) return;

    const macdChart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: isDark ? '#0b0e14' : '#ffffff' },
        textColor: isDark ? '#94a3b8' : '#475569',
        fontSize: 10,
      },
      grid: {
        vertLines: { color: isDark ? 'rgba(100,116,139,0.08)' : 'rgba(0,0,0,0.04)' },
        horzLines: { color: isDark ? 'rgba(100,116,139,0.08)' : 'rgba(0,0,0,0.04)' },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: {
        borderColor: isDark ? 'rgba(100,116,139,0.15)' : 'rgba(0,0,0,0.08)',
        scaleMargins: { top: 0.05, bottom: 0.05 },
      },
      timeScale: {
        borderColor: isDark ? 'rgba(100,116,139,0.15)' : 'rgba(0,0,0,0.08)',
        timeVisible: true,
        secondsVisible: false,
      },
      handleScroll: { vertTouchDrag: false },
      width: container.clientWidth,
      height: 120,
    });

    const histSeries = macdChart.addSeries(HistogramSeries, {
      title: 'Hist',
      priceLineVisible: false,
      lastValueVisible: false,
    });

    const macdLineSeries = macdChart.addSeries(LineSeries, {
      color: '#3b82f6',
      lineWidth: 1.5,
      title: 'MACD',
      priceLineVisible: false,
      lastValueVisible: true,
    });

    const signalSeries = macdChart.addSeries(LineSeries, {
      color: '#f97316',
      lineWidth: 1.5,
      title: 'Signal',
      priceLineVisible: false,
      lastValueVisible: true,
    });

    macdChartRef.current = macdChart;
    macdLineSeriesRef.current = macdLineSeries;
    macdSignalSeriesRef.current = signalSeries;
    macdHistSeriesRef.current = histSeries;

    // Set data
    if (chartData.length) {
      const histData = [];
      const macdData = [];
      const signalData = [];
      chartData.forEach(c => {
        const t = toChartTime(c.timestamp);
        if (c.histogram != null) {
          histData.push({
            time: t,
            value: c.histogram,
            color: c.histogram >= 0 ? 'rgba(34,197,94,0.6)' : 'rgba(239,68,68,0.6)',
          });
        }
        if (c.macd != null) macdData.push({ time: t, value: c.macd });
        if (c.signal != null) signalData.push({ time: t, value: c.signal });
      });
      histSeries.setData(histData);
      macdLineSeries.setData(macdData);
      signalSeries.setData(signalData);
      macdChart.timeScale().fitContent();
    }

    const handleResize = () => {
      if (macdContainerRef.current) {
        macdChart.applyOptions({ width: macdContainerRef.current.clientWidth });
      }
    };
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      macdChart.remove();
      macdChartRef.current = null;
      macdLineSeriesRef.current = null;
      macdSignalSeriesRef.current = null;
      macdHistSeriesRef.current = null;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subChart, chartData, isDark]);

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

      {/* ── Main Candlestick Chart (lightweight-charts) ── */}
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

        <div
          ref={chartContainerRef}
          style={{ height: 500, width: '100%' }}
          className="rounded-lg overflow-hidden"
        />
      </div>

      {/* ── Sub Chart: RSI ── */}
      {subChart === 'rsi' && (
        <div className="glass-card !p-3">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2">RSI (14)</h3>
          <div
            ref={rsiContainerRef}
            style={{ height: 120, width: '100%' }}
            className="rounded-lg overflow-hidden"
          />
        </div>
      )}

      {/* ── Sub Chart: MACD ── */}
      {subChart === 'macd' && (
        <div className="glass-card !p-3">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2">MACD (12, 26, 9)</h3>
          <div
            ref={macdContainerRef}
            style={{ height: 120, width: '100%' }}
            className="rounded-lg overflow-hidden"
          />
        </div>
      )}
    </div>
  );
}
