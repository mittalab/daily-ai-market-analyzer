const CACHE_PREFIX = 'mkt_';
const CACHE_KEYS = ['today', 'deep-analysis', 'active-trades'] as const;
export type CacheKey = typeof CACHE_KEYS[number];

// Analysis runs at 10:00 PM IST = 16:30 UTC
function lastAnalysisTimeMs(): number {
  const now = Date.now();
  const d = new Date();
  d.setUTCHours(16, 30, 0, 0);
  const todayMs = d.getTime();
  return now >= todayMs ? todayMs : todayMs - 86_400_000;
}

export function getCached<T>(key: CacheKey): T | null {
  try {
    const raw = localStorage.getItem(CACHE_PREFIX + key);
    if (!raw) return null;
    const { data, ts } = JSON.parse(raw) as { data: T; ts: number };
    if (ts < lastAnalysisTimeMs()) {
      localStorage.removeItem(CACHE_PREFIX + key);
      return null;
    }
    return data as T;
  } catch {
    return null;
  }
}

export function setCached<T>(key: CacheKey, data: T): void {
  try {
    localStorage.setItem(CACHE_PREFIX + key, JSON.stringify({ data, ts: Date.now() }));
  } catch {
    // Quota exceeded — ignore silently
  }
}

export function clearAllCache(): void {
  CACHE_KEYS.forEach(k => localStorage.removeItem(CACHE_PREFIX + k));
}

export function isCachePresent(): boolean {
  const threshold = lastAnalysisTimeMs();
  return CACHE_KEYS.some(k => {
    try {
      const raw = localStorage.getItem(CACHE_PREFIX + k);
      if (!raw) return false;
      const { ts } = JSON.parse(raw) as { ts: number };
      return ts >= threshold;
    } catch {
      return false;
    }
  });
}
