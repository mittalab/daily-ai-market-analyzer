import { describe, it, expect } from 'vitest';
import { parseConfluenceFlags } from './KeyLevelsScreen';

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
