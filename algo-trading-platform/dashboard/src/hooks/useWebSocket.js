import { useEffect, useRef, useState, useCallback } from 'react';

/**
 * Custom hook for a single WebSocket connection with auto-reconnect.
 * Throttles state updates to avoid flooding React.
 */
export function useWebSocket(path, { onMessage, enabled = true } = {}) {
  const wsRef = useRef(null);
  const reconnectTimer = useRef(null);
  const onMessageRef = useRef(onMessage);
  const [status, setStatus] = useState('disconnected');
  const mountedRef = useRef(true);

  useEffect(() => { onMessageRef.current = onMessage; }, [onMessage]);

  useEffect(() => {
    if (!enabled) return;
    mountedRef.current = true;

    function connect() {
      if (!mountedRef.current) return;
      if (wsRef.current) { try { wsRef.current.close(); } catch {} }

      try {
        // Connect WS directly to the backend API server (port 8080)
        // Vite HTTP proxy doesn't handle WebSocket upgrade from browser WS API
        const wsHost = window.location.hostname + ':8080';
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const ws = new WebSocket(`${protocol}//${wsHost}${path}`);
        wsRef.current = ws;

        ws.onopen = () => { if (mountedRef.current) setStatus('connected'); };
        ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            if (data.type === 'ping') { ws.send('{"type":"pong"}'); return; }
            onMessageRef.current?.(data);
          } catch {}
        };
        ws.onclose = () => {
          if (mountedRef.current) {
            setStatus('disconnected');
            wsRef.current = null;
            reconnectTimer.current = setTimeout(connect, 500);
          }
        };
        ws.onerror = () => { ws.close(); };
      } catch {
        if (mountedRef.current) setStatus('error');
      }
    }

    connect();
    return () => {
      mountedRef.current = false;
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      if (wsRef.current) { try { wsRef.current.close(); } catch {} }
    };
  }, [path, enabled]);

  return { status };
}

/**
 * Hook for streaming market data ticks with throttled updates.
 * Batches tick updates and flushes to state every 800ms.
 */
export function useMarketDataStream(symbols = 'NIFTY,BANKNIFTY,FINNIFTY,MIDCPNIFTY') {
  const [ticks, setTicks] = useState({});
  const bufferRef = useRef({});
  const timerRef = useRef(null);

  const onMessage = useCallback((msg) => {
    if (msg.channel === 'ticks' && msg.data) {
      bufferRef.current[msg.data.symbol] = msg.data;
    }
  }, []);

  // Flush buffer to state every 200ms for near-instant UI updates
  useEffect(() => {
    timerRef.current = setInterval(() => {
      const buf = bufferRef.current;
      if (Object.keys(buf).length > 0) {
        setTicks((prev) => ({ ...prev, ...buf }));
        bufferRef.current = {};
      }
    }, 200);
    return () => clearInterval(timerRef.current);
  }, []);

  const { status } = useWebSocket(`/ws/market-data?symbols=${symbols}`, { onMessage });
  return { ticks, status };
}

/**
 * Hook for streaming portfolio updates (every 2s from server).
 */
export function usePortfolioStream() {
  const [portfolio, setPortfolio] = useState(null);
  const onMessage = useCallback((msg) => {
    if (msg.channel === 'pnl' && msg.data) setPortfolio(msg.data);
  }, []);
  const { status } = useWebSocket('/ws/portfolio', { onMessage });
  return { portfolio, status };
}

/**
 * Hook for streaming order updates (every 3-5s from server).
 */
export function useOrderStream() {
  const [orders, setOrders] = useState([]);
  const onMessage = useCallback((msg) => {
    if (msg.channel === 'orders' && msg.data) {
      setOrders((prev) => [msg.data, ...prev].slice(0, 50));
    }
  }, []);
  const { status } = useWebSocket('/ws/orders', { onMessage });
  return { orders, status };
}

/**
 * Hook for streaming alerts (every 5-15s from server).
 */
export function useAlertStream() {
  const [alerts, setAlerts] = useState([]);
  const onMessage = useCallback((msg) => {
    if (msg.channel === 'alerts' && msg.data) {
      setAlerts((prev) => [msg.data, ...prev].slice(0, 30));
    }
  }, []);
  const { status } = useWebSocket('/ws/alerts', { onMessage });
  return { alerts, status };
}
