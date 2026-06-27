import { useEffect, useState } from 'react';
import { fetchToday } from '../api';
import Badge from '../components/Badge';
import ConvictionBar from '../components/ConvictionBar';
import Expander from '../components/Expander';
import type { TodayResponse, TradeSetup } from '../types';

const INR = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });

function dte(expiry: string | null): number {
  if (!expiry) return 0;
  return Math.ceil((new Date(expiry).getTime() - Date.now()) / 86400000);
}

function fmt(v: number | null, decimals = 0): string {
  if (v == null) return '—';
  return decimals > 0 ? v.toFixed(decimals) : String(Math.round(v));
}

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

function SetupCard({ s }: { s: TradeSetup }) {
  const expiryStr = s.expiry_date
    ? new Date(s.expiry_date).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })
    : '—';

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden mb-3">
      {/* Header */}
      <div className="px-4 pt-4 pb-3 border-b border-gray-100">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <Badge stage={s.stage} />
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
              s.direction === 'LONG' ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
            }`}>
              {s.direction === 'LONG' ? '↑ LONG' : '↓ SHORT'}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-lg font-bold text-gray-900">{s.symbol}</span>
            {s.lot_size && (
              <span className="text-[10px] bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded font-mono">
                LOT: {s.lot_size}
              </span>
            )}
          </div>
        </div>
        <ConvictionBar score={s.conviction_score} />
        <div className="flex items-center gap-2 flex-wrap mt-2">
          {s.setup_type && (
            <span className="text-xs bg-gray-100 text-gray-700 px-2 py-0.5 rounded-full">
              {s.setup_type}
            </span>
          )}
          {s.iv_assessment && s.iv_assessment !== 'UNKNOWN' && (
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
              s.iv_assessment === 'LOW'  ? 'bg-green-100 text-green-800' :
              s.iv_assessment === 'HIGH' ? 'bg-red-100 text-red-800' :
                                           'bg-amber-100 text-amber-800'
            }`}>
              IV: {s.iv_assessment}
            </span>
          )}
        </div>
      </div>

      {/* Option premiums */}
      <div className="px-4 py-3 border-b border-gray-100">
        <p className="text-xs font-medium text-gray-500 mb-2">
          {s.option_type} {s.strike ? Math.round(s.strike) : '—'} · {expiryStr}
          {s.expiry_date ? ` (${dte(s.expiry_date)}d)` : ''}
        </p>
        <div className="grid grid-cols-4 gap-2 text-center">
          {[
            { label: 'Entry', value: s.entry_zone_low != null && s.entry_zone_high != null
                ? `${fmt(s.entry_zone_low)}–${fmt(s.entry_zone_high)}` : '—' },
            { label: 'SL',    value: fmt(s.stop_loss_premium) },
            { label: 'T1',    value: fmt(s.target_1_premium) },
            { label: 'T2',    value: fmt(s.target_2_premium) },
          ].map(({ label, value }) => (
            <div key={label} className="bg-gray-50 rounded-lg py-2">
              <p className="text-xs text-gray-500 mb-0.5">{label}</p>
              <p className="text-sm font-semibold text-gray-900">{value}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Trade metrics */}
      <div className="px-4 py-3 border-b border-gray-100">
        <div className="grid grid-cols-4 gap-2 text-center">
          {[
            { label: 'Risk',  value: s.max_risk_inr != null ? `₹${INR.format(s.max_risk_inr)}` : '—' },
            { label: 'R:R',   value: s.risk_reward != null ? `1:${s.risk_reward.toFixed(1)}` : '—' },
            { label: 'Lots',  value: s.lots != null && s.lot_size != null ? `${s.lots}×${s.lot_size}` : '—' },
            { label: 'DTE',   value: s.expiry_date ? `${dte(s.expiry_date)}d` : '—' },
          ].map(({ label, value }) => (
            <div key={label} className="bg-gray-50 rounded-lg py-2">
              <p className="text-xs text-gray-500 mb-0.5">{label}</p>
              <p className="text-sm font-semibold text-gray-900">{value}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Expanders */}
      {(s.claude_full_rationale || s.rr_reasoning || s.mentor_explanation || s.why_could_be_wrong) && (
        <div className="px-4 pb-2">
          {s.claude_full_rationale && (
            <Expander title="Claude's Analysis">{s.claude_full_rationale}</Expander>
          )}
          {s.rr_reasoning && (
            <Expander title="R:R & Target Reasoning">
              <p className="text-sm text-blue-700 leading-relaxed italic">{s.rr_reasoning}</p>
            </Expander>
          )}
          {s.mentor_explanation && (
            <Expander title="Learning">{s.mentor_explanation}</Expander>
          )}
          {s.why_could_be_wrong && (
            <Expander title="Risk">{s.why_could_be_wrong}</Expander>
          )}
        </div>
      )}
    </div>
  );
}

function WatchRow({ s }: { s: TradeSetup }) {
  return (
    <div className="flex items-center justify-between py-3 border-b border-gray-100 last:border-0">
      <div>
        {/* Line 1: Symbol */}
        <div className="flex items-center gap-2 mb-1">
            <span className="font-semibold text-gray-900 text-sm">{s.symbol}</span>
        </div>
        
        {/* Line 2: Bias and Lot Size */}
        <div className="flex items-center gap-2">
            <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
              s.direction === 'LONG' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
            }`}>{s.direction}</span>
            {s.lot_size && (
                <span className="text-[9px] bg-gray-50 text-gray-400 px-1 py-0.5 rounded font-mono">
                    LOT: {s.lot_size}
                </span>
            )}
        </div>
        {s.setup_type && (
          <p className="text-[10px] text-gray-400 mt-1">{s.setup_type}</p>
        )}
      </div>
      <div className="text-right">
        <span className="text-sm font-semibold text-gray-700">{s.conviction_score}</span>
        <p className="text-[10px] text-gray-400">conviction</p>
      </div>
    </div>
  );
}

export default function TodayScreen() {
  const [data, setData]       = useState<TodayResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);

  useEffect(() => {
    fetchToday()
      .then(setData)
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
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
          <p className="text-sm text-gray-400">Loading today's setups…</p>
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

  const ctx = data?.market_context;

  return (
    <div className="pb-20">
      <h1 className="text-xl font-semibold text-gray-900 px-4 pt-5 pb-3">Today's Action - v2</h1>

      {/* Stale banner */}
      {data?.stale && (
        <div className="mx-4 mb-3 bg-amber-50 border border-amber-200 rounded-xl px-4 py-2.5 flex items-center gap-2">
          <span className="text-amber-500">⚠️</span>
          <p className="text-xs text-amber-700">
            Data is stale — last pipeline ran{' '}
            {data.session_info?.hours_since_run != null
              ? `${data.session_info.hours_since_run}h ago`
              : 'over 24h ago'}
          </p>
        </div>
      )}

      {/* Market context */}
      {ctx && (
        <div className="bg-white rounded-xl shadow-sm border border-gray-100 mx-4 p-4 mb-4">
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
            Market Context · {ctx.session_date}
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
      )}

      {/* Trade ready */}
      <div className="px-4">
        {data?.trade_ready && data.trade_ready.length > 0 ? (
          <>
            <p className="text-sm font-semibold text-gray-500 mb-2">
              🟢 Trade Ready ({data.trade_ready.length})
            </p>
            {data.trade_ready.map(s => <SetupCard key={s.id} s={s} />)}
          </>
        ) : (
          <div className="bg-gray-50 rounded-xl border border-gray-100 p-4 mb-3 text-center">
            <p className="text-sm font-semibold text-gray-700">No Trade Ready setups</p>
            <p className="text-xs text-gray-400 mt-1">
              {ctx?.regime ? `Regime: ${ctx.regime}` : 'Run the pipeline tonight for new setups'}
            </p>
          </div>
        )}

        {/* Watch list */}
        {data?.watch && data.watch.length > 0 && (
          <>
            <p className="text-sm font-semibold text-gray-500 mb-2 mt-2">
              🟡 Watching ({data.watch.length})
            </p>
            <div className="bg-white rounded-xl shadow-sm border border-gray-100 px-4">
              {data.watch.map(s => <WatchRow key={s.id} s={s} />)}
            </div>
          </>
        )}

        {/* Session footer */}
        {data?.session_info?.completed_at && (
          <p className="text-xs text-gray-400 text-center mt-4">
            Pipeline ran {data.session_info.hours_since_run}h ago
            {data.session_info.cost_usd != null
              ? ` · $${data.session_info.cost_usd.toFixed(2)}`
              : ''}
          </p>
        )}
      </div>
    </div>
  );
}
