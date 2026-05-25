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
  // Only RUNNING strategies in the live view (EXITED and STOPPED are historical
  // and shown in the History tab on the Paper Trading page).
  const activeStrategies = strategies.filter((s) => s.status === 'RUNNING');
  // If compact prop is set, show only the top N strategies (used on Dashboard)
  const displayList = compact ? activeStrategies.slice(0, 3) : activeStrategies;

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

  // Aggregate stats for the compact header
  const totalPnL = activeStrategies.reduce((sum, s) => sum + (Number(s.pnl) || 0), 0);
  const enteredCount = activeStrategies.filter((s) => s.entered).length;
  const waitingCount = activeStrategies.length - enteredCount;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h3 className="text-sm font-bold text-white flex items-center gap-2">
          <Activity className="w-4 h-4 text-accent" />
          Live Strategy P&amp;L
          <span className="text-xs text-slate-500 font-normal">
            ({activeStrategies.length} active{compact && activeStrategies.length > 3 ? `, showing 3 of ${activeStrategies.length}` : ''})
          </span>
        </h3>
        {activeStrategies.length > 0 && (
          <div className="flex items-center gap-3 text-xs">
            <span className="text-slate-500">
              <span className="text-profit font-semibold">{enteredCount}</span> entered
              {' · '}
              <span className="text-yellow-400 font-semibold">{waitingCount}</span> waiting
            </span>
            <span className="text-slate-500">Total P&amp;L</span>
            <span
              className={`font-mono font-bold ${
                totalPnL >= 0 ? 'text-profit' : 'text-loss'
              }`}
            >
              ₹{totalPnL.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
            </span>
          </div>
        )}
      </div>

      <div className={compact ? 'space-y-2' : 'grid grid-cols-1 md:grid-cols-2 gap-3'}>
        {displayList.map((s) => {
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
                  <div className="flex items-center gap-2 text-[10px] text-slate-500 flex-wrap">
                    <span>{s.underlying}</span>
                    <span>·</span>
                    <span>{s.positions?.length || 0} legs</span>
                    <span>·</span>
                    <span
                      className={
                        s.status === 'RUNNING'
                          ? 'text-profit'
                          : s.status === 'EXITED'
                          ? 'text-blue-400'
                          : 'text-slate-400'
                      }
                    >
                      {s.status}
                    </span>
                    {s.entered && s.status === 'RUNNING' && (
                      <>
                        <span>·</span>
                        <span className="text-profit font-semibold">● ENTERED</span>
                      </>
                    )}
                    {!s.entered && s.status === 'RUNNING' && (
                      <>
                        <span>·</span>
                        <span className="text-yellow-400 font-semibold animate-pulse">⏳ WAITING</span>
                      </>
                    )}
                    {s.ai_deployed && (
                      <>
                        <span>·</span>
                        <span className="text-accent font-semibold" title={s.ai_reasoning?.join(' · ') || ''}>
                          🤖 AI {s.ai_confidence ? `${Math.round(s.ai_confidence)}%` : ''}
                        </span>
                      </>
                    )}
                    {s.exit_reason && (
                      <>
                        <span>·</span>
                        <span className="text-loss" title={s.exit_reason}>
                          exit: {s.exit_reason.split(' ')[0]}
                        </span>
                      </>
                    )}
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
