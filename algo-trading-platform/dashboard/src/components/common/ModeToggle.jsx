import React, { useState } from 'react';
import { ShieldCheck, Zap, AlertTriangle, X } from 'lucide-react';
import { useTradingMode, useSetTradingMode } from '../../hooks/useApi';
import { useToast } from './ToastProvider';

/**
 * Global Live/Paper trading mode toggle.
 *
 * Sits in the header. When in PAPER mode the pill is blue; when in LIVE mode
 * it is red and pulses. Switching from PAPER -> LIVE shows a confirmation
 * modal because LIVE orders hit real money via the Fyers gateway.
 */
export default function ModeToggle() {
  const toast = useToast();
  const { data: modeData } = useTradingMode();
  const setMode = useSetTradingMode();
  const [showConfirm, setShowConfirm] = useState(false);

  const currentMode = modeData?.mode || 'paper';
  const fyersConnected = modeData?.fyers_connected;
  const isLive = currentMode === 'live';

  const switchToPaper = async () => {
    try {
      await setMode.mutateAsync({ mode: 'paper', confirm: false });
      toast?.addToast?.({ level: 'info', message: 'Switched to PAPER mode', source: 'mode' });
    } catch (e) {
      toast?.addToast?.({
        level: 'CRITICAL',
        message: `Mode switch failed: ${e?.response?.data?.detail || e.message}`,
        source: 'mode',
      });
    }
  };

  const switchToLive = async () => {
    setShowConfirm(false);
    if (!fyersConnected) {
      toast?.addToast?.({
        level: 'CRITICAL',
        message: 'Cannot switch to LIVE — Fyers gateway is not connected',
        source: 'mode',
      });
      return;
    }
    try {
      await setMode.mutateAsync({ mode: 'live', confirm: true });
      toast?.addToast?.({
        level: 'warning',
        message: 'LIVE mode active — strategies will place real orders',
        source: 'mode',
      });
    } catch (e) {
      toast?.addToast?.({
        level: 'CRITICAL',
        message: `Mode switch failed: ${e?.response?.data?.detail || e.message}`,
        source: 'mode',
      });
    }
  };

  const handleClick = () => {
    if (isLive) {
      switchToPaper();
    } else {
      setShowConfirm(true);
    }
  };

  return (
    <>
      <button
        onClick={handleClick}
        disabled={setMode.isPending}
        title={
          isLive
            ? 'Click to switch to PAPER trading (safe simulation)'
            : 'Click to switch to LIVE trading (real orders)'
        }
        className={`flex items-center gap-1.5 px-3 py-1 rounded-full text-[11px] font-bold uppercase tracking-wide transition-all border ${
          isLive
            ? 'bg-loss/15 text-loss border-loss/40 hover:bg-loss/25 animate-pulse'
            : 'bg-accent/15 text-accent border-accent/40 hover:bg-accent/25'
        } ${setMode.isPending ? 'opacity-50 cursor-wait' : 'cursor-pointer'}`}
      >
        {isLive ? <Zap className="w-3.5 h-3.5" /> : <ShieldCheck className="w-3.5 h-3.5" />}
        <span>{isLive ? 'LIVE' : 'PAPER'}</span>
      </button>

      {/* Confirmation modal for PAPER -> LIVE switch */}
      {showConfirm && (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 backdrop-blur-sm animate-fade-in"
          onClick={() => setShowConfirm(false)}
        >
          <div
            className="relative bg-terminal-bg border border-loss/40 rounded-2xl p-6 max-w-md w-[90vw] shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={() => setShowConfirm(false)}
              className="absolute top-3 right-3 text-slate-500 hover:text-white p-1"
            >
              <X className="w-4 h-4" />
            </button>

            <div className="flex items-center gap-3 mb-4">
              <div className="w-12 h-12 rounded-2xl bg-loss/20 border border-loss/40 flex items-center justify-center">
                <AlertTriangle className="w-6 h-6 text-loss" />
              </div>
              <div>
                <h3 className="text-base font-bold text-white">Switch to LIVE trading?</h3>
                <p className="text-xs text-slate-400">All orders will hit your real Fyers account</p>
              </div>
            </div>

            <ul className="text-xs text-slate-300 space-y-1.5 mb-5 pl-4 list-disc">
              <li>Strategies deployed after switching will place <b>real orders</b></li>
              <li>Real money will be debited from your linked Fyers account</li>
              <li>You can switch back to PAPER mode at any time</li>
              <li>
                Fyers gateway is currently{' '}
                <span className={fyersConnected ? 'text-profit' : 'text-loss'}>
                  {fyersConnected ? 'connected ✓' : 'NOT connected ✗'}
                </span>
              </li>
            </ul>

            <div className="flex gap-2">
              <button
                onClick={() => setShowConfirm(false)}
                className="flex-1 px-4 py-2.5 rounded-lg text-sm font-semibold text-slate-300 bg-white/[0.06] hover:bg-white/[0.1] transition-colors"
              >
                Cancel — stay in PAPER
              </button>
              <button
                onClick={switchToLive}
                disabled={!fyersConnected || setMode.isPending}
                className="flex-1 px-4 py-2.5 rounded-lg text-sm font-bold text-white bg-loss hover:bg-loss/90 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                Yes, go LIVE
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
