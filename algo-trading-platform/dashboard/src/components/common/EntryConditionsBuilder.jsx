import React, { useMemo } from 'react';
import { Plus, X, Activity, Check, AlertTriangle } from 'lucide-react';
import { useIndicators, useEvaluateConditions } from '../../hooks/useApi';

/**
 * Entry Conditions Builder.
 *
 * Lets the user define a list of indicator-based entry conditions for a
 * strategy, plus the combine logic (ALL / ANY). Live indicator values are
 * fetched from /api/indicators/{symbol} every 5s and shown as a reference panel
 * so the user knows what "RSI 14 < 30" means right now.
 *
 * Props:
 *   underlying       — symbol the conditions evaluate against (NIFTY etc.)
 *   conditions       — array of {indicator, period, operator, value}
 *   trigger          — "ALL" or "ANY"
 *   onConditionsChange — (newArray) => void
 *   onTriggerChange    — (newTrigger) => void
 */

const INDICATOR_PRESETS = [
  { key: 'RSI', label: 'RSI', defaultParams: { period: 14 }, defaultValue: 30, defaultOp: '<', help: '< 30 oversold, > 70 overbought' },
  { key: 'ADX', label: 'ADX (trend strength)', defaultParams: { period: 14 }, defaultValue: 25, defaultOp: '>', help: '> 25 strong trend, < 20 ranging' },
  { key: 'ATR', label: 'ATR (volatility)', defaultParams: { period: 14 }, defaultValue: 50, defaultOp: '>', help: 'Higher = more volatile' },
  { key: 'MACD_HISTOGRAM', label: 'MACD Histogram', defaultParams: {}, defaultValue: 0, defaultOp: '>', help: '> 0 bullish, < 0 bearish' },
  { key: 'VWAP_CROSS', label: 'Price vs VWAP', defaultParams: {}, defaultValue: 0, defaultOp: '>', help: '> 0 above VWAP, < 0 below' },
  { key: 'PRICE_VS_SMA', label: 'Price vs SMA', defaultParams: { period: 20 }, defaultValue: 0, defaultOp: '>', help: '> 0 above SMA' },
  { key: 'SUPERTREND_DIR', label: 'Supertrend Direction', defaultParams: { period: 10, multiplier: 3 }, defaultValue: 'UP', defaultOp: '==', help: 'UP or DOWN' },
  { key: 'BB_BANDWIDTH', label: 'BB Bandwidth', defaultParams: { period: 20 }, defaultValue: 0.05, defaultOp: '>', help: 'Higher = wider bands (more vol)' },
  { key: 'IV_RANK', label: 'IV Rank', defaultParams: {}, defaultValue: 70, defaultOp: '>', help: '> 70 expensive, < 30 cheap' },
  { key: 'IV_PERCENTILE', label: 'IV Percentile', defaultParams: {}, defaultValue: 80, defaultOp: '>', help: '% of historical days IV was lower' },
  { key: 'REGIME', label: 'Market Regime', defaultParams: {}, defaultValue: 'RANGE_BOUND', defaultOp: '==', help: 'HIGH_VOL / LOW_VOL / TRENDING_UP / TRENDING_DOWN / RANGE_BOUND' },
  { key: 'VIX', label: 'India VIX', defaultParams: {}, defaultValue: 15, defaultOp: '<', help: '< 12 low, > 18 high' },
];

const OPERATORS = ['>', '<', '>=', '<=', '==', '!='];

const REGIME_VALUES = ['HIGH_VOL', 'LOW_VOL', 'TRENDING_UP', 'TRENDING_DOWN', 'RANGE_BOUND', 'UNKNOWN'];
const SUPERTREND_VALUES = ['UP', 'DOWN'];

function presetFor(key) {
  return INDICATOR_PRESETS.find((p) => p.key === key) || INDICATOR_PRESETS[0];
}

function liveValueForIndicator(indicators, key, params) {
  if (!indicators) return null;
  switch (key) {
    case 'RSI': return indicators.rsi_14;
    case 'ADX': return indicators.adx_14?.adx ?? null;
    case 'ATR': return indicators.atr_14;
    case 'MACD_HISTOGRAM': return indicators.macd?.histogram;
    case 'VWAP_CROSS':
      if (indicators.vwap != null && indicators.close != null)
        return indicators.close - indicators.vwap;
      return null;
    case 'PRICE_VS_SMA':
      if (indicators.sma_50 != null && indicators.close != null)
        return indicators.close - indicators.sma_50;
      return null;
    case 'SUPERTREND_DIR': return indicators.supertrend_10_3?.direction;
    case 'BB_BANDWIDTH': return indicators.bbands_20?.bandwidth;
    case 'IV_RANK': return indicators.iv_rank;
    case 'IV_PERCENTILE': return indicators.iv_percentile;
    case 'VIX': return indicators.vix;
    default: return null;
  }
}

function fmt(v) {
  if (v == null) return '—';
  if (typeof v === 'string') return v;
  if (Math.abs(v) >= 100) return v.toFixed(0);
  if (Math.abs(v) >= 10) return v.toFixed(1);
  return v.toFixed(2);
}

export default function EntryConditionsBuilder({
  underlying = 'NIFTY',
  conditions = [],
  trigger = 'ALL',
  onConditionsChange = () => {},
  onTriggerChange = () => {},
}) {
  const { data: indicatorsResp } = useIndicators(underlying, 'M5');
  const indicators = indicatorsResp?.indicators || {};
  const regime = indicatorsResp?.regime;
  const evaluator = useEvaluateConditions();

  const addCondition = () => {
    const p = INDICATOR_PRESETS[0];
    onConditionsChange([
      ...conditions,
      { indicator: p.key, ...p.defaultParams, operator: p.defaultOp, value: p.defaultValue },
    ]);
  };

  const removeCondition = (idx) => {
    onConditionsChange(conditions.filter((_, i) => i !== idx));
  };

  const updateCondition = (idx, updates) => {
    onConditionsChange(
      conditions.map((c, i) => (i === idx ? { ...c, ...updates } : c)),
    );
  };

  const handlePresetChange = (idx, key) => {
    const p = presetFor(key);
    updateCondition(idx, {
      indicator: p.key,
      ...p.defaultParams,
      operator: p.defaultOp,
      value: p.defaultValue,
    });
  };

  const runDryEvaluation = async () => {
    if (conditions.length === 0) return;
    try {
      await evaluator.mutateAsync({
        symbol: underlying,
        entry_conditions: conditions,
        entry_trigger: trigger,
        timeframe: 'M5',
      });
    } catch (e) {
      // shown via evaluator.error below
    }
  };

  const evalResult = evaluator.data;

  return (
    <div className="space-y-3">
      {/* ── Header + trigger ── */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-bold text-white flex items-center gap-2">
            <Activity className="w-4 h-4 text-accent" />
            Entry Conditions
          </h3>
          <p className="text-[10px] text-slate-500 mt-0.5">
            Strategy enters only when these conditions are met. Leave empty to enter at schedule time only.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-slate-500 uppercase font-semibold">Combine</span>
          <div className="inline-flex rounded-lg border border-terminal-border overflow-hidden">
            {['ALL', 'ANY'].map((t) => (
              <button
                key={t}
                onClick={() => onTriggerChange(t)}
                className={`px-3 py-1 text-[11px] font-bold transition-colors ${
                  trigger === t
                    ? t === 'ALL'
                      ? 'bg-accent text-white'
                      : 'bg-blue-500 text-white'
                    : 'bg-transparent text-slate-400 hover:text-white'
                }`}
              >
                {t === 'ALL' ? 'AND (all)' : 'OR (any)'}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* ── Live regime banner ── */}
      {regime && (
        <div className="rounded-lg bg-slate-800/40 border border-slate-700/30 px-3 py-2 flex items-center gap-3 text-xs">
          <span className="text-[10px] text-slate-500 uppercase font-semibold">Current regime</span>
          <span className="font-bold text-white">{regime.regime}</span>
          <span className="text-slate-500">·</span>
          <span className="text-slate-400">{regime.recommendation}</span>
          <span className="ml-auto text-[10px] text-slate-500">
            confidence {regime.confidence}%
          </span>
        </div>
      )}

      {/* ── Conditions list ── */}
      <div className="space-y-2">
        {conditions.length === 0 ? (
          <div className="rounded-lg border border-dashed border-slate-700/40 px-3 py-6 text-center">
            <p className="text-xs text-slate-500">No conditions yet. Click "Add Condition" to start.</p>
          </div>
        ) : (
          conditions.map((cond, idx) => {
            const preset = presetFor(cond.indicator);
            const liveValue = liveValueForIndicator(indicators, cond.indicator, cond);
            const isStringTarget = cond.indicator === 'REGIME' || cond.indicator === 'SUPERTREND_DIR';
            const valueOptions = cond.indicator === 'REGIME' ? REGIME_VALUES : SUPERTREND_VALUES;

            return (
              <div
                key={idx}
                className="rounded-lg bg-slate-800/30 border border-slate-700/40 p-2.5 flex flex-wrap items-center gap-2 text-xs"
              >
                {/* Indicator dropdown */}
                <select
                  value={cond.indicator}
                  onChange={(e) => handlePresetChange(idx, e.target.value)}
                  className="bg-terminal-bg border border-terminal-border rounded px-2 py-1 text-xs text-white focus:border-accent focus:outline-none min-w-[160px]"
                >
                  {INDICATOR_PRESETS.map((p) => (
                    <option key={p.key} value={p.key}>{p.label}</option>
                  ))}
                </select>

                {/* Period (only for indicators that take one) */}
                {('period' in (preset.defaultParams || {})) && (
                  <input
                    type="number"
                    value={cond.period ?? preset.defaultParams.period}
                    onChange={(e) => updateCondition(idx, { period: Number(e.target.value) })}
                    className="bg-terminal-bg border border-terminal-border rounded px-2 py-1 text-xs text-white w-16 focus:border-accent focus:outline-none"
                    title="Period"
                  />
                )}

                {/* Operator */}
                <select
                  value={cond.operator}
                  onChange={(e) => updateCondition(idx, { operator: e.target.value })}
                  className="bg-terminal-bg border border-terminal-border rounded px-2 py-1 text-xs font-mono text-white focus:border-accent focus:outline-none"
                >
                  {OPERATORS.map((op) => <option key={op} value={op}>{op}</option>)}
                </select>

                {/* Value */}
                {isStringTarget ? (
                  <select
                    value={cond.value ?? valueOptions[0]}
                    onChange={(e) => updateCondition(idx, { value: e.target.value })}
                    className="bg-terminal-bg border border-terminal-border rounded px-2 py-1 text-xs text-white focus:border-accent focus:outline-none"
                  >
                    {valueOptions.map((v) => <option key={v} value={v}>{v}</option>)}
                  </select>
                ) : (
                  <input
                    type="number"
                    value={cond.value ?? 0}
                    onChange={(e) => updateCondition(idx, { value: Number(e.target.value) })}
                    className="bg-terminal-bg border border-terminal-border rounded px-2 py-1 text-xs font-mono text-white w-20 focus:border-accent focus:outline-none"
                    step="any"
                  />
                )}

                {/* Live value */}
                <div className="ml-auto flex items-center gap-2">
                  <span className="text-[10px] text-slate-500 uppercase font-semibold">live</span>
                  <span className="font-mono font-semibold text-accent text-xs">
                    {fmt(liveValue)}
                  </span>
                  <button
                    onClick={() => removeCondition(idx)}
                    className="text-slate-500 hover:text-loss p-1 rounded"
                    title="Remove condition"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>

                {/* Help text */}
                <div className="basis-full text-[10px] text-slate-500 pl-1 italic">
                  {preset.help}
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* ── Add + dry-run ── */}
      <div className="flex items-center gap-2">
        <button
          onClick={addCondition}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-accent bg-accent/10 hover:bg-accent/20 transition-colors border border-accent/20"
        >
          <Plus className="w-3.5 h-3.5" /> Add Condition
        </button>
        <button
          onClick={runDryEvaluation}
          disabled={conditions.length === 0 || evaluator.isPending}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-white bg-slate-700 hover:bg-slate-600 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {evaluator.isPending ? 'Evaluating…' : 'Would this enter now?'}
        </button>
      </div>

      {/* ── Dry-run result ── */}
      {evalResult && (
        <div
          className={`rounded-lg px-3 py-2.5 text-xs flex items-start gap-2 ${
            evalResult.passed
              ? 'bg-profit/10 border border-profit/30 text-profit'
              : 'bg-yellow-500/10 border border-yellow-500/30 text-yellow-400'
          }`}
        >
          {evalResult.passed ? (
            <Check className="w-4 h-4 mt-0.5 flex-shrink-0" />
          ) : (
            <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
          )}
          <span className="font-mono">{evalResult.summary}</span>
        </div>
      )}
    </div>
  );
}
