import { useEffect, useState } from 'react';
import { fetchSystemStatus } from '../api';
import type { CostInfo, SessionTurn, SystemStatus } from '../types';

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

// ── Main screen ───────────────────────────────────────────────────────────────

export default function PerformanceScreen() {
  const [status, setStatus]   = useState<SystemStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);

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

      {/* Health */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 mx-4 p-4 mb-3">
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">Health</p>
        <Row label="Database" value={
          <span className="flex items-center gap-2">
            <StatusDot ok={status?.database.connected ?? false} />
            {status?.database.connected ? 'Connected' : 'Unreachable'}
          </span>
        } />
        <Row label="Kite Token" value={
          <span className="flex items-center gap-2">
            <StatusDot ok={status?.kite_token.valid ?? false} />
            {status?.kite_token.valid
              ? `Valid · ${status.kite_token.hours_remaining?.toFixed(1)}h left`
              : 'Expired / missing'}
          </span>
        } />
      </div>

      {/* Kite refresh */}
      <div className="mx-4 mb-3">
        <a
          href="https://api.abhishekmittal.in/kite/refresh"
          target="_blank"
          rel="noreferrer"
          className="block w-full text-center bg-white border border-gray-300 text-gray-700 rounded-xl py-3 text-sm font-medium hover:bg-gray-50 transition-colors duration-150"
        >
          🔑 Refresh Kite Token
        </a>
      </div>

      {/* Last pipeline */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 mx-4 p-4 mb-3">
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">Last Pipeline</p>
        <Row label="Date"       value={lp?.session_date ?? '—'} />
        <Row label="Status"     value={lp?.status ?? '—'} />
        <Row label="Completed"  value={fmtTime(lp?.completed_at)} />
        <Row label="Age"        value={lp?.hours_since_run != null ? `${lp.hours_since_run}h ago` : '—'} />
        <Row label="Cost"       value={lp?.cost_usd != null ? `$${lp.cost_usd.toFixed(4)}` : '—'} />
      </div>

      {/* ── API Cost Tracker ── */}
      {cost && (
        <div className="bg-white rounded-xl shadow-sm border border-gray-100 mx-4 p-4 mb-3">
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
        <div className="bg-white rounded-xl shadow-sm border border-gray-100 mx-4 p-4 mb-3">
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

      <p className="text-xs text-gray-400 text-center mt-2 mb-4">
        Server time: {fmtTime(status?.server_time_ist)}
      </p>
    </div>
  );
}
