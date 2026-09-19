import { describe, it, expect } from 'vitest';
import { parseConfluenceFlags, getZoneStyle } from './KeyLevelsScreen';

describe('parseConfluenceFlags', () => {
  it('returns array of strings unchanged when given an array', () => {
    expect(parseConfluenceFlags(['EMA50', 'ROUND_NUMBER'])).toEqual(['EMA50', 'ROUND_NUMBER']);
  });

  it('parses JSON array string', () => {
    expect(parseConfluenceFlags('["EMA50", "ROUND_NUMBER", "VOLUME_CLIMAX"]')).toEqual([
      'EMA50',
      'ROUND_NUMBER',
      'VOLUME_CLIMAX',
    ]);
  });

  it('parses comma-separated string', () => {
    expect(parseConfluenceFlags('EMA50, ROUND_NUMBER, OI_WALL')).toEqual([
      'EMA50',
      'ROUND_NUMBER',
      'OI_WALL',
    ]);
  });

  it('handles empty string or whitespace', () => {
    expect(parseConfluenceFlags('')).toEqual([]);
    expect(parseConfluenceFlags('   ')).toEqual([]);
  });

  it('handles null and undefined', () => {
    expect(parseConfluenceFlags(null)).toEqual([]);
    expect(parseConfluenceFlags(undefined)).toEqual([]);
  });

  it('handles invalid JSON string by splitting on comma', () => {
    expect(parseConfluenceFlags('invalid_json, flag2')).toEqual(['invalid_json', 'flag2']);
  });
});

describe('getZoneStyle', () => {
  function extractAlpha(rgbaStr: string): number {
    const match = rgbaStr.match(/rgba\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*([0-9.]+)\s*\)/);
    return match ? parseFloat(match[1]) : 0;
  }

  it('increases green shading intensity from first support zone to next', () => {
    const s1 = getZoneStyle('SUPPORT', 0);
    const s2 = getZoneStyle('SUPPORT', 1);
    const s3 = getZoneStyle('SUPPORT', 2);

    const a1 = extractAlpha(s1.fillColor);
    const a2 = extractAlpha(s2.fillColor);
    const a3 = extractAlpha(s3.fillColor);

    expect(a1).toBeGreaterThan(0);
    expect(a2).toBeGreaterThan(a1);
    expect(a3).toBeGreaterThan(a2);

    expect(s1.bg).toContain('emerald-50');
    expect(s2.bg).toContain('emerald-100');
    expect(s3.bg).toContain('emerald-200');
  });

  it('increases red shading intensity from first resistance zone to next', () => {
    const r1 = getZoneStyle('RESISTANCE', 0);
    const r2 = getZoneStyle('RESISTANCE', 1);
    const r3 = getZoneStyle('RESISTANCE', 2);

    const a1 = extractAlpha(r1.fillColor);
    const a2 = extractAlpha(r2.fillColor);
    const a3 = extractAlpha(r3.fillColor);

    expect(a1).toBeGreaterThan(0);
    expect(a2).toBeGreaterThan(a1);
    expect(a3).toBeGreaterThan(a2);

    expect(r1.bg).toContain('rose-50');
    expect(r2.bg).toContain('rose-100');
    expect(r3.bg).toContain('rose-200');
  });
});
