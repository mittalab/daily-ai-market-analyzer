import type { AnalyseResponse, TodayResponse, WatchlistEntry, SystemStatus, DeepAnalysisResponse, IndicatorValidation, ActiveTradesResponse } from './types';

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

/** HEAD-only check — returns session date/id from headers without downloading the body. */
export async function checkChatContext(): Promise<{ date: string; sessionId: string } | null> {
  try {
    const res = await fetch(`${API_URL}/api/session/today/chat-context`, { method: 'HEAD' });
    if (!res.ok) return null;
    return {
      date:      res.headers.get('X-Session-Date') ?? '',
      sessionId: res.headers.get('X-Session-Id')   ?? '',
    };
  } catch {
    return null;
  }
}

export interface ChatMessage { role: 'user' | 'assistant'; content: string; }
export interface ChatReply {
  reply:         string;
  cost_usd:      number;
  input_tokens:  number;
  output_tokens: number;
  session_id:    string;
}

export function sendChatMessage(
  messages:  ChatMessage[],
  sessionId?: string,
): Promise<ChatReply> {
  return apiFetch<ChatReply>('/api/chat', {
    method: 'POST',
    body:   JSON.stringify({ messages, session_id: sessionId ?? null }),
  });
}

/** GET — returns the full plain-text analysis context. */
export async function fetchChatContextText(): Promise<string> {
  const res = await fetch(`${API_URL}/api/session/today/chat-context`);
  if (res.status === 404) {
    const body = await res.json().catch(() => ({})) as Record<string, string>;
    const err  = Object.assign(new Error(body['message'] ?? 'No session'), { code: 'no_session' });
    throw err;
  }
  if (!res.ok) {
    const err = Object.assign(new Error(`HTTP ${res.status}`), { code: 'server_error' });
    throw err;
  }
  return res.text();
}

export interface SessionTurnMeta {
  turn_number: number;
  turn_type: 'market_context' | 'prescan' | 'deep_analysis';
  symbol: string | null;
}

export function fetchSessionTurns(): Promise<{ turns: SessionTurnMeta[] }> {
  return apiFetch<{ turns: SessionTurnMeta[] }>('/api/session/today/turns');
}

export async function fetchTurnInput(turnType: string, symbol?: string): Promise<string> {
  const query = symbol ? `?turn_type=${turnType}&symbol=${symbol}` : `?turn_type=${turnType}`;
  const res = await fetch(`${API_URL}/api/session/today/turn-input${query}`);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  return res.text();
}

export function fetchFoStocks(): Promise<string[]> {
  return apiFetch<string[]>('/api/fo-stocks');
}

export function fetchIndicatorValidation(
  symbol: string,
  date?: string
): Promise<IndicatorValidation> {
  const query = date ? `?symbol=${symbol}&date=${date}` : `?symbol=${symbol}`;
  return apiFetch<IndicatorValidation>(`/api/validate/indicators${query}`);
}

export function fetchActiveTrades(): Promise<ActiveTradesResponse> {
  return apiFetch<ActiveTradesResponse>('/api/active-trades');
}

