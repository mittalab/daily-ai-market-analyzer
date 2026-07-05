import { useEffect, useState, useCallback } from 'react';
import { fetchActiveTrades } from '../api';
import ConvictionBar from '../components/ConvictionBar';
import Expander from '../components/Expander';
import type { ActiveTradesResponse, DeepAnalysisTurn, KiteHolding, KitePosition } from '../types';

const INR = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });

function highlightKeyTerms(text: string): React.ReactNode[] {
  const regex = /(\b(?:EMA20|EMA50|EMA180|RSI14|MACD|ATR|CE|PE|Nifty|Banknifty|LONG|SHORT|SL)\b|\b\d{3,5}(?:\.\d{1,2})?\b)/gi;
  const tokens = text.split(regex);
  return tokens.map((token, i) => {
    if (token.match(regex)) {
      return (
        <span key={i} className="font-mono bg-gray-100 border border-gray-200 px-1 py-0.5 rounded text-[10px] font-semibold text-gray-900 mx-0.5">
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

// ── Zerodha status panel at top of card ────────────────────────────────────────

function KiteTradePanel({ holding, position }: { holding?: KiteHolding; position?: KitePosition }) {
  if (!holding && !position) return null;

  const isPosition = !!position;
  const qty = isPosition ? position.qty : holding!.qty;
  const avgPrice = isPosition ? position.avg : holding!.avg;
  const ltp = isPosition ? position.ltp : holding!.ltp;
  const pnl = isPosition ? position.pnl : holding!.pnl;
  const typeLabel = isPosition ? 'F&O Position' : 'Equity Holding';
  const typeCls = isPosition ? 'bg-indigo-50 text-indigo-700 border-indigo-150' : 'bg-green-50 text-green-700 border-green-150';

  const pnlCls = pnl >= 0 ? 'text-green-600 font-bold' : 'text-red-600 font-bold';
  const pnlPrefix = pnl >= 0 ? '+' : '';

  return (
    <div className="bg-gray-50 border border-gray-150/70 rounded-lg p-3 text-xs mb-3 font-sans shadow-xs">
      <div className="flex items-center justify-between mb-2">
        <span className={`text-[9px] uppercase font-bold tracking-wider px-2 py-0.5 rounded border ${typeCls}`}>
          {typeLabel}
        </span>
        <span className={pnlCls}>
          PNL: {pnlPrefix}₹{INR.format(pnl)}
        </span>
      </div>
      <div className="grid grid-cols-4 gap-1 text-center font-mono">
        <div>
          <p className="text-[8px] text-gray-400 uppercase font-sans mb-0.5">Sizing</p>
          <p className="font-bold text-gray-800">{qty}</p>
        </div>
        <div>
          <p className="text-[8px] text-gray-400 uppercase font-sans mb-0.5">Avg Entry</p>
          <p className="font-bold text-gray-800">₹{avgPrice.toFixed(1)}</p>
        </div>
        <div>
          <p className="text-[8px] text-gray-400 uppercase font-sans mb-0.5">LTP</p>
          <p className="font-bold text-gray-800">₹{ltp.toFixed(1)}</p>
        </div>
        <div>
          <p className="text-[8px] text-gray-400 uppercase font-sans mb-0.5">Value</p>
          <p className="font-bold text-gray-800">
            ₹{INR.format(Math.abs(qty) * ltp)}
          </p>
        </div>
      </div>
    </div>
  );
}

// ── Individual stock card ─────────────────────────────────────────────────────

function StockCard({ turn, holding, position }: { turn: DeepAnalysisTurn; holding?: KiteHolding; position?: KitePosition }) {
  const [isExpanded, setIsExpanded] = useState(false);
  const { symbol } = turn;
  const s = turn.analysis;

  const dirClass =
    s.direction === 'LONG'  ? 'bg-green-100 text-green-800' :
    s.direction === 'SHORT' ? 'bg-red-100 text-red-800'     : 'bg-gray-100 text-gray-700';

  const dirLabel =
    s.direction === 'LONG' ? '↑ LONG' : s.direction === 'SHORT' ? '↓ SHORT' : s.direction || 'AUTO';

  const recInstrument = s.instrument_recommendation || 'NONE';
  const recColor = 
    recInstrument === 'OPTIONS' ? 'bg-purple-100 text-purple-800' :
    recInstrument === 'FUT' ? 'bg-indigo-100 text-indigo-800' : 'bg-gray-100 text-gray-800';

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
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${recColor}`}>
              {recInstrument}
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
        <ConvictionBar score={s.conviction_score} />
        {s.adjusted_score && (
          <div className="flex items-center justify-between text-[10px] text-gray-400 mt-1">
            <span>Score: {s.conviction_score} / 100</span>
            <span>Adjusted: <b>{s.adjusted_score}</b> ({s.conviction_multiplier_applied}x)</span>
          </div>
        )}
      </div>

      {/* Expanded content */}
      {isExpanded && (
        <div className="px-4 py-3 divide-y divide-gray-100">
          
          {/* Zerodha Holdings / Positions status panel inside card */}
          {(holding || position) && (
            <div className="py-3">
              <KiteTradePanel holding={holding} position={position} />
            </div>
          )}

          {/* Section 1: Overview Grid */}
          <div className="py-3 grid grid-cols-2 gap-2 text-xs">
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
              <p className="font-semibold text-gray-800">{recInstrument} — {s.instrument_reason || 'N/A'}</p>
            </div>
            <div>
              <p className="text-gray-400 mb-0.5 font-medium">Hard Gate Status</p>
              <p className={`font-semibold ${s.hard_gate_triggered ? 'text-red-600' : 'text-green-600'}`}>
                {s.hard_gate_triggered ? `TRIGGERED (${s.hard_gate_reason})` : 'PASSED'}
              </p>
            </div>
          </div>

          {/* Section 2: Spot Levels & Trade Parameters */}
          <div className="py-3">
            <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-2">Spot (Underlying) Levels</p>
            <div className="grid grid-cols-4 gap-1.5 text-center mb-2.5">
              {[
                ['Entry Zone', s.trade_parameters?.entry_low != null && s.trade_parameters?.entry_high != null
                  ? `${s.trade_parameters.entry_low}–${s.trade_parameters.entry_high}` : '—'],
                ['Stop Loss', s.key_levels?.stop_loss != null ? String(s.key_levels.stop_loss) : '—'],
                ['Target 1', s.trade_parameters?.target_1 != null ? String(s.trade_parameters.target_1) : '—'],
                ['Target 2', s.trade_parameters?.target_2 != null ? String(s.trade_parameters.target_2) : '—'],
              ].map(([lbl, val]) => (
                <div key={lbl} className="bg-gray-50 rounded-lg py-2">
                  <p className="text-[9px] text-gray-400 uppercase mb-0.5">{lbl}</p>
                  <p className="text-xs font-mono font-bold text-gray-800">{val}</p>
                </div>
              ))}
            </div>

            <div className="text-xs space-y-1.5 bg-gray-50 rounded-lg p-2.5">
              <div className="flex flex-col sm:flex-row sm:justify-between items-start sm:items-center">
                <span className="text-gray-400 font-medium">Support Basis:</span>
                <span className="font-semibold text-gray-800 mt-0.5 sm:mt-0 text-left sm:text-right">{s.key_levels?.support_basis || '—'}</span>
              </div>
              <div className="flex flex-col sm:flex-row sm:justify-between items-start sm:items-center border-t border-gray-200/50 pt-1.5">
                <span className="text-gray-400 font-medium">SL Invalidation Basis:</span>
                <span className="font-semibold text-gray-800 mt-0.5 sm:mt-0 text-left sm:text-right">{s.key_levels?.stop_loss_basis || '—'}</span>
              </div>
              <div className="flex justify-between items-center border-t border-gray-200/50 pt-1.5">
                <span className="text-gray-400 font-medium">Target 1 R:R Ratio:</span>
                <span className="font-mono font-bold text-gray-900 bg-white border border-gray-150 px-1.5 py-0.5 rounded text-[10px]">
                  {s.trade_parameters?.rr_t1 != null ? `1:${s.trade_parameters.rr_t1.toFixed(2)}` : '—'}
                </span>
              </div>
              <div className="flex justify-between items-center border-t border-gray-200/50 pt-1.5">
                <span className="text-gray-400 font-medium">Target 2 R:R Ratio:</span>
                <span className="font-mono font-bold text-gray-900 bg-white border border-gray-150 px-1.5 py-0.5 rounded text-[10px]">
                  {s.trade_parameters?.rr_t2 != null ? `1:${s.trade_parameters.rr_t2.toFixed(2)}` : '—'}
                </span>
              </div>
            </div>
          </div>

          {/* Section 3: Recommended Setup Details */}
          {recInstrument === 'OPTIONS' && s.options_setup && (
            <div className="py-3">
              <p className="text-[10px] font-bold text-purple-400 uppercase tracking-wider mb-2">Options Contract Setup</p>
              <div className="bg-purple-50/50 border border-purple-100 rounded-lg p-3 text-xs mb-2.5">
                <div className="flex justify-between mb-1.5">
                  <span>Contract: <strong>{s.options_setup.strike} {s.options_setup.option_type}</strong></span>
                  <span>Expiry: <strong>{s.options_setup.expiry}</strong> ({s.options_setup.days_to_expiry} DTE)</span>
                </div>
                <div className="flex justify-between pt-1 border-t border-purple-100/50">
                  <span>IV Status: <strong>{s.options_setup.iv_note || 'N/A'}</strong></span>
                </div>
              </div>
              <div className="grid grid-cols-4 gap-1.5 text-center">
                {[
                  ['Premium Entry', s.options_setup.entry_premium_low != null && s.options_setup.entry_premium_high != null
                    ? `${s.options_setup.entry_premium_low}–${s.options_setup.entry_premium_high}` : '—'],
                  ['Premium SL', s.options_setup.sl_premium != null ? String(s.options_setup.sl_premium) : '—'],
                  ['Premium T1', s.options_setup.target_1_premium != null ? String(s.options_setup.target_1_premium) : '—'],
                  ['Premium T2', s.options_setup.target_2_premium != null ? String(s.options_setup.target_2_premium) : '—'],
                ].map(([lbl, val]) => (
                  <div key={lbl} className="bg-purple-50/30 border border-purple-100/50 rounded-lg py-1.5">
                    <p className="text-[8px] text-purple-400 uppercase mb-0.5">{lbl}</p>
                    <p className="text-xs font-mono font-bold text-purple-900">{val}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {recInstrument === 'FUT' && s.fut_setup && (
            <div className="py-3">
              <p className="text-[10px] font-bold text-indigo-400 uppercase tracking-wider mb-2">Futures Trade Setup</p>
              <div className="bg-indigo-50/50 border border-indigo-100 rounded-lg p-3 text-xs mb-2.5">
                <div className="flex justify-between mb-1.5">
                  <span>Contract Lot Size: <strong>{s.fut_setup.lot_size || s.lot_size || '—'}</strong></span>
                  <span>Sized Lots: <strong>{s.fut_setup.lots || s.lots || '—'} lot(s)</strong></span>
                </div>
                <div className="flex justify-between pt-1 border-t border-indigo-100/50">
                  <span>Margin Risk Allocation: <strong className="text-indigo-700">₹{INR.format(s.fut_setup.risk_inr || s.max_risk_inr || 0)}</strong></span>
                  <span>Capital Risk %: <strong>{s.fut_setup.risk_pct_capital || s.risk_pct_capital || '—'}%</strong></span>
                </div>
              </div>
              <div className="grid grid-cols-4 gap-1.5 text-center">
                {[
                  ['Futures Entry', s.fut_setup.entry_low != null && s.fut_setup.entry_high != null
                    ? `${s.fut_setup.entry_low}–${s.fut_setup.entry_high}` : '—'],
                  ['Futures SL', s.fut_setup.stop_loss != null ? String(s.fut_setup.stop_loss) : '—'],
                  ['Futures T1', s.fut_setup.target_1 != null ? String(s.fut_setup.target_1) : '—'],
                  ['Futures T2', s.fut_setup.target_2 != null ? String(s.fut_setup.target_2) : '—'],
                ].map(([lbl, val]) => (
                  <div key={lbl} className="bg-indigo-50/30 border border-indigo-100/50 rounded-lg py-1.5">
                    <p className="text-[8px] text-indigo-400 uppercase mb-0.5">{lbl}</p>
                    <p className="text-xs font-mono font-bold text-indigo-900">{val}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Section 4: Position Sizing & Capital Allocation */}
          {s.lots != null && s.lots > 0 && recInstrument !== 'FUT' && (
            <div className="py-3 text-xs">
              <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-2">Position Sizing & Risk Allocation</p>
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

          {/* Section 5: Scoring Breakdown Visuals */}
          {s.scoring_breakdown && (
            <div className="py-3">
              <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-2">Scoring Breakdown</p>
              <div className="grid grid-cols-4 gap-1 text-center text-[10px]">
                {[
                  ['D1: Technicals', s.scoring_breakdown.dimension_1, 'text-blue-700 bg-blue-50 border-blue-100'],
                  ['D2: Trade parameters', s.scoring_breakdown.dimension_2, 'text-green-700 bg-green-50 border-green-100'],
                  ['D3: Market & Sector', s.scoring_breakdown.dimension_3, 'text-amber-700 bg-amber-50 border-amber-100'],
                  ['D4: Stock F&O', s.scoring_breakdown.dimension_4, 'text-purple-700 bg-purple-50 border-purple-100'],
                ].map(([label, dim, cls]) => (
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

          {/* Section 6: Meticulous Narratives (Collapsible Expanders) */}
          <div className="py-3">
            {s.dimension_1_narrative && (
              <Expander title="Dimension 1: Chart & Indicators Analysis" defaultOpen={s.stage !== 'SKIP' && s.stage !== 'REJECT'}>
                {formatNarrative(s.dimension_1_narrative)}
              </Expander>
            )}
            {s.dimension_2_narrative && (
              <Expander title="Dimension 2: Levels, Targets & Stop Loss Logic">
                {formatNarrative(s.dimension_2_narrative)}
              </Expander>
            )}
            {s.dimension_3_narrative && (
              <Expander title="Dimension 3: Nifty & Sector Outperformance">
                {formatNarrative(s.dimension_3_narrative)}
              </Expander>
            )}
            {s.dimension_4_narrative && (
              <Expander title="Dimension 4: Derivatives (Basis & PCR) Data">
                {formatNarrative(s.dimension_4_narrative)}
              </Expander>
            )}
            {s.mentor_notes && (
              <Expander title="Swing Trading Mentorship Lessons">
                <div className="bg-amber-50/50 border-l-4 border-amber-400 p-3.5 rounded-r-lg text-xs leading-relaxed text-amber-950 shadow-xs">
                  <span className="text-lg font-serif text-amber-400 font-bold block leading-none mb-1">“</span>
                  <p className="italic">{highlightKeyTerms(s.mentor_notes)}</p>
                </div>
              </Expander>
            )}
            {s.why_could_be_wrong && (
              <Expander title="Three Specific Invalidation Scenarios">
                {formatRejectionNarrative(s.why_could_be_wrong)}
              </Expander>
            )}
            {s.key_thing_to_watch && (
              <Expander title="Key Trigger for Morning Market Open">
                <div className="text-blue-950 font-semibold text-xs leading-relaxed bg-blue-50/50 border border-blue-200 rounded-lg p-3.5 shadow-xs">
                  <div className="flex gap-2.5 items-start">
                    <span className="text-sm">🔔</span>
                    <span className="flex-1">{highlightKeyTerms(s.key_thing_to_watch)}</span>
                  </div>
                </div>
              </Expander>
            )}
          </div>

          {/* Skip / Rejection Reason */}
          {(s.skip_reason || s.rejection_reason) && (
            <div className="pt-3">
              <div className="p-3 bg-red-50 border border-red-100 rounded-lg">
                <p className="text-[9px] font-bold text-red-700 uppercase tracking-wide mb-1 font-semibold">Rejection/Skip Reason</p>
                <p className="text-xs text-red-700 font-semibold">{s.rejection_reason || s.skip_reason}</p>
              </div>
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

function StageGroup({
  stage,
  turns,
  holdings,
  positions,
}: {
  stage: string;
  turns: DeepAnalysisTurn[];
  holdings: Record<string, KiteHolding>;
  positions: Record<string, KitePosition>;
}) {
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
          {turns.map(turn => {
            const sym = turn.symbol || '';
            return (
              <StockCard
                key={turn.turn_number}
                turn={turn}
                holding={holdings[sym]}
                position={positions[sym]}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────

const STAGE_ORDER = ['TRADE_READY', 'REJECT', 'WATCH', 'ON_RADAR', 'SKIP'] as const;

export default function ActiveTradesScreen() {
  const [data, setData]       = useState<ActiveTradesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);

  const loadActiveTrades = useCallback(() => {
    setLoading(true);
    fetchActiveTrades()
      .then(setData)
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    loadActiveTrades();
  }, [loadActiveTrades]);

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="text-center">
          <div className="animate-spin h-8 w-8 mx-auto mb-3 border-4 border-blue-100 border-t-blue-500 rounded-full" />
          <p className="text-sm text-gray-400">Loading active trades…</p>
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
  const holdings = data?.holdings ?? {};
  const positions = data?.positions ?? {};

  // Group and sort each group by conviction score DESC
  const grouped = STAGE_ORDER.reduce<Record<string, DeepAnalysisTurn[]>>((acc, stage) => {
    acc[stage] = deepTurns
      .filter(t => t.analysis?.stage === stage)
      .sort((a, b) => (b.analysis?.conviction_score ?? 0) - (a.analysis?.conviction_score ?? 0));
    return acc;
  }, {} as Record<string, DeepAnalysisTurn[]>);

  const others = deepTurns.filter(t => !(STAGE_ORDER as readonly string[]).includes(t.analysis?.stage));
  const totalTrades = deepTurns.length;

  // Calculate total portfolio value and P&L sums
  let totalPNL = 0;
  let totalHoldingValue = 0;
  let totalPositionValue = 0;

  Object.values(holdings).forEach(h => {
    totalPNL += h.pnl;
    totalHoldingValue += Math.abs(h.qty) * h.ltp;
  });

  Object.values(positions).forEach(p => {
    totalPNL += p.pnl;
    totalPositionValue += Math.abs(p.qty) * p.ltp;
  });

  const portfolioValue = totalHoldingValue + totalPositionValue;
  const pnlCls = totalPNL >= 0 ? 'text-green-600' : 'text-red-600';
  const pnlPrefix = totalPNL >= 0 ? '+' : '';

  return (
    <div className="pb-20 bg-gray-50/50 min-h-screen">
      {/* Portfolio overview header */}
      <div className="bg-white border-b border-gray-200 px-4 pt-5 pb-4 sticky top-0 z-40 shadow-xs">
        <h1 className="text-xl font-bold text-gray-900 flex items-center gap-1.5">
          💼 Active Trades
        </h1>
        
        {totalTrades > 0 && (
          <div className="grid grid-cols-3 gap-2 mt-4 text-xs font-mono text-center">
            <div className="bg-gray-50 border border-gray-100 rounded-lg py-2">
              <p className="text-[9px] text-gray-400 font-sans uppercase mb-0.5">Live Portfolio Value</p>
              <p className="font-bold text-gray-800">₹{INR.format(portfolioValue)}</p>
            </div>
            <div className="bg-gray-50 border border-gray-100 rounded-lg py-2">
              <p className="text-[9px] text-gray-400 font-sans uppercase mb-0.5">Net Profit/Loss</p>
              <p className={`font-bold ${pnlCls}`}>{pnlPrefix}₹{INR.format(totalPNL)}</p>
            </div>
            <div className="bg-gray-50 border border-gray-100 rounded-lg py-2">
              <p className="text-[9px] text-gray-400 font-sans uppercase mb-0.5">Active Underlyings</p>
              <p className="font-bold text-gray-800">{totalTrades} Stocks</p>
            </div>
          </div>
        )}
      </div>

      <div className="px-4 mt-3">
        {totalTrades > 0 ? (
          <>
            {STAGE_ORDER.map(stage =>
              grouped[stage].length > 0 ? (
                <StageGroup
                  key={stage}
                  stage={stage}
                  turns={grouped[stage]}
                  holdings={holdings}
                  positions={positions}
                />
              ) : null
            )}
            {others.length > 0 && (
              <div className="mt-2">
                {others.map(turn => {
                  const sym = turn.symbol || '';
                  return (
                    <StockCard
                      key={turn.turn_number}
                      turn={turn}
                      holding={holdings[sym]}
                      position={positions[sym]}
                    />
                  );
                })}
              </div>
            )}
          </>
        ) : (
          <div className="bg-white rounded-2xl border border-gray-100 p-8 text-center mt-4">
            <span className="text-3xl">📭</span>
            <p className="text-sm font-semibold text-gray-700 mt-3">No active trades found</p>
            <p className="text-xs text-gray-400 mt-2 max-w-xs mx-auto">
              Your Zerodha Kite portfolio does not contain any open equity holdings or F&O positions currently.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
