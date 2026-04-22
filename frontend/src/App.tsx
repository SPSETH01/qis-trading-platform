import { useState, useEffect } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, AreaChart, Area } from 'recharts';

const API_URL = 'http://localhost:8000';

// ─── TYPES ────────────────────────────────────────────────────

interface Trade {
  id: number;
  timestamp: string;
  strategy: string;
  symbol: string;
  side: string;
  quantity: number;
  price: number;
  status: string;
  pnl: number;
}

interface Signal {
  id: number;
  name: string;
  strategy: string;
  status: 'triggered' | 'waiting' | 'inactive';
  value: string;
  threshold: string;
}

interface Position {
  symbol: string;
  quantity: number;
  avgPrice: number;
  currentPrice: number;
  pnl: number;
  pnlPct: number;
  strategy: string;
}

// ─── MOCK DATA ────────────────────────────────────────────────

const generateGrowthData = () => {
  const data = [];
  let value = 500;
  const now = new Date();
  for (let i = 30; i >= 0; i--) {
    const date = new Date(now);
    date.setDate(date.getDate() - i);
    value = value * (1 + (Math.random() * 0.04 - 0.01));
    data.push({
      date: date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
      value: parseFloat(value.toFixed(2))
    });
  }
  return data;
};

const MOCK_SIGNALS: Signal[] = [
  { id: 1, name: 'EMA(50) crosses above EMA(200)', strategy: 'Crypto Trend', status: 'triggered', value: 'CROSSED', threshold: 'Cross' },
  { id: 2, name: 'RSI(14) < 35 on BTC', strategy: 'Crypto Trend', status: 'waiting', value: '42.3', threshold: '35' },
  { id: 3, name: 'VIX > 20 bear threshold', strategy: 'Macro Regime', status: 'waiting', value: '17.8', threshold: '20' },
  { id: 4, name: 'SPY below 200 EMA', strategy: 'Macro Regime', status: 'inactive', value: 'Above', threshold: 'Below' },
  { id: 5, name: 'BOTZ 3M momentum positive', strategy: 'Thematic', status: 'triggered', value: '+12.4%', threshold: '>0%' },
  { id: 6, name: 'Volume > 1.5x 20-day avg', strategy: 'Crypto Trend', status: 'triggered', value: '1.8x', threshold: '1.5x' },
];

const MOCK_TRADES: Trade[] = [
  { id: 1, timestamp: '09:35:12', strategy: 'Macro Regime', symbol: 'SPY', side: 'BUY', quantity: 12, price: 521.40, status: 'FILLED', pnl: 0 },
  { id: 2, timestamp: '09:40:08', strategy: 'Thematic', symbol: 'BOTZ', side: 'BUY', quantity: 45, price: 32.18, status: 'FILLED', pnl: 0 },
  { id: 3, timestamp: '12:00:01', strategy: 'Crypto Trend', symbol: 'BTC', side: 'BUY', quantity: 0.002, price: 84320.00, status: 'FILLED', pnl: 124.50 },
  { id: 4, timestamp: '16:00:33', strategy: 'Macro Regime', symbol: 'QQQ', side: 'BUY', quantity: 8, price: 441.20, status: 'FILLED', pnl: 0 },
  { id: 5, timestamp: '20:00:01', strategy: 'Crypto Trend', symbol: 'ETH', side: 'SELL', quantity: 0.05, price: 3210.00, status: 'FILLED', pnl: -18.20 },
];

const MOCK_POSITIONS: Position[] = [
  { symbol: 'SPY', quantity: 12, avgPrice: 521.40, currentPrice: 524.80, pnl: 40.80, pnlPct: 0.65, strategy: 'Macro Regime' },
  { symbol: 'BOTZ', quantity: 45, avgPrice: 32.18, currentPrice: 33.42, pnl: 55.80, pnlPct: 3.85, strategy: 'Thematic' },
  { symbol: 'BTC', quantity: 0.002, avgPrice: 84320.00, currentPrice: 86150.00, pnl: 3.66, pnlPct: 2.17, strategy: 'Crypto Trend' },
  { symbol: 'QQQ', quantity: 8, avgPrice: 441.20, currentPrice: 438.90, pnl: -18.40, pnlPct: -0.52, strategy: 'Macro Regime' },
];

// ─── STYLES ───────────────────────────────────────────────────

const S = {
  app: {
    minHeight: '100vh',
    background: '#03050a',
    color: '#e2e8f0',
    fontFamily: "'DM Sans', sans-serif",
  } as React.CSSProperties,
  header: {
    background: '#0a0e1a',
    borderBottom: '1px solid #1e2a3a',
    padding: '0 24px',
    height: '56px',
    display: 'flex',
    alignItems: 'center',
    gap: '16px',
    position: 'sticky' as const,
    top: 0,
    zIndex: 100,
  },
  logo: {
    fontFamily: "'Space Mono', monospace",
    fontSize: '15px',
    color: '#00e5ff',
    letterSpacing: '0.1em',
    fontWeight: 700,
  },
  logoSpan: { color: '#7c3aed' },
  liveIndicator: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    fontFamily: "'Space Mono', monospace",
    fontSize: '11px',
    color: '#00e676',
    marginLeft: 'auto',
  },
  liveDot: {
    width: '7px',
    height: '7px',
    borderRadius: '50%',
    background: '#00e676',
    boxShadow: '0 0 8px #00e676',
    animation: 'pulse 2s infinite',
  },
  nav: {
    display: 'flex',
    gap: '4px',
    marginLeft: '32px',
  },
  navBtn: (active: boolean) => ({
    padding: '6px 16px',
    borderRadius: '4px',
    border: 'none',
    cursor: 'pointer',
    fontFamily: "'Space Mono', monospace",
    fontSize: '11px',
    fontWeight: 700,
    letterSpacing: '0.06em',
    background: active ? 'rgba(0,229,255,0.1)' : 'transparent',
    color: active ? '#00e5ff' : '#4a5568',
    borderBottom: active ? '2px solid #00e5ff' : '2px solid transparent',
    transition: 'all 0.15s',
  } as React.CSSProperties),
  main: {
    padding: '24px',
    maxWidth: '1400px',
    margin: '0 auto',
    display: 'flex',
    flexDirection: 'column' as const,
    gap: '20px',
  },
  metricGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(5, 1fr)',
    gap: '12px',
  },
  metric: {
    background: '#0a0e1a',
    border: '1px solid #1e2a3a',
    borderRadius: '8px',
    padding: '16px',
  },
  metricLabel: {
    fontFamily: "'Space Mono', monospace",
    fontSize: '10px',
    color: '#4a5568',
    letterSpacing: '0.08em',
    textTransform: 'uppercase' as const,
    marginBottom: '8px',
  },
  metricValue: (color?: string) => ({
    fontFamily: "'Space Mono', monospace",
    fontSize: '22px',
    fontWeight: 700,
    color: color || '#e2e8f0',
  }),
  metricSub: {
    fontFamily: "'Space Mono', monospace",
    fontSize: '11px',
    color: '#4a5568',
    marginTop: '4px',
  },
  card: {
    background: '#0a0e1a',
    border: '1px solid #1e2a3a',
    borderRadius: '8px',
    padding: '20px',
  },
  cardTitle: {
    fontFamily: "'Space Mono', monospace",
    fontSize: '11px',
    color: '#4a5568',
    letterSpacing: '0.1em',
    textTransform: 'uppercase' as const,
    marginBottom: '16px',
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
  },
  badge: (color: string) => ({
    background: color === 'accent' ? 'rgba(0,229,255,0.1)' : 'rgba(124,58,237,0.1)',
    color: color === 'accent' ? '#00e5ff' : '#a78bfa',
    padding: '2px 8px',
    borderRadius: '3px',
    fontSize: '10px',
  }),
  grid2: {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gap: '20px',
  },
  signalRow: (status: string) => ({
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    padding: '10px 14px',
    borderRadius: '6px',
    border: '1px solid #1e2a3a',
    marginBottom: '8px',
    background: '#111827',
    fontFamily: "'Space Mono', monospace",
    fontSize: '12px',
  } as React.CSSProperties),
  sigStatus: (status: string) => ({
    padding: '3px 10px',
    borderRadius: '3px',
    fontSize: '11px',
    fontWeight: 700,
    marginLeft: 'auto',
    background: status === 'triggered' ? 'rgba(0,230,118,0.15)' :
                status === 'waiting' ? 'rgba(255,214,0,0.1)' : 'rgba(74,85,104,0.2)',
    color: status === 'triggered' ? '#00e676' :
           status === 'waiting' ? '#ffd600' : '#4a5568',
  } as React.CSSProperties),
  table: {
    width: '100%',
    borderCollapse: 'collapse' as const,
    fontFamily: "'Space Mono', monospace",
    fontSize: '12px',
  },
  th: {
    textAlign: 'left' as const,
    padding: '8px 12px',
    fontSize: '10px',
    color: '#4a5568',
    borderBottom: '1px solid #1e2a3a',
    letterSpacing: '0.08em',
    textTransform: 'uppercase' as const,
  },
  td: {
    padding: '10px 12px',
    borderBottom: '1px solid rgba(30,42,58,0.5)',
  },
  regime: (regime: string) => ({
    display: 'inline-block',
    padding: '4px 12px',
    borderRadius: '4px',
    fontFamily: "'Space Mono', monospace",
    fontSize: '12px',
    fontWeight: 700,
    background: regime === 'STRONG_BULL' ? 'rgba(0,230,118,0.15)' :
                regime === 'MILD_BULL' ? 'rgba(0,229,255,0.1)' :
                regime === 'NEUTRAL' ? 'rgba(255,214,0,0.1)' :
                regime === 'MILD_BEAR' ? 'rgba(255,152,0,0.1)' :
                'rgba(255,23,68,0.15)',
    color: regime === 'STRONG_BULL' ? '#00e676' :
           regime === 'MILD_BULL' ? '#00e5ff' :
           regime === 'NEUTRAL' ? '#ffd600' :
           regime === 'MILD_BEAR' ? '#ff9800' :
           '#ff1744',
  } as React.CSSProperties),
};

// ─── COMPONENTS ───────────────────────────────────────────────

const Metric = ({ label, value, sub, color }: {
  label: string; value: string; sub?: string; color?: string
}) => (
  <div style={S.metric}>
    <div style={S.metricLabel}>{label}</div>
    <div style={S.metricValue(color)}>{value}</div>
    {sub && <div style={S.metricSub}>{sub}</div>}
  </div>
);

// ─── MAIN APP ─────────────────────────────────────────────────

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard');
  const [growthData] = useState(generateGrowthData());
  const [regime, setRegime] = useState('MILD_BULL');
  const [paperMode] = useState(true);

  const portfolioValue = growthData[growthData.length - 1]?.value || 500;
  const pnl = portfolioValue - 500;
  const pnlPct = ((pnl / 500) * 100).toFixed(2);
  const totalPnl = MOCK_POSITIONS.reduce((sum, p) => sum + p.pnl, 0);

    // ─── LIVE DATA ──────────────────────────────────────────────
  const [livePortfolio, setLivePortfolio] = useState<any>(null);
  const [liveSignals, setLiveSignals] = useState<any[]>([]);
  const [livePositions, setLivePositions] = useState<any[]>([]);
  const [liveRegime, setLiveRegime] = useState<string>('LOADING...');
  const [connected, setConnected] = useState<boolean>(false);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const statusRes = await fetch(`${API_URL}/api/status`);
        const status = await statusRes.json();
        setConnected(status.connected);

        const portfolioRes = await fetch(`${API_URL}/api/portfolio`);
        const portfolio = await portfolioRes.json();
        setLivePortfolio(portfolio);

        const signalsRes = await fetch(`${API_URL}/api/signals`);
        const signals = await signalsRes.json();
        setLiveSignals(signals.signals || []);

        const positionsRes = await fetch(`${API_URL}/api/positions`);
        const positions = await positionsRes.json();
        setLivePositions(positions.positions || []);

        const regimeRes = await fetch(`${API_URL}/api/regime`);
        const regime = await regimeRes.json();
        setLiveRegime(regime.regime || 'UNKNOWN');

      } catch (e) {
        console.error('API fetch error:', e);
      }
    };

    fetchData();
    const interval = setInterval(fetchData, 30000);
    return () => clearInterval(interval);
  }, []);


  return (
    <div style={S.app}>
      {/* Google Fonts */}
      <link
        href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;400;500;600&display=swap"
        rel="stylesheet"
      />
      <style>{`
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
        body { margin: 0; background: #03050a; }
        tr:hover td { background: rgba(0,229,255,0.02); }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-track { background: #0a0e1a; }
        ::-webkit-scrollbar-thumb { background: #1e2a3a; border-radius: 2px; }
      `}</style>

      {/* HEADER */}
      <div style={S.header}>
        <div style={S.logo}>
          QIS<span style={S.logoSpan}>.</span>PLATFORM
        </div>
        {paperMode && (
          <div style={{
            background: 'rgba(255,214,0,0.1)',
            color: '#ffd600',
            padding: '3px 10px',
            borderRadius: '3px',
            fontSize: '10px',
            fontFamily: "'Space Mono', monospace",
            fontWeight: 700,
          }}>
            PAPER TRADING
          </div>
        )}
        <div style={S.nav}>
          {['dashboard', 'signals', 'positions', 'trades', 'risk'].map(tab => (
            <button
              key={tab}
              style={S.navBtn(activeTab === tab)}
              onClick={() => setActiveTab(tab)}
            >
              {tab.toUpperCase()}
            </button>
          ))}
        </div>
        <div style={S.liveIndicator}>
          <div style={S.liveDot} />
          LIVE
        </div>
      </div>

      {/* MAIN CONTENT */}
      <div style={S.main}>

        {/* METRICS */}
        <div style={S.metricGrid}>
          <Metric
            label="Portfolio Value"
            value={`$${portfolioValue.toFixed(2)}`}
            sub="Started $500.00"
            color="#00e5ff"
          />
          <Metric
            label="Total P&L"
            value={`${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}`}
            sub={`${pnlPct}% return`}
            color={pnl >= 0 ? '#00e676' : '#ff1744'}
          />
          <Metric
            label="Open Positions"
            value={`${MOCK_POSITIONS.length}`}
            sub="Across 3 strategies"
          />
          <Metric
            label="Market Regime"
            value={regime}
            sub="Bear score: 2/6"
            color="#00e5ff"
          />
          <Metric
            label="Today's P&L"
            value={`+$${totalPnl.toFixed(2)}`}
            sub="5 trades today"
            color="#00e676"
          />
        </div>

        {/* GROWTH CHART */}
        <div style={S.card}>
          <div style={S.cardTitle}>
            Portfolio Growth
            <span style={S.badge('accent')}>30 DAYS</span>
          </div>
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={growthData}>
              <defs>
                <linearGradient id="colorValue" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#00e5ff" stopOpacity={0.15} />
                  <stop offset="95%" stopColor="#00e5ff" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e2a3a" />
              <XAxis
                dataKey="date"
                stroke="#4a5568"
                tick={{ fontSize: 10, fontFamily: 'Space Mono' }}
                interval={4}
              />
              <YAxis
                stroke="#4a5568"
                tick={{ fontSize: 10, fontFamily: 'Space Mono' }}
                tickFormatter={(v) => `$${v.toFixed(0)}`}
              />
              <Tooltip
                contentStyle={{
                  background: '#0a0e1a',
                  border: '1px solid #1e2a3a',
                  borderRadius: '6px',
                  fontFamily: 'Space Mono',
                  fontSize: '12px',
                }}
                formatter={(value: any) => [`$${Number(value).toFixed(2)}`, 'Portfolio']}
              />
              <Area
                type="monotone"
                dataKey="value"
                stroke="#00e5ff"
                strokeWidth={2}
                fill="url(#colorValue)"
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        {/* SIGNALS + POSITIONS */}
        <div style={S.grid2}>

          {/* SIGNALS */}
          <div style={S.card}>
            <div style={S.cardTitle}>
              Live Signals
              <span style={S.badge('accent')}>
                {MOCK_SIGNALS.filter(s => s.status === 'triggered').length} TRIGGERED
              </span>
            </div>
            {MOCK_SIGNALS.map(signal => (
              <div key={signal.id} style={S.signalRow(signal.status)}>
                <div>
                  <div style={{ color: '#e2e8f0', marginBottom: '2px' }}>
                    {signal.name}
                  </div>
                  <div style={{ fontSize: '10px', color: '#4a5568' }}>
                    {signal.strategy}
                  </div>
                </div>
                <div style={{ marginLeft: 'auto', textAlign: 'right' as const }}>
                  <div style={S.sigStatus(signal.status)}>
                    {signal.status.toUpperCase()}
                  </div>
                  <div style={{
                    fontSize: '10px',
                    color: '#4a5568',
                    marginTop: '4px'
                  }}>
                    {signal.value}
                  </div>
                </div>
              </div>
            ))}
          </div>

          {/* POSITIONS */}
          <div style={S.card}>
            <div style={S.cardTitle}>
              Open Positions
              <span style={S.badge('purple')}>
                {MOCK_POSITIONS.length} OPEN
              </span>
            </div>
            <table style={S.table}>
              <thead>
                <tr>
                  <th style={S.th}>Symbol</th>
                  <th style={S.th}>Qty</th>
                  <th style={S.th}>Avg Price</th>
                  <th style={S.th}>P&L</th>
                  <th style={S.th}>Strategy</th>
                </tr>
              </thead>
              <tbody>
                {MOCK_POSITIONS.map((pos, i) => (
                  <tr key={i}>
                    <td style={{ ...S.td, color: '#00e5ff', fontWeight: 700 }}>
                      {pos.symbol}
                    </td>
                    <td style={S.td}>{pos.quantity}</td>
                    <td style={S.td}>${pos.avgPrice.toFixed(2)}</td>
                    <td style={{
                      ...S.td,
                      color: pos.pnl >= 0 ? '#00e676' : '#ff1744',
                      fontWeight: 700
                    }}>
                      {pos.pnl >= 0 ? '+' : ''}${pos.pnl.toFixed(2)}
                      <span style={{ fontSize: '10px', marginLeft: '4px' }}>
                        ({pos.pnlPct >= 0 ? '+' : ''}{pos.pnlPct}%)
                      </span>
                    </td>
                    <td style={{ ...S.td, color: '#4a5568', fontSize: '10px' }}>
                      {pos.strategy}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* TRADE BLOTTER */}
        <div style={S.card}>
          <div style={S.cardTitle}>
            Trade Blotter
            <span style={S.badge('accent')}>{MOCK_TRADES.length} TODAY</span>
          </div>
          <table style={S.table}>
            <thead>
              <tr>
                <th style={S.th}>Time</th>
                <th style={S.th}>Strategy</th>
                <th style={S.th}>Symbol</th>
                <th style={S.th}>Side</th>
                <th style={S.th}>Qty</th>
                <th style={S.th}>Price</th>
                <th style={S.th}>Value</th>
                <th style={S.th}>P&L</th>
                <th style={S.th}>Status</th>
              </tr>
            </thead>
            <tbody>
              {MOCK_TRADES.map((trade) => (
                <tr key={trade.id}>
                  <td style={{ ...S.td, color: '#4a5568' }}>{trade.timestamp}</td>
                  <td style={{ ...S.td, fontSize: '10px', color: '#4a5568' }}>
                    {trade.strategy}
                  </td>
                  <td style={{ ...S.td, color: '#00e5ff', fontWeight: 700 }}>
                    {trade.symbol}
                  </td>
                  <td style={{
                    ...S.td,
                    color: trade.side === 'BUY' ? '#00e676' : '#ff1744',
                    fontWeight: 700
                  }}>
                    {trade.side}
                  </td>
                  <td style={S.td}>{trade.quantity}</td>
                  <td style={S.td}>${trade.price.toLocaleString()}</td>
                  <td style={S.td}>
                    ${(trade.quantity * trade.price).toFixed(2)}
                  </td>
                  <td style={{
                    ...S.td,
                    color: trade.pnl >= 0 ? '#00e676' : '#ff1744',
                    fontWeight: 700
                  }}>
                    {trade.pnl !== 0
                      ? `${trade.pnl >= 0 ? '+' : ''}$${trade.pnl.toFixed(2)}`
                      : '—'
                    }
                  </td>
                  <td style={{
                    ...S.td,
                    color: '#00e676',
                    fontSize: '11px',
                    fontWeight: 700
                  }}>
                    {trade.status}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* RISK PANEL */}
        <div style={S.card}>
          <div style={S.cardTitle}>
            Risk Monitor
            <span style={S.badge('accent')}>KILL SWITCH ARMED</span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: '12px' }}>
            {[
              { label: 'Max Drawdown Limit', value: '15.0%', color: '#ffd600' },
              { label: 'Current Drawdown', value: '0.0%', color: '#00e676' },
              { label: 'Risk Per Trade', value: '2.0%', color: '#00e5ff' },
              { label: 'Kill Switch', value: 'ARMED', color: '#00e676' },
            ].map((item, i) => (
              <div key={i} style={{
                background: '#111827',
                border: '1px solid #1e2a3a',
                borderRadius: '6px',
                padding: '14px 16px',
              }}>
                <div style={S.metricLabel}>{item.label}</div>
                <div style={S.metricValue(item.color)}>{item.value}</div>
              </div>
            ))}
          </div>
        </div>

      </div>
    </div>
  );
}