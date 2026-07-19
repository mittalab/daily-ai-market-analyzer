export interface OHLCVRow {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

// TradingView encodes & as _ in NSE symbols. Hyphens are fine as-is.
// Indices use a different exchange prefix.
const NSE_TV_SYMBOL_MAP: Record<string, string> = {
  'L&T':       'NSE:LT',
  'L&TFH':     'NSE:L_TFH',
  'NIFTY 50':  'NSE:NIFTY',
  'NIFTY BANK':'NSE:BANKNIFTY',
};

export function getTVSymbol(kiteSymbol: string): string {
  const explicit = NSE_TV_SYMBOL_MAP[kiteSymbol];
  if (explicit) return explicit;
  // Replace & with _ for any unmapped symbols; hyphens are fine in TV
  const sanitised = kiteSymbol.replace(/&/g, '_');
  return `NSE:${sanitised}`;
}

export function computeEMA(data: number[], period: number): number[] {
  if (data.length < period) return data.map(() => NaN);
  const k = 2 / (period + 1);
  const result: number[] = new Array(data.length).fill(NaN);
  // Seed with SMA of first `period` values
  let seed = 0;
  for (let i = 0; i < period; i++) seed += data[i];
  result[period - 1] = seed / period;
  for (let i = period; i < data.length; i++) {
    result[i] = data[i] * k + result[i - 1] * (1 - k);
  }
  return result;
}

// Wilder smoothing RSI
export function computeRSI(closes: number[], period = 14): number[] {
  const result: number[] = new Array(closes.length).fill(NaN);
  if (closes.length < period + 1) return result;

  let avgGain = 0;
  let avgLoss = 0;

  for (let i = 1; i <= period; i++) {
    const diff = closes[i] - closes[i - 1];
    if (diff >= 0) avgGain += diff;
    else avgLoss += Math.abs(diff);
  }
  avgGain /= period;
  avgLoss /= period;

  const rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
  result[period] = 100 - 100 / (1 + rs);

  for (let i = period + 1; i < closes.length; i++) {
    const diff = closes[i] - closes[i - 1];
    const gain = diff > 0 ? diff : 0;
    const loss = diff < 0 ? Math.abs(diff) : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    const rs2 = avgLoss === 0 ? 100 : avgGain / avgLoss;
    result[i] = 100 - 100 / (1 + rs2);
  }

  return result;
}
