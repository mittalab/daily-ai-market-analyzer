import { useEffect, useState } from 'react';
import { fetchToday, fetchDeepAnalysis } from '../api';
import Expander from '../components/Expander';
import type { TodayResponse, DeepAnalysisTurn, DeepAnalysisResponse } from '../types';

const INR = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });

// ── IST / stale helpers ───────────────────────────────────────────────────────

function getISTNow() {
  // Shift current UTC time by +5:30 and read as UTC to get IST fields
  const d = new Date(Date.now() + (5 * 60 + 30) * 60_000);
  return {
    year:  d.getUTCFullYear(),
    month: d.getUTCMonth() + 1,
    day:   d.getUTCDate(),
    hour:  d.getUTCHours(),
    dow:   d.getUTCDay(),  // 0=Sun … 6=Sat
  };
}

function extractDateFromSessionId(sessionId: string | null | undefined): string | null {
  if (!sessionId) return null;
  const match = sessionId.match(/[a-zA-Z]+_(\d{8})/);
  if (match && match[1]) {
    const yyyymmdd = match[1];
    const yyyy = yyyymmdd.substring(0, 4);
    const mm = yyyymmdd.substring(4, 6);
    const dd = yyyymmdd.substring(6, 8);
    return `${yyyy}-${mm}-${dd}`;
  }
  return null;
}

function toYMD(year: number, month: number, day: number): string {
  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

// Returns the previous trading day (always goes back, never returns today)
function prevTradingDay(year: number, month: number, day: number, dow: number): string {
  // Mon(1)→Fri, Sun(0)→Fri, Sat(6)→Fri, Tue–Fri → day−1
  const back = dow === 0 ? 2 : dow === 1 ? 3 : dow === 6 ? 1 : 1;
  const d = new Date(Date.UTC(year, month - 1, day - back));
  return toYMD(d.getUTCFullYear(), d.getUTCMonth() + 1, d.getUTCDate());
}

function computeStale(sessionDate: string | undefined): boolean {
  if (!sessionDate) return true;
  const { year, month, day, hour, dow } = getISTNow();
  const isWeekend = dow === 0 || dow === 6;
  const isAfter11 = hour >= 23;
  const todayStr  = toYMD(year, month, day);
  const prevStr   = prevTradingDay(year, month, day, dow);

  if (!isWeekend && isAfter11) {
    // After 11 PM on a trading day: pipeline must have run tonight
    return sessionDate !== todayStr;
  }
  if (isWeekend) {
    // Weekend: last trading session was Friday
    return sessionDate !== prevStr;
  }
  // Weekday before 11 PM: pipeline may have run today OR only last night's
  // data is available — accept either today or the previous trading day
  return sessionDate !== todayStr && sessionDate !== prevStr;
}

// ── Small helpers ─────────────────────────────────────────────────────────────

function VixBullet({ vix }: { vix: number | null }) {
  if (vix == null) return <span className="text-gray-400">—</span>;
  const color = vix < 15 ? 'text-green-600' : vix <= 20 ? 'text-amber-500' : 'text-red-600';
  return <span className={`font-semibold ${color}`}>{vix.toFixed(1)}</span>;
}

function FiiChip({ v }: { v: number | null }) {
  if (v == null) return <span className="text-gray-400">N/A</span>;
  const pos = v >= 0;
  return (
    <span className={`font-semibold ${pos ? 'text-green-600' : 'text-red-600'}`}>
      {pos ? '+' : ''}₹{INR.format(Math.abs(v))} Cr
    </span>
  );
}

// ── Market Analysis Panel ─────────────────────────────────────────────────────

const TREND_PILL: Record<string, string> = {
  BULLISH:  'bg-green-100 text-green-800',
  BEARISH:  'bg-red-100 text-red-800',
  SIDEWAYS: 'bg-amber-100 text-amber-800',
};

const RISK_PILL: Record<string, string> = {
  LOW:     'bg-green-100 text-green-800',
  MEDIUM:  'bg-amber-100 text-amber-800',
  HIGH:    'bg-red-100 text-red-800',
  EXTREME: 'bg-red-200 text-red-900',
};

const STANCE_CARD: Record<string, string> = {
  TAILWIND: 'bg-green-50 text-green-900 border-green-100',
  NEUTRAL:  'bg-gray-50 text-gray-800 border-gray-100',
  HEADWIND: 'bg-red-50 text-red-900 border-red-100',
};

function MarketAnalysisPanel({ turn, sessionDate }: { turn: DeepAnalysisTurn; sessionDate?: string }) {
  let a = turn.analysis;
  if (a?.error === 'JSON parse failure' && a.raw) {
    try {
      const clean = a.raw.replace(/```json\n?/, '').replace(/\n?```/, '').trim();
      a = JSON.parse(clean);
    } catch {
      a = { session_narrative: a.raw };
    }
  }

  const vix      = (a.vix_assessment     as any) || {};
  const fii      = (a.fii_dii_assessment as any) || {};
  const nps      = (a.nifty_price_structure as any) || {};
  const ikl      = (a.index_key_levels   as any) || {};
  const sectors  = (a.sector_pictures    as any) || {};
  const mentor   = (a.mentor_notes       as any) || {};
  const guidance = (a.guidance           as any) || {};
  const flags    = (a.risk_flags as string[]) || [];

  const formattedSessionDate = sessionDate ? (() => {
    const parts = sessionDate.split('-');
    if (parts.length === 3) {
      const d = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
      return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
    }
    return sessionDate;
  })() : '';

  const dateLabel = formattedSessionDate || (turn.completed_at
    ? new Date(turn.completed_at).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })
    : '');

  const mentorFields: [string, string][] = [
    ["Today's Key Lesson",   mentor.todays_key_lesson],
    ['First Signal',         mentor.what_i_looked_at_first],
    ['Sector Rotation',      mentor.sector_rotation_insight],
    ['FII/DII Reading',      mentor.fii_dii_reading],
    ['Pattern to Watch',     mentor.pattern_to_watch],
  ].filter(([, v]) => v) as [string, string][];

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 mx-4 mb-4">
      {/* Always-visible summary chips */}
      <div className="px-4 py-3 border-b border-gray-100">
        <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
          Market Analysis{dateLabel ? ` · ${dateLabel}` : ''}
        </p>
        <div className="flex flex-wrap gap-1.5">
          {/* 🟢 NEW NIFTY CLOSE VALUE CHIP */}
          {ikl.current && (
              <span className="text-xs px-2.5 py-0.5 rounded-full font-bold bg-green-600 text-white shadow-sm shrink-0">
                NIFTY: {INR.format(ikl.current)}
              </span>
          )}

          {a.market_trend && (
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${TREND_PILL[a.market_trend] || 'bg-gray-100 text-gray-700'}`}>
              {a.market_trend}
            </span>
          )}
          {a.market_volatility && (
            <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-blue-50 text-blue-700">
              Vol: {a.market_volatility}
            </span>
          )}
          {a.execution_bias && (
            <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-purple-50 text-purple-700">
              {(a.execution_bias as string).replace(/_/g, ' ')}
            </span>
          )}
          {a.session_risk_level && (
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${RISK_PILL[a.session_risk_level] || 'bg-gray-100 text-gray-700'}`}>
              Risk: {a.session_risk_level}
            </span>
          )}
          {a.conviction_multiplier != null && (
            <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-gray-100 text-gray-700">
              {(a.conviction_multiplier as number).toFixed(2)}×
            </span>
          )}
        </div>
      </div>

      {/* Expandable sections */}
      <div className="px-4 pb-2">
        {a.session_narrative && (
          <Expander title="Session Narrative">{a.session_narrative as string}</Expander>
        )}

        {(vix.current != null || vix.trend) && (
          <Expander title={`VIX — ${vix.current ?? '—'} (${vix.trend || ''})`}>
            <div className="space-y-2">
              <div className="grid grid-cols-2 gap-2">
                <div className="bg-gray-50 rounded-lg p-2">
                  <p className="text-[10px] text-gray-400 uppercase tracking-wide mb-0.5">Current</p>
                  <p className="text-sm font-bold text-gray-900">{vix.current ?? '—'}</p>
                </div>
                <div className="bg-gray-50 rounded-lg p-2">
                  <p className="text-[10px] text-gray-400 uppercase tracking-wide mb-0.5">Trend</p>
                  <p className="text-sm font-bold text-gray-900">{vix.trend || '—'}</p>
                </div>
              </div>
              {vix.character && <p className="text-sm text-gray-700">{vix.character}</p>}
              {vix.options_implication && (
                <p className="text-xs text-blue-700 bg-blue-50 rounded-lg p-2 leading-snug">{vix.options_implication}</p>
              )}
            </div>
          </Expander>
        )}

        {(fii.fii_20d_character || fii.key_insight) && (
          <Expander title="FII / DII Assessment">
            <div className="space-y-2">
              {fii.fii_20d_character && (
                <div>
                  <p className="text-[10px] font-bold text-gray-500 uppercase tracking-wide mb-0.5">FII 20d</p>
                  <p className="text-sm text-gray-700">{fii.fii_20d_character}</p>
                </div>
              )}
              {fii.dii_stance_description && (
                <div>
                  <p className="text-[10px] font-bold text-gray-500 uppercase tracking-wide mb-0.5">DII Stance</p>
                  <p className="text-sm text-gray-700">{fii.dii_stance_description}</p>
                </div>
              )}
              {fii.recent_shift === 'YES' && fii.shift_description && (
                <div className="bg-amber-50 rounded-lg p-2">
                  <p className="text-[10px] font-bold text-amber-600 uppercase tracking-wide mb-0.5">Recent Shift</p>
                  <p className="text-xs text-amber-800">{fii.shift_description}</p>
                </div>
              )}
              {fii.key_insight && (
                <div className="bg-blue-50 rounded-lg p-2">
                  <p className="text-[10px] font-bold text-blue-600 uppercase tracking-wide mb-0.5">Key Insight</p>
                  <p className="text-xs text-blue-800">{fii.key_insight}</p>
                </div>
              )}
            </div>
          </Expander>
        )}

        {nps.overall_structure && (
          <Expander title={`Nifty Price Structure — ${nps.overall_structure}`}>
            <div className="space-y-2">
              <div className="grid grid-cols-2 gap-2">
                <div className="bg-gray-50 rounded-lg p-2">
                  <p className="text-[10px] text-gray-400 uppercase tracking-wide mb-0.5">Structure</p>
                  <p className="text-xs font-bold text-gray-900">{nps.overall_structure}</p>
                </div>
                <div className="bg-gray-50 rounded-lg p-2">
                  <p className="text-[10px] text-gray-400 uppercase tracking-wide mb-0.5">Quality</p>
                  <p className="text-xs font-bold text-gray-900">{nps.trend_quality || '—'}</p>
                </div>
              </div>
              {nps.ema_structure?.arrangement_note && (
                <p className="text-xs text-blue-700 bg-blue-50 rounded-lg p-2 leading-snug">{nps.ema_structure.arrangement_note}</p>
              )}
              {nps.price_narrative && (
                <p className="text-sm text-gray-700">{nps.price_narrative}</p>
              )}
              {nps.trading_implication?.summary && (
                <div className="border-t border-gray-100 pt-2">
                  <p className="text-[10px] font-bold text-gray-500 uppercase tracking-wide mb-0.5">Trading Implication</p>
                  <p className="text-sm text-gray-700">{nps.trading_implication.summary}</p>
                  {nps.trading_implication.key_condition_to_watch && (
                    <p className="text-xs text-amber-800 bg-amber-50 rounded-lg p-2 mt-1 leading-snug">
                      Watch: {nps.trading_implication.key_condition_to_watch}
                    </p>
                  )}
                </div>
              )}
            </div>
          </Expander>
        )}

        {(ikl.support || ikl.resistance) && (
          <Expander title={`Index Key Levels — S: ${ikl.support ?? '—'} · R: ${ikl.resistance ?? '—'}`}>
            <div className="space-y-2">
              <div className="grid grid-cols-3 gap-1.5">
                {([
                  ['Str Sup',  ikl.strong_support],
                  ['Support',  ikl.support],
                  ['Current',  ikl.current],
                  ['Resist',   ikl.resistance],
                  ['Str Res',  ikl.strong_resistance],
                  ['Max Pain', ikl.max_pain],
                ] as [string, number | null | undefined][]).map(([label, val]) => (
                  <div key={label} className="bg-gray-50 rounded-lg p-2 text-center">
                    <p className="text-[9px] text-gray-400 uppercase">{label}</p>
                    <p className="text-xs font-bold text-gray-900">{val ?? '—'}</p>
                  </div>
                ))}
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                {ikl.pcr_signal && (
                  <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                    ikl.pcr_signal === 'BULLISH' ? 'bg-green-100 text-green-700' :
                    ikl.pcr_signal === 'BEARISH' ? 'bg-red-100 text-red-700' : 'bg-gray-100 text-gray-600'
                  }`}>PCR: {ikl.pcr_signal}</span>
                )}
                {ikl.levels_note && <p className="text-xs text-gray-600">{ikl.levels_note}</p>}
              </div>
            </div>
          </Expander>
        )}

        {Object.keys(sectors).length > 0 && (
          <Expander title={`Sector Pictures (${Object.keys(sectors).length})`}>
            <div className="space-y-1.5">
              {(Object.entries(sectors) as [string, any][]).map(([name, sec]) => (
                <div key={name} className={`rounded-lg p-2.5 border text-xs ${STANCE_CARD[sec.stance] || 'bg-gray-50 text-gray-800 border-gray-100'}`}>
                  <div className="flex items-center justify-between mb-0.5">
                    <span className="font-bold">{name}</span>
                    <div className="flex gap-1">
                      <span className="px-1.5 py-0.5 bg-white/70 rounded font-medium text-[9px]">{sec.trend}</span>
                      <span className="px-1.5 py-0.5 bg-white/70 rounded font-medium text-[9px]">{sec.strength}</span>
                    </div>
                  </div>
                  {sec.character    && <p className="opacity-80 leading-snug">{sec.character}</p>}
                  {sec.momentum_note && <p className="opacity-70 italic leading-snug mt-0.5">{sec.momentum_note}</p>}
                  {sec.trading_note && <p className="font-medium mt-0.5">{sec.trading_note}</p>}
                </div>
              ))}
            </div>
          </Expander>
        )}

        {flags.length > 0 && (
          <Expander title={`Risk Flags (${flags.length})`}>
            <div className="space-y-2">
              {flags.map((f, i) => (
                <div key={i} className="flex gap-2">
                  <div className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-red-400" />
                  <p className="text-sm text-red-800 leading-tight">{f}</p>
                </div>
              ))}
            </div>
          </Expander>
        )}

        {(guidance.favour || guidance.caution) && (
          <Expander title="Guidance">
            <div className="space-y-2">
              {guidance.favour && (
                <div>
                  <p className="text-[10px] font-bold text-green-600 uppercase tracking-wide mb-0.5">Favour</p>
                  <p className="text-sm text-gray-700">{guidance.favour}</p>
                </div>
              )}
              {guidance.caution && (
                <div>
                  <p className="text-[10px] font-bold text-amber-600 uppercase tracking-wide mb-0.5">Caution</p>
                  <p className="text-sm text-gray-700">{guidance.caution}</p>
                </div>
              )}
            </div>
          </Expander>
        )}

        {mentorFields.length > 0 && (
          <Expander title="Mentor Notes" defaultOpen>
            <div className="space-y-3">
              {mentorFields.map(([label, val]) => (
                <div key={label}>
                  <p className="text-[10px] font-bold text-purple-600 uppercase tracking-wide mb-0.5">{label}</p>
                  <p className="text-sm text-gray-700">{val}</p>
                </div>
              ))}
            </div>
          </Expander>
        )}
      </div>
    </div>
  );
}

// ── Fallback simple market context (when no deep analysis available) ───────────

function SimpleMarketContext({ ctx, sessionDate }: { ctx: NonNullable<TodayResponse['market_context']>, sessionDate?: string }) {
  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 mx-4 p-4 mb-4">
      <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
        Market Context · {sessionDate || ctx.session_date}
      </p>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <p className="text-xs text-gray-400">Nifty 50</p>
          <p className="text-base font-bold text-gray-900">
            {ctx.nifty_close != null ? INR.format(ctx.nifty_close) : '—'}
          </p>
        </div>
        <div>
          <p className="text-xs text-gray-400">Regime</p>
          <p className="text-sm font-semibold text-gray-800">{ctx.regime ?? '—'}</p>
        </div>
        <div>
          <p className="text-xs text-gray-400">VIX</p>
          <VixBullet vix={ctx.vix_close} />
        </div>
        <div>
          <p className="text-xs text-gray-400">FII Flow</p>
          <FiiChip v={ctx.fii_net_flow_cr} />
        </div>
      </div>
    </div>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────

export default function TodayScreen() {
  const [todayData, setTodayData]   = useState<TodayResponse | null>(null);
  const [deepData, setDeepData]     = useState<DeepAnalysisResponse | null>(null);
  const [marketTurn, setMarketTurn] = useState<DeepAnalysisTurn | null>(null);
  const [loading, setLoading]       = useState(true);
  const [error, setError]           = useState<string | null>(null);

  useEffect(() => {
    Promise.allSettled([fetchToday(), fetchDeepAnalysis()])
      .then(([todayRes, deepRes]) => {
        if (todayRes.status === 'fulfilled') {
          setTodayData(todayRes.value);
        } else {
          setError((todayRes.reason as Error)?.message || 'Failed to load');
        }
        if (deepRes.status === 'fulfilled') {
          setDeepData(deepRes.value);
          const mc = deepRes.value.turns?.find(t => t.turn_type === 'market_context');
          if (mc) setMarketTurn(mc);
        }
      })
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="text-center">
          <svg className="animate-spin h-8 w-8 text-blue-500 mx-auto mb-3" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
          </svg>
          <p className="text-sm text-gray-400">Loading today's analysis…</p>
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

  const ctx               = todayData?.market_context;
  const sessionDateFromId = extractDateFromSessionId(todayData?.session_info?.session_id);
  const displayDate       = sessionDateFromId || ctx?.session_date;
  const isStale           = computeStale(displayDate);

  // Group tonight's setups from session_claude_turns
  const deepTurns   = (deepData?.turns ?? []).filter((t: DeepAnalysisTurn) => t.turn_type === 'deep_analysis');
  const trCount     = deepTurns.filter((t: DeepAnalysisTurn) => t.analysis?.stage === 'TRADE_READY').length;
  const rejectCount = deepTurns.filter((t: DeepAnalysisTurn) => t.analysis?.stage === 'REJECT').length;
  const wtCount     = deepTurns.filter((t: DeepAnalysisTurn) => t.analysis?.stage === 'WATCH').length;
  const radarCount  = deepTurns.filter((t: DeepAnalysisTurn) => t.analysis?.stage === 'ON_RADAR').length;
  const skipCount   = deepTurns.filter((t: DeepAnalysisTurn) => t.analysis?.stage === 'SKIP').length;

  return (
    <div className="pb-20">
      <h1 className="text-xl font-semibold text-gray-900 px-4 pt-5 pb-3">Today</h1>

      {/* Stale banner */}
      {isStale && (
        <div className="mx-4 mb-3 bg-amber-50 border border-amber-200 rounded-xl px-4 py-2.5 flex items-center gap-2">
          <span className="text-amber-500">⚠️</span>
          <p className="text-xs text-amber-700">
            {todayData?.session_info?.hours_since_run != null
              ? `Data may be stale — last pipeline ran ${todayData.session_info.hours_since_run}h ago`
              : displayDate
                ? `Showing data from ${displayDate} — pipeline has not run tonight`
                : 'No analysis found — pipeline has not run yet'}
          </p>
        </div>
      )}

      {/* Market analysis — rich panel if deep analysis available, else simple fallback */}
      {marketTurn ? (
        <MarketAnalysisPanel turn={marketTurn} sessionDate={displayDate} />
      ) : ctx ? (
        <SimpleMarketContext ctx={ctx} sessionDate={displayDate} />
      ) : null}

      {/* Divider */}
      <div className="mx-4 my-4 border-t-2 border-dashed border-gray-200" />

      {/* Setup summary */}
      <div className="px-4">
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">
          Tonight's Setups
        </p>

        <div className="grid grid-cols-2 gap-2.5">
          <div className="flex items-center justify-between px-4 py-3 rounded-xl border bg-green-50 border-green-100">
            <span className="text-sm font-semibold text-green-900">🟢 Trade Ready</span>
            <span className="text-xl font-bold text-green-700">{trCount}</span>
          </div>
          <div className="flex items-center justify-between px-4 py-3 rounded-xl border bg-red-50 border-red-150">
            <span className="text-sm font-semibold text-red-955">🔴 Reject</span>
            <span className="text-xl font-bold text-red-800">{rejectCount}</span>
          </div>
          <div className="flex items-center justify-between px-4 py-3 rounded-xl border bg-amber-50 border-amber-150 col-span-2">
            <span className="text-sm font-semibold text-amber-900">🟡 Watch</span>
            <span className="text-xl font-bold text-amber-700">{wtCount}</span>
          </div>
        </div>

        {deepTurns.length > 0 && (
          <div className="grid grid-cols-2 gap-2 mt-2.5">
            <div className="flex items-center justify-between px-3 py-1.5 rounded-lg border bg-blue-50/50 border-blue-100 text-xs">
              <span className="text-blue-900 font-medium">On Radar</span>
              <span className="font-bold text-blue-700">{radarCount}</span>
            </div>
            <div className="flex items-center justify-between px-3 py-1.5 rounded-lg border bg-gray-50 border-gray-150 text-xs">
              <span className="text-gray-600 font-medium">Skip</span>
              <span className="font-bold text-gray-700">{skipCount}</span>
            </div>
          </div>
        )}

        {deepTurns.length === 0 && (
          <p className="text-xs text-gray-400 text-center mt-3">
            {ctx?.regime ? `Regime: ${ctx.regime}` : 'Run the pipeline tonight for new setups'}
          </p>
        )}

        <p className="text-xs text-gray-400 text-center mt-4">
          Open the Deep tab for full stock detail
        </p>

        {/* Session footer */}
        {todayData?.session_info?.completed_at && (
          <p className="text-xs text-gray-400 text-center mt-2">
            Pipeline ran {todayData.session_info.hours_since_run}h ago
            {todayData.session_info.cost_usd != null
              ? ` · $${todayData.session_info.cost_usd.toFixed(2)}`
              : ''}
          </p>
        )}
      </div>
    </div>
  );
}
