import { getTVSymbol } from './chartUtils';

interface TradingViewChartProps {
  symbol: string;
  spotPrice?: number;
}

export default function TradingViewChart({ symbol, spotPrice }: TradingViewChartProps) {
  const tvSymbol = getTVSymbol(symbol);
  const tvUrl = `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(tvSymbol)}`;

  return (
    <div className="flex flex-col items-center justify-center h-full bg-gray-50 gap-5 px-6">
      {/* Symbol header */}
      <div className="text-center">
        <div className="inline-flex items-center gap-2 bg-white border border-gray-200 rounded-xl px-4 py-2.5 shadow-sm mb-3">
          <span className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">TradingView</span>
          <span className="text-sm font-mono font-bold text-gray-900">{tvSymbol}</span>
          {spotPrice != null && (
            <span className="text-sm font-mono text-gray-500">
              ₹{spotPrice.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
            </span>
          )}
        </div>
      </div>

      {/* Open button */}
      <a
        href={tvUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="flex items-center gap-2.5 bg-[#2962FF] hover:bg-[#1a4fd6] text-white font-semibold text-sm px-6 py-3 rounded-xl shadow transition-colors"
      >
        {/* TradingView logo mark */}
        <svg width="18" height="18" viewBox="0 0 36 28" fill="none" xmlns="http://www.w3.org/2000/svg">
          <path d="M14 28L0 0h10l4.5 9.33L19 0h17L22 28H14z" fill="white"/>
        </svg>
        Open Live Chart ↗
      </a>

      {/* Explanation */}
      <p className="text-[10px] text-gray-400 text-center max-w-[260px] leading-relaxed">
        TradingView's chart embed requires a TradingView login.
        Click above to open the full interactive chart in a new tab — or switch to
        <span className="font-semibold text-gray-600"> 🔍 Analysis</span> to see the chart with Claude's levels.
      </p>
    </div>
  );
}
