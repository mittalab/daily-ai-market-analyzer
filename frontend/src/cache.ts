const CACHE_PREFIX = 'mkt_';
const CACHE_KEYS = ['today', 'deep-analysis', 'active-trades', 'key-levels'] as const;
export type CacheKey = typeof CACHE_KEYS[number];

// Analysis runs at 10:00 PM IST = 16:30 UTC
function lastAnalysisTimeMs(): number {
  const now = Date.now();
  const d = new Date();
  d.setUTCHours(16, 30, 0, 0);
  const todayMs = d.getTime();
  return now >= todayMs ? todayMs : todayMs - 86_400_000;
}

// Saturday key-level job completes before 11:00 AM IST.
// IST = UTC+5:30, so 11:00 AM IST = 05:30 AM UTC.
// Returns the Unix timestamp (ms) of the next Saturday 11:00 AM IST:
//   if now < this Saturday 11:00 AM IST → this Saturday
//   else                                → next Saturday
export function nextSaturday11amIST(): number {
  const IST_OFFSET_MS = 5.5 * 60 * 60 * 1000; // +05:30 in ms
  // Shift wall clock to IST so we can reason about IST day/hour using UTC methods
  const nowIst = new Date(Date.now() + IST_OFFSET_MS);

  const dow = nowIst.getUTCDay(); // 0=Sun, 1=Mon, ..., 6=Sat  (IST day)
  let daysUntilSat: number;

  if (dow === 6) {
    // Today is Saturday in IST — check whether we're before 11:00 AM
    daysUntilSat = nowIst.getUTCHours() < 11 ? 0 : 7;
  } else {
    daysUntilSat = (6 - dow + 7) % 7; // 1–6 for Mon–Fri, 6 for Sun
  }

  const targetIst = new Date(nowIst);
  targetIst.setUTCDate(targetIst.getUTCDate() + daysUntilSat);
  targetIst.setUTCHours(11, 0, 0, 0); // 11:00 AM IST (as a shifted-UTC clock)

  // Convert back to real UTC milliseconds
  return targetIst.getTime() - IST_OFFSET_MS;
}

// Cache entries may carry an explicit expiry wall-clock timestamp (ms).
// When present: stale if Date.now() >= expiry.
// When absent:  legacy check — stale if ts < lastAnalysisTimeMs().
export function getCached<T>(key: CacheKey): T | null {
  try {
    const raw = localStorage.getItem(CACHE_PREFIX + key);
    if (!raw) return null;
    const { data, ts, expiry } = JSON.parse(raw) as { data: T; ts: number; expiry?: number };
    if (expiry !== undefined) {
      if (Date.now() >= expiry) {
        localStorage.removeItem(CACHE_PREFIX + key);
        return null;
      }
    } else {
      if (ts < lastAnalysisTimeMs()) {
        localStorage.removeItem(CACHE_PREFIX + key);
        return null;
      }
    }
    return data as T;
  } catch {
    return null;
  }
}

// Pass expiry (ms since epoch) for keys that use a custom expiry boundary
// (e.g. key-levels uses nextSaturday11amIST()). Omit for daily-analysis keys.
export function setCached<T>(key: CacheKey, data: T, expiry?: number): void {
  try {
    localStorage.setItem(
      CACHE_PREFIX + key,
      JSON.stringify({ data, ts: Date.now(), ...(expiry !== undefined ? { expiry } : {}) }),
    );
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
      const { ts, expiry } = JSON.parse(raw) as { ts: number; expiry?: number };
      if (expiry !== undefined) return Date.now() < expiry;
      return ts >= threshold;
    } catch {
      return false;
    }
  });
}
