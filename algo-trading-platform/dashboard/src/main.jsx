import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider } from './context/ThemeContext';
import { UnderlyingProvider } from './context/UnderlyingContext';
import App from './App';
import './index.css';
import './styles/scifi-theme.css';
import './styles/pro-theme.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchInterval: 5000,
      refetchIntervalInBackground: false,
      retry: 2,
      staleTime: 2000,
    },
  },
});

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ThemeProvider>
      <UnderlyingProvider>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </QueryClientProvider>
      </UnderlyingProvider>
    </ThemeProvider>
  </React.StrictMode>
);
