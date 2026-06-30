import { useEffect, useState } from 'react';
import { fetchDeepAnalysis } from '../api';
import ConvictionBar from '../components/ConvictionBar';
import Expander from '../components/Expander';
import type { DeepAnalysisResponse, DeepAnalysisTurn } from '../types';

const INR = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });

// ── Individual stock card (collapsed by default) ───────────────────────────────

function StockCard({ turn }: { turn: DeepAnalysisTurn }) {
  const [isExpanded, setIsExpanded] = useState(false);
  const { symbol } = turn;
  const s = turn.analysis;

  const dirClass =
    s.direction === 'LONG'  ? 'bg-green-100 text-green-800' :
    s.direction === 'SHORT' ? 'bg-red-100 text-red-800'     : 'bg-gray-100 text-gray-700';

  const dirLabel =
    s.direction === 'LONG' ? '↑ LONG' : s.direction === 'SHORT' ? '↓ SHORT' : s.direction || 'AUTO';

  return (
    <div className="bg-white rounded-xl border border-gray-100 overflow-hidden mb-2">
      {/* Header — always visible */}
      <div
        className={`px-4 py-3 cursor-pointer hover:bg-gray-50 transition-colors ${isExpanded ? 'border-b border-gray-100' : ''}`}
        onClick={() => setIsExpanded(o => !o)}
      >
        <div className="flex items-center justify-between mb-1.5">
          <div className="flex items-center gap-2">
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${dirClass}`}>
              {dirLabel}
            </span>
            {s.setup_type && (
              <span className="text-[10px] bg-blue-50 text-blue-700 px-1.5 py-0.5 rounded">
                {s.setup_type}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-base font-bold text-gray-900">{symbol}</span>
            <span className="text-gray-300 text-sm w-3 text-center">{isExpanded ? '−' : '+'}</span>
          </div>
        </div>
        <ConvictionBar score={s.conviction_score} />
      </div>

      {/* Expanded content */}
      {isExpanded && (
        <div className="px-4 py-3">

          {/* Trade params — TRADE_READY only */}
          {s.stage === 'TRADE_READY' && (s.entry_premium_low != null || s.strike) && (
            <div className="mb-3">
              <p className="text-xs text-gray-500 mb-1.5">
                {s.option_type} {s.strike ? Math.round(s.strike) : '—'}
                {s.expiry_date
                  ? ` · ${new Date(s.expiry_date).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })}`
                  : ''}
                {s.lot_size ? ` · LOT: ${s.lot_size}` : ''}
                {s.lots     ? ` · ${s.lots} lot${s.lots > 1 ? 's' : ''}` : ''}
              </p>
              <div className="grid grid-cols-4 gap-1.5 mb-2">
                {([
                  ['Entry', s.entry_premium_low != null && s.entry_premium_high != null
                    ? `${Math.round(s.entry_premium_low)}–${Math.round(s.entry_premium_high)}` : '—'],
                  ['SL',    s.stop_loss_premium  != null ? String(Math.round(s.stop_loss_premium))  : '—'],
                  ['T1',    s.target_1_premium   != null ? String(Math.round(s.target_1_premium))   : '—'],
                  ['T2',    s.target_2_premium   != null ? String(Math.round(s.target_2_premium))   : '—'],
                ] as [string, string][]).map(([label, value]) => (
                  <div key={label} className="bg-gray-50 rounded-lg py-2 text-center">
                    <p className="text-[9px] text-gray-400 uppercase mb-0.5">{label}</p>
                    <p className="text-xs font-bold text-gray-900">{value}</p>
                  </div>
                ))}
              </div>
              <div className="grid grid-cols-3 gap-1.5">
                {([
                  ['Risk',  s.max_risk_inr != null ? `₹${INR.format(s.max_risk_inr)}` : '—'],
                  ['R:R',   s.risk_reward  != null ? `1:${s.risk_reward.toFixed(1)}`  : '—'],
                  ['IV',    s.iv_assessment || '—'],
                ] as [string, string][]).map(([label, value]) => (
                  <div key={label} className="bg-gray-50 rounded-lg py-2 text-center">
                    <p className="text-[9px] text-gray-400 uppercase mb-0.5">{label}</p>
                    <p className={`text-xs font-bold ${
                      label === 'IV' && value === 'LOW'  ? 'text-green-600' :
                      label === 'IV' && value === 'HIGH' ? 'text-red-600'   : 'text-gray-900'
                    }`}>{value}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Rationale expanders */}
          {s.claude_full_rationale && (
            <Expander title="Deep Rationale" defaultOpen={s.stage !== 'SKIP'}>
              <p className="text-gray-700">{s.claude_full_rationale}</p>
            </Expander>
          )}
          {s.rr_reasoning && (
            <Expander title="R:R & Target Reasoning">
              <p className="text-blue-700 italic">{s.rr_reasoning}</p>
            </Expander>
          )}
          {s.mentor_explanation && (
            <Expander title="Mentor Explanation">
              <p className="text-gray-700 italic">"{s.mentor_explanation}"</p>
            </Expander>
          )}
          {s.why_could_be_wrong && (
            <Expander title="What Could Go Wrong?">
              <p className="text-red-700">{s.why_could_be_wrong}</p>
            </Expander>
          )}

          {/* Skip reason */}
          {s.skip_reason && (
            <div className="mt-2 p-3 bg-red-50 border border-red-100 rounded-lg">
              <p className="text-[10px] font-bold text-red-700 uppercase tracking-wide mb-1">Skip Reason</p>
              <p className="text-xs text-red-700">{s.skip_reason}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Stage group (collapsible section) ─────────────────────────────────────────

interface StageConfig {
  icon: string;
  label: string;
  defaultOpen: boolean;
  headerCls: string;
  chevronCls: string;
}

const STAGE_CFG: Record<string, StageConfig> = {
  TRADE_READY: {
    icon: '🟢', label: 'Trade Ready', defaultOpen: true,
    headerCls: 'bg-green-50 border-green-200 text-green-900',
    chevronCls: 'text-green-400',
  },
  WATCH: {
    icon: '🟡', label: 'Watch', defaultOpen: true,
    headerCls: 'bg-amber-50 border-amber-200 text-amber-900',
    chevronCls: 'text-amber-400',
  },
  ON_RADAR: {
    icon: '🔵', label: 'On Radar', defaultOpen: false,
    headerCls: 'bg-blue-50 border-blue-200 text-blue-900',
    chevronCls: 'text-blue-400',
  },
  SKIP: {
    icon: '⚪', label: 'Skip', defaultOpen: false,
    headerCls: 'bg-gray-50 border-gray-200 text-gray-600',
    chevronCls: 'text-gray-400',
  },
};

function StageGroup({ stage, turns }: { stage: string; turns: DeepAnalysisTurn[] }) {
  const cfg = STAGE_CFG[stage] ?? {
    icon: '•', label: stage, defaultOpen: false,
    headerCls: 'bg-gray-50 border-gray-200 text-gray-700',
    chevronCls: 'text-gray-400',
  };
  const [open, setOpen] = useState(cfg.defaultOpen);

  return (
    <div className="mb-3">
      {/* Group header */}
      <button
        onClick={() => setOpen(o => !o)}
        className={`w-full flex items-center justify-between px-4 py-3 rounded-xl border font-semibold transition-colors ${cfg.headerCls}`}
      >
        <span className="flex items-center gap-2 text-sm">
          {cfg.icon} {cfg.label}
          <span className="text-xs font-normal opacity-60">({turns.length})</span>
        </span>
        <svg
          className={`w-4 h-4 transition-transform duration-200 ${cfg.chevronCls} ${open ? 'rotate-180' : ''}`}
          fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {/* Stock cards */}
      {open && (
        <div className="mt-2">
          {turns.map(turn => (
            <StockCard key={turn.turn_number} turn={turn} />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────

const STAGE_ORDER = ['TRADE_READY', 'WATCH', 'ON_RADAR', 'SKIP'] as const;

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
          <div className="animate-spin h-8 w-8 mx-auto mb-3 border-4 border-blue-100 border-t-blue-500 rounded-full" />
          <p className="text-sm text-gray-400">Loading stock analysis…</p>
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

  // Strip market_context turns — those live in the Today tab
  const deepTurns = (data?.turns ?? []).filter(t => t.turn_type === 'deep_analysis');

  // Group and sort each group by conviction score DESC
  const grouped = STAGE_ORDER.reduce<Record<string, DeepAnalysisTurn[]>>((acc, stage) => {
    acc[stage] = deepTurns
      .filter(t => t.analysis?.stage === stage)
      .sort((a, b) => (b.analysis?.conviction_score ?? 0) - (a.analysis?.conviction_score ?? 0));
    return acc;
  }, {} as Record<string, DeepAnalysisTurn[]>);

  // Any turns with an unrecognised stage
  const others = deepTurns.filter(t => !(STAGE_ORDER as readonly string[]).includes(t.analysis?.stage));

  const totalStocks = deepTurns.length;

  return (
    <div className="pb-20">
      <div className="px-4 pt-5 pb-3">
        <h1 className="text-xl font-semibold text-gray-900">Deep Analysis</h1>
        <p className="text-xs text-gray-500 mt-1">
          {data?.session_date ? `Session: ${data.session_date}` : 'Latest session'}
          {totalStocks > 0 ? ` · ${totalStocks} stocks` : ''}
        </p>
      </div>

      <div className="px-4">
        {totalStocks > 0 ? (
          <>
            {STAGE_ORDER.map(stage =>
              grouped[stage].length > 0 ? (
                <StageGroup key={stage} stage={stage} turns={grouped[stage]} />
              ) : null
            )}
            {others.length > 0 && (
              <div className="mt-2">
                {others.map(turn => <StockCard key={turn.turn_number} turn={turn} />)}
              </div>
            )}
          </>
        ) : (
          <div className="bg-gray-50 rounded-xl border border-gray-100 p-8 text-center">
            <p className="text-sm font-semibold text-gray-500">No stock analysis found</p>
            <p className="text-xs text-gray-400 mt-2">
              Market analysis is on the Today tab. Stock detail appears here after the pipeline runs.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
