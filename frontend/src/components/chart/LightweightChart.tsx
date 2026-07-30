import { useEffect, useRef, useState } from 'react';
import {
  createChart,
  LineStyle,
  type IChartApi,
  type CandlestickData,
  type HistogramData,
  type LineData,
  type Time,
} from 'lightweight-charts';
import { type OHLCVRow, computeEMA, computeRSI } from './chartUtils';

interface LightweightChartProps {
  symbol: string;
  analysisData: any;
  ohlcvData: OHLCVRow[];
  entryPrice?: number;
}

export default function LightweightChart({
  symbol,
  analysisData,
  ohlcvData,
  entryPrice,
}: LightweightChartProps) {
  const mainRef    = useRef<HTMLDivElement>(null);
  const volumeRef  = useRef<HTMLDivElement>(null);
  const rsiRef     = useRef<HTMLDivElement>(null);
  const chartsRef  = useRef<IChartApi[]>([]);
  const [showLegend, setShowLegend] = useState(window.innerWidth >= 768);

  useEffect(() => {
    const mainEl   = mainRef.current;
    const volumeEl = volumeRef.current;
    const rsiEl    = rsiRef.current;
    if (!mainEl || !volumeEl || !rsiEl) return;

    // Destroy any existing charts
    chartsRef.current.forEach(c => c.remove());
    chartsRef.current = [];

    if (ohlcvData.length === 0) return;

    const baseOpts = {
      layout: {
        background: { color: '#ffffff' },
        textColor: '#333333',
      },
      grid: {
        vertLines: { color: '#f0f0f0' },
        horzLines: { color: '#f0f0f0' },
      },
      rightPriceScale: { borderVisible: false },
      timeScale: {
        borderVisible: false,
        timeVisible: true,
        secondsVisible: false,
      },
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: true,
        horzTouchDrag: true,
        vertTouchDrag: true,   // allows dragging the price axis to zoom vertically on touch
      },
      handleScale: {
        mouseWheel: true,
        pinch: true,
        axisPressedMouseMove: { time: true, price: true },
      },
      crosshair: { mode: 1 },
    };

    // ── Main chart ──────────────────────────────────────────────────────────────
    const mainChart = createChart(mainEl, {
      ...baseOpts,
      autoSize: true,
    });

    const candleSeries = mainChart.addCandlestickSeries({
      upColor:        '#26a69a',
      downColor:      '#ef5350',
      borderVisible:  false,
      wickUpColor:    '#26a69a',
      wickDownColor:  '#ef5350',
    });

    const candleData: CandlestickData<Time>[] = ohlcvData.map(r => ({
      time:  r.date as Time,
      open:  r.open,
      high:  r.high,
      low:   r.low,
      close: r.close,
    }));
    candleSeries.setData(candleData);

    // EMA lines (horizontal price lines at last computed value)
    const closes = ohlcvData.map(r => r.close);
    const ema20Arr  = computeEMA(closes, 20);
    const ema50Arr  = computeEMA(closes, 50);
    const ema200Arr = computeEMA(closes, 200);

    const lastValid = (arr: number[]) => {
      for (let i = arr.length - 1; i >= 0; i--) {
        if (!isNaN(arr[i])) return arr[i];
      }
      return null;
    };

    const ema20Val  = lastValid(ema20Arr);
    const ema50Val  = lastValid(ema50Arr);
    const ema200Val = lastValid(ema200Arr);

    // EMAs — LargeDashed so they're visually distinct from trade-level lines
    if (ema20Val  != null) candleSeries.createPriceLine({ price: ema20Val,  color: '#2196F3', lineWidth: 1, lineStyle: LineStyle.LargeDashed, axisLabelVisible: true, title: 'EMA20' });
    if (ema50Val  != null) candleSeries.createPriceLine({ price: ema50Val,  color: '#FF9800', lineWidth: 1, lineStyle: LineStyle.LargeDashed, axisLabelVisible: true, title: 'EMA50' });
    if (ema200Val != null) candleSeries.createPriceLine({ price: ema200Val, color: '#9C27B0', lineWidth: 1, lineStyle: LineStyle.LargeDashed, axisLabelVisible: true, title: 'EMA200' });

    // Trade level lines — solid/dashed with distinct colors, thicker than EMAs
    const sl  = analysisData?.key_levels?.stop_loss;
    const el  = analysisData?.trade_parameters?.entry_low;
    const eh  = analysisData?.trade_parameters?.entry_high;
    const t1  = analysisData?.trade_parameters?.target_1;
    const t2  = analysisData?.trade_parameters?.target_2;

    // SL: red solid — most critical, most prominent
    if (sl  != null) candleSeries.createPriceLine({ price: sl,  color: '#EF5350', lineWidth: 2, lineStyle: LineStyle.Solid,  axisLabelVisible: true, title: 'SL' });
    // Entry zone: cyan/teal — clearly different from EMA20 blue
    if (el  != null) candleSeries.createPriceLine({ price: el,  color: '#00ACC1', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: 'Entry Low' });
    if (eh  != null) candleSeries.createPriceLine({ price: eh,  color: '#00ACC1', lineWidth: 2, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: 'Entry High' });
    // T1: light green dashed; T2: dark green solid — solid makes T2 the definitive target
    if (t1  != null) candleSeries.createPriceLine({ price: t1,  color: '#66BB6A', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: 'T1' });
    if (t2  != null) candleSeries.createPriceLine({ price: t2,  color: '#2E7D32', lineWidth: 2, lineStyle: LineStyle.Solid,  axisLabelVisible: true, title: 'T2' });
    // Active entry (Active tab): dark blue solid
    if (entryPrice != null) candleSeries.createPriceLine({ price: entryPrice, color: '#1565C0', lineWidth: 2, lineStyle: LineStyle.Solid, axisLabelVisible: true, title: 'Your Entry' });

    // ── Volume chart ────────────────────────────────────────────────────────────
    const volChart = createChart(volumeEl, {
      ...baseOpts,
      autoSize: true,
      rightPriceScale: { ...baseOpts.rightPriceScale, scaleMargins: { top: 0.1, bottom: 0 } },
      timeScale: { ...baseOpts.timeScale, visible: false },
    });

    const volSeries = volChart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: 'right',
    });

    const volData: HistogramData<Time>[] = ohlcvData.map(r => ({
      time:  r.date as Time,
      value: r.volume,
      color: r.close >= r.open ? '#26a69a80' : '#ef535080',
    }));
    volSeries.setData(volData);

    // 20-day avg volume line
    const volumes = ohlcvData.map(r => r.volume);
    const avgVol20Arr = computeEMA(volumes, 20);
    const avgVolData: LineData<Time>[] = ohlcvData
      .map((r, i) => ({ time: r.date as Time, value: avgVol20Arr[i] }))
      .filter(d => !isNaN(d.value));
    if (avgVolData.length > 0) {
      const avgVolSeries = volChart.addLineSeries({
        color: '#9E9E9E',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        title: '20d avg',
        priceScaleId: 'right',
      });
      avgVolSeries.setData(avgVolData);
    }

    // ── RSI chart ───────────────────────────────────────────────────────────────
    const rsiChart = createChart(rsiEl, {
      ...baseOpts,
      autoSize: true,
      rightPriceScale: {
        ...baseOpts.rightPriceScale,
        scaleMargins: { top: 0.1, bottom: 0.1 },
        autoScale: false,
        minimum: 0,
        maximum: 100,
      } as any,
      timeScale: { ...baseOpts.timeScale },
    });

    const rsiSeries = rsiChart.addLineSeries({
      color:     '#FF6D00',
      lineWidth: 2,
      title:     'RSI14',
    });

    const rsiArr  = computeRSI(closes, 14);
    const rsiData: LineData<Time>[] = ohlcvData
      .map((r, i) => ({ time: r.date as Time, value: rsiArr[i] }))
      .filter(d => !isNaN(d.value));
    rsiSeries.setData(rsiData);

    rsiSeries.createPriceLine({ price: 70, color: '#ef5350', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: 'OB' });
    rsiSeries.createPriceLine({ price: 30, color: '#26a69a', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: 'OS' });
    rsiSeries.createPriceLine({ price: 50, color: '#9E9E9E', lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: '' });

    // ── Sync time scales ────────────────────────────────────────────────────────
    const syncCharts = [mainChart, volChart, rsiChart];
    chartsRef.current = syncCharts;

    let syncing = false;
    const syncHandlers = syncCharts.map((chart, idx) => {
      const handler = (range: any) => {
        if (syncing || !range) return;
        syncing = true;
        syncCharts.forEach((other, otherIdx) => {
          if (otherIdx !== idx) other.timeScale().setVisibleLogicalRange(range);
        });
        syncing = false;
      };
      chart.timeScale().subscribeVisibleLogicalRangeChange(handler);
      return { chart, handler };
    });

    // Default visible range: last 60 candles
    if (candleData.length > 60) {
      mainChart.timeScale().setVisibleLogicalRange({
        from: candleData.length - 60,
        to:   candleData.length - 1,
      });
    } else {
      mainChart.timeScale().fitContent();
    }

    // ── Resize observers ────────────────────────────────────────────────────────
    const observers: ResizeObserver[] = [];
    [[mainEl, mainChart], [volumeEl, volChart], [rsiEl, rsiChart]].forEach(([el, chart]) => {
      const ro = new ResizeObserver(entries => {
        const { width, height } = entries[0].contentRect;
        (chart as IChartApi).resize(width, height);
      });
      ro.observe(el as Element);
      observers.push(ro);
    });

    return () => {
      syncHandlers.forEach(({ chart, handler }) =>
        chart.timeScale().unsubscribeVisibleLogicalRangeChange(handler)
      );
      observers.forEach(ro => ro.disconnect());
      syncCharts.forEach(c => c.remove());
      chartsRef.current = [];
    };
  }, [symbol, ohlcvData, entryPrice]);

  const lastDate = ohlcvData.length > 0 ? ohlcvData[ohlcvData.length - 1].date : null;

  const handleReset = () => {
    chartsRef.current.forEach(c => c.timeScale().fitContent());
  };

  if (ohlcvData.length === 0) {
    return (
      <div className="flex items-center justify-center h-full bg-gray-50 text-gray-400 text-xs">
        Price history not available for this session
      </div>
    );
  }

  const sl = analysisData?.key_levels?.stop_loss;
  const t1 = analysisData?.trade_parameters?.target_1;
  const t2 = analysisData?.trade_parameters?.target_2;

  return (
    <div className="relative h-full w-full flex flex-col">
      {/* Reset button */}
      <button
        onClick={handleReset}
        className="absolute top-1 right-1 z-20 bg-white border border-gray-200 text-gray-500 text-[10px] px-2 py-0.5 rounded shadow-sm hover:bg-gray-50"
      >
        ⟲ Reset
      </button>

      {/* Legend toggle */}
      <button
        onClick={() => setShowLegend(v => !v)}
        className="absolute top-1 left-1 z-20 bg-white border border-gray-200 text-gray-500 text-[10px] w-5 h-5 rounded shadow-sm flex items-center justify-center hover:bg-gray-50"
        title="Toggle legend"
      >
        i
      </button>

      {/* Legend overlay */}
      {showLegend && (
        <div className="absolute top-7 left-1 z-20 bg-white/90 border border-gray-100 rounded shadow-sm p-2 text-[9px] space-y-1 pointer-events-none">
          {/* EMAs — LargeDashed, thinner */}
          <div className="flex items-center gap-1"><span className="w-5 border-b border-dashed" style={{ borderColor: '#2196F3' }} />EMA20</div>
          <div className="flex items-center gap-1"><span className="w-5 border-b border-dashed" style={{ borderColor: '#FF9800' }} />EMA50</div>
          <div className="flex items-center gap-1"><span className="w-5 border-b border-dashed" style={{ borderColor: '#9C27B0' }} />EMA200</div>
          {/* Trade levels — distinct colors */}
          {sl != null && <div className="flex items-center gap-1"><span className="w-5 border-b-2" style={{ borderColor: '#EF5350' }} />SL</div>}
          <div className="flex items-center gap-1"><span className="w-5 border-b-2 border-dashed" style={{ borderColor: '#00ACC1' }} />Entry</div>
          {t1 != null && <div className="flex items-center gap-1"><span className="w-5 border-b border-dashed" style={{ borderColor: '#66BB6A' }} />T1</div>}
          {t2 != null && <div className="flex items-center gap-1"><span className="w-5 border-b-2" style={{ borderColor: '#2E7D32' }} />T2</div>}
          {entryPrice != null && <div className="flex items-center gap-1"><span className="w-5 border-b-2" style={{ borderColor: '#1565C0' }} />Your Entry</div>}
        </div>
      )}

      {/* Data timestamp */}
      {lastDate && (
        <div className="absolute bottom-[calc(40%+1px)] right-1 z-20 text-[9px] text-gray-400 pointer-events-none">
          Data as of: {lastDate} close
        </div>
      )}

      {/* Main chart — 60% */}
      <div ref={mainRef} className="w-full" style={{ height: '60%' }} />
      {/* Volume chart — 20% */}
      <div ref={volumeRef} className="w-full border-t border-gray-100" style={{ height: '20%' }} />
      {/* RSI chart — 20% */}
      <div ref={rsiRef} className="w-full border-t border-gray-100" style={{ height: '20%' }} />
    </div>
  );
}
