/* eslint-disable @typescript-eslint/no-unused-vars */
import { useState, useEffect, useCallback } from 'react';
import { XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, AreaChart, Area } from 'recharts';

const API_URL = 'https://qis-trading.com';

// ─── TYPES ────────────────────────────────────────────────────

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
  avg_price: number;
  market_value: number;
  unrealized_pnl: number;
  realized_pnl: number;
  currency: string;
}

interface Portfolio {
  portfolio_value: number;
  starting_capital: number;
  pnl: number;
  pnl_pct: number;
  peak_value: number;
  drawdown_pct: number;
  timestamp: string;
}

interface Regime {
  regime: string;
  bear_score: number;
  vix: number | null;
  timestamp: string;
}

interface SchedulerJob {
  id: string;
  name: string;
  next_run: string | null;
  paused: boolean;
}

interface SchedulerStatus {
  running: boolean;
  paused: boolean;
  error_count: number;
  last_run: Record<string, string>;
  last_error: Record<string, string>;
  last_signal_check: string | null;
  last_drawdown_check: string | null;
  current_signals: Record<string, string>;
  drawdown_status: {
    portfolio_drawdown_pct: number;
    kill_switch_fired: boolean;
    warnings: string[];
    position_alerts: string[];
  };
  jobs: SchedulerJob[];
}

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
  liveIndicator: (connected: boolean) => ({
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    fontFamily: "'Space Mono', monospace",
    fontSize: '11px',
    color: connected ? '#00e676' : '#ff1744',
    marginLeft: 'auto',
  }),
  liveDot: (connected: boolean) => ({
    width: '7px',
    height: '7px',
    borderRadius: '50%',
    background: connected ? '#00e676' : '#ff1744',
    boxShadow: connected ? '0 0 8px #00e676' : '0 0 8px #ff1744',
    animation: 'pulse 2s infinite',
  }),
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
    background: color === 'accent' ? 'rgba(0,229,255,0.1)' :
                color === 'green' ? 'rgba(0,230,118,0.1)' :
                color === 'red'   ? 'rgba(255,23,68,0.1)' :
                color === 'yellow'? 'rgba(255,214,0,0.1)' :
                'rgba(124,58,237,0.1)',
    color: color === 'accent' ? '#00e5ff' :
           color === 'green'  ? '#00e676' :
           color === 'red'    ? '#ff1744' :
           color === 'yellow' ? '#ffd600' :
           '#a78bfa',
    padding: '2px 8px',
    borderRadius: '3px',
    fontSize: '10px',
    fontFamily: "'Space Mono', monospace",
    fontWeight: 700,
  }),
  grid2: {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gap: '20px',
  },
  signalRow: {
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
  } as React.CSSProperties,
  sigStatus: (status: string) => ({
    padding: '3px 10px',
    borderRadius: '3px',
    fontSize: '11px',
    fontWeight: 700,
    marginLeft: 'auto',
    background: status === 'triggered' ? 'rgba(0,230,118,0.15)' :
                status === 'waiting'   ? 'rgba(255,214,0,0.1)' :
                'rgba(74,85,104,0.2)',
    color: status === 'triggered' ? '#00e676' :
           status === 'waiting'   ? '#ffd600' : '#4a5568',
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
                regime === 'MILD_BULL'   ? 'rgba(0,229,255,0.1)' :
                regime === 'NEUTRAL'     ? 'rgba(255,214,0,0.1)' :
                regime === 'MILD_BEAR'   ? 'rgba(255,152,0,0.1)' :
                'rgba(255,23,68,0.15)',
    color: regime === 'STRONG_BULL' ? '#00e676' :
           regime === 'MILD_BULL'   ? '#00e5ff' :
           regime === 'NEUTRAL'     ? '#ffd600' :
           regime === 'MILD_BEAR'   ? '#ff9800' :
           '#ff1744',
  } as React.CSSProperties),
  btn: (color: string) => ({
    padding: '8px 16px',
    borderRadius: '4px',
    border: 'none',
    cursor: 'pointer',
    fontFamily: "'Space Mono', monospace",
    fontSize: '11px',
    fontWeight: 700,
    letterSpacing: '0.06em',
    background: color === 'accent' ? 'rgba(0,229,255,0.15)' :
                color === 'red'    ? 'rgba(255,23,68,0.15)' :
                color === 'green'  ? 'rgba(0,230,118,0.15)' :
                'rgba(124,58,237,0.15)',
    color: color === 'accent' ? '#00e5ff' :
           color === 'red'    ? '#ff1744' :
           color === 'green'  ? '#00e676' :
           '#a78bfa',
    transition: 'all 0.15s',
  } as React.CSSProperties),
  jobRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '10px 14px',
    borderRadius: '6px',
    border: '1px solid #1e2a3a',
    marginBottom: '8px',
    background: '#111827',
    fontFamily: "'Space Mono', monospace",
    fontSize: '12px',
  } as React.CSSProperties,
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

const fmt = (n: number, decimals = 2) => n.toFixed(decimals);
const fmtUSD = (n: number) => `$${Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const fmtTime = (iso: string) => iso ? new Date(iso).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—';

// ─── MAIN APP ─────────────────────────────────────────────────

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard');
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [lastUpdate, setLastUpdate] = useState<string>('—');
  const [runningStrategy, setRunningStrategy] = useState<string | null>(null);

  // Live data state
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [regime, setRegime] = useState<Regime | null>(null);
  const [scheduler, setScheduler] = useState<SchedulerStatus | null>(null);
  const [portfolioHistory, setPortfolioHistory] = useState<{ time: string; value: number }[]>([]);

  // ─── DATA FETCHING ──────────────────────────────────────────

  const fetchAll = useCallback(async () => {
    try {
      const [statusRes, portfolioRes, signalsRes, positionsRes, regimeRes, schedulerRes] =
        await Promise.all([
          fetch(`${API_URL}/api/status`),
          fetch(`${API_URL}/api/portfolio`),
          fetch(`${API_URL}/api/signals`),
          fetch(`${API_URL}/api/positions`),
          fetch(`${API_URL}/api/regime`),
          fetch(`${API_URL}/api/scheduler/status`),
        ]);

      const [status, port, sigs, pos, reg, sched] = await Promise.all([
        statusRes.json(),
        portfolioRes.json(),
        signalsRes.json(),
        positionsRes.json(),
        regimeRes.json(),
        schedulerRes.json(),
      ]);

      setConnected(status.connected);
      setPortfolio(port);
      setSignals(sigs.signals || []);
      setPositions(pos.positions || []);
      setRegime(reg);
      setScheduler(sched);
      setLastUpdate(new Date().toLocaleTimeString());

      // Append to portfolio history
      if (port?.portfolio_value) {
        setPortfolioHistory(prev => {
          const next = [...prev, {
            time: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
            value: port.portfolio_value
          }];
          return next.slice(-48); // keep last 48 data points
        });
      }

    } catch (e) {
      console.error('API fetch error:', e);
      setConnected(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const interval = setInterval(fetchAll, 30000); // refresh every 30s
    return () => clearInterval(interval);
  }, [fetchAll]);

  // ─── ACTIONS ────────────────────────────────────────────────

  const runStrategy = async (strategy: string) => {
    setRunningStrategy(strategy);
    try {
      await fetch(`${API_URL}/api/trade/run/${strategy}`, { method: 'POST' });
      await fetchAll();
    } catch (e) {
      console.error('Run strategy error:', e);
    } finally {
      setRunningStrategy(null);
    }
  };

  const checkSignals = async () => {
    try {
      await fetch(`${API_URL}/api/scheduler/check-signals`, { method: 'POST' });
      await fetchAll();
    } catch (e) {
      console.error('Check signals error:', e);
    }
  };

  const resumeScheduler = async () => {
    try {
      await fetch(`${API_URL}/api/scheduler/resume`, { method: 'POST' });
      await fetchAll();
    } catch (e) {
      console.error('Resume scheduler error:', e);
    }
  };

  const pauseScheduler = async () => {
    try {
      await fetch(`${API_URL}/api/scheduler/pause`, { method: 'POST' });
      await fetchAll();
    } catch (e) {
      console.error('Pause scheduler error:', e);
    }
  };

  // ─── COMPUTED ───────────────────────────────────────────────

  const pnl = portfolio?.pnl ?? 0;
  const pnlPct = portfolio?.pnl_pct ?? 0;
  const portfolioValue = portfolio?.portfolio_value ?? 0;
  const drawdown = portfolio?.drawdown_pct ?? 0;
  const triggeredSignals = signals.filter(s => s.status === 'triggered').length;

  // ─── RENDER ─────────────────────────────────────────────────

  return (
    <div style={S.app}>
      <link
        href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;400;500;600&display=swap"
        rel="stylesheet"
      />
      <style>{`
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
        @keyframes spin { from{transform:rotate(0deg)} to{transform:rotate(360deg)} }
        body { margin: 0; background: #03050a; }
        tr:hover td { background: rgba(0,229,255,0.02); }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-track { background: #0a0e1a; }
        ::-webkit-scrollbar-thumb { background: #1e2a3a; border-radius: 2px; }
        button:hover { opacity: 0.8; }
      `}</style>

      {/* HEADER */}
      <div style={S.header}>
        <div style={S.logo}>
          QIS<span style={S.logoSpan}>.</span>PLATFORM
        </div>
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
        {scheduler?.paused && (
          <div style={{
            background: 'rgba(255,23,68,0.15)',
            color: '#ff1744',
            padding: '3px 10px',
            borderRadius: '3px',
            fontSize: '10px',
            fontFamily: "'Space Mono', monospace",
            fontWeight: 700,
          }}>
            ⚠ SCHEDULER PAUSED
          </div>
        )}
        <div style={S.nav}>
          {['dashboard', 'signals', 'positions', 'scheduler', 'risk'].map(tab => (
            <button key={tab} style={S.navBtn(activeTab === tab)} onClick={() => setActiveTab(tab)}>
              {tab.toUpperCase()}
            </button>
          ))}
        </div>
        <div style={S.liveIndicator(connected)}>
          <div style={S.liveDot(connected)} />
          {connected ? 'TWS LIVE' : 'DISCONNECTED'}
        </div>
        <div style={{
          fontFamily: "'Space Mono', monospace",
          fontSize: '10px',
          color: '#4a5568',
          marginLeft: '16px',
        }}>
          {lastUpdate}
        </div>
      </div>

      {/* MAIN */}
      <div style={S.main}>

        {/* ── DASHBOARD TAB ─────────────────────────────────── */}
        {activeTab === 'dashboard' && (
          <>
            {/* METRICS */}
            <div style={S.metricGrid}>
              <Metric
                label="Portfolio Value"
                value={loading ? '...' : `$${portfolioValue.toLocaleString('en-US', { minimumFractionDigits: 2 })}`}
                sub={`Started $${(portfolio?.starting_capital ?? 0).toLocaleString()}`}
                color="#00e5ff"
              />
              <Metric
                label="Total P&L"
                value={loading ? '...' : `${pnl >= 0 ? '+' : '-'}${fmtUSD(pnl)}`}
                sub={`${pnlPct >= 0 ? '+' : ''}${fmt(pnlPct)}% return`}
                color={pnl >= 0 ? '#00e676' : '#ff1744'}
              />
              <Metric
                label="Open Positions"
                value={`${positions.length}`}
                sub="Across 3 strategies"
              />
              <Metric
                label="Market Regime"
                value={regime?.regime ?? '—'}
                sub={`Bear score: ${regime?.bear_score ?? 0}/6`}
                color={
                  regime?.regime === 'STRONG_BULL' ? '#00e676' :
                  regime?.regime === 'MILD_BULL'   ? '#00e5ff' :
                  regime?.regime === 'NEUTRAL'     ? '#ffd600' :
                  '#ff1744'
                }
              />
              <Metric
                label="VIX"
                value={regime?.vix != null ? fmt(regime.vix, 1) : 'N/A'}
                sub={`Threshold: ${process.env.REACT_APP_VIX_THRESHOLD ?? '20.0'}`}
                color={
                  regime?.vix == null ? '#4a5568' :
                  regime.vix > 35 ? '#ff1744' :
                  regime.vix > 20 ? '#ffd600' : '#00e676'
                }
              />
            </div>

            {/* PORTFOLIO CHART */}
            <div style={S.card}>
              <div style={S.cardTitle}>
                Portfolio Value
                <span style={S.badge('accent')}>LIVE — 30s refresh</span>
              </div>
              {portfolioHistory.length > 1 ? (
                <ResponsiveContainer width="100%" height={200}>
                  <AreaChart data={portfolioHistory}>
                    <defs>
                      <linearGradient id="colorValue" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#00e5ff" stopOpacity={0.15} />
                        <stop offset="95%" stopColor="#00e5ff" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e2a3a" />
                    <XAxis dataKey="time" stroke="#4a5568" tick={{ fontSize: 10, fontFamily: 'Space Mono' }} interval="preserveStartEnd" />
                    <YAxis stroke="#4a5568" tick={{ fontSize: 10, fontFamily: 'Space Mono' }} tickFormatter={v => `$${(v/1000).toFixed(0)}K`} domain={['auto', 'auto']} />
                    <Tooltip
                      contentStyle={{ background: '#0a0e1a', border: '1px solid #1e2a3a', borderRadius: '6px', fontFamily: 'Space Mono', fontSize: '12px' }}
                      formatter={(value: any) => [`$${Number(value).toLocaleString('en-US', { minimumFractionDigits: 2 })}`, 'Portfolio']}
                    />
                    <Area type="monotone" dataKey="value" stroke="#00e5ff" strokeWidth={2} fill="url(#colorValue)" />
                  </AreaChart>
                </ResponsiveContainer>
              ) : (
                <div style={{ height: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#4a5568', fontFamily: 'Space Mono', fontSize: '12px' }}>
                  Collecting data points... refresh in 30s
                </div>
              )}
            </div>

            {/* SIGNALS + POSITIONS */}
            <div style={S.grid2}>
              {/* SIGNALS */}
              <div style={S.card}>
                <div style={S.cardTitle}>
                  Live Signals
                  <span style={S.badge('green')}>{triggeredSignals} TRIGGERED</span>
                </div>
                {signals.length === 0 ? (
                  <div style={{ color: '#4a5568', fontFamily: 'Space Mono', fontSize: '12px' }}>Loading signals...</div>
                ) : signals.map(signal => (
                  <div key={signal.id} style={S.signalRow}>
                    <div>
                      <div style={{ color: '#e2e8f0', marginBottom: '2px' }}>{signal.name}</div>
                      <div style={{ fontSize: '10px', color: '#4a5568' }}>{signal.strategy}</div>
                    </div>
                    <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
                      <div style={S.sigStatus(signal.status)}>{signal.status.toUpperCase()}</div>
                      <div style={{ fontSize: '10px', color: '#4a5568', marginTop: '4px' }}>{signal.value}</div>
                    </div>
                  </div>
                ))}
              </div>

              {/* POSITIONS */}
              <div style={S.card}>
                <div style={S.cardTitle}>
                  Open Positions
                  <span style={S.badge('purple')}>{positions.length} OPEN</span>
                </div>
                {positions.length === 0 ? (
                  <div style={{ color: '#4a5568', fontFamily: 'Space Mono', fontSize: '12px' }}>No open positions</div>
                ) : (
                  <table style={S.table}>
                    <thead>
                      <tr>
                        <th style={S.th}>Symbol</th>
                        <th style={S.th}>Qty</th>
                        <th style={S.th}>Avg Price</th>
                        <th style={S.th}>Mkt Value</th>
                        <th style={S.th}>Currency</th>
                      </tr>
                    </thead>
                    <tbody>
                      {positions.map((pos, i) => (
                        <tr key={i}>
                          <td style={{ ...S.td, color: '#00e5ff', fontWeight: 700 }}>{pos.symbol}</td>
                          <td style={S.td}>{pos.quantity}</td>
                          <td style={S.td}>${fmt(pos.avg_price)}</td>
                          <td style={S.td}>${pos.market_value.toLocaleString('en-US', { minimumFractionDigits: 2 })}</td>
                          <td style={{ ...S.td, color: '#4a5568', fontSize: '10px' }}>{pos.currency}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          </>
        )}

        {/* ── SIGNALS TAB ───────────────────────────────────── */}
        {activeTab === 'signals' && (
          <div style={S.card}>
            <div style={S.cardTitle}>
              Signal Monitor
              <span style={S.badge('accent')}>15 MIN REFRESH</span>
              <button style={{ ...S.btn('accent'), marginLeft: 'auto' }} onClick={checkSignals}>
                CHECK NOW
              </button>
            </div>
            {signals.length === 0 ? (
              <div style={{ color: '#4a5568', fontFamily: 'Space Mono', fontSize: '12px' }}>Loading...</div>
            ) : signals.map(signal => (
              <div key={signal.id} style={S.signalRow}>
                <div style={{ flex: 1 }}>
                  <div style={{ color: '#e2e8f0', marginBottom: '4px' }}>{signal.name}</div>
                  <div style={{ fontSize: '10px', color: '#4a5568' }}>{signal.strategy}</div>
                </div>
                <div style={{ textAlign: 'center', minWidth: '80px' }}>
                  <div style={{ fontSize: '10px', color: '#4a5568' }}>VALUE</div>
                  <div style={{ color: '#00e5ff', fontWeight: 700 }}>{signal.value}</div>
                </div>
                <div style={{ textAlign: 'center', minWidth: '80px' }}>
                  <div style={{ fontSize: '10px', color: '#4a5568' }}>THRESHOLD</div>
                  <div style={{ color: '#4a5568' }}>{signal.threshold}</div>
                </div>
                <div style={S.sigStatus(signal.status)}>{signal.status.toUpperCase()}</div>
              </div>
            ))}
            {scheduler?.current_signals && Object.keys(scheduler.current_signals).length > 0 && (
              <div style={{ marginTop: '20px' }}>
                <div style={S.cardTitle}>Signal History (Scheduler)</div>
                {Object.entries(scheduler.current_signals).map(([key, val]) => (
                  <div key={key} style={{ ...S.signalRow, marginBottom: '6px' }}>
                    <span style={{ color: '#4a5568', fontSize: '11px' }}>{key}</span>
                    <span style={{ marginLeft: 'auto', color: '#00e5ff', fontWeight: 700, fontSize: '11px' }}>{val}</span>
                  </div>
                ))}
                <div style={{ fontSize: '10px', color: '#4a5568', fontFamily: 'Space Mono', marginTop: '8px' }}>
                  Last check: {scheduler.last_signal_check ? fmtTime(scheduler.last_signal_check) : '—'}
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── POSITIONS TAB ─────────────────────────────────── */}
        {activeTab === 'positions' && (
          <div style={S.card}>
            <div style={S.cardTitle}>
              Open Positions
              <span style={S.badge('purple')}>{positions.length} OPEN</span>
            </div>
            {positions.length === 0 ? (
              <div style={{ color: '#4a5568', fontFamily: 'Space Mono', fontSize: '12px', padding: '20px 0' }}>
                No open positions
              </div>
            ) : (
              <table style={S.table}>
                <thead>
                  <tr>
                    <th style={S.th}>Symbol</th>
                    <th style={S.th}>Quantity</th>
                    <th style={S.th}>Avg Cost</th>
                    <th style={S.th}>Market Value</th>
                    <th style={S.th}>Unrealized P&L</th>
                    <th style={S.th}>Currency</th>
                  </tr>
                </thead>
                <tbody>
                  {positions.map((pos, i) => (
                    <tr key={i}>
                      <td style={{ ...S.td, color: '#00e5ff', fontWeight: 700 }}>{pos.symbol}</td>
                      <td style={S.td}>{pos.quantity}</td>
                      <td style={S.td}>${fmt(pos.avg_price)}</td>
                      <td style={S.td}>${pos.market_value.toLocaleString('en-US', { minimumFractionDigits: 2 })}</td>
                      <td style={{
                        ...S.td,
                        color: pos.unrealized_pnl >= 0 ? '#00e676' : '#ff1744',
                        fontWeight: 700,
                      }}>
                        {pos.unrealized_pnl >= 0 ? '+' : ''}${fmt(pos.unrealized_pnl)}
                      </td>
                      <td style={{ ...S.td, color: '#4a5568' }}>{pos.currency}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}

        {/* ── SCHEDULER TAB ─────────────────────────────────── */}
        {activeTab === 'scheduler' && scheduler && (
          <>
            {/* Status + controls */}
            <div style={S.card}>
              <div style={S.cardTitle}>
                Scheduler Control
                <span style={S.badge(scheduler.paused ? 'red' : 'green')}>
                  {scheduler.paused ? 'PAUSED' : 'RUNNING'}
                </span>
                {scheduler.error_count > 0 && (
                  <span style={S.badge('red')}>{scheduler.error_count} ERRORS</span>
                )}
                <div style={{ marginLeft: 'auto', display: 'flex', gap: '8px' }}>
                  {scheduler.paused ? (
                    <button style={S.btn('green')} onClick={resumeScheduler}>▶ RESUME</button>
                  ) : (
                    <button style={S.btn('yellow')} onClick={pauseScheduler}>⏸ PAUSE</button>
                  )}
                  <button style={S.btn('accent')} onClick={checkSignals}>🔍 CHECK SIGNALS</button>
                </div>
              </div>

              {/* Jobs */}
              {scheduler.jobs.map(job => (
                <div key={job.id} style={S.jobRow}>
                  <div>
                    <div style={{ color: '#e2e8f0', marginBottom: '2px' }}>{job.name}</div>
                    <div style={{ fontSize: '10px', color: '#4a5568' }}>
                      Next run: {job.next_run ? fmtTime(job.next_run) : '—'}
                    </div>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    {scheduler.last_run[job.id] && (
                      <div style={{ fontSize: '10px', color: '#4a5568' }}>
                        Last: {fmtTime(scheduler.last_run[job.id])}
                      </div>
                    )}
                    {scheduler.last_error[job.id] && (
                      <span style={S.badge('red')}>ERROR</span>
                    )}
                    <span style={S.badge(job.paused ? 'red' : 'green')}>
                      {job.paused ? 'PAUSED' : 'ACTIVE'}
                    </span>
                  </div>
                </div>
              ))}
            </div>

            {/* Manual strategy triggers */}
            <div style={S.card}>
              <div style={S.cardTitle}>Manual Strategy Triggers</div>
              <div style={{ display: 'flex', gap: '12px' }}>
                {['macro_regime', 'crypto_trend', 'thematic_rotation'].map(s => (
                  <button
                    key={s}
                    style={S.btn('accent')}
                    onClick={() => runStrategy(s)}
                    disabled={runningStrategy === s}
                  >
                    {runningStrategy === s ? '⟳ RUNNING...' : `▶ ${s.replace('_', ' ').toUpperCase()}`}
                  </button>
                ))}
              </div>
              {Object.entries(scheduler.last_error).length > 0 && (
                <div style={{ marginTop: '16px' }}>
                  <div style={{ ...S.metricLabel, marginBottom: '8px' }}>Last Errors</div>
                  {Object.entries(scheduler.last_error).map(([strategy, error]) => (
                    <div key={strategy} style={{
                      padding: '10px',
                      background: 'rgba(255,23,68,0.05)',
                      border: '1px solid rgba(255,23,68,0.2)',
                      borderRadius: '4px',
                      marginBottom: '6px',
                      fontFamily: 'Space Mono',
                      fontSize: '11px',
                    }}>
                      <span style={{ color: '#ff1744', fontWeight: 700 }}>{strategy}: </span>
                      <span style={{ color: '#4a5568' }}>{error}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}

        {/* ── RISK TAB ──────────────────────────────────────── */}
        {activeTab === 'risk' && (
          <>
            <div style={S.card}>
              <div style={S.cardTitle}>
                Risk Monitor
                <span style={S.badge(scheduler?.drawdown_status?.kill_switch_fired ? 'red' : 'green')}>
                  {scheduler?.drawdown_status?.kill_switch_fired ? '🚨 KILL SWITCH FIRED' : 'KILL SWITCH ARMED'}
                </span>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: '12px' }}>
                {[
                  { label: 'Max Drawdown Limit', value: '15.0%', color: '#ffd600' },
                  {
                    label: 'Current Drawdown',
                    value: `${fmt(scheduler?.drawdown_status?.portfolio_drawdown_pct ?? 0)}%`,
                    color: (scheduler?.drawdown_status?.portfolio_drawdown_pct ?? 0) > 10 ? '#ff1744' :
                           (scheduler?.drawdown_status?.portfolio_drawdown_pct ?? 0) > 5  ? '#ffd600' : '#00e676',
                  },
                  { label: 'Risk Per Trade', value: '2.0%', color: '#00e5ff' },
                  { label: 'Position Stop-Loss', value: '20.0%', color: '#00e5ff' },
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

              {/* Drawdown bar */}
              <div style={{ marginTop: '20px' }}>
                <div style={{ ...S.metricLabel, marginBottom: '8px' }}>
                  Portfolio Drawdown — {fmt(scheduler?.drawdown_status?.portfolio_drawdown_pct ?? 0)}%
                </div>
                <div style={{ background: '#111827', borderRadius: '4px', height: '8px', overflow: 'hidden' }}>
                  <div style={{
                    height: '100%',
                    width: `${Math.min(Math.abs(scheduler?.drawdown_status?.portfolio_drawdown_pct ?? 0) / 15 * 100, 100)}%`,
                    background: (scheduler?.drawdown_status?.portfolio_drawdown_pct ?? 0) > 10 ? '#ff1744' :
                                (scheduler?.drawdown_status?.portfolio_drawdown_pct ?? 0) > 5  ? '#ffd600' : '#00e676',
                    borderRadius: '4px',
                    transition: 'width 0.5s',
                  }} />
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '4px', fontFamily: 'Space Mono', fontSize: '10px', color: '#4a5568' }}>
                  <span>0%</span><span>WARN 10%</span><span>KILL 15%</span>
                </div>
              </div>

              {/* Warnings */}
              {(scheduler?.drawdown_status?.warnings?.length ?? 0) > 0 && (
                <div style={{ marginTop: '16px' }}>
                  <div style={{ ...S.metricLabel, marginBottom: '8px' }}>Warnings</div>
                  {scheduler!.drawdown_status.warnings.map((w, i) => (
                    <div key={i} style={{
                      padding: '8px 12px',
                      background: 'rgba(255,214,0,0.05)',
                      border: '1px solid rgba(255,214,0,0.2)',
                      borderRadius: '4px',
                      marginBottom: '6px',
                      fontFamily: 'Space Mono',
                      fontSize: '11px',
                      color: '#ffd600',
                    }}>{w}</div>
                  ))}
                </div>
              )}

              {/* Last drawdown check */}
              <div style={{ marginTop: '16px', fontFamily: 'Space Mono', fontSize: '10px', color: '#4a5568' }}>
                Last check: {scheduler?.last_drawdown_check ? fmtTime(scheduler.last_drawdown_check) : '—'}
              </div>

              {/* Kill switch resume */}
              {scheduler?.drawdown_status?.kill_switch_fired && (
                <div style={{ marginTop: '16px' }}>
                  <div style={{
                    padding: '12px',
                    background: 'rgba(255,23,68,0.05)',
                    border: '1px solid rgba(255,23,68,0.3)',
                    borderRadius: '6px',
                    marginBottom: '12px',
                    fontFamily: 'Space Mono',
                    fontSize: '11px',
                    color: '#ff1744',
                  }}>
                    🚨 Kill switch has been fired. All positions closed. Scheduler paused.
                    Resume only after reviewing portfolio state.
                  </div>
                  <button style={S.btn('green')} onClick={resumeScheduler}>
                    ▶ RESUME TRADING
                  </button>
                </div>
              )}
            </div>

            {/* Regime summary */}
            {regime && (
              <div style={S.card}>
                <div style={S.cardTitle}>Market Regime</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
                  <div style={S.regime(regime.regime)}>{regime.regime}</div>
                  <div>
                    <div style={{ fontFamily: 'Space Mono', fontSize: '12px', color: '#4a5568' }}>
                      Bear Score: <span style={{ color: '#e2e8f0', fontWeight: 700 }}>{regime.bear_score}/6</span>
                    </div>
                    <div style={{ fontFamily: 'Space Mono', fontSize: '12px', color: '#4a5568', marginTop: '4px' }}>
                      VIX: <span style={{ color: regime.vix && regime.vix > 20 ? '#ffd600' : '#00e676', fontWeight: 700 }}>
                        {regime.vix != null ? fmt(regime.vix, 1) : 'N/A'}
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </>
        )}

      </div>
    </div>
  );
}
