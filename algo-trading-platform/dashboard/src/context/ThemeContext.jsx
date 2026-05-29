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

  // Apply/remove scifi class on <html>
  useEffect(() => {
    const root = document.documentElement;
    if (uiStyle === 'scifi') {
      root.classList.add('scifi');
      // Sci-fi forces dark mode for best visuals
      if (theme !== 'dark') {
        setTheme('dark');
      }
    } else {
      root.classList.remove('scifi');
    }
    try {
      localStorage.setItem('smartalgo-ui-style', uiStyle);
    } catch {}
  }, [uiStyle]);

  const toggleTheme = () => {
    // Don't allow light mode while scifi is active
    if (uiStyle === 'scifi') return;
    setTheme(prev => (prev === 'dark' ? 'light' : 'dark'));
  };

  const toggleUiStyle = () => setUiStyle(prev => (prev === 'classic' ? 'scifi' : 'classic'));

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme, uiStyle, toggleUiStyle }}>
      {children}
    </ThemeContext.Provider>
  );
}

export const useTheme = () => useContext(ThemeContext);
