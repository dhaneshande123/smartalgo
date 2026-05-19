/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    screens: {
      'xs': '480px',
      'sm': '640px',
      'md': '768px',
      'lg': '1024px',
      'xl': '1280px',
      '2xl': '1536px',
    },
    extend: {
      colors: {
        profit: '#22c55e',
        loss: '#ef4444',
        accent: {
          DEFAULT: '#7c3aed',
          light: '#a855f7',
          dark: '#6d28d9',
        },
        terminal: {
          bg: '#0b0e14',
          card: '#131720',
          border: '#1e2433',
          hover: '#1a2030',
        },
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      animation: {
        'fade-in': 'fadeIn 0.35s ease-out',
        'slide-up': 'slideUp 0.35s ease-out both',
        'glow-pulse': 'glowPulse 2.5s ease-in-out infinite',
        'card-reveal': 'cardReveal 0.45s cubic-bezier(0.16, 1, 0.3, 1) both',
        'page-enter': 'pageFadeIn 0.4s cubic-bezier(0.16, 1, 0.3, 1) both',
        'number-pop': 'numberCountUp 0.3s ease-out both',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%': { opacity: '0', transform: 'translateY(10px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        glowPulse: {
          '0%, 100%': { boxShadow: '0 0 15px rgba(124, 58, 237, 0.15)' },
          '50%': { boxShadow: '0 0 30px rgba(124, 58, 237, 0.3)' },
        },
        cardReveal: {
          '0%': { opacity: '0', transform: 'translateY(12px) scale(0.98)' },
          '100%': { opacity: '1', transform: 'translateY(0) scale(1)' },
        },
        pageFadeIn: {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        numberCountUp: {
          '0%': { opacity: '0', transform: 'translateY(6px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
    },
  },
  plugins: [],
};
