import { useState, useEffect } from 'react';
import Badge from '../components/Badge';
import ConvictionBar from '../components/ConvictionBar';
import Expander from '../components/Expander';
import { NIFTY50_STOCKS } from '../data/sectorMap';
import { addToWatchlist, runAnalysis, fetchFoStocks } from '../api';
import type { AnalyseResponse, Direction } from '../types';

const INR = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });

function dte(expiry: string): number {
  return Math.ceil((new Date(expiry).getTime() - Date.now()) / 86400000);
}

const MATURITY_LABEL: Record<string, string> = {
  EARLY: 'Early Stage',
  DEVELOPING: 'Developing',
  READY: 'Ready',
};

const IV_CONFIG: Record<string, string> = {
  LOW:     'bg-green-100 text-green-800',
  MEDIUM:  'bg-amber-100 text-amber-800',
  HIGH:    'bg-red-100 text-red-800',
  UNKNOWN: 'bg-gray-100 text-gray-600',
};

const SCORING_ROWS: { key: keyof import('../types').ScoringBreakdown; label: string; max: number }[] = [
  { key: 'price_structure',  label: 'Price Structure',    max: 30 },
  { key: 'momentum_volume',  label: 'Momentum / Volume',  max: 25 },
  { key: 'index_fo_context', label: 'Index F&O Context',  max: 25 },
  { key: 'stock_fo',         label: 'Stock F&O',          max: 10 },
  { key: 'market_context',   label: 'Market Context',     max: 10 },
];

export default function AnalyseScreen() {
  const [mode, setMode]               = useState<'nifty50' | 'custom'>('nifty50');
  const [selectedNifty, setSelected]  = useState('');
  const [customInput, setCustom]      = useState('');
  const [direction, setDirection]     = useState<Direction>('AUTO');
  const [saveToLedger, setSave]       = useState(false);
  const [loading, setLoading]         = useState(false);
  const [result, setResult]           = useState<AnalyseResponse | null>(null);
  const [error, setError]             = useState<string | null>(null);
  const [savedLedger, setSavedLedger]     = useState(false);
  const [savedWatch, setSavedWatch]       = useState(false);
  const [watchlistError, setWatchlistErr] = useState<string | null>(null);

  const [foStocks, setFoStocks]       = useState<string[]>([]);
  const [searchTerm, setSearchTerm]   = useState('');

  useEffect(() => {
    fetchFoStocks()
      .then(data => {
        setFoStocks(data);
      })
      .catch(err => {
        console.error('Failed to load F&O stocks list:', err);
        // Fallback to NIFTY50 symbols
        setFoStocks(NIFTY50_STOCKS.map(s => s.symbol));
      });
  }, []);

  const symbolToSector: Record<string, string> = {};
  NIFTY50_STOCKS.forEach(s => {
    symbolToSector[s.symbol] = s.sector;
  });

  const nifty50SymbolsOrder = NIFTY50_STOCKS.map(s => s.symbol);

  const filteredNifty50 = nifty50SymbolsOrder.filter(sym =>
    foStocks.includes(sym) && sym.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const filteredOthers = foStocks.filter(sym =>
    !nifty50SymbolsOrder.includes(sym) && sym.toLowerCase().includes(searchTerm.toLowerCase())
  ).sort();

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
    setSavedLedger(false);
    setSavedWatch(false);
    try {
      const data = await runAnalysis(effectiveSymbol, direction, saveToLedger);
      setResult(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }

  async function handleSaveLedger() {
    if (!result) return;
    try {
      await runAnalysis(result.symbol, direction, true);
      setSavedLedger(true);
      setTimeout(() => setSavedLedger(false), 2000);
    } catch {
      // ignore
    }
  }

  async function handleAddWatchlist() {
    if (!result) return;
    setWatchlistErr(null);
    try {
      await addToWatchlist(result.symbol);
      setSavedWatch(true);
      setTimeout(() => setSavedWatch(false), 2000);
    } catch (e) {
      setWatchlistErr(e instanceof Error ? e.message : 'Failed to add to watchlist');
      setTimeout(() => setWatchlistErr(null), 4000);
    }
  }

  const analysis = result?.analysis;

  return (
    <div className="pb-20">
      <h1 className="text-xl font-semibold text-gray-900 px-4 pt-5 pb-3">Manual Stock Analysis</h1>

      {/* Stock selection */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 mx-4 p-4 mb-3">
        <div className="mb-3">
          <label className="block text-sm font-medium text-gray-700 mb-1.5">Search Stock</label>
          <input
            type="text"
            placeholder="Search F&O symbol (e.g. RELIANCE, TCS)..."
            value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
            className="border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none w-full"
          />
        </div>
        <label className="block text-sm font-medium text-gray-700 mb-2">Select Stock</label>
        <select
          value={mode === 'custom' ? '__custom__' : selectedNifty}
          onChange={e => {
            if (e.target.value === '__custom__') {
              setMode('custom');
            } else {
              setMode('nifty50');
              setSelected(e.target.value);
            }
          }}
          className="border border-gray-300 rounded-lg px-3 py-2.5 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none w-full bg-white"
        >
          <option value="" disabled>— Select a stock —</option>
          {displayStocks.map(sym => {
            const sector = symbolToSector[sym] || 'F&O';
            return (
              <option key={sym} value={sym}>{sym} — {sector}</option>
            );
          })}
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
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 mx-4 p-4 mb-3">
        <label className="block text-sm font-medium text-gray-700 mb-2">Direction</label>
        <div className="flex gap-2">
          {(['AUTO', 'LONG', 'SHORT'] as Direction[]).map(d => {
            const active = direction === d;
            const activeClass =
              d === 'LONG'  ? 'bg-green-600 text-white border-green-600' :
              d === 'SHORT' ? 'bg-red-600 text-white border-red-600' :
                              'bg-blue-600 text-white border-blue-600';
            return (
              <button
                key={d}
                onClick={() => setDirection(d)}
                className={`flex-1 py-2 rounded-lg border text-sm font-medium transition-colors duration-150 ${
                  active ? activeClass : 'border-gray-200 text-gray-600 bg-white hover:bg-gray-50'
                }`}
              >
                {d === 'AUTO' ? 'Auto' : d === 'LONG' ? 'Long ↑' : 'Short ↓'}
              </button>
            );
          })}
        </div>
      </div>

      {/* Options row */}
      <div className="mx-4 mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <button
            role="switch"
            aria-checked={saveToLedger}
            onClick={() => setSave(v => !v)}
            className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors duration-150 focus:outline-none ${
              saveToLedger ? 'bg-blue-600' : 'bg-gray-300'
            }`}
          >
            <span
              className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform duration-150 ${
                saveToLedger ? 'translate-x-4' : 'translate-x-0.5'
              }`}
            />
          </button>
          <div>
            <span className="text-sm text-gray-700">Save to trade ledger</span>
            <p className="text-xs text-gray-400">Adds to tracked setups</p>
          </div>
        </div>
      </div>

      {/* Run button */}
      <div className="mx-4 mb-6">
        <button
          onClick={handleRun}
          disabled={!canRun}
          className={`w-full rounded-xl py-3.5 text-sm font-semibold transition-colors duration-150 ${
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
              Analysing…
            </span>
          ) : 'Run Analysis'}
        </button>
        <p className="text-xs text-center text-gray-400 mt-2">~₹8 per analysis · ~30 seconds</p>
      </div>

      {/* Error */}
      {error && (
        <div className="mx-4 mb-4 bg-red-50 border border-red-200 rounded-xl p-4">
          <p className="text-sm text-red-700">{error}</p>
        </div>
      )}

      {/* Results */}
      {result && analysis && (
        <div className="mx-4 mb-6">
          {analysis.stage === 'SKIP' ? (
            /* SKIP card */
            <div className="bg-amber-50 border border-amber-200 rounded-xl p-4">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-semibold text-amber-800">No Actionable Setup</h3>
                <Badge stage="SKIP" />
              </div>
              <div className="flex items-center gap-2 mb-3">
                <span className="text-base font-bold text-gray-900">{result.symbol}</span>
                <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                  analysis.direction === 'LONG' ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
                }`}>
                  {analysis.direction}
                </span>
              </div>
              {analysis.skip_reason && (
                <p className="text-sm text-amber-700 mb-2">{analysis.skip_reason}</p>
              )}
              {analysis.why_could_be_wrong && (
                <>
                  <p className="text-xs font-medium text-gray-500 mb-1">Claude's Assessment:</p>
                  <p className="text-sm text-gray-700">{analysis.why_could_be_wrong}</p>
                </>
              )}
            </div>
          ) : (
            /* Setup card */
            <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
              {/* Header */}
              <div className="px-4 pt-4 pb-3 border-b border-gray-100">
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <Badge stage={analysis.stage} />
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                      analysis.direction === 'LONG' ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
                    }`}>
                      {analysis.direction === 'LONG' ? '↑ LONG' : '↓ SHORT'}
                    </span>
                  </div>
                  <span className="text-lg font-bold text-gray-900">{result.symbol}</span>
                </div>

                <div className="mb-3">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs text-gray-500">Conviction</span>
                  </div>
                  <ConvictionBar score={analysis.conviction_score} />
                </div>

                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-xs bg-gray-100 text-gray-700 px-2 py-0.5 rounded-full">
                    {analysis.setup_type}
                  </span>
                  <span className="text-xs bg-blue-50 text-blue-700 px-2 py-0.5 rounded-full">
                    {MATURITY_LABEL[analysis.setup_maturity] ?? analysis.setup_maturity}
                  </span>
                  <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${IV_CONFIG[analysis.iv_assessment]}`}>
                    IV: {analysis.iv_assessment}
                  </span>
                </div>
              </div>

              {/* Option premiums grid */}
              <div className="px-4 py-3 border-b border-gray-100">
                <p className="text-xs font-medium text-gray-500 mb-2">
                  {analysis.option_type} {analysis.strike} · {analysis.expiry_date} ({dte(analysis.expiry_date)}d)
                </p>
                <div className="grid grid-cols-4 gap-2 text-center">
                  {[
                    { label: 'Entry', value: `${analysis.entry_premium_low}–${analysis.entry_premium_high}` },
                    { label: 'SL',    value: String(analysis.stop_loss_premium) },
                    { label: 'T1',    value: String(analysis.target_1_premium) },
                    { label: 'T2',    value: String(analysis.target_2_premium) },
                  ].map(({ label, value }) => (
                    <div key={label} className="bg-gray-50 rounded-lg py-2">
                      <p className="text-xs text-gray-500 mb-0.5">{label}</p>
                      <p className="text-sm font-semibold text-gray-900">{value}</p>
                    </div>
                  ))}
                </div>
              </div>

              {/* Trade metrics grid */}
              <div className="px-4 py-3 border-b border-gray-100">
                <div className="grid grid-cols-4 gap-2 text-center">
                  {[
                    { label: 'Risk',  value: analysis.max_risk_inr != null ? `₹${INR.format(analysis.max_risk_inr)}` : '—' },
                    { label: 'R:R',   value: `1:${analysis.risk_reward.toFixed(1)}` },
                    { label: 'Lots',  value: analysis.lots != null && analysis.lot_size != null ? `${analysis.lots}×${analysis.lot_size}` : '—' },
                    { label: 'DTE',   value: `${dte(analysis.expiry_date)}d` },
                  ].map(({ label, value }) => (
                    <div key={label} className="bg-gray-50 rounded-lg py-2">
                      <p className="text-xs text-gray-500 mb-0.5">{label}</p>
                      <p className="text-sm font-semibold text-gray-900">{value}</p>
                    </div>
                  ))}
                </div>
              </div>

              {/* Underlying levels */}
              <div className="px-4 py-3 border-b border-gray-100">
                <p className="text-xs font-medium text-gray-500 mb-2">Underlying levels</p>
                <div className="grid grid-cols-4 gap-2 text-center">
                  {[
                    { label: 'Entry',  value: `${INR.format(analysis.entry_zone_low)}–${INR.format(analysis.entry_zone_high)}` },
                    { label: 'SL',     value: INR.format(analysis.underlying_stop) },
                    { label: 'T1',     value: INR.format(analysis.underlying_target_1) },
                    { label: 'T2',     value: INR.format(analysis.underlying_target_2) },
                  ].map(({ label, value }) => (
                    <div key={label} className="bg-gray-50 rounded-lg py-2">
                      <p className="text-xs text-gray-500 mb-0.5">{label}</p>
                      <p className="text-xs font-semibold text-gray-900">{value}</p>
                    </div>
                  ))}
                </div>
              </div>

              {/* Expanders */}
              <div className="px-4">
                <Expander title="Claude's Analysis" defaultOpen>
                  {analysis.claude_full_rationale}
                </Expander>
                <Expander title="Learning">
                  {analysis.mentor_explanation}
                </Expander>
                <Expander title="Risk">
                  {analysis.why_could_be_wrong}
                </Expander>
                <Expander title="Scoring Breakdown">
                  <div className="space-y-2 pt-1">
                    {SCORING_ROWS.map(row => {
                      const score = analysis.scoring_breakdown[row.key];
                      const pct = Math.round((score / row.max) * 100);
                      return (
                        <div key={row.key}>
                          <div className="flex justify-between text-xs mb-1">
                            <span className="text-gray-600">{row.label}</span>
                            <span className="font-semibold text-gray-800">{score}/{row.max}</span>
                          </div>
                          <div className="h-1.5 bg-gray-200 rounded-full overflow-hidden">
                            <div
                              className="h-full bg-blue-500 rounded-full transition-all duration-500"
                              style={{ width: `${pct}%` }}
                            />
                          </div>
                        </div>
                      );
                    })}
                    <div className="flex justify-between text-sm pt-2 border-t border-gray-100">
                      <span className="font-semibold text-gray-700">Total</span>
                      <span className="font-bold text-gray-900">{analysis.conviction_score}/100</span>
                    </div>
                  </div>
                </Expander>
              </div>

              {/* Action buttons */}
              <div className="px-4 pb-2 pt-3 flex gap-2">
                <button
                  onClick={handleSaveLedger}
                  className="flex-1 bg-blue-600 hover:bg-blue-700 text-white rounded-lg py-2.5 text-sm font-semibold transition-colors duration-150"
                >
                  {savedLedger ? 'Saved! ✓' : 'Save to Ledger'}
                </button>
                <button
                  onClick={handleAddWatchlist}
                  className="flex-1 bg-white border border-gray-300 text-gray-700 rounded-lg py-2.5 text-sm font-medium hover:bg-gray-50 transition-colors duration-150"
                >
                  {savedWatch ? 'Added! ✓' : 'Add to Watchlist'}
                </button>
              </div>
              {watchlistError && (
                <p className="px-4 pb-3 text-xs text-red-600">⚠ {watchlistError}</p>
              )}
            </div>
          )}

          {/* Data quality notes */}
          {result.data_quality_notes.length > 0 && (
            <div className="mt-2 space-y-1">
              {result.data_quality_notes.map((note, i) => (
                <p key={i} className="text-xs text-gray-400 italic">ⓘ {note}</p>
              ))}
            </div>
          )}

          {/* Footer */}
          <p className="text-xs text-gray-400 text-center mt-2">
            Analysis took {result.duration_seconds.toFixed(1)}s · Cost: ${result.estimated_cost_usd.toFixed(4)}
          </p>
        </div>
      )}
    </div>
  );
}
