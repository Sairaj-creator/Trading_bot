import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertCircle, CheckCircle2, TrendingUp, DollarSign, Activity } from 'lucide-react'

// You'd typically load this from import.meta.env
const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'
const API_KEY = import.meta.env.VITE_DASHBOARD_API_KEY || ''

function App() {
  const [stopConfirmed, setStopConfirmed] = useState(false)
  const [stopLoading, setStopLoading] = useState(false)
  const [stopMessage, setStopMessage] = useState('')

  // Queries
  const { data: health } = useQuery({
    queryKey: ['health'],
    queryFn: () => fetch(`${API_URL}/health`).then(res => res.json()),
    refetchInterval: 60000,
  })

  const { data: daily } = useQuery({
    queryKey: ['daily'],
    queryFn: () => fetch(`${API_URL}/daily`).then(res => res.json()),
    refetchInterval: 60000,
  })

  const { data: gridData } = useQuery({
    queryKey: ['grid'],
    queryFn: () => fetch(`${API_URL}/grid`).then(res => res.json()),
    refetchInterval: 60000,
  })

  const { data: tradesData } = useQuery({
    queryKey: ['trades'],
    queryFn: () => fetch(`${API_URL}/trades?limit=50`).then(res => res.json()),
    refetchInterval: 60000,
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

  return (
    <div className="min-h-screen p-6 max-w-7xl mx-auto space-y-6">
      {/* Header section */}
      <header className="flex justify-between items-center pb-4 border-b border-border">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-3">
            <Activity className="text-blue-500" />
            SmartTrade Dashboard
          </h1>
          <p className="text-sm text-gray-400 mt-1">
            Pair: {health?.pair || 'Loading...'} | Env: {health?.environment || '-'}
          </p>
        </div>
        
        <div className="flex items-center gap-4">
          {isCircuitBroken ? (
            <div className="px-4 py-2 bg-red-900/30 text-red-500 rounded-full border border-red-500/50 flex items-center gap-2">
              <AlertCircle size={18} />
              Circuit Broken
            </div>
          ) : isRunning ? (
            <div className="px-4 py-2 bg-green-900/30 text-green-500 rounded-full border border-green-500/50 flex items-center gap-2">
              <CheckCircle2 size={18} />
              Active
            </div>
          ) : (
            <div className="px-4 py-2 bg-gray-800 text-gray-400 rounded-full border border-gray-600 flex items-center gap-2">
              <Activity size={18} />
              Unknown State
            </div>
          )}

          <div className="relative">
            <button 
              onClick={handleEmergencyStop}
              disabled={stopLoading}
              className={`px-4 py-2 rounded-md font-medium transition-colors ${
                stopConfirmed 
                  ? 'bg-red-600 hover:bg-red-700 text-white animate-pulse' 
                  : 'bg-red-500/10 text-red-500 border border-red-500 hover:bg-red-500/20'
              }`}
            >
              {stopLoading ? 'Stopping...' : stopConfirmed ? 'Confirm Stop!' : 'Emergency Stop'}
            </button>
            {stopMessage && (
              <div className="absolute top-full right-0 mt-2 text-sm text-red-400 whitespace-nowrap">
                {stopMessage}
              </div>
            )}
          </div>
        </div>
      </header>

      {/* P&L Summary Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-card p-5 rounded-xl border border-border">
          <div className="flex items-center gap-2 text-gray-400 mb-2"><DollarSign size={16}/> Daily P&L</div>
          <div className={`text-2xl font-bold ${(daily?.pnl || 0) >= 0 ? 'text-green-500' : 'text-red-500'}`}>
            ${daily?.pnl?.toFixed(4) || '0.0000'}
          </div>
        </div>
        <div className="bg-card p-5 rounded-xl border border-border">
          <div className="flex items-center gap-2 text-gray-400 mb-2"><TrendingUp size={16}/> Daily Trades</div>
          <div className="text-2xl font-bold">{daily?.trades || 0}</div>
        </div>
        <div className="bg-card p-5 rounded-xl border border-border">
          <div className="flex items-center gap-2 text-gray-400 mb-2">Consecutive Losses</div>
          <div className={`text-2xl font-bold ${(daily?.consecutive_losses || 0) > 0 ? 'text-orange-500' : ''}`}>
            {daily?.consecutive_losses || 0}
          </div>
        </div>
        <div className="bg-card p-5 rounded-xl border border-border">
          <div className="flex items-center gap-2 text-gray-400 mb-2">Total Balance</div>
          <div className="text-2xl font-bold">${daily?.balance?.toFixed(2) || '0.00'}</div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Open Positions */}
        <div className="bg-card rounded-xl border border-border overflow-hidden flex flex-col">
          <div className="p-4 border-b border-border bg-gray-800/50">
            <h2 className="font-semibold text-lg">Open Grid Positions</h2>
          </div>
          <div className="overflow-y-auto max-h-[500px]">
            <table className="w-full text-left text-sm">
              <thead className="bg-gray-900/50 text-gray-400 sticky top-0">
                <tr>
                  <th className="p-4 font-medium">Level</th>
                  <th className="p-4 font-medium">Entry Price</th>
                  <th className="p-4 font-medium">Quantity</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {Object.entries(gridData?.open_positions || {}).map(([key, pos]: [string, any]) => {
                  const levelId = key.replace(/[^\d]/g, '') // naive extract
                  return (
                    <tr key={key} className="hover:bg-gray-800/30 transition-colors">
                      <td className="p-4 text-gray-300">#{levelId || '?'}</td>
                      <td className="p-4 font-mono">${pos.entry_price?.toFixed(4)}</td>
                      <td className="p-4 font-mono">{pos.quantity?.toFixed(6)}</td>
                    </tr>
                  )
                })}
                {Object.keys(gridData?.open_positions || {}).length === 0 && (
                  <tr>
                    <td colSpan={3} className="p-8 text-center text-gray-500">No open positions</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Trade History */}
        <div className="bg-card rounded-xl border border-border overflow-hidden flex flex-col">
          <div className="p-4 border-b border-border bg-gray-800/50">
            <h2 className="font-semibold text-lg">Recent Trades</h2>
          </div>
          <div className="overflow-y-auto max-h-[500px]">
            <table className="w-full text-left text-sm">
              <thead className="bg-gray-900/50 text-gray-400 sticky top-0">
                <tr>
                  <th className="p-4 font-medium">Time</th>
                  <th className="p-4 font-medium">Type</th>
                  <th className="p-4 font-medium text-right">PnL</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {tradesData?.trades?.map((trade: any) => (
                  <tr key={trade.id} className="hover:bg-gray-800/30 transition-colors">
                    <td className="p-4 text-gray-400 whitespace-nowrap">
                      {new Date(trade.timestamp).toLocaleTimeString()}
                    </td>
                    <td className="p-4">
                      <span className={`px-2 py-1 rounded text-xs font-medium ${
                        trade.side === 'buy' ? 'bg-green-500/10 text-green-500' : 'bg-red-500/10 text-red-500'
                      }`}>
                        {trade.side.toUpperCase()}
                      </span>
                    </td>
                    <td className="p-4 text-right font-mono">
                      {trade.pnl !== null ? (
                        <span className={trade.pnl >= 0 ? 'text-green-500' : 'text-red-500'}>
                          ${trade.pnl > 0 ? '+' : ''}{trade.pnl.toFixed(4)}
                        </span>
                      ) : (
                        <span className="text-gray-500">-</span>
                      )}
                    </td>
                  </tr>
                ))}
                {(!tradesData?.trades || tradesData.trades.length === 0) && (
                  <tr>
                    <td colSpan={3} className="p-8 text-center text-gray-500">No recent trades</td>
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
