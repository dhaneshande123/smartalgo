import React, { createContext, useContext, useState, useEffect } from 'react';

const ThemeContext = createContext();

export function ThemeProvider({ children }) {
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem('smartalgo-theme') || 'dark';
    } catch {
      return 'dark';
    }
  });

  // UI style: 'classic' or 'scifi'
  const [uiStyle, setUiStyle] = useState(() => {
    try {
      return localStorage.getItem('smartalgo-ui-style') || 'classic';
    } catch {
      return 'classic';
    }
  });

  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'dark') {
      root.classList.add('dark');
      root.classList.remove('light');
    } else {
      root.classList.add('light');
      root.classList.remove('dark');
    }
    try {
      localStorage.setItem('smartalgo-theme', theme);
    } catch {}
  }, [theme]);

  // Apply/remove scifi and pro classes on <html>
  useEffect(() => {
    const root = document.documentElement;
    root.classList.remove('scifi', 'pro');
    if (uiStyle === 'scifi') {
      root.classList.add('scifi');
    } else if (uiStyle === 'pro') {
      root.classList.add('pro');
    }
    // Both scifi and pro force dark mode
    if ((uiStyle === 'scifi' || uiStyle === 'pro') && theme !== 'dark') {
      setTheme('dark');
    }
    try {
      localStorage.setItem('smartalgo-ui-style', uiStyle);
    } catch {}
  }, [uiStyle]);

  const toggleTheme = () => {
    if (uiStyle === 'scifi' || uiStyle === 'pro') return;
    setTheme(prev => (prev === 'dark' ? 'light' : 'dark'));
  };

  const toggleUiStyle = () => setUiStyle(prev => (prev === 'classic' ? 'scifi' : 'classic'));
  const toggleProStyle = () => setUiStyle(prev => (prev === 'pro' ? 'classic' : 'pro'));

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme, uiStyle, toggleUiStyle, toggleProStyle }}>
      {children}
    </ThemeContext.Provider>
  );
}

export const useTheme = () => useContext(ThemeContext);
