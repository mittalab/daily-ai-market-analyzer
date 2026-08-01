import { useState } from 'react';
import TradingViewChart from './TradingViewChart';
import LightweightChart, { type FutureLevels } from './LightweightChart';
import { getTVSymbol, type OHLCVRow } from './chartUtils';

type ChartMode = 'tradingview' | 'lightweight' | 'near_fut' | 'next_fut';

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
    if (v === 'tradingview' || v === 'lightweight' || v === 'near_fut' || v === 'next_fut') return v;
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

  // ── Futures data ────────────────────────────────────────────────────────────
  const nearFutOhlcv: OHLCVRow[] = analysisData?.near_futures_ohlcv ?? [];
  const nextFutOhlcv: OHLCVRow[] = analysisData?.next_futures_ohlcv ?? [];
  const nearExpiry: string | undefined = analysisData?.near_futures_expiry;
  const nextExpiry: string | undefined = analysisData?.next_futures_expiry;
  const hasNearFut = nearFutOhlcv.length > 0;
  const hasNextFut = nextFutOhlcv.length > 0;

  // Derive futures price levels from fut_setup.
  // contract_selected tells us which expiry Claude's levels apply to.
  const futSetup = analysisData?.fut_setup;
  const contractSelected: string | undefined = futSetup?.contract_selected; // 'near_month' | 'next_month'

  const claudeFutureLevels: FutureLevels | undefined = futSetup
    ? {
        entryLow:  futSetup.entry_low  ?? null,
        entryHigh: futSetup.entry_high ?? null,
        sl:        futSetup.stop_loss  ?? null,
        t1:        futSetup.target_1   ?? null,
        t2:        futSetup.target_2   ?? null,
      }
    : undefined;

  // Only apply Claude's levels to the tab matching the contract Claude analysed
  const nearFutLevels = contractSelected === 'near_month' ? claudeFutureLevels : undefined;
  const nextFutLevels = contractSelected === 'next_month' ? claudeFutureLevels : undefined;

  // ── Tabs config ─────────────────────────────────────────────────────────────
  type TabDef = { id: ChartMode; label: string; show: boolean };
  const tabs: TabDef[] = [
    { id: 'tradingview', label: '📈 Live',      show: true },
    { id: 'lightweight', label: '🔍 Analysis',  show: true },
    { id: 'near_fut',    label: '📊 Near FUT',  show: hasNearFut },
    { id: 'next_fut',    label: '📊 Next FUT',  show: hasNextFut },
  ];

  // If stored mode references a tab that no longer has data, fall back to lightweight
  const activeMode = (mode === 'near_fut' && !hasNearFut) || (mode === 'next_fut' && !hasNextFut)
    ? 'lightweight'
    : mode;

  // ── Expanded panel ───────────────────────────────────────────────────────────
  const expandedPanel = (
    <div className={`border-b border-gray-100 ${minimised ? 'hidden sm:block' : ''}`}>
      {/* Mode toggle row */}
      <div className="flex items-center justify-between px-3 py-2 bg-gray-50 border-b border-gray-100">
        <div className="flex gap-1 flex-wrap">
          {tabs.filter(t => t.show).map(t => (
            <button
              key={t.id}
              onClick={() => handleSetMode(t.id)}
              className={`text-[11px] font-semibold px-2.5 py-1 rounded-md transition-colors ${
                activeMode === t.id
                  ? 'bg-gray-900 text-white'
                  : 'bg-white text-gray-500 border border-gray-200 hover:bg-gray-100'
              }`}
            >
              {t.label}
              {/* Show "Claude" badge next to the tab that has Claude's levels */}
              {((t.id === 'near_fut' && nearFutLevels) || (t.id === 'next_fut' && nextFutLevels)) && (
                <span className="ml-1 text-[8px] bg-indigo-100 text-indigo-700 px-1 py-0.5 rounded font-bold">
                  Claude
                </span>
              )}
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
      <div className="h-[420px] sm:h-[500px] w-full relative">
        {activeMode === 'tradingview' ? (
          <TradingViewChart symbol={symbol} spotPrice={analysisData?.spot_price} />
        ) : activeMode === 'near_fut' ? (
          <LightweightChart
            symbol={symbol}
            analysisData={analysisData}
            ohlcvData={nearFutOhlcv}
            futureLevels={nearFutLevels}
            futureExpiry={nearExpiry}
          />
        ) : activeMode === 'next_fut' ? (
          <LightweightChart
            symbol={symbol}
            analysisData={analysisData}
            ohlcvData={nextFutOhlcv}
            futureLevels={nextFutLevels}
            futureExpiry={nextExpiry}
          />
        ) : (
          <LightweightChart
            symbol={symbol}
            analysisData={analysisData}
            ohlcvData={ohlcvData}
            entryPrice={entryPrice}
          />
        )}
      </div>

      {/* Minimise button — hidden on sm+ */}
      <button
        onClick={handleMinimise}
        className="sm:hidden w-full flex items-center justify-center gap-1 py-2 bg-gray-50 border-t border-gray-100 text-[11px] text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
      >
        <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M5 15l7-7 7 7" />
        </svg>
        Minimise chart
      </button>
    </div>
  );

  return (
    <>
      {/* Slim bar — only on mobile when minimised */}
      {minimised && (
        <div className="sm:hidden flex items-center justify-between px-4 h-11 bg-gray-50 border-b border-gray-100 text-xs">
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
      )}
      {expandedPanel}
    </>
  );
}
