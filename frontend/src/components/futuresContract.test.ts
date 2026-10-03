import { describe, it, expect } from 'vitest';
import { getFutContract, dteSuffix } from './futuresContract';

const TODAY = '2026-10-03';

describe('getFutContract', () => {
  it('prefers backend-resolved fut_setup_expiry over the stale relative label', () => {
    const c = getFutContract(
      { fut_setup_expiry: '2026-10-27', fut_setup: { contract_selected: 'next_month', expiry: '2026-09-29' } },
      TODAY,
    );
    expect(c).toEqual({ name: 'Oct FUT', expiry: '2026-10-27', dte: 24, expired: false });
    expect(dteSuffix(c)).toBe(' (24 DTE)');
  });

  it('falls back to Claude-written fut_setup.expiry', () => {
    const c = getFutContract({ fut_setup: { contract_selected: 'near_month', expiry: '2026-10-27' } }, TODAY);
    expect(c.name).toBe('Oct FUT');
  });

  it('marks a contract expired the day after its expiry, not on it', () => {
    expect(getFutContract({ fut_setup_expiry: '2026-10-03' }, TODAY).expired).toBe(false);
    const c = getFutContract({ fut_setup_expiry: '2026-09-29' }, TODAY);
    expect(c.expired).toBe(true);
    expect(dteSuffix(c)).toBe(' (expired)');
  });

  it('uses the relative label and session DTE when no expiry is known', () => {
    const c = getFutContract({ fut_setup: { contract_selected: 'next_month', days_to_expiry: 30 } }, TODAY);
    expect(c).toEqual({ name: 'Next-Month', expiry: null, dte: 30, expired: false });
  });

  it('ignores a malformed expiry', () => {
    expect(getFutContract({ fut_setup: { contract_selected: 'near_month', expiry: 'soon' } }, TODAY).name)
      .toBe('Near-Month');
  });
});
