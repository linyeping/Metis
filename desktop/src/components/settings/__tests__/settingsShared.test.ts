import { describe, expect, it } from 'vitest';
import { stripConfigWhitespace } from '../settingsShared';

describe('settingsShared', () => {
  it('strips accidental whitespace from endpoint credentials', () => {
    expect(stripConfigWhitespace(' https://relay.example /v1\n')).toBe('https://relay.example/v1');
    expect(stripConfigWhitespace('sk-test 123\t\u200B')).toBe('sk-test123');
  });
});
