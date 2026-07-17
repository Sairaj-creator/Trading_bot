import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertCircle, CheckCircle2, TrendingUp, DollarSign, Activity } from 'lucide-react'
import { LineChart, Line, YAxis, ResponsiveContainer, Tooltip, XAxis } from 'recharts'
import './App.css'

// You'd typically load this from import.meta.env
const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'
const API_KEY = import.meta.env.VITE_DASHBOARD_API_KEY || ''

function App() {
  const [stopConfirmed, setStopConfirmed] = useState(false)
  const [stopLoading, setStopLoading] = useState(false)
  const [stopMessage, setStopMessage] = useState('')

  // Queries - Real-time polling every 5s
  const { data: health } = useQuery({
    queryKey: ['health'],
    queryFn: () => fetch(`${API_URL}/health`).then(res => res.json()),
    refetchInterval: 5000,
  })

  const { data: daily } = useQuery({
    queryKey: ['daily'],
    queryFn: () => fetch(`${API_URL}/daily`).then(res => res.json()),
    refetchInterval: 5000,
  })

  const { data: gridData } = useQuery({
    queryKey: ['grid'],
    queryFn: () => fetch(`${API_URL}/grid`).then(res => res.json()),
    refetchInterval: 5000,
  })

  // Fetch up to 500 trades to ensure we have a full day's history for the chart
  const { data: tradesData } = useQuery({
    queryKey: ['trades'],
    queryFn: () => fetch(`${API_URL}/trades?limit=500`).then(res => res.json()),
    refetchInterval: 5000,
  })

  const handleEmergencyStop = async () => {
    if (!stopConfirmed) {
      setStopConfirmed(true)
      return
    }
    
    setStopLoading(true)
    try {
      const res = await fetch(`${API_URL}/stop`, {
        method: 'POST',
        headers: {
          'X-API-Key': API_KEY
        }
      })
      const data = await res.json()
      setStopMessage(data.message || 'Halted.')
    } catch (err) {
      setStopMessage('Failed to stop. Check console.')
    }
    setStopLoading(false)
    setStopConfirmed(false)
  }

  const isRunning = health?.status === 'running'
  const isCircuitBroken = health?.circuit_breaker
  const currentPrice = health?.current_price

  // PnL Chart Data processing
  const pnlData = useMemo(() => {
    if (!tradesData?.trades) return []
    const today = new Date().toISOString().split('T')[0]
    
    // Filter only today's trades and those with PnL (sells)
    const closedTrades = tradesData.trades
      .filter((t: any) => t.pnl !== null && t.timestamp.startsWith(today))
      .sort((a: any, b: any) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime())
    
    let cumulative = 0
    return closedTrades.map((t: any) => {
      cumulative += t.pnl
      return { 
        time: new Date(t.timestamp).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'}), 
        cumulative: Number(cumulative.toFixed(4)), 
        pnl: t.pnl 
      }
    })
  }, [tradesData])

  // Grid Visualizer Calculation
  const gridStatus = gridData?.status
  const activeLevels = gridStatus?.levels || []
  const gridLow = gridStatus?.grid_low
  const gridHigh = gridStatus?.grid_high

  return (
    <div className="dashboard-container">
      {/* Header section */}
      <header className="dashboard-header">
        <div className="header-title-container">
          <h1>
            <Activity className="header-icon" size={28} />
            SmartTrade Dashboard
          </h1>
          <p className="header-subtitle">
            Pair: {health?.pair || 'Loading...'} | Env: {health?.environment || '-'}
            {currentPrice && ` | Price: $${currentPrice.toFixed(4)}`}
          </p>
        </div>
        
        <div className="header-actions">
          {isCircuitBroken ? (
            <div className="status-badge status-broken">
              <AlertCircle size={18} />
              Circuit Broken
            </div>
          ) : isRunning ? (
            <div className="status-badge status-active">
              <CheckCircle2 size={18} />
              Active
            </div>
          ) : (
            <div className="status-badge status-unknown">
              <Activity size={18} />
              Unknown State
            </div>
          )}

          <div className="relative-container">
            <button 
              onClick={handleEmergencyStop}
              disabled={stopLoading}
              className={`btn-stop ${stopConfirmed ? 'confirming' : ''}`}
            >
              {stopLoading ? 'Stopping...' : stopConfirmed ? 'Confirm Stop!' : 'Emergency Stop'}
            </button>
            {stopMessage && (
              <div className="error-message">
                {stopMessage}
              </div>
            )}
          </div>
        </div>
      </header>

      {/* P&L Summary Cards */}
      <div className="stats-grid">
        <div className="glass-panel">
          <div className="stat-label"><DollarSign size={16}/> Daily P&L</div>
          <div className={`stat-value ${(daily?.pnl || 0) >= 0 ? 'text-positive' : 'text-negative'}`}>
            ${daily?.pnl?.toFixed(4) || '0.0000'}
          </div>
        </div>
        
        <div className="glass-panel">
          <div className="stat-label"><TrendingUp size={16}/> Daily Trades</div>
          <div className="stat-value text-neutral">{daily?.trades || 0}</div>
        </div>
        
        <div className="glass-panel">
          <div className="stat-label">Consecutive Losses</div>
          <div className={`stat-value ${(daily?.consecutive_losses || 0) > 0 ? 'text-warning' : 'text-neutral'}`}>
            {daily?.consecutive_losses || 0}
          </div>
        </div>
        
        <div className="glass-panel">
          <div className="stat-label">Total Balance</div>
          <div className="stat-value text-neutral">${daily?.balance?.toFixed(2) || '0.00'}</div>
        </div>
      </div>

      <div className="charts-grid">
        {/* Performance Sparkline */}
        <div className="glass-panel chart-panel">
          <div className="panel-header">
            <h2>Today's Cumulative PnL</h2>
          </div>
          <div className="sparkline-container">
            {pnlData.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={pnlData}>
                  <XAxis dataKey="time" hide />
                  <YAxis domain={['auto', 'auto']} hide />
                  <Tooltip 
                    contentStyle={{ backgroundColor: 'rgba(17, 24, 39, 0.9)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px' }}
                    itemStyle={{ color: '#10b981' }}
                  />
                  <Line 
                    type="monotone" 
                    dataKey="cumulative" 
                    stroke="#10b981" 
                    strokeWidth={2} 
                    dot={false}
                    isAnimationActive={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <div className="empty-state">No closed trades today</div>
            )}
          </div>
        </div>

        {/* Grid Ladder Visualization */}
        <div className="glass-panel chart-panel">
          <div className="panel-header">
            <h2>Live Grid Ladder</h2>
          </div>
          <div className="ladder-container">
            {gridLow && gridHigh && currentPrice ? (
              <div className="ladder-bar-wrapper">
                <div className="ladder-labels">
                  <span>${gridHigh.toFixed(2)}</span>
                  <span>${gridLow.toFixed(2)}</span>
                </div>
                <div className="ladder-bar">
                  {/* Price Indicator */}
                  {currentPrice >= gridLow && currentPrice <= gridHigh && (
                    <div 
                      className="ladder-price-marker"
                      style={{ bottom: `${((currentPrice - gridLow) / (gridHigh - gridLow)) * 100}%` }}
                    >
                      <div className="ladder-price-label">${currentPrice.toFixed(2)}</div>
                    </div>
                  )}
                  {/* Grid Levels */}
                  {activeLevels.map((lvl: any) => (
                    <div 
                      key={lvl.index}
                      className={`ladder-level ${lvl.has_buy ? 'level-buy' : lvl.has_sell ? 'level-sell' : ''}`}
                      style={{ bottom: `${((lvl.price - gridLow) / (gridHigh - gridLow)) * 100}%` }}
                    />
                  ))}
                </div>
              </div>
            ) : (
              <div className="empty-state">Grid not initialized or loading</div>
            )}
          </div>
        </div>
      </div>

      <div className="tables-grid">
        {/* Open Positions */}
        <div className="glass-panel">
          <div className="panel-header">
            <h2>Open Grid Positions</h2>
          </div>
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th>Level</th>
                  <th>Entry Price</th>
                  <th>Quantity</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(gridData?.open_positions || {}).map(([key, pos]: [string, any]) => {
                  const levelId = key.replace(/[^\d]/g, '') // naive extract
                  return (
                    <tr key={key}>
                      <td>#{levelId || '?'}</td>
                      <td className="font-mono">${pos.entry_price?.toFixed(4)}</td>
                      <td className="font-mono">{pos.quantity?.toFixed(6)}</td>
                    </tr>
                  )
                })}
                {Object.keys(gridData?.open_positions || {}).length === 0 && (
                  <tr>
                    <td colSpan={3} className="text-center">No open positions</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Trade History */}
        <div className="glass-panel">
          <div className="panel-header">
            <h2>Recent Trades</h2>
          </div>
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Type</th>
                  <th className="text-right">PnL</th>
                </tr>
              </thead>
              <tbody>
                {/* Take the first 50 from our 500 limit fetch to display in the table */}
                {tradesData?.trades?.slice(0, 50).map((trade: any) => (
                  <tr key={trade.id}>
                    <td>
                      {new Date(trade.timestamp).toLocaleTimeString()}
                    </td>
                    <td>
                      <span className={`tag ${trade.side === 'buy' ? 'tag-buy' : 'tag-sell'}`}>
                        {trade.side}
                      </span>
                    </td>
                    <td className="text-right font-mono">
                      {trade.pnl !== null ? (
                        <span className={`pnl-value ${trade.pnl >= 0 ? 'text-positive' : 'text-negative'}`}>
                          ${trade.pnl > 0 ? '+' : ''}{trade.pnl.toFixed(4)}
                        </span>
                      ) : (
                        <span className="text-neutral">-</span>
                      )}
                    </td>
                  </tr>
                ))}
                {(!tradesData?.trades || tradesData.trades.length === 0) && (
                  <tr>
                    <td colSpan={3} className="text-center">No recent trades</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  )
}

export default App
