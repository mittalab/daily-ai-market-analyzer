import { useEffect, useState, useCallback } from 'react';
import { fetchDeepAnalysisCached } from '../api';
import ConvictionBar from '../components/ConvictionBar';
import Expander from '../components/Expander';
import StockChartPanel from '../components/chart/StockChartPanel';
import type { DeepAnalysisResponse, DeepAnalysisTurn } from '../types';

const INR = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });

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

// ── Text Highlighters ─────────────────────────────────────────────────────────

// Fix 3: boldChips param for Key Trigger; comma-adjacent numbers skipped to
// prevent large formatted numbers (e.g. 1,008,244) from being split into chips.
function highlightKeyTerms(text: string, boldChips = false): React.ReactNode[] {
  const regex = /(\b(?:EMA20|EMA50|EMA180|RSI14|MACD|ATR|CE|PE|Nifty|Banknifty|LONG|SHORT|SL)\b|\b\d{3,6}(?:\.\d{1,2})?\b)/gi;
  const tokens = text.split(regex);
  return tokens.map((token, i) => {
    if (token.match(regex)) {
      const prev = i > 0 ? tokens[i - 1] : '';
      const next = i < tokens.length - 1 ? tokens[i + 1] : '';
      if (prev.endsWith(',') || next.startsWith(',')) {
        return <span key={i}>{token}</span>;
      }
      return (
        <span
          key={i}
          className={`font-mono bg-gray-100 border border-gray-200 px-1 py-0.5 rounded text-[10px] ${boldChips ? 'font-bold' : 'font-semibold'} text-gray-900 mx-0.5`}
        >
          {token}
        </span>
      );
    }
    return <span key={i}>{token}</span>;
  });
}

function formatNarrative(text: string | null | undefined): React.ReactNode {
  if (!text) return null;

  let parts: string[] = [];

  if (text.includes('(') && (text.includes('(1)') || text.includes('(a)'))) {
    parts = text.split(/\((\d+|[a-zA-Z])\)/).filter(Boolean);
    const assembled: React.ReactNode[] = [];
    let numberIndex = 1;
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i].trim();
      if (!part || part.match(/^\d+$/) || part.length === 1) continue;
      assembled.push(
        <div key={i} className="flex gap-2 mb-2 items-start text-xs leading-relaxed text-gray-700">
          <span className="flex-shrink-0 bg-blue-50 text-blue-600 font-bold px-1.5 py-0.5 rounded text-[10px] mt-0.5">
            {numberIndex++}
          </span>
          <span className="flex-1">{highlightKeyTerms(part)}</span>
        </div>
      );
    }
    return <div className="space-y-1">{assembled}</div>;
  }

  if (text.includes('•') || text.includes('\n-')) {
    parts = text.split(/[\n\r]+[•-]\s*/).filter(Boolean);
    return (
      <ul className="space-y-2 list-none pl-0 my-1">
        {parts.map((part, i) => (
          <li key={i} className="flex gap-2 items-start text-xs leading-relaxed text-gray-700">
            <span className="text-blue-500 font-bold mt-0.5">•</span>
            <span className="flex-1">{highlightKeyTerms(part.trim())}</span>
          </li>
        ))}
      </ul>
    );
  }

  if (text.match(/\b\d+\.\s/)) {
    parts = text.split(/\b\d+\.\s+/).filter(Boolean);
    return (
      <div className="space-y-2 my-1">
        {parts.map((part, i) => (
          <div key={i} className="flex gap-2 items-start text-xs leading-relaxed text-gray-700">
            <span className="flex-shrink-0 bg-blue-50 text-blue-600 font-bold px-1.5 py-0.5 rounded text-[10px] mt-0.5">
              {i + 1}
            </span>
            <span className="flex-1">{highlightKeyTerms(part.trim())}</span>
          </div>
        ))}
      </div>
    );
  }

  parts = text.split(/(?<=[.!?])\s+(?=[A-Z])/).filter(Boolean);
  return (
    <div className="space-y-2 my-1">
      {parts.map((part, i) => (
        <p key={i} className="text-xs leading-relaxed text-gray-700 pl-2 border-l-2 border-blue-200">
          {highlightKeyTerms(part.trim())}
        </p>
      ))}
    </div>
  );
}

function formatRejectionNarrative(text: string | null | undefined): React.ReactNode {
  if (!text) return null;

  let parts: string[] = [];

  if (text.includes('(') && (text.includes('(1)') || text.includes('(a)'))) {
    parts = text.split(/\((\d+|[a-zA-Z])\)/).filter(Boolean);
    const assembled: React.ReactNode[] = [];
    let numberIndex = 1;
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i].trim();
      if (!part || part.match(/^\d+$/) || part.length === 1) continue;
      assembled.push(
        <div key={i} className="flex gap-2 mb-2 items-start text-xs leading-relaxed text-red-950 bg-red-50/20 border border-red-100/30 rounded-lg p-2">
          <span className="flex-shrink-0 bg-red-100 text-red-800 font-bold px-1.5 py-0.5 rounded text-[10px] mt-0.5">
            {numberIndex++}
          </span>
          <span className="flex-1">{highlightKeyTerms(part)}</span>
        </div>
      );
    }
    return <div className="space-y-1">{assembled}</div>;
  }

  if (text.match(/\b\d+\.\s/)) {
    parts = text.split(/\b\d+\.\s+/).filter(Boolean);
    return (
      <div className="space-y-2 my-1">
        {parts.map((part, i) => (
          <div key={i} className="flex gap-2 items-start text-xs leading-relaxed text-red-950 bg-red-50/20 border border-red-100/30 rounded-lg p-2">
            <span className="flex-shrink-0 bg-red-100 text-red-800 font-bold px-1.5 py-0.5 rounded text-[10px] mt-0.5">
              {i + 1}
            </span>
            <span className="flex-1">{highlightKeyTerms(part.trim())}</span>
          </div>
        ))}
      </div>
    );
  }

  parts = text.split(/(?<=[.!?])\s+(?=[A-Z])/).filter(Boolean);
  return (
    <div className="space-y-2 my-1">
      {parts.map((part, i) => (
        <p key={i} className="text-xs leading-relaxed text-red-900 pl-2 border-l-2 border-red-200 bg-red-50/10 py-1 px-2 rounded">
          {highlightKeyTerms(part.trim())}
        </p>
      ))}
    </div>
  );
}

// ── Invalidation helpers ───────────────────────────────────────────────────────

function extractInvalidationSignal(text: string): string {
  const sigMatch = text.match(/invalidation signal:?\s*([^.]+\.?)/i);
  if (sigMatch) return sigMatch[1].trim();
  const exitMatch = text.match(/exit if\s+([^.]+\.?)/i);
  if (exitMatch) return exitMatch[1].trim();
  const sentences = text.split(/[.!?]+/).map(s => s.trim()).filter(Boolean);
  return sentences[sentences.length - 1] ?? text.slice(0, 120);
}

function splitScenarios(text: string): string[] {
  if (text.includes('(1)')) return text.split(/\(\d+\)/).map(s => s.trim()).filter(Boolean);
  if (text.match(/\b1\.\s/)) return text.split(/\b\d+\.\s+/).map(s => s.trim()).filter(Boolean);
  return [text];
}

// ── Fix 6: Mentor lesson blocks ───────────────────────────────────────────────

function formatMentorLessons(text: string | null | undefined): React.ReactNode {
  if (!text) return null;
  let items: string[] = [];
  if (text.includes('(1)')) {
    items = text.split(/\(\d+\)/).map(s => s.trim()).filter(Boolean);
  } else if (text.match(/\b1\.\s/)) {
    items = text.split(/\b\d+\.\s+/).map(s => s.trim()).filter(Boolean);
  }
  const blocks = items.length > 1 ? items : [text];
  return (
    <div className="space-y-3">
      {blocks.map((item, i) => (
        <div key={i} className="bg-amber-50/60 border-l-4 border-amber-400 pl-3 pr-3 py-2.5 rounded-r-lg flex gap-2 items-start">
          {blocks.length > 1 && (
            <span className="flex-shrink-0 bg-amber-200 text-amber-800 text-[10px] font-bold px-1.5 py-0.5 rounded mt-0.5">
              {i + 1}
            </span>
          )}
          <span className="text-xs leading-relaxed text-amber-950">{item}</span>
        </div>
      ))}
    </div>
  );
}

// ── Fix 4: Dimension narrative with label/score/read-more ────────────────────

function DimensionPoint({ content, isFirst }: { content: string; isFirst: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const labelMatch = content.match(/^([A-Z][A-Z0-9\s/&-]+?)\s*\((\d+)\/(\d+)\)\s*:\s*/);
  let label = '', score = 0, max = 0, body = content;
  if (labelMatch) {
    label = labelMatch[1].trim();
    score = parseInt(labelMatch[2]);
    max   = parseInt(labelMatch[3]);
    body  = content.slice(labelMatch[0].length).trim();
  }
  const pct      = max > 0 ? score / max : 0;
  const badgeCls = pct >= 0.8 ? 'bg-green-100 text-green-800'
                 : pct >= 0.6 ? 'bg-amber-100 text-amber-800'
                 : 'bg-red-100 text-red-800';
  const LIMIT  = 500;
  const isLong = body.length > LIMIT;
  return (
    <div>
      {!isFirst && <div className="border-t border-gray-100 mt-4 mb-4" />}
      {label && (
        <div className="flex items-center gap-2 mb-1.5">
          <span className="text-xs font-bold text-gray-800">{label}</span>
          {max > 0 && (
            <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${badgeCls}`}>
              {score}/{max}
            </span>
          )}
        </div>
      )}
      <div className="text-xs leading-relaxed text-gray-700">
        {highlightKeyTerms((isLong && !expanded) ? body.slice(0, LIMIT) + '…' : body)}
      </div>
      {isLong && (
        <button onClick={() => setExpanded(v => !v)} className="text-[10px] text-gray-400 mt-1 block">
          {expanded ? 'Show less ↑' : 'Read more ↓'}
        </button>
      )}
    </div>
  );
}

function formatDimensionNarrative(text: string | null | undefined): React.ReactNode {
  if (!text) return null;
  const rawParts = text.split(/\b(\d+)\.\s+/);
  // After split with capturing group: [pre, '1', content1, '2', content2, ...]
  // Content items are at even indices >= 2
  const items: string[] = [];
  for (let i = 2; i < rawParts.length; i += 2) {
    const piece = rawParts[i]?.trim();
    if (piece) items.push(piece);
  }
  if (items.length < 2) return formatNarrative(text);
  return (
    <div>
      {items.map((item, idx) => (
        <DimensionPoint key={idx} content={item} isFirst={idx === 0} />
      ))}
    </div>
  );
}

// ── Fix 1: Action View components ─────────────────────────────────────────────

function CompactScenarioItem({ text, index }: { text: string; index: number }) {
  const [expanded, setExpanded] = useState(false);
  const signal = extractInvalidationSignal(text);
  return (
    <div
      className="bg-red-50 border border-red-100 rounded-lg p-2.5 cursor-pointer"
      onClick={() => setExpanded(v => !v)}
    >
      <div className="flex items-start gap-2">
        <span className="text-[10px] mt-0.5">⚠️</span>
        <div className="flex-1">
          <p className="text-[10px] font-bold text-red-700 uppercase mb-0.5">Scenario {index}</p>
          <p className="text-xs text-red-800">{expanded ? text : signal}</p>
          <p className="text-[10px] text-gray-400 mt-1">{expanded ? '↑ tap to collapse' : '↓ tap to expand'}</p>
        </div>
      </div>
    </div>
  );
}

function ActionView({ s, onSwitchToAnalysis }: { s: any; onSwitchToAnalysis: () => void }) {
  const [reasonExpanded, setReasonExpanded] = useState(false);
  const recInstrument = s.instrument_decision?.instrument_recommendation || s.instrument || 'NONE';
  const scenarios     = s.why_could_be_wrong ? splitScenarios(s.why_could_be_wrong) : [];

  return (
    <div className="px-4 py-3 space-y-4">

      {/* 1 — Key Trigger (most prominent element) */}
      {s.key_thing_to_watch && (
        <div>
          <p className="text-[10px] font-bold text-amber-600 uppercase tracking-widest mb-1.5">⚡ KEY TRIGGER</p>
          <div className="border-l-4 border-amber-400 bg-amber-50 rounded-r-lg p-4">
            <p className="text-[15px] leading-relaxed font-semibold text-amber-950">
              {highlightKeyTerms(s.key_thing_to_watch, true)}
            </p>
          </div>
        </div>
      )}

      {/* 2 — Trade Levels */}
      <div>
        <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-2">
          Trade Levels (Spot/Underlying)
        </p>
        <div className="grid grid-cols-4 gap-1.5 text-center">
          {([
            ['ENTRY ZONE', s.key_levels?.support_zone_low != null && s.key_levels?.support_zone_high != null
              ? `${s.key_levels.support_zone_low}–${s.key_levels.support_zone_high}` : '—'],
            ['STOP LOSS', s.key_levels?.stop_loss != null ? String(s.key_levels.stop_loss) : '—'],
            ['TARGET 1',  s.key_levels?.resistance_1 != null ? String(s.key_levels.resistance_1) : '—'],
            ['TARGET 2',  s.key_levels?.resistance_2 != null ? String(s.key_levels.resistance_2) : '—'],
          ] as [string, string][]).map(([lbl, val]) => (
            <div key={lbl} className="bg-gray-50 rounded-lg py-2">
              <p className="text-[9px] text-gray-400 uppercase mb-0.5">{lbl}</p>
              <p className="text-xs font-mono font-bold text-gray-800">{val}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Direction Flip Alert */}
      {s.setup_delta_vs_previous?.direction_changed && (
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-2.5">
          <p className="text-[10px] font-bold text-amber-700 uppercase mb-1">Direction Flip</p>
          <p className="text-xs font-semibold text-amber-900">
            {s.setup_delta_vs_previous.previous_direction} → {s.direction}
            {s.setup_delta_vs_previous.score_delta != null && ` · Score Δ: ±${s.setup_delta_vs_previous.score_delta}`}
          </p>
          {s.setup_delta_vs_previous.justification && (
            <p className="text-[10px] text-amber-700 mt-1">{s.setup_delta_vs_previous.justification}</p>
          )}
        </div>
      )}

      {/* 3 — Instrument Recommendation (compact + expand) */}
      <div>
        {s.actionable_now === false ? (
          <div className="flex items-start gap-1.5">
            <span className="text-amber-500 text-sm leading-none mt-0.5">⚠</span>
            <div>
              <p className="text-xs font-semibold text-gray-800">NONE — not actionable</p>
              {s.actionable_note && (
                <p className="text-[11px] text-amber-700 mt-0.5">{s.actionable_note}</p>
              )}
            </div>
          </div>
        ) : (
          <>
            <p className="text-xs font-semibold text-gray-800">
              {recInstrument} recommended
              {s.instrument_reason ? (
                <>
                  {' — '}
                  {reasonExpanded
                    ? s.instrument_reason
                    : s.instrument_reason.slice(0, 80) + (s.instrument_reason.length > 80 ? '…' : '')}
                </>
              ) : ''}
            </p>
            {s.instrument_reason && s.instrument_reason.length > 80 && (
              <button
                onClick={() => setReasonExpanded(v => !v)}
                className="text-[10px] text-gray-400 mt-0.5"
              >
                {reasonExpanded ? 'Show less ↑' : 'See reasoning ↓'}
              </button>
            )}
          </>
        )}
        {s.options_setup && (
          <div className="mt-1 space-y-0.5">
            <p className="text-[10px] text-gray-500">
              {s.options_setup.strike} {s.options_setup.option_type} · {s.options_setup.expiry} · {s.options_setup.days_to_expiry} DTE
            </p>
            {s.options_setup.iv_used_for_trade != null && (
              <p className="text-[10px] text-purple-600 font-medium">
                IV (traded side): {s.options_setup.iv_used_for_trade}
                {s.options_setup.atm_iv_skew != null && ` · Skew: ${s.options_setup.atm_iv_skew > 0 ? '+' : ''}${s.options_setup.atm_iv_skew}`}
                {' · '}<span className="text-gray-400">{s.options_setup.iv_source_used}</span>
              </p>
            )}
          </div>
        )}
        {!s.options_setup && s.fut_setup && (
          <div className="mt-1 space-y-0.5">
            <p className="text-[10px] text-gray-500">
              Futures · {s.fut_setup.contract_selected === 'next_month' ? 'Next-Month' : 'Near-Month'}
              {s.fut_setup.expiry ? ` · Expiry: ${s.fut_setup.expiry} (${s.fut_setup.days_to_expiry} DTE)` : ''}
            </p>
            {s.fut_setup.contract_selection_note && (
              <p className="text-[10px] text-indigo-600">{s.fut_setup.contract_selection_note}</p>
            )}
          </div>
        )}
      </div>

      {/* 4 — Options levels */}
      {s.options_setup && (
        <div>
          <p className="text-[10px] font-bold text-purple-400 uppercase tracking-wider mb-2">Options Levels</p>
          <div className="grid grid-cols-4 gap-1.5 text-center mb-1.5">
            {([
              ['ENTRY', s.options_setup.entry_premium_low != null && s.options_setup.entry_premium_high != null
                ? `${s.options_setup.entry_premium_low}–${s.options_setup.entry_premium_high}` : '—'],
              ['MID', s.options_setup.entry_premium_mid != null ? String(s.options_setup.entry_premium_mid) : '—'],
              ['SL', s.options_setup.sl_premium != null ? String(s.options_setup.sl_premium) : '—'],
              ['SL%', s.options_setup.sl_pct != null ? `${s.options_setup.sl_pct}%` : '—'],
            ] as [string, string][]).map(([lbl, val]) => (
              <div key={lbl} className="bg-purple-50/30 border border-purple-100/50 rounded-lg py-1.5">
                <p className="text-[8px] text-purple-400 uppercase mb-0.5">{lbl}</p>
                <p className="text-xs font-mono font-bold text-purple-900">{val}</p>
              </div>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-1.5 text-center">
            {([
              ['T1 PREMIUM', s.options_setup.target_1_premium != null
                ? `${s.options_setup.target_1_premium}${s.options_setup.rr_premium_t1 != null ? ` · RR ${s.options_setup.rr_premium_t1}x` : ''}` : '—'],
              ['T2 PREMIUM', s.options_setup.target_2_premium != null
                ? `${s.options_setup.target_2_premium}${s.options_setup.rr_premium_t2 != null ? ` · RR ${s.options_setup.rr_premium_t2}x` : ''}` : '—'],
            ] as [string, string][]).map(([lbl, val]) => (
              <div key={lbl} className="bg-purple-50/30 border border-purple-100/50 rounded-lg py-1.5">
                <p className="text-[8px] text-purple-400 uppercase mb-0.5">{lbl}</p>
                <p className="text-xs font-mono font-bold text-purple-900">{val}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 4 — Futures levels (when no options) */}
      {!s.options_setup && s.fut_setup && (
        <div>
          <p className="text-[10px] font-bold text-indigo-400 uppercase tracking-wider mb-2">Futures Levels</p>
          <div className="grid grid-cols-4 gap-1.5 text-center mb-1.5">
            {([
              ['ENTRY', s.fut_setup.entry_low != null && s.fut_setup.entry_high != null
                ? `${s.fut_setup.entry_low}–${s.fut_setup.entry_high}` : '—'],
              ['MID', s.fut_setup.entry_mid != null ? String(s.fut_setup.entry_mid) : '—'],
              ['FUT SL', s.fut_setup.stop_loss != null ? String(s.fut_setup.stop_loss) : '—'],
              ['SL%', s.fut_setup.sl_pct != null ? `${s.fut_setup.sl_pct}%` : '—'],
            ] as [string, string][]).map(([lbl, val]) => (
              <div key={lbl} className="bg-indigo-50/30 border border-indigo-100/50 rounded-lg py-1.5">
                <p className="text-[8px] text-indigo-400 uppercase mb-0.5">{lbl}</p>
                <p className="text-xs font-mono font-bold text-indigo-900">{val}</p>
              </div>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-1.5 text-center">
            {([
              ['TARGET 1', s.fut_setup.target_1 != null
                ? `${s.fut_setup.target_1}${s.fut_setup.rr_t1 != null ? ` · RR ${s.fut_setup.rr_t1}x` : ''}` : '—'],
              ['TARGET 2', s.fut_setup.target_2 != null
                ? `${s.fut_setup.target_2}${s.fut_setup.rr_t2 != null ? ` · RR ${s.fut_setup.rr_t2}x` : ''}` : '—'],
            ] as [string, string][]).map(([lbl, val]) => (
              <div key={lbl} className="bg-indigo-50/30 border border-indigo-100/50 rounded-lg py-1.5">
                <p className="text-[8px] text-indigo-400 uppercase mb-0.5">{lbl}</p>
                <p className="text-xs font-mono font-bold text-indigo-900">{val}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 5 — Compact invalidation scenarios */}
      {scenarios.length > 0 && (
        <div>
          <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-2">
            ⚠️ Invalidation Scenarios
          </p>
          <div className="space-y-2">
            {scenarios.map((sc, i) => (
              <CompactScenarioItem key={i} text={sc} index={i + 1} />
            ))}
          </div>
        </div>
      )}

      {/* 6 — Switch to Analysis View */}
      <button
        onClick={onSwitchToAnalysis}
        className="w-full py-2.5 bg-amber-50 border border-amber-200 rounded-lg text-sm font-semibold text-amber-900"
      >
        📖 Read Full Analysis
      </button>
    </div>
  );
}

function AnalysisView({ s }: { s: any }) {
  const recInstrument = s.instrument_decision?.instrument_recommendation || s.instrument || 'NONE';

  return (
    <div className="px-4 py-3 divide-y divide-gray-100">

      {/* 1 — Overview Grid (Fix 2: single col on mobile) */}
      <div className="pb-3 grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
        <div>
          <p className="text-gray-400 mb-0.5 font-medium">Pattern Summary</p>
          <p className="font-semibold text-gray-800">
            {s.setup_summary?.pattern_name || 'None'} ({s.setup_summary?.pattern_status || 'N/A'})
          </p>
        </div>
        <div>
          <p className="text-gray-400 mb-0.5 font-medium">Key Candle Signal</p>
          <p className="font-semibold text-gray-800">
            {s.setup_summary?.key_candle || 'None'} ({s.setup_summary?.key_candle_location || 'N/A'})
          </p>
        </div>
        <div>
          <p className="text-gray-400 mb-0.5 font-medium">Recomm. Instrument</p>
          {s.actionable_now === false ? (
            <div className="flex items-start gap-1">
              <span className="text-amber-500 text-xs leading-none mt-0.5">⚠</span>
              <div>
                <p className="font-semibold text-gray-800">NONE — not actionable</p>
                {s.actionable_note && <p className="text-[10px] text-amber-700 mt-0.5">{s.actionable_note}</p>}
              </div>
            </div>
          ) : (
            <p className="font-semibold text-gray-800">{recInstrument} — {s.instrument_reason || 'N/A'}</p>
          )}
        </div>
        <div>
          <p className="text-gray-400 mb-0.5 font-medium">Hard Gate Status</p>
          <p className={`font-semibold ${s.hard_gate_triggered ? 'text-red-600' : 'text-green-600'}`}>
            {s.hard_gate_triggered ? `TRIGGERED (${s.hard_gate_reason})` : 'PASSED'}
          </p>
        </div>
        {/* OI Wall Check */}
        {s.instrument_decision?.oi_wall_proximity_check && (
          <div>
            <p className="text-gray-400 mb-0.5 font-medium">OI Wall Check</p>
            {(() => {
              const wall = s.instrument_decision.oi_wall_proximity_check;
              return (
                <div>
                  <p className={`font-semibold ${wall.pass ? 'text-green-600' : 'text-red-600'}`}>
                    {wall.pass ? 'PASS' : 'FAIL'}
                    {wall.nearest_obstructing_wall_strike != null && ` · Wall @ ${wall.nearest_obstructing_wall_strike}`}
                  </p>
                  {wall.wall_oi_vs_neighbors_ratio != null && (
                    <p className="text-[10px] text-gray-400">Ratio: {wall.wall_oi_vs_neighbors_ratio}×</p>
                  )}
                </div>
              );
            })()}
          </div>
        )}
        {/* Direction Flip */}
        {s.setup_delta_vs_previous?.direction_changed && (
          <div className="bg-amber-50 border border-amber-200 rounded-lg p-2 col-span-1 md:col-span-2">
            <p className="text-[9px] font-bold text-amber-700 uppercase mb-1">Direction Flip</p>
            <p className="font-semibold text-amber-900">
              {s.setup_delta_vs_previous.previous_direction} → {s.direction}
              {s.setup_delta_vs_previous.score_delta != null && ` · Score Δ: ±${s.setup_delta_vs_previous.score_delta}`}
            </p>
            {s.setup_delta_vs_previous.justification && (
              <p className="text-[10px] text-amber-700 mt-1">{s.setup_delta_vs_previous.justification}</p>
            )}
          </div>
        )}
      </div>

      {/* 2 — Spot Levels & Trade Parameters */}
      <div className="py-3">
        <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-2">
          Spot (Underlying) Levels
        </p>
        <div className="grid grid-cols-4 gap-1.5 text-center mb-2.5">
          {([
            ['Entry Zone', s.key_levels?.support_zone_low != null && s.key_levels?.support_zone_high != null
              ? `${s.key_levels.support_zone_low}–${s.key_levels.support_zone_high}` : '—'],
            ['Stop Loss', s.key_levels?.stop_loss != null ? String(s.key_levels.stop_loss) : '—'],
            ['Target 1',  s.key_levels?.resistance_1 != null ? String(s.key_levels.resistance_1) : '—'],
            ['Target 2',  s.key_levels?.resistance_2 != null ? String(s.key_levels.resistance_2) : '—'],
          ] as [string, string][]).map(([lbl, val]) => (
            <div key={lbl} className="bg-gray-50 rounded-lg py-2">
              <p className="text-[9px] text-gray-400 uppercase mb-0.5">{lbl}</p>
              <p className="text-xs font-mono font-bold text-gray-800">{val}</p>
            </div>
          ))}
        </div>
        <div className="text-xs space-y-1.5 bg-gray-50 rounded-lg p-2.5">
          <div className="flex flex-col sm:flex-row sm:justify-between items-start sm:items-center">
            <span className="text-gray-400 font-medium">Support Basis:</span>
            <span className="font-semibold text-gray-800 mt-0.5 sm:mt-0 text-left sm:text-right">
              {s.key_levels?.support_basis || '—'}
            </span>
          </div>
          <div className="flex flex-col sm:flex-row sm:justify-between items-start sm:items-center border-t border-gray-200/50 pt-1.5">
            <span className="text-gray-400 font-medium">SL Invalidation Basis:</span>
            <span className="font-semibold text-gray-800 mt-0.5 sm:mt-0 text-left sm:text-right">
              {s.key_levels?.stop_loss_basis || '—'}
            </span>
          </div>
          <div className="flex justify-between items-center border-t border-gray-200/50 pt-1.5">
            <span className="text-gray-400 font-medium">Target 1 R:R Ratio:</span>
            <span className="font-mono font-bold text-gray-900 bg-white border border-gray-150 px-1.5 py-0.5 rounded text-[10px]">
              {recInstrument === 'OPTIONS'
                ? (s.options_setup?.rr_premium_t1 != null ? `1:${Number(s.options_setup.rr_premium_t1).toFixed(2)}` : '—')
                : recInstrument === 'FUT'
                ? (s.fut_setup?.rr_t1 != null ? `1:${Number(s.fut_setup.rr_t1).toFixed(2)}` : '—')
                : '—'}
            </span>
          </div>
          <div className="flex justify-between items-center border-t border-gray-200/50 pt-1.5">
            <span className="text-gray-400 font-medium">Target 2 R:R Ratio:</span>
            <span className="font-mono font-bold text-gray-900 bg-white border border-gray-150 px-1.5 py-0.5 rounded text-[10px]">
              {recInstrument === 'OPTIONS'
                ? (s.options_setup?.rr_premium_t2 != null ? `1:${Number(s.options_setup.rr_premium_t2).toFixed(2)}` : '—')
                : recInstrument === 'FUT'
                ? (s.fut_setup?.rr_t2 != null ? `1:${Number(s.fut_setup.rr_t2).toFixed(2)}` : '—')
                : '—'}
            </span>
          </div>
        </div>
      </div>

      {/* 3 — Options Contract Setup */}
      {s.options_setup && (
        <div className="py-3">
          <p className="text-[10px] font-bold text-purple-400 uppercase tracking-wider mb-2">
            Options Contract Setup
          </p>
          <div className="bg-purple-50/50 border border-purple-100 rounded-lg p-3 text-xs mb-2.5">
            <div className="flex justify-between mb-1.5">
              <span>Contract: <strong>{s.options_setup.strike} {s.options_setup.option_type}</strong></span>
              <span>Expiry: <strong>{s.options_setup.expiry}</strong> ({s.options_setup.days_to_expiry} DTE)</span>
            </div>
            {/* IV breakdown grid */}
            <div className="grid grid-cols-4 gap-1 text-center mt-1.5 pt-1.5 border-t border-purple-100/50">
              {([
                ['ATM CE IV',  s.options_setup.atm_ce_iv      != null ? String(s.options_setup.atm_ce_iv) : '—'],
                ['ATM PE IV',  s.options_setup.atm_pe_iv      != null ? String(s.options_setup.atm_pe_iv) : '—'],
                ['IV Skew',    s.options_setup.atm_iv_skew    != null
                  ? (s.options_setup.atm_iv_skew > 0 ? '+' : '') + s.options_setup.atm_iv_skew : '—'],
                ['IV (Trade)', s.options_setup.iv_used_for_trade != null ? String(s.options_setup.iv_used_for_trade) : '—'],
              ] as [string, string][]).map(([lbl, val]) => (
                <div key={lbl}>
                  <p className="text-[8px] text-purple-400 uppercase mb-0.5">{lbl}</p>
                  <p className="text-xs font-mono font-bold text-purple-900">{val}</p>
                </div>
              ))}
            </div>
            <div className="flex justify-between pt-1.5 mt-1.5 border-t border-purple-100/50">
              <span className="text-purple-400">IV Source:</span>
              <span className="font-medium text-purple-800">{s.options_setup.iv_source_used || 'N/A'}</span>
            </div>
            {s.options_setup.iv_note && (
              <div className="pt-1.5 mt-1.5 border-t border-purple-100/50">
                <p className="text-purple-400 mb-0.5">IV Note:</p>
                <p className="text-purple-800 leading-relaxed">{s.options_setup.iv_note}</p>
              </div>
            )}
          </div>
          <div className="grid grid-cols-4 gap-1.5 text-center mb-1.5">
            {([
              ['Entry', s.options_setup.entry_premium_low != null && s.options_setup.entry_premium_high != null
                ? `${s.options_setup.entry_premium_low}–${s.options_setup.entry_premium_high}` : '—'],
              ['Mid', s.options_setup.entry_premium_mid != null ? String(s.options_setup.entry_premium_mid) : '—'],
              ['SL', s.options_setup.sl_premium != null ? String(s.options_setup.sl_premium) : '—'],
              ['SL%', s.options_setup.sl_pct != null ? `${s.options_setup.sl_pct}%` : '—'],
            ] as [string, string][]).map(([lbl, val]) => (
              <div key={lbl} className="bg-purple-50/30 border border-purple-100/50 rounded-lg py-1.5">
                <p className="text-[8px] text-purple-400 uppercase mb-0.5">{lbl}</p>
                <p className="text-xs font-mono font-bold text-purple-900">{val}</p>
              </div>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-1.5 text-center">
            {([
              ['T1 Premium', s.options_setup.target_1_premium != null
                ? `${s.options_setup.target_1_premium}${s.options_setup.rr_premium_t1 != null ? ` · RR ${s.options_setup.rr_premium_t1}x` : ''}` : '—'],
              ['T2 Premium', s.options_setup.target_2_premium != null
                ? `${s.options_setup.target_2_premium}${s.options_setup.rr_premium_t2 != null ? ` · RR ${s.options_setup.rr_premium_t2}x` : ''}` : '—'],
            ] as [string, string][]).map(([lbl, val]) => (
              <div key={lbl} className="bg-purple-50/30 border border-purple-100/50 rounded-lg py-1.5">
                <p className="text-[8px] text-purple-400 uppercase mb-0.5">{lbl}</p>
                <p className="text-xs font-mono font-bold text-purple-900">{val}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 4 — Futures Trade Setup */}
      {s.fut_setup && (
        <div className="py-3">
          <p className="text-[10px] font-bold text-indigo-400 uppercase tracking-wider mb-2">
            Futures Trade Setup
          </p>
          <div className="bg-indigo-50/50 border border-indigo-100 rounded-lg p-3 text-xs mb-2.5">
            {/* Contract selection */}
            <div className="flex justify-between mb-1.5">
              <span>
                Contract:{' '}
                <strong>
                  {s.fut_setup.contract_selected === 'next_month' ? 'Next-Month ↩' :
                   s.fut_setup.contract_selected === 'near_month' ? 'Near-Month' : '—'}
                </strong>
              </span>
              <span>
                Expiry: <strong>{s.fut_setup.expiry || '—'}</strong>
                {s.fut_setup.days_to_expiry != null && ` (${s.fut_setup.days_to_expiry} DTE)`}
              </span>
            </div>
            {s.fut_setup.contract_selection_note && (
              <div className="pt-1 border-t border-indigo-100/50 mb-1.5">
                <span className="text-indigo-600 italic">{s.fut_setup.contract_selection_note}</span>
              </div>
            )}
            {s.fut_setup.basis_note && (
              <div className="pt-1 border-t border-indigo-100/50 mb-1.5">
                <span className="text-indigo-400">Basis: </span>
                <span className="font-medium text-indigo-800">{s.fut_setup.basis_note}</span>
              </div>
            )}
            <div className="flex justify-between pt-1 border-t border-indigo-100/50">
              <span>Lot Size: <strong>{s.fut_setup.lot_size || s.lot_size || '—'}</strong></span>
              <span>Sized Lots: <strong>{s.fut_setup.lots || s.lots || '—'} lot(s)</strong></span>
            </div>
            <div className="flex justify-between pt-1 border-t border-indigo-100/50">
              <span>
                Margin Risk:{' '}
                <strong className="text-indigo-700">
                  ₹{INR.format(s.fut_setup.risk_inr || s.max_risk_inr || 0)}
                </strong>
              </span>
              <span>Capital Risk %: <strong>{s.fut_setup.risk_pct_capital || s.risk_pct_capital || '—'}%</strong></span>
            </div>
          </div>
          <div className="grid grid-cols-4 gap-1.5 text-center mb-1.5">
            {([
              ['Entry', s.fut_setup.entry_low != null && s.fut_setup.entry_high != null
                ? `${s.fut_setup.entry_low}–${s.fut_setup.entry_high}` : '—'],
              ['Mid', s.fut_setup.entry_mid != null ? String(s.fut_setup.entry_mid) : '—'],
              ['FUT SL', s.fut_setup.stop_loss != null ? String(s.fut_setup.stop_loss) : '—'],
              ['SL%', s.fut_setup.sl_pct != null ? `${s.fut_setup.sl_pct}%` : '—'],
            ] as [string, string][]).map(([lbl, val]) => (
              <div key={lbl} className="bg-indigo-50/30 border border-indigo-100/50 rounded-lg py-1.5">
                <p className="text-[8px] text-indigo-400 uppercase mb-0.5">{lbl}</p>
                <p className="text-xs font-mono font-bold text-indigo-900">{val}</p>
              </div>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-1.5 text-center">
            {([
              ['Target 1', s.fut_setup.target_1 != null
                ? `${s.fut_setup.target_1}${s.fut_setup.rr_t1 != null ? ` · RR ${s.fut_setup.rr_t1}x` : ''}` : '—'],
              ['Target 2', s.fut_setup.target_2 != null
                ? `${s.fut_setup.target_2}${s.fut_setup.rr_t2 != null ? ` · RR ${s.fut_setup.rr_t2}x` : ''}` : '—'],
            ] as [string, string][]).map(([lbl, val]) => (
              <div key={lbl} className="bg-indigo-50/30 border border-indigo-100/50 rounded-lg py-1.5">
                <p className="text-[8px] text-indigo-400 uppercase mb-0.5">{lbl}</p>
                <p className="text-xs font-mono font-bold text-indigo-900">{val}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 5 — Position Sizing & Capital Allocation */}
      {s.lots != null && s.lots > 0 && recInstrument !== 'FUT' && (
        <div className="py-3 text-xs">
          <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-2">
            Position Sizing & Risk Allocation
          </p>
          <div className="grid grid-cols-3 gap-2 bg-gray-50 rounded-lg p-2.5">
            <div>
              <p className="text-gray-400 text-[10px] mb-0.5 font-medium">Sized Lots</p>
              <p className="font-bold text-gray-800">{s.lots} lot{s.lots > 1 ? 's' : ''} (Size: {s.lot_size})</p>
            </div>
            <div>
              <p className="text-gray-400 text-[10px] mb-0.5 font-medium">Capital Risk</p>
              <p className="font-bold text-red-600">₹{INR.format(s.max_risk_inr)}</p>
            </div>
            <div>
              <p className="text-gray-400 text-[10px] mb-0.5 font-medium">Risk % Capital</p>
              <p className="font-bold text-gray-800">{s.risk_pct_capital}%</p>
            </div>
          </div>
        </div>
      )}

      {/* 6 — Scoring Breakdown */}
      {s.scoring_breakdown && (
        <div className="py-3">
          <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-2">
            Scoring Breakdown
          </p>
          <div className="grid grid-cols-4 gap-1 text-center text-[10px]">
            {([
              ['D1: Technicals',       s.scoring_breakdown.dimension_1, 'text-blue-700 bg-blue-50 border-blue-100'],
              ['D2: Trade parameters', s.scoring_breakdown.dimension_2, 'text-green-700 bg-green-50 border-green-100'],
              ['D3: Market & Sector',  s.scoring_breakdown.dimension_3, 'text-amber-700 bg-amber-50 border-amber-100'],
              ['D4: Stock F&O',        s.scoring_breakdown.dimension_4, 'text-purple-700 bg-purple-50 border-purple-100'],
            ] as [string, any, string][]).map(([label, dim, cls]) => (
              <div key={label} className={`border rounded p-1.5 ${cls}`}>
                <p className="text-[8px] font-normal uppercase opacity-75 leading-tight truncate">{label}</p>
                <p className="font-bold font-mono mt-0.5">
                  {dim ? `${dim.score}/${dim.max}` : '—'}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Price/OI Regime Last 10 */}
      {Array.isArray(s.price_oi_regime_last_10) && s.price_oi_regime_last_10.length > 0 && (
        <div className="py-3">
          <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-2">
            Price / OI Regime (Last 10 Sessions)
          </p>
          <div className="space-y-1.5">
            {s.price_oi_regime_last_10.map((r: any, i: number) => {
              const regimeCls =
                r.regime === 'LONG_BUILDUP'   ? 'bg-green-100 text-green-800' :
                r.regime === 'SHORT_BUILDUP'  ? 'bg-red-100 text-red-800' :
                r.regime === 'LONG_UNWINDING' ? 'bg-amber-100 text-amber-800' :
                r.regime === 'SHORT_COVERING' ? 'bg-blue-100 text-blue-800' : 'bg-gray-100 text-gray-700';
              return (
                <div key={i} className="flex items-center gap-2 text-xs">
                  <span className="text-gray-400 font-mono text-[10px] w-20 shrink-0">{r.date}</span>
                  <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded-full uppercase shrink-0 ${regimeCls}`}>
                    {(r.regime || '').replace(/_/g, ' ')}
                  </span>
                  <span className="text-gray-500 text-[10px]">
                    Δprice {r.price_change_pct != null ? `${r.price_change_pct > 0 ? '+' : ''}${r.price_change_pct}%` : '—'}
                  </span>
                  <span className="text-gray-400 text-[10px]">
                    ΔOI {r.oi_change_pct != null ? `${r.oi_change_pct > 0 ? '+' : ''}${r.oi_change_pct}%` : '—'}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 7–12 — Dimension narratives, mentor, invalidation scenarios */}
      <div className="py-3">
        {s.dimension_1_narrative && (
          <Expander
            title="Dimension 1: Chart & Indicators Analysis"
            defaultOpen={s.stage !== 'SKIP' && s.stage !== 'REJECT'}
          >
            {formatDimensionNarrative(s.dimension_1_narrative)}
          </Expander>
        )}
        {s.dimension_2_narrative && (
          <Expander title="Dimension 2: Levels, Targets & Stop Loss Logic">
            {formatDimensionNarrative(s.dimension_2_narrative)}
          </Expander>
        )}
        {s.dimension_3_narrative && (
          <Expander title="Dimension 3: Nifty & Sector Outperformance">
            {formatDimensionNarrative(s.dimension_3_narrative)}
          </Expander>
        )}
        {s.dimension_4_narrative && (
          <Expander title="Dimension 4: Derivatives (Basis & PCR) Data">
            {formatDimensionNarrative(s.dimension_4_narrative)}
          </Expander>
        )}
        {s.mentor_notes && (
          <Expander title="Swing Trading Mentorship Lessons">
            {formatMentorLessons(s.mentor_notes)}
          </Expander>
        )}
        {/* Fix 5: full text, no truncation */}
        {s.why_could_be_wrong && (
          <Expander title="Three Specific Invalidation Scenarios">
            {formatRejectionNarrative(s.why_could_be_wrong)}
          </Expander>
        )}
      </div>

      {/* 13 — Key Trigger: most prominent section (Fix 7) */}
      {s.key_thing_to_watch && (
        <div className="pt-3">
          <p className="text-[10px] font-bold text-amber-600 uppercase tracking-widest mb-1.5">
            ⚡ KEY TRIGGER
          </p>
          <div className="border-l-4 border-amber-400 bg-amber-50 rounded-r-lg p-4">
            <p className="text-[15px] leading-relaxed font-semibold text-amber-950">
              {highlightKeyTerms(s.key_thing_to_watch, true)}
            </p>
          </div>
        </div>
      )}

      {/* Rejection / Skip Reason */}
      {(s.skip_reason || s.rejection_reason) && (
        <div className="pt-3">
          <div className="p-3 bg-red-50 border border-red-100 rounded-lg">
            <p className="text-[9px] font-bold text-red-700 uppercase tracking-wide mb-1">
              Rejection/Skip Reason
            </p>
            <p className="text-xs text-red-700 font-semibold">{s.rejection_reason || s.skip_reason}</p>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Individual stock card ──────────────────────────────────────────────────────

function StockCard({ turn }: { turn: DeepAnalysisTurn }) {
  const [isExpanded, setIsExpanded] = useState(false);
  const { symbol } = turn;
  const s = turn.analysis;

  // Fix 1: per-symbol view mode persisted in sessionStorage for the session
  const storageKey = `deep_view_${symbol}`;
  const [viewMode, setViewMode] = useState<'action' | 'analysis'>(() => {
    try { return (sessionStorage.getItem(storageKey) as 'action' | 'analysis') ?? 'action'; }
    catch { return 'action'; }
  });
  const switchView = (mode: 'action' | 'analysis') => {
    setViewMode(mode);
    try { sessionStorage.setItem(storageKey, mode); } catch {}
  };

  const dirClass =
    s.direction === 'LONG'  ? 'bg-green-100 text-green-800' :
    s.direction === 'SHORT' ? 'bg-red-100 text-red-800'     : 'bg-gray-100 text-gray-700';

  const dirLabel =
    s.direction === 'LONG'  ? '↑ LONG'  :
    s.direction === 'SHORT' ? '↓ SHORT' : s.direction || 'AUTO';

  const recInstrument = s.instrument_decision?.instrument_recommendation || s.instrument || 'NONE';
  const notActionable = s.actionable_now === false;
  const recColor =
    notActionable                ? 'bg-amber-100 text-amber-800'   :
    recInstrument === 'OPTIONS'  ? 'bg-purple-100 text-purple-800' :
    recInstrument === 'FUT'      ? 'bg-indigo-100 text-indigo-800' : 'bg-gray-100 text-gray-800';

  return (
    <div className="bg-white rounded-xl border border-gray-100 overflow-hidden mb-2 shadow-sm">
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
            <span
              className={`text-xs px-2 py-0.5 rounded-full font-medium ${recColor}`}
              title={notActionable ? (s.actionable_note ?? '') : ''}
            >
              {notActionable ? '⚠ NONE' : recInstrument}
            </span>
            {s.setup_summary?.pattern_name && (
              <span className="text-[10px] bg-blue-50 text-blue-700 px-1.5 py-0.5 rounded font-semibold">
                {s.setup_summary.pattern_name}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-base font-bold text-gray-900">{symbol}</span>
            <span className="text-gray-300 text-sm w-3 text-center">{isExpanded ? '−' : '+'}</span>
          </div>
        </div>
        {s.spot_price != null && (
          <div className="flex items-center justify-between text-[10px] mb-1 mt-0.5">
            <span className="text-gray-400">Spot Close</span>
            <span className="font-mono font-bold text-gray-700">₹{INR.format(s.spot_price)}</span>
          </div>
        )}
        <ConvictionBar score={s.conviction_score} />
        {s.adjusted_score && (
          <div className="flex items-center justify-between text-[10px] text-gray-400 mt-1">
            <span>Score: {s.conviction_score} / 100</span>
            <span>Adjusted: <b>{s.adjusted_score}</b> ({s.conviction_multiplier_applied}x)</span>
          </div>
        )}
      </div>

      {/* Toggle + Content */}
      {isExpanded && (
        /* Unfolded Z Fold / tablet: chart left, analysis right. Mobile: stacked. */
        <div className="sm:flex sm:flex-row sm:divide-x sm:divide-gray-100">

          {/* Left column: chart (sm+: 50% width; mobile: full width above analysis) */}
          {(s.stage === 'TRADE_READY' || s.stage === 'WATCH') && (
            <div className="sm:w-1/2 sm:flex-shrink-0">
              <StockChartPanel
                symbol={symbol ?? ''}
                analysisData={s}
                ohlcvData={s.ohlcv_data ?? []}
                defaultMinimised={s.stage === 'WATCH'}
              />
            </div>
          )}

          {/* Right column: analysis (sm+: 50% when chart present, else full width) */}
          <div className={`overflow-y-auto max-h-[60vh] sm:max-h-[560px] ${
            (s.stage === 'TRADE_READY' || s.stage === 'WATCH') ? 'sm:w-1/2' : 'w-full'
          }`}>
            {/* View mode toggle — sticky within the scroll container */}
            <div className="sticky top-0 z-10 bg-white px-4 py-2 border-b border-gray-100 flex gap-2">
              {(['action', 'analysis'] as const).map(mode => (
                <button
                  key={mode}
                  onClick={() => switchView(mode)}
                  className={`flex-1 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
                    viewMode === mode
                      ? 'bg-gray-900 text-white'
                      : 'bg-gray-100 text-gray-500 border border-gray-200'
                  }`}
                >
                  {mode === 'action' ? '⚡ Action View' : '📖 Full Analysis'}
                </button>
              ))}
            </div>

            {viewMode === 'action'
              ? <ActionView s={s} onSwitchToAnalysis={() => switchView('analysis')} />
              : <AnalysisView s={s} />}
          </div>
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
