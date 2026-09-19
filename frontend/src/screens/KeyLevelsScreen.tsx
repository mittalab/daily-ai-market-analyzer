import { useState, useEffect, useMemo } from 'react';
import { fetchKeyLevelsCached } from '../api';
import type { KeyLevelsResponse, KeyLevelsStock, KeyLevelsZone } from '../types';
import LightweightChart, { type PriceBand } from '../components/chart/LightweightChart';
import type { OHLCVRow } from '../components/chart/chartUtils';

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

// ── Zone row ──────────────────────────────────────────────────────────────────

function ZoneRow({ zone }: { zone: KeyLevelsZone }) {
  const isSupport = zone.level_type === 'SUPPORT';
  const typeColor = isSupport ? 'text-green-700'  : 'text-red-700';
  const typeBg    = isSupport ? 'bg-green-50 border-green-100' : 'bg-red-50 border-red-100';

  return (
    <div className={`rounded-lg border p-3 mb-2 ${typeBg}`}>
      <div className="flex items-center justify-between mb-1.5">
        <span className={`text-xs font-bold ${typeColor}`}>{zone.level_type}</span>
        <ConvictionBadge conviction={zone.conviction} />
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-gray-700">
        <div>
          <span className="text-gray-400">Zone: </span>
          {zone.zone_low != null && zone.zone_high != null
            ? `₹${zone.zone_low.toFixed(1)} – ₹${zone.zone_high.toFixed(1)}`
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
        {zone.confluence_flags.length > 0 && (
          <div className="col-span-2">
            <span className="text-gray-400">Confluence: </span>
            {zone.confluence_flags.join(', ')}
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

  // Derive PriceBands from zones, numbering supports and resistances separately
  const priceBands = useMemo<PriceBand[]>(() => {
    let sCount = 0;
    let rCount = 0;
    return stock.zones
      .filter(z => z.zone_low != null && z.zone_high != null)
      .map(z => {
        if (z.level_type === 'SUPPORT') {
          sCount++;
          return { low: z.zone_low!, high: z.zone_high!, label: `S${sCount}`, type: 'SUPPORT' as const };
        } else {
          rCount++;
          return { low: z.zone_low!, high: z.zone_high!, label: `R${rCount}`, type: 'RESISTANCE' as const };
        }
      });
  }, [stock.zones]);

  // Badge shows the highest conviction level across all zones
  const highestConviction = useMemo<string | null>(() => {
    const rank: Record<string, number> = { HIGH: 3, MEDIUM: 2, LOW: 1 };
    return stock.zones.reduce<string | null>((best, z) => {
      const zRank = z.conviction ? (rank[z.conviction] ?? 0) : 0;
      const bRank = best ? (rank[best] ?? 0) : 0;
      return zRank > bRank ? z.conviction : best;
    }, null);
  }, [stock.zones]);

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 mb-2 overflow-hidden">
      {/* Collapsed header — always visible */}
      <button
        onClick={() => setExpanded(v => !v)}
        className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-gray-50 transition-colors"
      >
        <div className="flex items-center gap-2">
          <span className="font-semibold text-gray-900 text-sm">{stock.symbol}</span>
          <ConvictionBadge conviction={highestConviction} />
        </div>
        <div className="flex items-center gap-2 text-xs text-gray-400">
          <span>{stock.zones.length} zone{stock.zones.length !== 1 ? 's' : ''}</span>
          <span className={`transition-transform duration-150 ${expanded ? 'rotate-180' : ''}`}>▼</span>
        </div>
      </button>

      {/* Expanded body */}
      {expanded && (
        <div className="border-t border-gray-100 px-4 pb-4 pt-3">
          <p className="text-[11px] text-gray-400 mb-3">Analysis date: {stock.analysis_date}</p>

          {/* Zone details */}
          {stock.zones.map((zone, i) => (
            <ZoneRow key={i} zone={zone} />
          ))}

          {/* Chart with zone bands overlaid */}
          {stock.ohlcv_data.length > 0 && (
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
