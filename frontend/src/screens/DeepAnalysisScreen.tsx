import { useEffect, useState, useCallback } from 'react';
import { fetchDeepAnalysisCached } from '../api';
import { StockAnalysisCard } from '../components/StockAnalysisCard';
import type { DeepAnalysisResponse, DeepAnalysisTurn } from '../types';

function extractDateFromSessionId(sessionId: string | null | undefined): string | null {
  if (!sessionId) return null;
  const match = sessionId.match(/[a-zA-Z]+_(\d{8})/);
  if (match && match[1]) {
    const yyyymmdd = match[1];
    const yyyy = yyyymmdd.substring(0, 4);
    const mm   = yyyymmdd.substring(4, 6);
    const dd   = yyyymmdd.substring(6, 8);
    return `${yyyy}-${mm}-${dd}`;
  }
  return null;
}

function StockCard({ turn }: { turn: DeepAnalysisTurn }) {
  return <StockAnalysisCard symbol={turn.symbol ?? ''} analysis={turn.analysis} collapsible />;
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
  REJECT: {
    icon: '🔴', label: 'Reject', defaultOpen: false,
    headerCls: 'bg-red-50 border-red-200 text-red-950',
    chevronCls: 'text-red-400',
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

      {open && (
        <div className="mt-2 grid grid-cols-1 gap-3">
          {turns.map(turn => (
            <StockCard key={turn.turn_number} turn={turn} />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────

const STAGE_ORDER = ['TRADE_READY', 'REJECT', 'WATCH', 'ON_RADAR', 'SKIP'] as const;

export default function DeepAnalysisScreen({ refreshKey = 0 }: { refreshKey?: number }) {
  const [data, setData]       = useState<DeepAnalysisResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);

  const loadDeepAnalysis = useCallback(() => {
    setLoading(true);
    fetchDeepAnalysisCached()
      .then(setData)
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false));
  }, [refreshKey]);

  useEffect(() => {
    loadDeepAnalysis();
  }, [loadDeepAnalysis]);

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

  const deepTurns = (data?.turns ?? []).filter(t => t.turn_type === 'deep_analysis');

  const grouped = STAGE_ORDER.reduce<Record<string, DeepAnalysisTurn[]>>((acc, stage) => {
    acc[stage] = deepTurns
      .filter(t => t.analysis?.stage === stage)
      .sort((a, b) => (b.analysis?.conviction_score ?? 0) - (a.analysis?.conviction_score ?? 0));
    return acc;
  }, {} as Record<string, DeepAnalysisTurn[]>);

  const others = deepTurns.filter(t => !(STAGE_ORDER as readonly string[]).includes(t.analysis?.stage));

  const totalStocks   = deepTurns.length;
  const extractedDate = extractDateFromSessionId(data?.session_id) || data?.session_date;

  return (
    <div className="pb-20 bg-gray-50/50 min-h-screen">
      <div className="px-4 pt-5 pb-3">
        <h1 className="text-xl font-semibold text-gray-900">Deep Analysis</h1>
        <p className="text-xs text-gray-500 mt-1">
          {extractedDate ? `Session: ${extractedDate}` : 'Latest session'}
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
              <div className="mt-2 grid grid-cols-1 md:grid-cols-2 gap-3">
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
