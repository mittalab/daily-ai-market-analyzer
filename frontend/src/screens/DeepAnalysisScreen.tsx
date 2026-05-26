import { useEffect, useState } from 'react';
import { fetchDeepAnalysis } from '../api';
import Badge from '../components/Badge';
import ConvictionBar from '../components/ConvictionBar';
import Expander from '../components/Expander';
import type { DeepAnalysisResponse, DeepAnalysisTurn } from '../types';

function AnalysisTurnCard({ turn }: { turn: DeepAnalysisTurn }) {
  const { symbol, analysis } = turn;
  const s = analysis;

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden mb-4">
      {/* Header */}
      <div className="px-4 pt-4 pb-3 border-b border-gray-100 bg-gray-50/30">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <Badge stage={s.stage} />
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
              s.direction === 'LONG' ? 'bg-green-100 text-green-800' : 
              s.direction === 'SHORT' ? 'bg-red-100 text-red-800' : 'bg-gray-100 text-gray-800'
            }`}>
              {s.direction === 'LONG' ? '↑ LONG' : s.direction === 'SHORT' ? '↓ SHORT' : 'AUTO'}
            </span>
          </div>
          <span className="text-lg font-bold text-gray-900">{symbol}</span>
        </div>
        <div className="flex items-center justify-between mt-2">
            <span className="text-xs text-gray-400">Turn #{turn.turn_number}</span>
            <ConvictionBar score={s.conviction_score} />
        </div>
      </div>

      <div className="px-4 py-3">
        {s.setup_type && (
            <p className="text-xs font-semibold text-blue-700 bg-blue-50 px-2 py-1 rounded inline-block mb-3">
                {s.setup_type}
            </p>
        )}

        {/* Rationales */}
        <div className="space-y-1">
            {s.claude_full_rationale && (
                <Expander title="Deep Rationale" defaultOpen={s.stage !== 'SKIP'}>
                    <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap">{s.claude_full_rationale}</p>
                </Expander>
            )}
            {s.mentor_explanation && (
                <Expander title="Mentor Explanation">
                    <p className="text-sm text-gray-700 leading-relaxed italic">"{s.mentor_explanation}"</p>
                </Expander>
            )}
            {s.why_could_be_wrong && (
                <Expander title="What Could Go Wrong?">
                    <p className="text-sm text-red-700 leading-relaxed">{s.why_could_be_wrong}</p>
                </Expander>
            )}
            {s.skip_reason && (
                <div className="p-3 bg-red-50 border border-red-100 rounded-lg">
                    <p className="text-xs font-bold text-red-800 uppercase tracking-wider mb-1">Skip Reason</p>
                    <p className="text-sm text-red-700">{s.skip_reason}</p>
                </div>
            )}
        </div>

        {/* Technical Snapshot (Mini) */}
        {s.stage !== 'SKIP' && (
            <div className="mt-4 pt-4 border-t border-gray-100 grid grid-cols-3 gap-2 text-center">
                <div className="bg-gray-50 rounded-lg p-2">
                    <p className="text-[10px] text-gray-400 uppercase tracking-tighter">RR</p>
                    <p className="text-xs font-bold text-gray-900">1:{s.risk_reward?.toFixed(1) || '—'}</p>
                </div>
                <div className="bg-gray-50 rounded-lg p-2">
                    <p className="text-[10px] text-gray-400 uppercase tracking-tighter">Strike</p>
                    <p className="text-xs font-bold text-gray-900">{s.strike || '—'} {s.option_type}</p>
                </div>
                <div className="bg-gray-50 rounded-lg p-2">
                    <p className="text-[10px] text-gray-400 uppercase tracking-tighter">IV</p>
                    <p className={`text-xs font-bold ${
                        s.iv_assessment === 'LOW' ? 'text-green-600' : 
                        s.iv_assessment === 'HIGH' ? 'text-red-600' : 'text-amber-600'
                    }`}>{s.iv_assessment || '—'}</p>
                </div>
            </div>
        )}
      </div>
    </div>
  );
}

export default function DeepAnalysisScreen() {
  const [data, setData]       = useState<DeepAnalysisResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);

  useEffect(() => {
    fetchDeepAnalysis()
      .then(setData)
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="text-center">
          <div className="animate-spin h-8 w-8 text-blue-500 mx-auto mb-3 border-4 border-blue-100 border-t-blue-500 rounded-full" />
          <p className="text-sm text-gray-400">Reviewing AI thought process…</p>
        </div>
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
      <div className="px-4 pt-5 pb-3">
        <h1 className="text-xl font-semibold text-gray-900">Deep Analysis Log</h1>
        <p className="text-xs text-gray-500 mt-1">
          {data?.session_date ? `Session Date: ${data.session_date}` : 'Viewing latest AI turns'}
        </p>
      </div>

      <div className="px-4 mt-2">
        {data?.turns && data.turns.length > 0 ? (
          data.turns.map(turn => (
            <AnalysisTurnCard key={turn.turn_number} turn={turn} />
          ))
        ) : (
          <div className="bg-gray-50 rounded-xl border border-gray-100 p-8 text-center">
            <p className="text-sm font-semibold text-gray-500">No deep analysis turns found</p>
            <p className="text-xs text-gray-400 mt-2">
              Deep analysis only runs for stocks that pass the pre-scan priority check.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
