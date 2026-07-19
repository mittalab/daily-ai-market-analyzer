import { useState } from 'react';
import TradingViewChart from './TradingViewChart';
import LightweightChart from './LightweightChart';
import { getTVSymbol, type OHLCVRow } from './chartUtils';

type ChartMode = 'tradingview' | 'lightweight';

interface StockChartPanelProps {
  symbol: string;
  analysisData: any;
  ohlcvData: OHLCVRow[];
  defaultMinimised?: boolean;
  // Active tab extras
  entryPrice?: number;
  currentPnl?: number;
  currentPnlPct?: number;
}

function getStoredMode(symbol: string): ChartMode {
  try {
    const v = localStorage.getItem(`chart_mode_${symbol}`);
    if (v === 'tradingview' || v === 'lightweight') return v;
  } catch { /* ignore */ }
  return 'lightweight';
}

function getStoredMinimised(symbol: string, defaultVal: boolean): boolean {
  try {
    const v = localStorage.getItem(`chart_minimised_${symbol}`);
    if (v !== null) return v === 'true';
  } catch { /* ignore */ }
  return defaultVal;
}

function saveMode(symbol: string, mode: ChartMode) {
  try { localStorage.setItem(`chart_mode_${symbol}`, mode); } catch { /* ignore */ }
}

function saveMinimised(symbol: string, val: boolean) {
  try { localStorage.setItem(`chart_minimised_${symbol}`, String(val)); } catch { /* ignore */ }
}

export default function StockChartPanel({
  symbol,
  analysisData,
  ohlcvData,
  defaultMinimised = false,
  entryPrice,
  currentPnl,
  currentPnlPct,
}: StockChartPanelProps) {
  const [mode, setMode] = useState<ChartMode>(() => getStoredMode(symbol));
  const [minimised, setMinimised] = useState<boolean>(() =>
    getStoredMinimised(symbol, defaultMinimised)
  );

  const handleSetMode = (m: ChartMode) => {
    setMode(m);
    saveMode(symbol, m);
  };

  const handleExpand = () => {
    setMinimised(false);
    saveMinimised(symbol, false);
  };

  const handleMinimise = () => {
    setMinimised(true);
    saveMinimised(symbol, true);
  };

  const spotPrice = analysisData?.spot_price;
  const tvSymbol  = getTVSymbol(symbol);

  const pnlPositive = currentPnl != null && currentPnl >= 0;
  const pnlStr = currentPnl != null
    ? `${pnlPositive ? '+' : ''}₹${Math.abs(currentPnl).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
    : null;
  const pnlPctStr = currentPnlPct != null
    ? `${currentPnlPct >= 0 ? '+' : ''}${currentPnlPct.toFixed(1)}%`
    : null;

  // ── Minimised slim bar ──────────────────────────────────────────────────────
  if (minimised) {
    return (
      <div className="flex items-center justify-between px-4 h-11 bg-gray-50 border-b border-gray-100 text-xs">
        <div className="flex items-center gap-2 font-mono text-gray-600">
          <span className="font-semibold text-gray-800">{tvSymbol}</span>
          {spotPrice != null && (
            <span className="text-gray-500">₹{Number(spotPrice).toLocaleString('en-IN', { maximumFractionDigits: 0 })}</span>
          )}
        </div>
        <button
          onClick={handleExpand}
          className="text-[11px] text-blue-600 font-semibold flex items-center gap-1 hover:text-blue-800"
        >
          <span>⤢</span> Expand chart
        </button>
      </div>
    );
  }

  // ── Expanded panel ──────────────────────────────────────────────────────────
  return (
    <div className="border-b border-gray-100">
      {/* Mode toggle row */}
      <div className="flex items-center justify-between px-3 py-2 bg-gray-50 border-b border-gray-100">
        <div className="flex gap-1">
          {(['tradingview', 'lightweight'] as ChartMode[]).map(m => (
            <button
              key={m}
              onClick={() => handleSetMode(m)}
              className={`text-[11px] font-semibold px-2.5 py-1 rounded-md transition-colors ${
                mode === m
                  ? 'bg-gray-900 text-white'
                  : 'bg-white text-gray-500 border border-gray-200 hover:bg-gray-100'
              }`}
            >
              {m === 'tradingview' ? '📈 TradingView ↗' : '🔍 Analysis'}
            </button>
          ))}
        </div>

        {/* P&L badge (Active tab) */}
        {pnlStr && (
          <div className={`text-[11px] font-mono font-bold px-2 py-0.5 rounded ${
            pnlPositive ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'
          }`}>
            {pnlStr}{pnlPctStr ? ` (${pnlPctStr})` : ''}
          </div>
        )}
      </div>

      {/* Chart area */}
      <div className="h-[420px] md:h-[500px] w-full relative">
        {mode === 'tradingview' ? (
          <TradingViewChart symbol={symbol} spotPrice={analysisData?.spot_price} />
        ) : (
          <LightweightChart
            symbol={symbol}
            analysisData={analysisData}
            ohlcvData={ohlcvData}
            entryPrice={entryPrice}
          />
        )}
      </div>

      {/* Minimise button */}
      <button
        onClick={handleMinimise}
        className="w-full flex items-center justify-center gap-1 py-2 bg-gray-50 border-t border-gray-100 text-[11px] text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
      >
        <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M5 15l7-7 7 7" />
        </svg>
        Minimise chart
      </button>
    </div>
  );
}
