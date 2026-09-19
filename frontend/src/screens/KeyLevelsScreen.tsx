import { useState, useEffect, useMemo } from 'react';
import { fetchKeyLevelsCached } from '../api';
import type { KeyLevelsResponse, KeyLevelsStock, KeyLevelsZone } from '../types';
import LightweightChart, { type PriceBand } from '../components/chart/LightweightChart';
import type { OHLCVRow } from '../components/chart/chartUtils';
import ErrorBoundary from '../components/ErrorBoundary';

// ── Confluence flags parser ──────────────────────────────────────────────────

export function parseConfluenceFlags(flags: unknown): string[] {
  if (Array.isArray(flags)) {
    return flags.map(String).map(s => s.trim()).filter(Boolean);
  }
  if (typeof flags === 'string') {
    const trimmed = flags.trim();
    if (!trimmed) return [];
    if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
      try {
        const parsed = JSON.parse(trimmed);
        if (Array.isArray(parsed)) {
          return parsed.map(String).map(s => s.trim()).filter(Boolean);
        }
      } catch {
        // Fall through to comma split
      }
    }
    return trimmed
      .split(',')
      .map(s => s.trim().replace(/^["']|["']$/g, ''))
      .filter(Boolean);
  }
  return [];
}

// ── Conviction badge ──────────────────────────────────────────────────────────

function ConvictionBadge({ conviction }: { conviction: string | null }) {
  if (!conviction) return null;
  const cls =
    conviction === 'HIGH'   ? 'bg-green-100 text-green-800 border-green-200'   :
    conviction === 'MEDIUM' ? 'bg-yellow-100 text-yellow-800 border-yellow-200' :
                              'bg-gray-100 text-gray-600 border-gray-200';
  return (
    <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded border ${cls}`}>
      {conviction}
    </span>
  );
}

// ── Zone styling with increasing intensity ───────────────────────────────────

export interface ZoneStyle {
  bg: string;
  badge: string;
  textColor: string;
  fillColor: string;
  lineColor: string;
}

export function getZoneStyle(type: 'SUPPORT' | 'RESISTANCE', index: number): ZoneStyle {
  if (type === 'SUPPORT') {
    if (index === 0) {
      return {
        bg: 'bg-emerald-50/70 border-emerald-200',
        badge: 'bg-emerald-100 text-emerald-800 border-emerald-300',
        textColor: 'text-emerald-700',
        fillColor: 'rgba(38, 166, 154, 0.12)',
        lineColor: '#26a69a',
      };
    } else if (index === 1) {
      return {
        bg: 'bg-emerald-100/70 border-emerald-300',
        badge: 'bg-emerald-200 text-emerald-900 border-emerald-400',
        textColor: 'text-emerald-800',
        fillColor: 'rgba(38, 166, 154, 0.24)',
        lineColor: '#1e8e82',
      };
    } else {
      const alpha = Math.min(0.36 + (index - 2) * 0.10, 0.55);
      return {
        bg: 'bg-emerald-200/70 border-emerald-400',
        badge: 'bg-emerald-300 text-emerald-950 border-emerald-500',
        textColor: 'text-emerald-900',
        fillColor: `rgba(38, 166, 154, ${alpha.toFixed(2)})`,
        lineColor: '#136e64',
      };
    }
  }

  // RESISTANCE
  if (index === 0) {
    return {
      bg: 'bg-rose-50/70 border-rose-200',
      badge: 'bg-rose-100 text-rose-800 border-rose-300',
      textColor: 'text-rose-700',
      fillColor: 'rgba(239, 83, 80, 0.12)',
      lineColor: '#ef5350',
    };
  } else if (index === 1) {
    return {
      bg: 'bg-rose-100/70 border-rose-300',
      badge: 'bg-rose-200 text-rose-900 border-rose-400',
      textColor: 'text-rose-800',
      fillColor: 'rgba(239, 83, 80, 0.24)',
      lineColor: '#e53935',
    };
  } else {
    const alpha = Math.min(0.36 + (index - 2) * 0.10, 0.55);
    return {
      bg: 'bg-rose-200/70 border-rose-400',
      badge: 'bg-rose-300 text-rose-950 border-rose-500',
      textColor: 'text-rose-900',
      fillColor: `rgba(239, 83, 80, ${alpha.toFixed(2)})`,
      lineColor: '#c62828',
    };
  }
}

// ── Zone row ──────────────────────────────────────────────────────────────────

function ZoneRow({
  zone,
  zoneLabel,
  style,
}: {
  zone: KeyLevelsZone;
  zoneLabel?: string;
  style?: ZoneStyle;
}) {
  const defaultStyle = style ?? getZoneStyle(zone.level_type, 0);

  const zoneLowNum = zone.zone_low != null ? Number(zone.zone_low) : null;
  const zoneHighNum = zone.zone_high != null ? Number(zone.zone_high) : null;
  const hasValidZone = zoneLowNum != null && !isNaN(zoneLowNum) && zoneHighNum != null && !isNaN(zoneHighNum);
  const confluenceFlags = parseConfluenceFlags(zone.confluence_flags);

  return (
    <div className={`rounded-lg border p-3 mb-2 transition-colors ${defaultStyle.bg}`}>
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-1.5">
          {zoneLabel && (
            <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded border ${defaultStyle.badge}`}>
              {zoneLabel}
            </span>
          )}
          <span className={`text-xs font-bold ${defaultStyle.textColor}`}>{zone.level_type}</span>
        </div>
        <ConvictionBadge conviction={zone.conviction} />
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-gray-700">
        <div>
          <span className="text-gray-400">Zone: </span>
          {hasValidZone
            ? `₹${zoneLowNum!.toFixed(1)} – ₹${zoneHighNum!.toFixed(1)}`
            : '—'}
        </div>
        <div>
          <span className="text-gray-400">Touches: </span>
          {zone.touch_count ?? '—'}
        </div>
        {zone.last_touch_date && (
          <div className="col-span-2">
            <span className="text-gray-400">Last touch: </span>
            {zone.last_touch_date}
          </div>
        )}
        {confluenceFlags.length > 0 && (
          <div className="col-span-2">
            <span className="text-gray-400">Confluence: </span>
            {confluenceFlags.join(', ')}
          </div>
        )}
      </div>
      {zone.reasoning && (
        <p className="text-xs text-gray-500 mt-2 leading-relaxed">{zone.reasoning}</p>
      )}
      {zone.breached_at && (
        <p className="text-xs text-red-500 mt-1">Breached: {zone.breached_at}</p>
      )}
    </div>
  );
}

// ── Stock card (collapsed + expanded) ─────────────────────────────────────────

function StockCard({ stock }: { stock: KeyLevelsStock }) {
  const [expanded, setExpanded] = useState(false);

  // Precompute labels (S1, S2..., R1, R2...) and increasing intensity styles per zone
  const zoneMeta = useMemo(() => {
    let sIdx = 0;
    let rIdx = 0;
    return (stock.zones || []).map(zone => {
      const isSupport = zone.level_type === 'SUPPORT';
      const index = isSupport ? sIdx++ : rIdx++;
      const label = `${isSupport ? 'S' : 'R'}${index + 1}`;
      const style = getZoneStyle(zone.level_type, index);
      return { zone, label, style, index };
    });
  }, [stock.zones]);

  // Derive PriceBands from zones with increasing intensity of red and green
  const priceBands = useMemo<PriceBand[]>(() => {
    return zoneMeta
      .filter(({ zone }) => zone && zone.zone_low != null && zone.zone_high != null && !isNaN(Number(zone.zone_low)) && !isNaN(Number(zone.zone_high)))
      .map(({ zone, label, style }) => ({
        low: Number(zone.zone_low),
        high: Number(zone.zone_high),
        label,
        type: zone.level_type,
        fillColor: style.fillColor,
        lineColor: style.lineColor,
      }));
  }, [zoneMeta]);

  // Badge shows the highest conviction level across all zones
  const highestConviction = useMemo<string | null>(() => {
    const rank: Record<string, number> = { HIGH: 3, MEDIUM: 2, LOW: 1 };
    return (stock.zones || []).reduce<string | null>((best, z) => {
      const zRank = z?.conviction ? (rank[z.conviction] ?? 0) : 0;
      const bRank = best ? (rank[best] ?? 0) : 0;
      return zRank > bRank ? z.conviction : best;
    }, null);
  }, [stock.zones]);

  return (
    <ErrorBoundary fallbackTitle={`Error rendering ${stock.symbol}`}>
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 mb-2">
        {/* Header — sticky when expanded so scrolling down keeps it in view */}
        <button
          onClick={() => setExpanded(v => !v)}
          className={`w-full flex items-center justify-between px-4 py-3 text-left transition-colors ${
            expanded
              ? 'sticky top-0 z-10 bg-white rounded-t-xl border-b border-gray-100 shadow-xs'
              : 'rounded-xl hover:bg-gray-50'
          }`}
        >
          <div className="flex items-center gap-2">
            <span className="font-semibold text-gray-900 text-sm">{stock.symbol}</span>
            <ConvictionBadge conviction={highestConviction} />
          </div>
          <div className="flex items-center gap-2 text-xs text-gray-400">
            <span>{(stock.zones || []).length} zone{(stock.zones || []).length !== 1 ? 's' : ''}</span>
            <span className={`transition-transform duration-150 ${expanded ? 'rotate-180' : ''}`}>▼</span>
          </div>
        </button>

        {/* Expanded body */}
        {expanded && (
          <div className="px-4 pb-4 pt-3 rounded-b-xl">
            <p className="text-[11px] text-gray-400 mb-3">Analysis date: {stock.analysis_date}</p>

            {/* Zone details */}
            {zoneMeta.map(({ zone, label, style }, i) => (
              <ZoneRow key={i} zone={zone} zoneLabel={label} style={style} />
            ))}

            {/* Chart with zone bands overlaid */}
            {Array.isArray(stock.ohlcv_data) && stock.ohlcv_data.length > 0 && (
              <div className="mt-3 h-[420px] sm:h-[500px] rounded-lg overflow-hidden border border-gray-100">
                <LightweightChart
                  symbol={stock.symbol}
                  analysisData={null}
                  ohlcvData={stock.ohlcv_data as OHLCVRow[]}
                  priceBands={priceBands}
                />
              </div>
            )}
          </div>
        )}
      </div>
    </ErrorBoundary>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────

export default function KeyLevelsScreen() {
  const [data, setData]       = useState<KeyLevelsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);
  const [search, setSearch]   = useState('');

  useEffect(() => {
    fetchKeyLevelsCached()
      .then(d  => { setData(d);                                              setLoading(false); })
      .catch(e => { setError(e instanceof Error ? e.message : 'Failed to load'); setLoading(false); });
  }, []);

  const filteredStocks = useMemo(() => {
    if (!data) return [];
    if (!search.trim()) return data.stocks;
    const term = search.toLowerCase();
    return data.stocks.filter(s => s.symbol.toLowerCase().includes(term));
  }, [data, search]);

  return (
    <div className="pb-20">
      <h1 className="text-xl font-semibold text-gray-900 px-4 pt-5 pb-3">Key S/R Levels</h1>

      {/* Banner — week of earliest analysis date */}
      {data?.earliest_analysis_date && (
        <div className="mx-4 mb-3 px-3 py-2 bg-indigo-50 border border-indigo-100 rounded-lg text-xs text-indigo-700 font-medium">
          Levels as of week of {data.earliest_analysis_date}
        </div>
      )}

      {/* Symbol search */}
      <div className="px-4 mb-3">
        <input
          type="text"
          placeholder="Search symbol…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none w-full"
        />
      </div>

      {/* Loading */}
      {loading && (
        <div className="flex items-center justify-center py-16 text-gray-400 text-sm">
          Loading key levels…
        </div>
      )}

      {/* Error */}
      {!loading && error && (
        <div className="mx-4 px-4 py-3 bg-red-50 border border-red-100 rounded-xl text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Stock list */}
      {!loading && !error && (
        <div className="px-4">
          {filteredStocks.length === 0 ? (
            <div className="text-center py-10 text-gray-400 text-sm">
              {search.trim()
                ? `No stocks matching "${search}"`
                : 'No active key levels found.'}
            </div>
          ) : (
            filteredStocks.map(stock => (
              <StockCard key={stock.symbol} stock={stock} />
            ))
          )}
        </div>
      )}
    </div>
  );
}
