import { useEffect, useState, useCallback } from 'react';
import { fetchSystemStatus, fetchFoStocks, fetchIndicatorValidation, fetchStockSources, saveStockSources, fetchDeepAnalysisStatusForRun, triggerDeepAnalysis } from '../api';
import type { CostInfo, SessionTurn, SystemStatus, IndicatorValidation, DeepAnalysisStatus } from '../types';

function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span className={`inline-block w-2.5 h-2.5 rounded-full ${ok ? 'bg-green-500' : 'bg-red-500'}`} />
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between py-2.5 border-b border-gray-100 last:border-0">
      <span className="text-sm text-gray-500">{label}</span>
      <span className="text-sm font-semibold text-gray-800 text-right max-w-[60%]">{value}</span>
    </div>
  );
}

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString('en-IN', {
      timeZone: 'Asia/Kolkata',
      day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
    });
  } catch { return iso; }
}

// ── Budget progress bar ───────────────────────────────────────────────────────

function BudgetBar({ spent, budget }: { spent: number; budget: number }) {
  const pct    = budget > 0 ? Math.min(100, (spent / budget) * 100) : 0;
  const danger = pct >= 80;
  const warn   = pct >= 60;
  const color  = danger ? 'bg-red-500' : warn ? 'bg-amber-500' : 'bg-blue-500';

  return (
    <div>
      <div className="flex justify-between text-xs text-gray-500 mb-1.5">
        <span>${spent.toFixed(2)} spent</span>
        <span>${budget.toFixed(2)} budget</span>
      </div>
      <div className="h-3 bg-gray-200 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-700 ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="text-right text-xs text-gray-500 mt-1">{pct.toFixed(1)}% used</p>
    </div>
  );
}

// ── Turn breakdown table ──────────────────────────────────────────────────────

function TurnBreakdown({ turns }: { turns: SessionTurn[] }) {
  // Group deep analysis turns under one line
  const turn1    = turns.find(t => t.turn_type === 'market_context');
  const turn2    = turns.find(t => t.turn_type === 'prescan');
  const deepTurns = turns.filter(t => t.turn_type === 'deep_analysis');
  const deepCost = deepTurns.reduce((s, t) => s + t.total_cost_usd, 0);

  const rows: { label: string; cost: number; sub?: string }[] = [];
  if (turn1)       rows.push({ label: 'Turn 1  Market Context', cost: turn1.total_cost_usd });
  if (turn2)       rows.push({ label: 'Turn 2  Pre-scan',       cost: turn2.total_cost_usd });
  if (deepTurns.length > 0)
    rows.push({
      label: `Turn 3-${2 + deepTurns.length}  Deep Analysis`,
      cost: deepCost,
      sub: `${deepTurns.length} stock${deepTurns.length !== 1 ? 's' : ''}`,
    });

  if (rows.length === 0) return null;

  const biggest = rows.reduce((a, b) => a.cost > b.cost ? a : b);

  return (
    <div>
      <div className="space-y-0">
        {rows.map(r => (
          <div key={r.label} className="flex items-center justify-between py-2 border-b border-gray-50 last:border-0">
            <div>
              <p className="text-sm text-gray-700 font-mono text-xs">{r.label}</p>
              {r.sub && <p className="text-xs text-gray-400">{r.sub}</p>}
            </div>
            <span className="text-sm font-semibold text-gray-800">${r.cost.toFixed(4)}</span>
          </div>
        ))}
      </div>
      <p className="text-xs text-gray-400 mt-2 pt-2 border-t border-gray-100">
        Biggest cost driver: <span className="font-medium text-gray-600">{biggest.label.trim().split(/\s{2,}/)[1]}</span>
        {biggest.sub && ` (${biggest.sub})`}
      </p>
    </div>
  );
}

// ── Context quality section ───────────────────────────────────────────────────

function ContextQualitySection({ cq }: { cq: NonNullable<CostInfo['context_quality']> }) {
  const checks = [
    { label: 'Prescan data',     ok: cq.prescan_data_complete },
    { label: 'Deep data',        ok: cq.deep_data_complete },
    { label: 'OI / Futures',     ok: cq.oi_data_available },
    { label: 'IV / Options',     ok: cq.iv_data_available },
  ];

  return (
    <div>
      <div className="grid grid-cols-2 gap-2 mb-3">
        {checks.map(c => (
          <div key={c.label} className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-medium ${
            c.ok ? 'bg-green-50 text-green-800' : 'bg-amber-50 text-amber-800'
          }`}>
            <span>{c.ok ? '✅' : '⚠️'}</span>
            <span>{c.label}</span>
          </div>
        ))}
      </div>
      <div className="flex items-center gap-2 text-xs text-gray-500 mb-2">
        <span>FII source:</span>
        <span className={`px-2 py-0.5 rounded-full font-medium ${
          cq.fii_data_source === 'LIVE' ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-700'
        }`}>{cq.fii_data_source}</span>
      </div>
      {cq.missing_data_flags.length > 0 && (
        <div className="bg-amber-50 border border-amber-100 rounded-lg p-3">
          <p className="text-xs font-semibold text-amber-700 mb-1">Missing data flags:</p>
          {cq.missing_data_flags.map((f, i) => (
            <p key={i} className="text-xs text-amber-700 italic">ⓘ {f}</p>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Indicator Validation Section ──────────────────────────────────────────────

function IndicatorValidationSection() {
  const [open, setOpen]           = useState(false);
  const [stocks, setStocks]       = useState<string[]>([]);
  const [stocksLoading, setStocksLoading] = useState(false);
  const [stocksLoaded, setStocksLoaded]   = useState(false);
  const [selectedStock, setSelectedStock] = useState('HDFCBANK');
  const [selectedDate, setSelectedDate]   = useState('');
  const [valData, setValData]     = useState<IndicatorValidation | null>(null);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState<string | null>(null);
  const [tvInputs, setTvInputs]   = useState<Record<string, string>>({});

  const loadValidation = useCallback((symbol: string, dateStr?: string) => {
    setLoading(true);
    setError(null);
    fetchIndicatorValidation(symbol, dateStr)
      .then(data => {
        setValData(data);
        setSelectedDate(data.date);
        setTvInputs({});
      })
      .catch(err => {
        setError(err instanceof Error ? err.message : 'Failed to fetch validation data');
        setValData(null);
      })
      .finally(() => setLoading(false));
  }, []);

  // Only load validation data after the section is first opened
  useEffect(() => {
    if (open && !valData && !loading) {
      loadValidation(selectedStock, selectedDate || undefined);
    }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSelectOpen = useCallback(() => {
    if (stocksLoaded || stocksLoading) return;
    setStocksLoading(true);
    fetchFoStocks()
      .then(data => { setStocks(data); setStocksLoaded(true); })
      .catch(err => console.error("Failed to load stocks:", err))
      .finally(() => setStocksLoading(false));
  }, [stocksLoaded, stocksLoading]);

  const handleStockChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const sym = e.target.value;
    setSelectedStock(sym);
    loadValidation(sym, selectedDate || undefined);
  };

  const handleDateChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setSelectedDate(e.target.value);
    loadValidation(selectedStock, e.target.value);
  };

  const handleTvInputChange = (key: string, val: string) => {
    setTvInputs(prev => ({ ...prev, [key]: val }));
  };

  const getDiffPct = (systemVal: number | null, tvStr: string): string => {
    if (systemVal === null || !tvStr) return '—';
    const tvVal = parseFloat(tvStr);
    if (isNaN(tvVal) || systemVal === 0) return '—';
    return ((Math.abs(systemVal - tvVal) / Math.abs(systemVal)) * 100).toFixed(2) + '%';
  };

  const isDiffWarning = (systemVal: number | null, tvStr: string): boolean => {
    if (systemVal === null || !tvStr) return false;
    const tvVal = parseFloat(tvStr);
    if (isNaN(tvVal) || systemVal === 0) return false;
    return (Math.abs(systemVal - tvVal) / Math.abs(systemVal)) * 100 > 1.0;
  };

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 mx-4 md:mx-0 p-4 mb-3">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between text-left"
      >
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">
          🔍 Indicator Validation
        </p>
        <span className="text-gray-400 text-xs">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="mt-4">
          <div className="grid grid-cols-2 gap-2 mb-4">
            <div>
              <label className="block text-xs text-gray-400 mb-1">Select Stock</label>
              <select
                value={selectedStock}
                onChange={handleStockChange}
                onMouseDown={handleSelectOpen}
                className="w-full bg-gray-50 border border-gray-200 rounded-lg p-2 text-sm text-gray-800 focus:outline-none focus:ring-1 focus:ring-blue-500"
              >
                {stocksLoading ? (
                  <option value={selectedStock}>{selectedStock} (loading…)</option>
                ) : stocks.length > 0 ? (
                  stocks.map(s => <option key={s} value={s}>{s}</option>)
                ) : (
                  <option value={selectedStock}>{selectedStock}</option>
                )}
              </select>
            </div>

            <div>
              <label className="block text-xs text-gray-400 mb-1">Date</label>
              <input
                type="date"
                value={selectedDate}
                onChange={handleDateChange}
                className="w-full bg-gray-50 border border-gray-200 rounded-lg p-2 text-sm text-gray-800 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>
          </div>

          {loading && (
            <div className="flex items-center justify-center py-6">
              <svg className="animate-spin h-5 w-5 text-blue-500" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
              </svg>
            </div>
          )}

          {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-xs text-red-700 mb-3">
              {error}
            </div>
          )}

          {valData && !loading && (
            <div>
              <div className="flex items-center justify-between text-xs text-gray-500 mb-3 bg-gray-50 rounded-lg p-2">
                <span>Method: <strong className="text-gray-700">{valData.computation_method}</strong></span>
                {valData.warnings.length > 0 && (
                  <span className="text-amber-600 font-medium">⚠️ {valData.warnings[0]}</span>
                )}
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="border-b border-gray-100 text-[11px] font-semibold text-gray-400 uppercase">
                      <th className="py-2">Indicator</th>
                      <th className="py-2 text-right">System</th>
                      <th className="py-2 text-center w-24">TradingView</th>
                      <th className="py-2 text-right">Diff%</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-50">
                    {Object.entries(valData.indicators).map(([key, item]) => {
                      const tvVal = tvInputs[key] || '';
                      const diffVal = getDiffPct(item.system, tvVal);
                      const isWarning = isDiffWarning(item.system, tvVal);
                      return (
                        <tr key={key} className="text-sm">
                          <td className="py-2 font-mono text-xs text-gray-600">{key}</td>
                          <td className="py-2 text-right font-semibold text-gray-800">
                            {item.system !== null ? item.system.toFixed(2) : '—'}
                          </td>
                          <td className="py-2 px-2 text-center">
                            <input
                              type="text"
                              placeholder="Manual"
                              value={tvVal}
                              onChange={(e) => handleTvInputChange(key, e.target.value)}
                              className="w-16 text-center border border-gray-200 rounded p-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-500 bg-white text-gray-800"
                            />
                          </td>
                          <td className={`py-2 text-right font-medium font-mono text-xs ${
                            isWarning ? 'text-red-500 font-semibold' : 'text-gray-500'
                          }`}>
                            {diffVal}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              <p className="text-[10px] text-gray-400 text-center mt-3 italic">
                Enter TradingView manual values to compute variance. Red indicates &gt; 1% deviation.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Deep Analysis Modal ───────────────────────────────────────────────────────

function DeepAnalysisModal({ onClose }: { onClose: () => void }) {
  const [status, setStatus]   = useState<DeepAnalysisStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [started, setStarted] = useState(false);
  const [error, setError]     = useState<string | null>(null);

  useEffect(() => {
    fetchDeepAnalysisStatusForRun()
      .then(setStatus)
      .catch(err => setError(err instanceof Error ? err.message : 'Failed to fetch status'))
      .finally(() => setLoading(false));
  }, []);

  const handleRun = async () => {
    setRunning(true);
    setError(null);
    try {
      await triggerDeepAnalysis();
      setStarted(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start analysis');
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm p-6">
        <h2 className="text-base font-semibold text-gray-800 mb-4">Run Deep Analysis</h2>

        {loading && (
          <div className="flex items-center justify-center py-6">
            <svg className="animate-spin h-6 w-6 text-blue-500" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
            </svg>
          </div>
        )}

        {!loading && error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-xs text-red-700 mb-4">
            {error}
          </div>
        )}

        {!loading && status && (
          <div className="space-y-3 mb-5">
            <div className="flex items-center justify-between bg-gray-50 rounded-lg px-3 py-2.5">
              <span className="text-xs text-gray-500">Trading day</span>
              <span className="text-sm font-semibold text-gray-800 font-mono">{status.trading_day}</span>
            </div>

            <div className="flex items-center justify-between bg-gray-50 rounded-lg px-3 py-2.5">
              <span className="text-xs text-gray-500">Analysis status</span>
              {status.already_analyzed ? (
                <span className="text-xs font-bold text-amber-600 bg-amber-50 border border-amber-200 px-2 py-0.5 rounded-full">
                  ALREADY ANALYZED
                </span>
              ) : (
                <span className="text-xs font-semibold text-green-600 bg-green-50 border border-green-200 px-2 py-0.5 rounded-full">
                  NOT YET RUN
                </span>
              )}
            </div>
          </div>
        )}

        {started && (
          <div className="bg-green-50 border border-green-200 rounded-lg p-3 text-xs text-green-700 mb-4">
            Pipeline started in the background. This will take several minutes.
          </div>
        )}

        <div className="flex gap-2">
          {!loading && status && !status.already_analyzed && !started && (
            <button
              onClick={handleRun}
              disabled={running}
              className="flex-1 bg-blue-500 hover:bg-blue-600 disabled:opacity-50 text-white rounded-xl py-2.5 text-sm font-semibold transition-colors"
            >
              {running ? 'Starting…' : 'Run'}
            </button>
          )}
          <button
            onClick={onClose}
            className="flex-1 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-xl py-2.5 text-sm font-semibold transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Stocks to Evaluate Section ────────────────────────────────────────────────

function StocksToEvaluateSection() {
  const [open, setOpen]           = useState(false);
  const [loaded, setLoaded]       = useState(false);
  const [sources, setSources]     = useState<string[]>([]);
  const [interested, setInterested] = useState<string[]>(['']);
  const [loading, setLoading]     = useState(false);
  const [saving, setSaving]       = useState(false);
  const [error, setError]         = useState<string | null>(null);
  const [saved, setSaved]         = useState(false);

  useEffect(() => {
    if (!open || loaded) return;
    setLoading(true);
    setError(null);
    fetchStockSources()
      .then(data => {
        setSources(data.stock_sources);
        setInterested(data.interested_stocks.length > 0 ? data.interested_stocks : ['']);
        setLoaded(true);
      })
      .catch(err => setError(err instanceof Error ? err.message : 'Failed to load'))
      .finally(() => setLoading(false));
  }, [open, loaded]);

  const toggleSource = (source: string) => {
    setSources(prev => prev.includes(source) ? prev.filter(s => s !== source) : [...prev, source]);
  };

  const updateInterested = (idx: number, val: string) => {
    setInterested(prev => prev.map((s, i) => i === idx ? val.toUpperCase() : s));
  };

  const removeInterested = (idx: number) => {
    setInterested(prev => {
      const next = prev.filter((_, i) => i !== idx);
      return next.length > 0 ? next : [''];
    });
  };

  const handleSave = async () => {
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      await saveStockSources({
        stock_sources: sources,
        interested_stocks: interested.filter(s => s.trim()),
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between text-left"
      >
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">
          Stocks to Evaluate
        </p>
        <span className="text-gray-400 text-xs">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="mt-4">
          {loading && (
            <div className="flex items-center justify-center py-4">
              <svg className="animate-spin h-5 w-5 text-blue-500" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
              </svg>
            </div>
          )}

          {!loading && (
            <>
              <div className="mb-4">
                <p className="text-xs text-gray-500 mb-2 font-medium">Sources</p>
                {(['NIFTY_50', 'KITE_ACTIVE_TRADES'] as const).map(src => (
                  <label key={src} className="flex items-center gap-2 py-1.5 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={sources.includes(src)}
                      onChange={() => toggleSource(src)}
                      className="w-4 h-4 rounded text-blue-500 focus:ring-blue-400"
                    />
                    <span className="text-sm text-gray-700">
                      {src === 'NIFTY_50' ? 'Nifty 50' : 'Kite Active Trades'}
                    </span>
                  </label>
                ))}
              </div>

              <div className="mb-4">
                <div className="flex items-center justify-between mb-2">
                  <p className="text-xs text-gray-500 font-medium">Interested Stocks</p>
                  <label className="flex items-center gap-1.5 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={sources.includes('INTERESTED_STOCKS')}
                      onChange={() => toggleSource('INTERESTED_STOCKS')}
                      className="w-4 h-4 rounded text-blue-500 focus:ring-blue-400"
                    />
                    <span className="text-xs text-gray-500">Enable</span>
                  </label>
                </div>
                <div className="space-y-2">
                  {interested.map((sym, idx) => (
                    <div key={idx} className="flex items-center gap-2">
                      <input
                        type="text"
                        value={sym}
                        onChange={e => updateInterested(idx, e.target.value)}
                        placeholder="e.g. TATAMOTORS"
                        className="flex-1 bg-gray-50 border border-gray-200 rounded-lg px-3 py-1.5 text-sm text-gray-800 font-mono focus:outline-none focus:ring-1 focus:ring-blue-500"
                      />
                      <button
                        onClick={() => removeInterested(idx)}
                        className="w-7 h-7 flex items-center justify-center rounded-full text-gray-400 hover:text-red-500 hover:bg-red-50 transition-colors text-sm font-bold"
                        title="Remove"
                      >
                        ✕
                      </button>
                    </div>
                  ))}
                  <button
                    onClick={() => setInterested(prev => [...prev, ''])}
                    className="flex items-center gap-1.5 text-xs text-blue-500 hover:text-blue-700 font-medium mt-1"
                  >
                    <span className="text-base leading-none font-bold">+</span> Add stock
                  </button>
                </div>
              </div>

              {error && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-2 text-xs text-red-700 mb-3">
                  {error}
                </div>
              )}

              {saved && (
                <div className="bg-green-50 border border-green-200 rounded-lg p-2 text-xs text-green-700 mb-3">
                  Settings saved successfully
                </div>
              )}

              <button
                onClick={handleSave}
                disabled={saving}
                className="w-full bg-blue-500 hover:bg-blue-600 disabled:opacity-50 text-white rounded-lg py-2 text-sm font-medium transition-colors"
              >
                {saving ? 'Saving…' : 'Save'}
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────

export default function PerformanceScreen() {
  const [status, setStatus]         = useState<SystemStatus | null>(null);
  const [loading, setLoading]       = useState(true);
  const [error, setError]           = useState<string | null>(null);
  const [showRunModal, setShowRunModal] = useState(false);

  useEffect(() => {
    fetchSystemStatus()
      .then(setStatus)
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <svg className="animate-spin h-7 w-7 text-blue-500" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
        </svg>
      </div>
    );
  }

  if (error) {
    return (
      <div className="mx-4 mt-6 bg-red-50 border border-red-200 rounded-xl p-4">
        <p className="text-sm text-red-700">{error}</p>
      </div>
    );
  }

  const lp   = status?.last_pipeline;
  const cost = status?.cost;
  const tot  = cost?.session_totals;

  return (
    <div className="pb-20">
      <h1 className="text-xl font-semibold text-gray-900 px-4 pt-5 pb-3">System Status</h1>

      {/* Main Grid: Responsive 2-column layout on md+ */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 px-4">
        {/* Left Column: Health, Token Refresh, Indicator Validation */}
        <div className="space-y-4">
          {/* Health */}
          <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4">
            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">Health</p>
            <Row label="Database" value={
              <span className="flex items-center gap-2">
                <StatusDot ok={status?.database.connected ?? false} />
                {status?.database.connected ? 'Connected' : 'Unreachable'}
              </span>
            } />
            <Row label="Kite Token" value={
              <div className="flex flex-col items-end">
                <span className="flex items-center gap-2">
                  <StatusDot ok={status?.kite_token.valid ?? false} />
                  {status?.kite_token.valid
                    ? `Valid · ${status.kite_token.hours_remaining?.toFixed(1)}h left`
                    : 'Invalid'}
                </span>
                {status?.kite_token.error_message && !status.kite_token.valid && (
                  <span className="text-[10px] text-red-500 font-normal mt-0.5 max-w-[200px] truncate" title={status.kite_token.error_message}>
                    {status.kite_token.error_message}
                  </span>
                )}
              </div>
            } />
          </div>

          {/* Kite refresh */}
          <div>
            <a
              href="https://api.abhishekmittal.in/kite/refresh"
              target="_blank"
              rel="noreferrer"
              className="block w-full text-center bg-white border border-gray-300 text-gray-700 rounded-xl py-3 text-sm font-medium hover:bg-gray-50 transition-colors duration-150"
            >
              🔑 Refresh Kite Token
            </a>
          </div>

          {/* Indicator Validation Section */}
          <IndicatorValidationSection />
        </div>

        {/* Right Column: Last Pipeline, API Cost Tracker, Scheduler Jobs */}
        <div className="space-y-4">
          {/* Last pipeline */}
          <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4">
            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">Last Pipeline</p>
            <Row label="Date"       value={lp?.session_date ?? '—'} />
            <Row label="Status"     value={lp?.status ?? '—'} />
            <Row label="Completed"  value={fmtTime(lp?.completed_at)} />
            <Row label="Age"        value={lp?.hours_since_run != null ? `${lp.hours_since_run}h ago` : '—'} />
            <Row label="Cost"       value={lp?.cost_usd != null ? `$${lp.cost_usd.toFixed(4)}` : '—'} />
          </div>

          {/* ── API Cost Tracker ── */}
          {cost && (
            <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4">
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">
                💰 API Cost Tracker
              </p>

              {/* Today's session summary */}
              {tot && (
                <div className="flex items-baseline justify-between mb-4 pb-3 border-b border-gray-100">
                  <div>
                    <p className="text-2xl font-bold text-gray-900">
                      ${tot.total_cost_usd.toFixed(2)}
                    </p>
                    <p className="text-xs text-gray-400 mt-0.5">
                      ₹{tot.total_cost_inr.toFixed(0)} · today's session
                      {cost.regime && (
                        <span className="ml-2 bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded text-xs">
                          {cost.regime}
                        </span>
                      )}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="text-sm font-semibold text-gray-700">
                      ~{tot.sessions_remaining_estimate} sessions left
                    </p>
                    <p className="text-xs text-gray-400">this month</p>
                  </div>
                </div>
              )}

              {/* Monthly budget bar */}
              {cost.monthly_spent_usd != null && cost.budget_usd != null && (
                <div className="mb-4 pb-3 border-b border-gray-100">
                  <p className="text-xs font-medium text-gray-600 mb-2">Monthly Budget</p>
                  <BudgetBar spent={cost.monthly_spent_usd} budget={cost.budget_usd} />
                  {tot && (
                    <p className="text-xs text-gray-400 mt-1.5">
                      ${tot.monthly_remaining_usd.toFixed(2)} remaining
                    </p>
                  )}
                </div>
              )}

              {/* Turn breakdown */}
              {cost.session_turns && cost.session_turns.length > 0 && (
                <div className="mb-4 pb-3 border-b border-gray-100">
                  <p className="text-xs font-medium text-gray-600 mb-2">Breakdown today</p>
                  <TurnBreakdown turns={cost.session_turns} />
                </div>
              )}

              {/* Token counts */}
              {tot && (
                <div className="grid grid-cols-2 gap-2 mb-4 pb-3 border-b border-gray-100">
                  <div className="bg-gray-50 rounded-lg p-2.5 text-center">
                    <p className="text-xs text-gray-500 mb-0.5">Input tokens</p>
                    <p className="text-sm font-semibold text-gray-800">
                      {(tot.total_input_tokens / 1000).toFixed(1)}K
                    </p>
                  </div>
                  <div className="bg-gray-50 rounded-lg p-2.5 text-center">
                    <p className="text-xs text-gray-500 mb-0.5">Output tokens</p>
                    <p className="text-sm font-semibold text-gray-800">
                      {(tot.total_output_tokens / 1000).toFixed(1)}K
                    </p>
                  </div>
                </div>
              )}

              {/* Context quality */}
              {cost.context_quality && (
                <div>
                  <p className="text-xs font-medium text-gray-600 mb-2">Context Quality</p>
                  <ContextQualitySection cq={cost.context_quality} />
                </div>
              )}

              {!tot && !cost.session_turns && (
                <p className="text-sm text-gray-400 text-center py-2">
                  Run the pipeline to see cost breakdown
                </p>
              )}
            </div>
          )}

          {/* Scheduler jobs */}
          {(status?.scheduler_jobs?.length ?? 0) > 0 && (
            <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4">
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">Scheduled Jobs</p>
              {status!.scheduler_jobs.map(job => (
                <Row
                  key={job.id}
                  label={job.name}
                  value={job.next_run ? fmtTime(job.next_run) : 'Unscheduled'}
                />
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="px-4 mt-4 space-y-3">
        <StocksToEvaluateSection />

        <button
          onClick={() => setShowRunModal(true)}
          className="w-full bg-white border border-gray-200 hover:border-blue-400 hover:bg-blue-50 text-gray-700 hover:text-blue-600 rounded-xl py-3 text-sm font-medium transition-colors shadow-sm"
        >
          Run Deep Analysis Again
        </button>
      </div>

      {showRunModal && <DeepAnalysisModal onClose={() => setShowRunModal(false)} />}

      <p className="text-xs text-gray-400 text-center mt-4 mb-4">
        Server time: {fmtTime(status?.server_time_ist)}
      </p>
    </div>
  );
}
