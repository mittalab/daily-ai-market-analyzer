import { useEffect, useState } from 'react';
import { fetchWatchlist } from '../api';
import type { WatchlistEntry } from '../types';

const STAGE_COLOR: Record<string, string> = {
  TRADE_READY: 'bg-green-100 text-green-800',
  WATCH:       'bg-amber-100 text-amber-800',
  ON_RADAR:    'bg-blue-100 text-blue-700',
  MANUAL_ADD:  'bg-purple-100 text-purple-700',
};

function stageColor(stage: string | null): string {
  return STAGE_COLOR[stage ?? ''] ?? 'bg-gray-100 text-gray-600';
}

export default function WatchlistScreen() {
  const [entries, setEntries] = useState<WatchlistEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);

  useEffect(() => {
    fetchWatchlist()
      .then(r => setEntries(r.watchlist))
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

  return (
    <div className="pb-20">
      <h1 className="text-xl font-semibold text-gray-900 px-4 pt-5 pb-3">Watchlist</h1>

      {entries.length === 0 ? (
        <div className="flex flex-col items-center justify-center min-h-[50vh] text-center px-4">
          <span className="text-5xl mb-4">👁</span>
          <p className="text-sm font-semibold text-gray-700">No stocks on watch</p>
          <p className="text-xs text-gray-400 mt-1">
            Stocks flagged as WATCH by the pipeline appear here
          </p>
        </div>
      ) : (
        <div className="mx-4">
          <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
            {entries.map((e, i) => (
              <div
                key={e.symbol}
                className={`px-4 py-3 flex items-start justify-between ${
                  i < entries.length - 1 ? 'border-b border-gray-100' : ''
                }`}
              >
                <div className="flex-1 min-w-0">
                  {/* Line 1: Symbol and Stage */}
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-semibold text-gray-900">{e.symbol}</span>
                    {e.current_stage && (
                      <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${stageColor(e.current_stage)}`}>
                        {e.current_stage.replace('_', ' ')}
                      </span>
                    )}
                  </div>
                  
                  {/* Line 2: Bias and Lot Size */}
                  <div className="flex items-center gap-2 mb-1">
                    {e.direction_bias && (
                      <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
                        e.direction_bias === 'LONG' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
                      }`}>
                        {e.direction_bias}
                      </span>
                    )}
                    {e.lot_size && (
                      <span className="text-[10px] bg-gray-50 text-gray-400 px-1.5 py-0.5 rounded font-mono">
                        LOT: {e.lot_size}
                      </span>
                    )}
                  </div>
                  {e.last_analysis_notes && (
                    <p className="text-xs text-gray-500 leading-snug line-clamp-2">
                      {e.last_analysis_notes}
                    </p>
                  )}
                </div>
                <div className="text-right ml-3 shrink-0">
                  {e.days_in_stage != null && (
                    <>
                      <p className="text-sm font-bold text-gray-700">{e.days_in_stage}d</p>
                      <p className="text-xs text-gray-400">in stage</p>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
