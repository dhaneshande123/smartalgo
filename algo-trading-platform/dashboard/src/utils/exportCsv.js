export function downloadCsv(rows, filename) {
  if (!rows || rows.length === 0) return;

  const headers = Object.keys(rows[0]);
  const csvLines = [headers.join(',')];

  for (const row of rows) {
    const values = headers.map(h => {
      const val = row[h];
      if (val === null || val === undefined) return '';
      const str = String(val);
      if (str.includes(',') || str.includes('"') || str.includes('\n'))
        return `"${str.replace(/"/g, '""')}"`;
      return str;
    });
    csvLines.push(values.join(','));
  }

  const blob = new Blob([csvLines.join('\n')], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export function formatTradesForExport(trades) {
  return trades.map(t => ({
    Date: t.timestamp || t.time || t.orderDateTime || '',
    Strategy: t.strategy_id || t.strategy || '',
    Symbol: t.symbol || '',
    Side: t.side || (t.action === 'entry' ? 'BUY' : 'SELL'),
    Action: t.action || '',
    Qty: t.qty || t.quantity || '',
    Price: t.price || '',
    Reason: t.reason || '',
  }));
}

export function formatPnLForExport(strategies) {
  return strategies.map(s => ({
    Strategy: s.strategy_class || s.name || s.id || '',
    Status: s.status || '',
    'Net P&L': s.pnl ?? s.net_pnl ?? '',
    'Gross P&L': s.gross_pnl ?? '',
    Charges: s.total_charges ?? '',
    'Entry Price': s.entry_price ?? '',
    'Exit Price': s.exit_price ?? '',
    Symbol: s.symbol || '',
    Side: s.side || '',
    Lots: s.lots ?? s.lot_size ?? '',
    'Entry Time': s.entry_time || s.created_at || '',
    'Exit Time': s.exit_time || '',
  }));
}

export function formatBacktestForExport(result) {
  if (!result) return [];
  return [{
    Strategy: result.strategy || '',
    'Net Return %': result.net_return_pct ?? '',
    'Total Return %': result.total_return_pct ?? '',
    Sharpe: result.sharpe_ratio ?? '',
    Sortino: result.sortino_ratio ?? '',
    'Max Drawdown %': result.max_drawdown_pct ?? '',
    'Win Rate %': result.win_rate ?? '',
    'Profit Factor': result.profit_factor ?? '',
    Trades: result.total_trades ?? '',
    'Avg Trade P&L': result.avg_trade_pnl ?? '',
    'Total Charges': result.total_charges ?? '',
    Calmar: result.calmar_ratio ?? '',
    'Candles Used': result.candles_count ?? '',
    Params: JSON.stringify(result.params_used || {}),
  }];
}

export function formatEquityCurveForExport(curve) {
  if (!curve || curve.length === 0) return [];
  if (Array.isArray(curve[0])) {
    return curve.map(([date, equity]) => ({ Date: date, Equity: equity }));
  }
  return curve.map(p => ({ Date: p.date, Equity: p.equity }));
}
