import { useState, useEffect } from 'react';
import Badge from '../components/Badge';
import { StockAnalysisCard } from '../components/StockAnalysisCard';
import { NIFTY50_STOCKS } from '../data/sectorMap';
import { addToWatchlist, runAnalysis, fetchFoStocksCached } from '../api';
import type { AnalyseResponse, Direction } from '../types';


function WatchlistButton({ symbol }: { symbol: string }) {
  const [saved, setSaved] = useState(false);
  const [err, setErr]     = useState<string | null>(null);

  async function handleAdd() {
    setErr(null);
    try {
      await addToWatchlist(symbol);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed');
      setTimeout(() => setErr(null), 3000);
    }
  }

  return (
    <div className="px-4 pb-3">
      <button
        onClick={handleAdd}
        className="w-full bg-white border border-gray-300 text-gray-700 rounded-lg py-2.5 text-sm font-medium hover:bg-gray-50 transition-colors"
      >
        {saved ? 'Added to Watchlist ✓' : 'Add to Watchlist'}
      </button>
      {err && <p className="text-xs text-red-600 mt-1">⚠ {err}</p>}
    </div>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────

export default function AnalyseScreen({ active }: { active: boolean }) {
  const [mode, setMode]               = useState<'nifty50' | 'custom'>('nifty50');
  const [selectedNifty, setSelected]  = useState('');
  const [customInput, setCustom]      = useState('');
  const [direction, setDirection]     = useState<Direction>('AUTO');
  const [forceRefresh, setForce]      = useState(false);
  const [loading, setLoading]         = useState(false);
  const [result, setResult]           = useState<AnalyseResponse | null>(null);
  const [error, setError]             = useState<string | null>(null);

  const [foStocks, setFoStocks]   = useState<string[]>([]);
  const [searchTerm, setSearch]   = useState('');
  const [foLoaded, setFoLoaded]   = useState(false);

  useEffect(() => {
    if (!active || foLoaded) return;
    fetchFoStocksCached()
      .then(data => { setFoStocks(data); setFoLoaded(true); })
      .catch(() => setFoStocks(NIFTY50_STOCKS.map(s => s.symbol)));
  }, [active, foLoaded]);

  const symbolToSector: Record<string, string> = {};
  NIFTY50_STOCKS.forEach(s => { symbolToSector[s.symbol] = s.sector; });

  const niftySymbols    = NIFTY50_STOCKS.map(s => s.symbol);
  const filteredNifty50 = niftySymbols.filter(sym => sym.toLowerCase().includes(searchTerm.toLowerCase()));
  const filteredOthers  = foStocks
    .filter(sym => !niftySymbols.includes(sym) && sym.toLowerCase().includes(searchTerm.toLowerCase()))
    .sort();

  const displayStocks = [...filteredNifty50, ...filteredOthers];
  if (selectedNifty && !displayStocks.includes(selectedNifty) && mode !== 'custom') {
    displayStocks.unshift(selectedNifty);
  }

  const effectiveSymbol = mode === 'custom' ? customInput.trim() : selectedNifty;
  const canRun = effectiveSymbol.length > 0 && !loading;

  async function handleRun() {
    if (!canRun) return;
    setLoading(true);
    setResult(null);
    setError(null);
    try {
      const data = await runAnalysis(effectiveSymbol, direction, false, forceRefresh);
      setResult(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="pb-20">
      <h1 className="text-xl font-semibold text-gray-900 px-4 pt-5 pb-3">Manual Stock Analysis</h1>

      <div className="grid grid-cols-1 md:grid-cols-12 gap-4 px-4">

        {/* ── Left column: inputs ─────────────────────────────────────────── */}
        <div className="md:col-span-5">

          {/* Stock search + select */}
          <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4 mb-3">
            <div className="mb-3">
              <label className="block text-sm font-medium text-gray-700 mb-1.5">Search Stock</label>
              <input
                type="text"
                placeholder="Search F&O symbol (e.g. RELIANCE, TCS)…"
                value={searchTerm}
                onChange={e => setSearch(e.target.value)}
                className="border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none w-full"
              />
            </div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Select Stock</label>
            <select
              value={mode === 'custom' ? '__custom__' : selectedNifty}
              onChange={e => {
                if (e.target.value === '__custom__') { setMode('custom'); }
                else { setMode('nifty50'); setSelected(e.target.value); }
              }}
              className="border border-gray-300 rounded-lg px-3 py-2.5 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none w-full bg-white"
            >
              <option value="" disabled>— Select a stock —</option>
              {displayStocks.map(sym => (
                <option key={sym} value={sym}>{sym} — {symbolToSector[sym] || 'F&O'}</option>
              ))}
              <option value="__custom__">Other (custom symbol)</option>
            </select>

            {mode === 'custom' && (
              <div className="mt-3">
                <input
                  type="text"
                  value={customInput}
                  onChange={e => setCustom(e.target.value.toUpperCase())}
                  placeholder="e.g. DMART, IRFC, ZOMATO"
                  className="border border-gray-300 rounded-lg px-3 py-2.5 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none w-full"
                />
                <p className="text-xs text-gray-400 mt-1">Any NSE F&amp;O stock with active futures</p>
              </div>
            )}
          </div>

          {/* Direction */}
          <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4 mb-3">
            <label className="block text-sm font-medium text-gray-700 mb-2">Direction</label>
            <div className="flex gap-2">
              {(['AUTO', 'LONG', 'SHORT'] as Direction[]).map(d => {
                const isActive = direction === d;
                const activeClass =
                  d === 'LONG'  ? 'bg-green-600 text-white border-green-600' :
                  d === 'SHORT' ? 'bg-red-600 text-white border-red-600'     :
                                  'bg-blue-600 text-white border-blue-600';
                return (
                  <button
                    key={d}
                    onClick={() => setDirection(d)}
                    className={`flex-1 py-2 rounded-lg border text-sm font-medium transition-colors ${
                      isActive ? activeClass : 'border-gray-200 text-gray-600 bg-white hover:bg-gray-50'
                    }`}
                  >
                    {d === 'AUTO' ? 'Auto' : d === 'LONG' ? 'Long ↑' : 'Short ↓'}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Force refresh toggle */}
          <div className="mb-4 flex items-center gap-2">
            <button
              role="switch"
              aria-checked={forceRefresh}
              onClick={() => setForce(v => !v)}
              className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                forceRefresh ? 'bg-blue-600' : 'bg-gray-300'
              }`}
            >
              <span className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                forceRefresh ? 'translate-x-4' : 'translate-x-0.5'
              }`} />
            </button>
            <div>
              <span className="text-sm text-gray-700">Force refresh</span>
              <p className="text-xs text-gray-400">Bypass cache and re-run Claude</p>
            </div>
          </div>

          {/* Run button */}
          <button
            onClick={handleRun}
            disabled={!canRun}
            className={`w-full rounded-xl py-3.5 text-sm font-semibold transition-colors mb-1 ${
              !canRun
                ? 'bg-blue-300 text-white cursor-not-allowed'
                : loading
                ? 'bg-blue-500 text-white cursor-wait'
                : 'bg-blue-600 hover:bg-blue-700 text-white'
            }`}
          >
            {loading ? (
              <span className="flex items-center justify-center gap-2">
                <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
                </svg>
                Analysing… (~30s)
              </span>
            ) : 'Run Analysis'}
          </button>
          <p className="text-xs text-center text-gray-400 mb-4">
            Uses latest session context · ~$0.10 per analysis
          </p>
        </div>

        {/* ── Right column: results ────────────────────────────────────────── */}
        <div className="md:col-span-7">
          {error && (
            <div className="bg-red-50 border border-red-200 rounded-xl p-4 mb-4">
              <p className="text-sm text-red-700">{error}</p>
            </div>
          )}

          {result ? (
            <div>
              <StockAnalysisCard
                symbol={result.symbol}
                analysis={result.analysis}
                extraBadge={
                  <>
                    <Badge stage={result.analysis.stage} />
                    {result.is_cached && (
                      <span className="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-500">
                        Cached
                      </span>
                    )}
                  </>
                }
              />
              <WatchlistButton symbol={result.symbol} />
              <p className="text-[10px] text-gray-400 text-center mt-1 mb-2">
                {result.is_cached
                  ? 'Cached result · no cost'
                  : `Took ${result.duration_seconds.toFixed(1)}s · Cost: $${result.estimated_cost_usd.toFixed(4)}`}
              </p>
              {result.data_quality_notes.length > 0 && (
                <div className="mt-2 space-y-0.5">
                  {result.data_quality_notes.map((note, i) => (
                    <p key={i} className="text-xs text-gray-400 italic">ⓘ {note}</p>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div className="hidden md:flex flex-col items-center justify-center border border-dashed border-gray-200 rounded-xl p-8 text-center bg-gray-50/30 h-[300px]">
              <span className="text-3xl mb-2">📊</span>
              <p className="text-sm font-semibold text-gray-500">Ready to Analyse</p>
              <p className="text-xs text-gray-400 mt-1">
                Select a stock and hit Run Analysis. Results from today's session are cached instantly.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
