import type { AnalyseResponse, TodayResponse, WatchlistEntry, SystemStatus, DeepAnalysisResponse } from './types';

const API_URL = import.meta.env.VITE_API_URL as string;

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      message = body.detail ?? body.message ?? message;
    } catch {
      // ignore parse error
    }
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

export function runAnalysis(
  symbol: string,
  direction: 'AUTO' | 'LONG' | 'SHORT',
  save_to_ledger: boolean,
): Promise<AnalyseResponse> {
  return apiFetch<AnalyseResponse>('/api/analyse', {
    method: 'POST',
    body: JSON.stringify({ symbol, direction, save_to_ledger }),
  });
}

export function addToWatchlist(symbol: string): Promise<unknown> {
  return apiFetch('/api/watchlist', {
    method: 'POST',
    body: JSON.stringify({ symbol }),
  });
}

export function fetchToday(): Promise<TodayResponse> {
  return apiFetch<TodayResponse>('/api/today');
}

export function fetchDeepAnalysis(): Promise<DeepAnalysisResponse> {
  return apiFetch<DeepAnalysisResponse>('/api/deep-analysis');
}

export function fetchWatchlist(): Promise<{ watchlist: WatchlistEntry[]; count: number }> {
  return apiFetch('/api/watchlist');
}

export function fetchSystemStatus(): Promise<SystemStatus> {
  return apiFetch('/api/system/status');
}
