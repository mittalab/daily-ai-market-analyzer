import { useEffect, useRef, useState } from 'react';
import {
  createChart,
  LineStyle,
  type IChartApi,
  type CandlestickData,
  type HistogramData,
  type LineData,
  type WhitespaceData,
  type Time,
  type ISeriesPrimitive,
  type SeriesAttachedParameter,
  type ISeriesPrimitivePaneView,
  type ISeriesPrimitivePaneRenderer,
  type SeriesPrimitivePaneViewZOrder,
} from 'lightweight-charts';
import { type OHLCVRow, computeEMA, computeRSI } from './chartUtils';

export interface FutureLevels {
  entryLow?:  number | null;
  entryHigh?: number | null;
  sl?:        number | null;
  t1?:        number | null;
  t2?:        number | null;
}

/** A support or resistance zone rendered as two horizontal lines (low + high) and a shaded area. */
export interface PriceBand {
  low:        number;
  high:       number;
  label:      string;
  type:       'SUPPORT' | 'RESISTANCE';
  fillColor?: string;
  lineColor?: string;
}

export interface ChartAnnotation {
  price: number;
  label: string;
  color: string;
}

// ── Shaded zones primitive for LightweightCharts ──────────────────────────────

class ShadedZonesPaneRenderer implements ISeriesPrimitivePaneRenderer {
  private _source: ChartOverlayPrimitive;

  constructor(source: ChartOverlayPrimitive) {
    this._source = source;
  }

  public draw(target: any): void {
    const series = this._source.series;
    const bands = this._source.bands;
    if (!series || !bands || bands.length === 0) return;

    target.useMediaCoordinateSpace(({ context, mediaSize }: { context: CanvasRenderingContext2D; mediaSize: { width: number; height: number } }) => {
      for (const band of bands) {
        if (band.low == null || band.high == null) continue;
        const y1 = series.priceToCoordinate(band.high);
        const y2 = series.priceToCoordinate(band.low);
        if (y1 == null || y2 == null) continue;

        const top = Math.min(y1, y2);
        const height = Math.abs(y1 - y2);
        if (height <= 0) continue;

        context.fillStyle = band.fillColor ?? (
          band.type === 'SUPPORT' ? 'rgba(38, 166, 154, 0.15)' : 'rgba(239, 83, 80, 0.15)'
        );
        context.fillRect(0, top, mediaSize.width, height);
      }
    });
  }
}

class ShadedZonesPaneView implements ISeriesPrimitivePaneView {
  private _renderer: ShadedZonesPaneRenderer;

  constructor(source: ChartOverlayPrimitive) {
    this._renderer = new ShadedZonesPaneRenderer(source);
  }

  public zOrder(): SeriesPrimitivePaneViewZOrder {
    return 'bottom';
  }

  public renderer(): ISeriesPrimitivePaneRenderer {
    return this._renderer;
  }
}

// ── Left-side annotations renderer with collision avoidance ───────────────────

class ChartAnnotationsPaneRenderer implements ISeriesPrimitivePaneRenderer {
  private _source: ChartOverlayPrimitive;

  constructor(source: ChartOverlayPrimitive) {
    this._source = source;
  }

  public draw(target: any): void {
    const series = this._source.series;
    const annotations = this._source.annotations;
    if (!series || !annotations || annotations.length === 0) return;

    target.useMediaCoordinateSpace(({ context, mediaSize }: { context: CanvasRenderingContext2D; mediaSize: { width: number; height: number } }) => {
      interface PlacedItem {
        y: number;
        price: number;
        label: string;
        text: string;
        color: string;
        width: number;
        height: number;
        x: number;
      }

      const items: PlacedItem[] = [];
      context.font = 'bold 10px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';

      for (const ann of annotations) {
        if (ann.price == null || isNaN(ann.price)) continue;
        const y = series.priceToCoordinate(ann.price);
        if (y == null) continue;
        if (y < -10 || y > mediaSize.height + 10) continue;

        const priceStr = ann.price.toFixed(2);
        const text = `${ann.label} ${priceStr}`;
        const textWidth = context.measureText(text).width;
        const paddingX = 4;
        const width = Math.round(textWidth + paddingX * 2);
        const height = 15;

        items.push({
          y,
          price: ann.price,
          label: ann.label,
          text,
          color: ann.color,
          width,
          height,
          x: 6,
        });
      }

      if (items.length === 0) return;

      // Sort by y ascending (from top of chart to bottom)
      items.sort((a, b) => a.y - b.y);

      const placed: PlacedItem[] = [];

      for (const item of items) {
        // Avoid overlapping the (i) info button at top-left (y < 28)
        const minStartX = item.y < 28 ? 28 : 6;
        let x = minStartX;

        const overlapping = placed.filter(p => Math.abs(p.y - item.y) < 15);
        if (overlapping.length > 0) {
          const maxX = Math.max(...overlapping.map(p => p.x + p.width));
          x = Math.max(minStartX, maxX + 4);
        }

        if (x + item.width > mediaSize.width - 60) {
          x = minStartX;
        }

        item.x = x;
        placed.push(item);
      }

      for (const item of placed) {
        const top = Math.round(item.y - item.height / 2);

        context.save();

        // Pill background
        context.fillStyle = item.color;
        context.beginPath();
        if (typeof (context as any).roundRect === 'function') {
          (context as any).roundRect(item.x, top, item.width, item.height, 3);
        } else {
          context.rect(item.x, top, item.width, item.height);
        }
        context.fill();

        // Text
        context.fillStyle = '#ffffff';
        context.font = 'bold 10px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        context.textAlign = 'center';
        context.textBaseline = 'middle';
        context.fillText(item.text, item.x + item.width / 2, item.y);

        context.restore();
      }
    });
  }
}

class ChartAnnotationsPaneView implements ISeriesPrimitivePaneView {
  private _renderer: ChartAnnotationsPaneRenderer;

  constructor(source: ChartOverlayPrimitive) {
    this._renderer = new ChartAnnotationsPaneRenderer(source);
  }

  public zOrder(): SeriesPrimitivePaneViewZOrder {
    return 'top';
  }

  public renderer(): ISeriesPrimitivePaneRenderer {
    return this._renderer;
  }
}

class ChartOverlayPrimitive implements ISeriesPrimitive<Time> {
  private _series: any = null;
  private _requestUpdate: (() => void) | null = null;
  private _bands: PriceBand[];
  private _annotations: ChartAnnotation[];
  private _paneViews: [ISeriesPrimitivePaneView, ISeriesPrimitivePaneView];

  constructor(bands: PriceBand[], annotations: ChartAnnotation[]) {
    this._bands = bands;
    this._annotations = annotations;
    this._paneViews = [
      new ShadedZonesPaneView(this),
      new ChartAnnotationsPaneView(this),
    ];
  }

  public attached(param: SeriesAttachedParameter<Time>): void {
    this._series = param.series;
    this._requestUpdate = param.requestUpdate;
    if (this._requestUpdate) {
      this._requestUpdate();
    }
  }

  public detached(): void {
    this._series = null;
    this._requestUpdate = null;
  }

  public updateAllViews(): void {}

  public paneViews(): readonly ISeriesPrimitivePaneView[] {
    return this._paneViews;
  }

  public get series(): any {
    return this._series;
  }

  public get bands(): PriceBand[] {
    return this._bands;
  }

  public get annotations(): ChartAnnotation[] {
    return this._annotations;
  }
}

interface LightweightChartProps {
  symbol: string;
  analysisData: any;
  ohlcvData: OHLCVRow[];
  entryPrice?: number;
  /** When set, price-level lines use these futures levels instead of key_levels. */
  futureLevels?: FutureLevels;
  /** Expiry date string (YYYY-MM-DD) shown as a badge on the chart. */
  futureExpiry?: string;
  /** Support/resistance zones rendered as paired horizontal price lines. */
  priceBands?: PriceBand[];
}

export default function LightweightChart({
  symbol,
  analysisData,
  ohlcvData,
  entryPrice,
  futureLevels,
  futureExpiry,
  priceBands,
}: LightweightChartProps) {
  const mainRef    = useRef<HTMLDivElement>(null);
  const volumeRef  = useRef<HTMLDivElement>(null);
  const rsiRef     = useRef<HTMLDivElement>(null);
  const chartsRef  = useRef<IChartApi[]>([]);
  const [showLegend, setShowLegend] = useState(false);

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

    const annotations: ChartAnnotation[] = [];

    // EMAs — LargeDashed so they're visually distinct from trade-level lines
    if (ema20Val != null && !isNaN(ema20Val)) {
      candleSeries.createPriceLine({ price: ema20Val, color: '#2196F3', lineWidth: 1, lineStyle: LineStyle.LargeDashed, axisLabelVisible: false, title: '' });
      annotations.push({ price: ema20Val, label: 'EMA20', color: '#2196F3' });
    }
    if (ema50Val != null && !isNaN(ema50Val)) {
      candleSeries.createPriceLine({ price: ema50Val, color: '#FF9800', lineWidth: 1, lineStyle: LineStyle.LargeDashed, axisLabelVisible: false, title: '' });
      annotations.push({ price: ema50Val, label: 'EMA50', color: '#FF9800' });
    }
    if (ema200Val != null && !isNaN(ema200Val)) {
      candleSeries.createPriceLine({ price: ema200Val, color: '#9C27B0', lineWidth: 1, lineStyle: LineStyle.LargeDashed, axisLabelVisible: false, title: '' });
      annotations.push({ price: ema200Val, label: 'EMA200', color: '#9C27B0' });
    }

    // Trade level lines — sourced from futureLevels (futures tabs) or key_levels (spot tab)
    const sl = futureLevels ? futureLevels.sl  : analysisData?.key_levels?.stop_loss;
    const el = futureLevels ? futureLevels.entryLow  : analysisData?.key_levels?.support_zone_low;
    const eh = futureLevels ? futureLevels.entryHigh : analysisData?.key_levels?.support_zone_high;
    const t1 = futureLevels ? futureLevels.t1  : analysisData?.key_levels?.resistance_1;
    const t2 = futureLevels ? futureLevels.t2  : analysisData?.key_levels?.resistance_2;

    // SL: red solid — most critical, most prominent
    if (sl != null && !isNaN(sl)) {
      candleSeries.createPriceLine({ price: sl, color: '#EF5350', lineWidth: 2, lineStyle: LineStyle.Solid, axisLabelVisible: false, title: '' });
      annotations.push({ price: sl, label: 'SL', color: '#EF5350' });
    }
    // Entry zone: cyan/teal — clearly different from EMA20 blue
    if (el != null && !isNaN(el)) {
      const label = futureLevels ? 'FUT Entry Low' : 'Entry Low';
      candleSeries.createPriceLine({ price: el, color: '#00ACC1', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false, title: '' });
      annotations.push({ price: el, label, color: '#00ACC1' });
    }
    if (eh != null && !isNaN(eh)) {
      const label = futureLevels ? 'FUT Entry High' : 'Entry High';
      candleSeries.createPriceLine({ price: eh, color: '#00ACC1', lineWidth: 2, lineStyle: LineStyle.Dashed, axisLabelVisible: false, title: '' });
      annotations.push({ price: eh, label, color: '#00ACC1' });
    }
    // T1: light green dashed; T2: dark green solid — solid makes T2 the definitive target
    if (t1 != null && !isNaN(t1)) {
      candleSeries.createPriceLine({ price: t1, color: '#66BB6A', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false, title: '' });
      annotations.push({ price: t1, label: 'T1', color: '#66BB6A' });
    }
    if (t2 != null && !isNaN(t2)) {
      candleSeries.createPriceLine({ price: t2, color: '#2E7D32', lineWidth: 2, lineStyle: LineStyle.Solid, axisLabelVisible: false, title: '' });
      annotations.push({ price: t2, label: 'T2', color: '#2E7D32' });
    }
    // Active entry (Active tab): dark blue solid
    if (entryPrice != null && !isNaN(entryPrice)) {
      candleSeries.createPriceLine({ price: entryPrice, color: '#1565C0', lineWidth: 2, lineStyle: LineStyle.Solid, axisLabelVisible: false, title: '' });
      annotations.push({ price: entryPrice, label: 'Your Entry', color: '#1565C0' });
    }

    // Key-level price bands — two lines per zone (low + high) in zone color
    // SUPPORT = green (#26a69a), RESISTANCE = red (#ef5350)
    (priceBands ?? []).forEach(band => {
      const defaultColor = band.type === 'SUPPORT' ? '#26a69a' : '#ef5350';
      const color = band.lineColor ?? defaultColor;
      if (band.high != null && !isNaN(band.high)) {
        candleSeries.createPriceLine({ price: band.high, color, lineWidth: 2, lineStyle: LineStyle.Solid, axisLabelVisible: false, title: '' });
        annotations.push({ price: band.high, label: `${band.label}↑`, color });
      }
      if (band.low != null && !isNaN(band.low) && band.low !== band.high) {
        candleSeries.createPriceLine({ price: band.low, color, lineWidth: 1, lineStyle: LineStyle.Solid, axisLabelVisible: false, title: '' });
        annotations.push({ price: band.low, label: `${band.label}↓`, color });
      }
    });

    // Shaded areas and left-side annotations
    const overlayPrimitive = new ChartOverlayPrimitive(priceBands ?? [], annotations);
    candleSeries.attachPrimitive(overlayPrimitive);

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
    const avgVolData: (LineData<Time> | WhitespaceData<Time>)[] = ohlcvData.map((r, i) => {
      const val = avgVol20Arr[i];
      return isNaN(val) ? { time: r.date as Time } : { time: r.date as Time, value: val };
    });
    const avgVolSeries = volChart.addLineSeries({
      color: '#9E9E9E',
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      title: '20d avg',
      priceScaleId: 'right',
    });
    avgVolSeries.setData(avgVolData);

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
    const rsiData: (LineData<Time> | WhitespaceData<Time>)[] = ohlcvData.map((r, i) => {
      const val = rsiArr[i];
      return isNaN(val) ? { time: r.date as Time } : { time: r.date as Time, value: val };
    });
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
  }, [symbol, ohlcvData, entryPrice, futureLevels, priceBands, analysisData]);

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

  const sl = futureLevels ? futureLevels.sl : analysisData?.key_levels?.stop_loss;
  const t1 = futureLevels ? futureLevels.t1 : analysisData?.key_levels?.resistance_1;
  const t2 = futureLevels ? futureLevels.t2 : analysisData?.key_levels?.resistance_2;

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
          {(priceBands ?? []).map(band => (
            <div key={band.label} className="flex items-center gap-1">
              <span className="w-5 border-b-2" style={{ borderColor: band.type === 'SUPPORT' ? '#26a69a' : '#ef5350' }} />
              {band.label} {band.type === 'SUPPORT' ? 'Sup' : 'Res'}
            </div>
          ))}
        </div>
      )}

      {/* Futures expiry badge */}
      {futureExpiry && (
        <div className="absolute top-1 left-1/2 -translate-x-1/2 z-20 bg-indigo-600 text-white text-[9px] font-semibold px-2 py-0.5 rounded pointer-events-none">
          Expiry: {futureExpiry}
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
