import { useEffect, useState } from 'react';
import { fetchDeepAnalysis } from '../api';
import Badge from '../components/Badge';
import ConvictionBar from '../components/ConvictionBar';
import Expander from '../components/Expander';
import type { DeepAnalysisResponse, DeepAnalysisTurn } from '../types';

function IndexAnalysisCard({ turn }: { turn: DeepAnalysisTurn }) {
  const [isExpanded, setIsExpanded] = useState(false);
  
  // Handle backend parse failure fallback
  let a = turn.analysis;
  if (a && a.error === "JSON parse failure" && a.raw) {
    try {
      const clean = a.raw.replace(/```json\n?/, '').replace(/\n?```/, '').trim();
      a = JSON.parse(clean);
    } catch (e) {
      a = { session_narrative: a.raw };
    }
  }

  return (
    <div className="bg-blue-50 rounded-xl shadow-sm border border-blue-100 overflow-hidden mb-4">
      <div 
        className="px-4 py-3 flex items-center justify-between cursor-pointer hover:bg-blue-100/50 transition-colors"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <div className="flex items-center gap-3">
          <div className="bg-blue-600 text-white text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider">
            Market Context
          </div>
          <span className="text-sm font-bold text-blue-900">Step 1: Index Analysis</span>
          
          {/* Quick Stats - Visible in header */}
          <div className="flex items-center gap-3 ml-2 border-l border-blue-200 pl-3">
            <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full ${
              a.favourable_setups === 'LONG' ? 'bg-green-100 text-green-700' :
              a.favourable_setups === 'SHORT' ? 'bg-red-100 text-red-700' : 'bg-blue-100 text-blue-700'
            }`}>
              {a.favourable_setups}
            </span>
            <span className="text-[10px] font-mono font-bold text-blue-600 bg-blue-100/50 px-1.5 py-0.5 rounded-full">
              S:{a.index_key_levels?.support} / R:{a.index_key_levels?.resistance}
            </span>
          </div>
        </div>
        <div className="text-blue-400">
            {isExpanded ? '−' : '+'}
        </div>
      </div>

      {isExpanded && (
        <div className="px-4 pb-4 animate-in fade-in slide-in-from-top-1 duration-200">
          <div className="bg-white rounded-lg p-4 border border-blue-100 shadow-sm">
            {/* Narrative / Detailed Rationale */}
            <div className="mb-4">
              <p className="text-xs font-bold text-blue-800 uppercase tracking-wider mb-2">Market Narrative</p>
              <p className="text-sm text-gray-800 leading-relaxed whitespace-pre-wrap">
                {a.session_narrative}
              </p>
            </div>
            
            <div className="grid grid-cols-2 gap-3 mb-4">
              <div className="bg-blue-50/50 rounded-lg p-3 border border-blue-100">
                <p className="text-[10px] text-blue-500 font-bold uppercase mb-1 tracking-tighter">Favourable Bias</p>
                <p className={`text-sm font-black ${
                  a.favourable_setups === 'LONG' ? 'text-green-600' :
                  a.favourable_setups === 'SHORT' ? 'text-red-600' : 'text-blue-700'
                }`}>{a.favourable_setups}</p>
              </div>
              <div className="bg-blue-50/50 rounded-lg p-3 border border-blue-100">
                <p className="text-[10px] text-blue-500 font-bold uppercase mb-1 tracking-tighter">Nifty Range</p>
                <p className="text-sm font-mono font-bold text-gray-700">
                  {a.index_key_levels?.support || '—'} <span className="text-gray-300 mx-1">↔</span> {a.index_key_levels?.resistance || '—'}
                </p>
              </div>
            </div>

            {a.risk_flags && a.risk_flags.length > 0 && (
              <div className="mt-2 pt-3 border-t border-gray-100">
                <p className="text-[10px] text-red-500 font-bold uppercase mb-2 tracking-wider">Key Risk Flags</p>
                <div className="space-y-2">
                  {a.risk_flags.map((f: string, i: number) => (
                    <div key={i} className="flex gap-2">
                      <div className="mt-1.5 h-1.5 w-1.5 rounded-full bg-red-400 shrink-0" />
                      <p className="text-xs text-red-800 leading-tight">{f}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function AnalysisTurnCard({ turn }: { turn: DeepAnalysisTurn }) {
  const [isExpanded, setIsExpanded] = useState(false);
  const { symbol, analysis } = turn;
  const s = analysis;

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden mb-4 transition-all duration-200">
      {/* Header - Always visible */}
      <div 
        className={`px-4 pt-4 pb-3 border-b border-gray-100 cursor-pointer hover:bg-gray-50/80 transition-colors ${
          !isExpanded ? 'bg-white' : 'bg-gray-50/30'
        }`}
        onClick={() => setIsExpanded(!isExpanded)}
      >
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
          <div className="flex items-center gap-2">
            <span className="text-lg font-bold text-gray-900">{symbol}</span>
            <div className="text-gray-300 text-xs ml-1">
              {isExpanded ? '−' : '+'}
            </div>
          </div>
        </div>
        <div className="flex items-center justify-between mt-2">
            <span className="text-xs text-gray-400">Turn #{turn.turn_number}</span>
            <ConvictionBar score={s.conviction_score} />
        </div>
      </div>

      {/* Expanded Content */}
      {isExpanded && (
        <div className="px-4 py-3 animate-in fade-in slide-in-from-top-1 duration-200">
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
      )}
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
          [...data.turns]
            .sort((a, b) => {
              // Market context (turn 1) always stays at top
              if (a.turn_type === 'market_context') return -1;
              if (b.turn_type === 'market_context') return 1;
              // Otherwise sort by conviction score descending
              const scoreA = a.analysis?.conviction_score || 0;
              const scoreB = b.analysis?.conviction_score || 0;
              return scoreB - scoreA;
            })
            .map(turn => (
              turn.turn_type === 'market_context' 
                ? <IndexAnalysisCard key={turn.turn_number} turn={turn} />
                : <AnalysisTurnCard key={turn.turn_number} turn={turn} />
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
