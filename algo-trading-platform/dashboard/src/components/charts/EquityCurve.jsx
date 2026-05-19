import React from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Area, AreaChart,
} from 'recharts';

const defaultData = Array.from({ length: 30 }, (_, i) => ({
  date: new Date(Date.now() - (29 - i) * 86400000).toISOString().slice(0, 10),
  equity: 1000000 + Math.random() * 50000 * Math.sin(i / 5) + i * 2000,
}));

export default function EquityCurve({ data = defaultData, height = 250 }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 5, right: 5, bottom: 5, left: 10 }}>
        <defs>
          <linearGradient id="equityGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
            <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#1e2433" />
        <XAxis
          dataKey="date"
          tick={{ fill: '#64748b', fontSize: 10 }}
          tickFormatter={(v) => {
            try {
              const d = new Date(v);
              const day = String(d.getDate()).padStart(2, '0');
              const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
              return `${day} ${months[d.getMonth()]}`;
            } catch { return v; }
          }}
          stroke="#1e2433"
        />
        <YAxis
          tick={{ fill: '#64748b', fontSize: 10 }}
          tickFormatter={(v) => `${(v / 100000).toFixed(1)}L`}
          stroke="#1e2433"
        />
        <Tooltip
          contentStyle={{ backgroundColor: '#131720', border: '1px solid #1e2433', borderRadius: '8px', fontSize: 12 }}
          labelStyle={{ color: '#94a3b8' }}
          formatter={(value) => [`Rs ${(value ?? 0).toLocaleString('en-IN')}`, 'Equity']}
        />
        <Area
          type="monotone"
          dataKey="equity"
          stroke="#3b82f6"
          strokeWidth={2}
          fill="url(#equityGradient)"
          dot={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
