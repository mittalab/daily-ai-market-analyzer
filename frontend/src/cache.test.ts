import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { nextSaturday11amIST } from './cache';

/**
 * Reference calendar (September 2026):
 *   Mon 14 | Tue 15 | Wed 16 | Thu 17 | Fri 18 | Sat 19 | Sun 20
 *   Mon 21 | Tue 22 | Wed 23 | Thu 24 | Fri 25 | Sat 26
 *
 * IST = UTC+5:30, so 11:00 AM IST = 05:30 AM UTC.
 */

const IST_OFFSET_MS = 5.5 * 60 * 60 * 1000;

/** Build a Date for a given IST wall-clock time (returned as UTC-epoch ms). */
function istMs(year: number, month: number, day: number, hour: number, min = 0): number {
  // Date.UTC gives UTC ms; subtracting IST_OFFSET_MS converts "IST clock" → UTC
  return Date.UTC(year, month - 1, day, hour, min) - IST_OFFSET_MS;
}

describe('nextSaturday11amIST', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it('mid-week (Wednesday 14:00 IST) → this Saturday 11:00 AM IST', () => {
    vi.setSystemTime(istMs(2026, 9, 16, 14, 0)); // Wed 2026-09-16
    expect(nextSaturday11amIST()).toBe(istMs(2026, 9, 19, 11, 0));
  });

  it('Friday night (23:30 IST) → this Saturday 11:00 AM IST', () => {
    vi.setSystemTime(istMs(2026, 9, 18, 23, 30)); // Fri 2026-09-18
    expect(nextSaturday11amIST()).toBe(istMs(2026, 9, 19, 11, 0));
  });

  it('Saturday 10:59 AM IST (before cutoff) → same Saturday 11:00 AM IST', () => {
    vi.setSystemTime(istMs(2026, 9, 19, 10, 59)); // Sat 2026-09-19
    expect(nextSaturday11amIST()).toBe(istMs(2026, 9, 19, 11, 0));
  });

  it('Saturday 11:01 AM IST (after cutoff) → next Saturday 11:00 AM IST', () => {
    vi.setSystemTime(istMs(2026, 9, 19, 11, 1)); // Sat 2026-09-19
    expect(nextSaturday11amIST()).toBe(istMs(2026, 9, 26, 11, 0));
  });

  it('Sunday (post-Saturday) → next Saturday 11:00 AM IST', () => {
    vi.setSystemTime(istMs(2026, 9, 20, 9, 0)); // Sun 2026-09-20
    expect(nextSaturday11amIST()).toBe(istMs(2026, 9, 26, 11, 0));
  });
});
