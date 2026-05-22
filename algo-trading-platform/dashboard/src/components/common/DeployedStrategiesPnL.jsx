import React from 'react';
import { TrendingUp, TrendingDown, Activity, Square, Layers } from 'lucide-react';
import { useDeployedStrategies, useStopDeployedStrategy } from '../../hooks/useApi';
import { useToast } from './ToastProvider';

/**
 * Live P&L display for all currently deployed strategies.
 *
 * Refreshes every second from /api/strategies/deployed.
 * Each card shows the strategy name, mode (PAPER/LIVE), running P&L,
 * per-leg positions, and a Stop button.
 */
export default function DeployedStrategiesPnL({ compact = false }) {
  const toast = useToast();
  const { data } = useDeployedStrategies();
  const stopMutation = useStopDeployedStrategy();

  const strategies = data?.strategies || [];
  const activeStrategies = strategies.filter((s) => s.status !== 'STOPPED');

  if (activeStrategies.length === 0) {
    return (
      <div className="rounded-xl border border-slate-700/30 bg-slate-800/20 p-5 text-center">
        <Layers className="w-8 h-8 text-slate-600 mx-auto mb-2" />
        <p className="text-sm text-slate-500 font-medium">No strategies currently deployed</p>
        <p className="text-xs text-slate-600 mt-1">
          Deploy a strategy above to see its live P&amp;L here.
        </p>
      </div>
    );
  }

  const handleStop = async (sid, name) => {
    try {
      await stopMutation.mutateAsync(sid);
      toast?.addToast?.({ level: 'info', message: `Stopped: ${name}`, source: 'strategies' });
    } catch (e) {
      toast?.addToast?.({
        level: 'CRITICAL',
        message: `Stop failed: ${e?.response?.data?.detail || e.message}`,
        source: 'strategies',
      });
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-bold text-white flex items-center gap-2">
          <Activity className="w-4 h-4 text-accent" />
          Live Strategy P&amp;L
          <span className="text-xs text-slate-500 font-normal">
            ({activeStrategies.length} active)
          </span>
        </h3>
      </div>

      <div className={compact ? 'space-y-2' : 'grid grid-cols-1 md:grid-cols-2 gap-3'}>
        {activeStrategies.map((s) => {
          const pnl = Number(s.pnl) || 0;
          const isProfit = pnl >= 0;
          const isLive = (s.mode || '').toLowerCase() === 'live';

          return (
            <div
              key={s.strategy_id}
              className="rounded-xl border border-slate-700/40 bg-slate-800/30 p-4 hover:border-slate-600/60 transition-colors"
            >
              <div className="flex items-start justify-between mb-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <h4 className="text-sm font-bold text-white truncate">{s.name}</h4>
                    <span
                      className={`text-[9px] font-bold uppercase px-1.5 py-0.5 rounded ${
                        isLive
                          ? 'bg-loss/15 text-loss border border-loss/30'
                          : 'bg-accent/15 text-accent border border-accent/30'
                      }`}
                    >
                      {s.mode || 'paper'}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 text-[10px] text-slate-500">
                    <span>{s.underlying}</span>
                    <span>·</span>
                    <span>{s.positions?.length || 0} legs</span>
                    <span>·</span>
                    <span className={s.status === 'RUNNING' ? 'text-profit' : 'text-slate-400'}>
                      {s.status}
                    </span>
                  </div>
                </div>
                <button
                  onClick={() => handleStop(s.strategy_id, s.name)}
                  disabled={stopMutation.isPending}
                  className="flex items-center justify-center w-7 h-7 rounded-lg bg-loss/10 hover:bg-loss/20 text-loss transition-colors"
                  title="Stop strategy"
                >
                  <Square className="w-3.5 h-3.5" />
                </button>
              </div>

              {/* P&L numbers */}
              <div className="grid grid-cols-2 gap-2 mb-3">
                <div className="rounded-lg bg-slate-900/40 px-2.5 py-1.5">
                  <div className="text-[9px] text-slate-500 uppercase font-semibold">Net P&amp;L</div>
                  <div
                    className={`text-base font-bold flex items-center gap-1 ${
                      isProfit ? 'text-profit' : 'text-loss'
                    }`}
                  >
                    {isProfit ? (
                      <TrendingUp className="w-3.5 h-3.5" />
                    ) : (
                      <TrendingDown className="w-3.5 h-3.5" />
                    )}
                    ₹{pnl.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
                  </div>
                </div>
                <div className="rounded-lg bg-slate-900/40 px-2.5 py-1.5">
                  <div className="text-[9px] text-slate-500 uppercase font-semibold">Unrealized</div>
                  <div
                    className={`text-base font-bold ${
                      (s.unrealized_pnl || 0) >= 0 ? 'text-profit' : 'text-loss'
                    }`}
                  >
                    ₹
                    {Number(s.unrealized_pnl || 0).toLocaleString('en-IN', {
                      maximumFractionDigits: 2,
                    })}
                  </div>
                </div>
              </div>

              {/* Per-leg breakdown */}
              {s.positions && s.positions.length > 0 && (
                <div className="space-y-1">
                  {s.positions.map((p, i) => {
                    const legPnl = Number(p.pnl) || 0;
                    const legProfit = legPnl >= 0;
                    return (
                      <div
                        key={i}
                        className="flex items-center justify-between text-[11px] py-1 px-2 rounded bg-slate-900/30"
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <span
                            className={`text-[9px] font-bold px-1 rounded ${
                              p.side === 'BUY'
                                ? 'bg-profit/15 text-profit'
                                : 'bg-loss/15 text-loss'
                            }`}
                          >
                            {p.side}
                          </span>
                          <span className="text-slate-300 font-mono truncate">{p.symbol}</span>
                          <span className="text-slate-500">×{p.qty}</span>
                        </div>
                        <div className="flex items-center gap-3 flex-shrink-0">
                          <span className="text-slate-400 font-mono">
                            ₹{Number(p.ltp || 0).toFixed(2)}
                          </span>
                          <span
                            className={`font-mono font-semibold ${
                              legProfit ? 'text-profit' : 'text-loss'
                            }`}
                          >
                            {legProfit ? '+' : ''}
                            {legPnl.toFixed(0)}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
