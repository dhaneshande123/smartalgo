import { createContext, useContext, useState } from 'react';

const UnderlyingContext = createContext();

export const UNDERLYINGS = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'SENSEX'];

export function UnderlyingProvider({ children }) {
  const [underlying, setUnderlying] = useState(() => {
    try {
      const saved = localStorage.getItem('smartalgo-underlying');
      return UNDERLYINGS.includes(saved) ? saved : 'NIFTY';
    } catch {
      return 'NIFTY';
    }
  });

  const changeUnderlying = (sym) => {
    if (UNDERLYINGS.includes(sym)) {
      setUnderlying(sym);
      try { localStorage.setItem('smartalgo-underlying', sym); } catch {}
    }
  };

  return (
    <UnderlyingContext.Provider value={{ underlying, setUnderlying: changeUnderlying }}>
      {children}
    </UnderlyingContext.Provider>
  );
}

export const useUnderlying = () => useContext(UnderlyingContext);
