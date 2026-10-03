// Display info for Claude's futures contract (fut_setup).
//
// fut_setup.contract_selected ('near_month' | 'next_month') is relative to the
// session date, so it goes stale once contracts roll after an expiry. Prefer the
// actual expiry date: fut_setup_expiry (resolved by the backend from the session
// date) and then fut_setup.expiry (written by Claude). DTE is computed from
// today rather than using the session-time fut_setup.days_to_expiry.

export interface FutContractInfo {
  name: string;          // e.g. "Oct FUT"; falls back to "Near-Month" / "Next-Month"
  expiry: string | null; // YYYY-MM-DD
  dte: number | null;    // calendar days from today (IST); negative once expired
  expired: boolean;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function todayIST(): string {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' }); // YYYY-MM-DD
}

function daysBetween(from: string, to: string): number {
  const [fy, fm, fd] = from.split('-').map(Number);
  const [ty, tm, td] = to.split('-').map(Number);
  return Math.round((Date.UTC(ty, tm - 1, td) - Date.UTC(fy, fm - 1, fd)) / 86_400_000);
}

export function getFutContract(s: any, today: string = todayIST()): FutContractInfo {
  const fut = s?.fut_setup ?? {};
  const raw: unknown = s?.fut_setup_expiry ?? fut.expiry;
  const expiry = typeof raw === 'string' && /^\d{4}-\d{2}-\d{2}/.test(raw) ? raw.slice(0, 10) : null;

  if (expiry) {
    const dte = daysBetween(today, expiry);
    return {
      name: `${MONTHS[Number(expiry.slice(5, 7)) - 1]} FUT`,
      expiry,
      dte,
      expired: dte < 0,
    };
  }

  const name =
    fut.contract_selected === 'next_month' ? 'Next-Month' :
    fut.contract_selected === 'near_month' ? 'Near-Month' : '—';
  const dte = typeof fut.days_to_expiry === 'number' ? fut.days_to_expiry : null;
  return { name, expiry: null, dte, expired: false };
}

/** " (12 DTE)", " (expired)", or "" — for appending after the expiry date. */
export function dteSuffix(c: FutContractInfo): string {
  if (c.expired) return ' (expired)';
  return c.dte != null ? ` (${c.dte} DTE)` : '';
}
