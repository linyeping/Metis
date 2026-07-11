import { describe, expect, it } from 'vitest';
import { themeNames, themes, themeSwatches } from '../themes';

describe('theme swatches', () => {
  it('defines three distinct representative colors for every theme', () => {
    expect(Object.keys(themeSwatches).sort()).toEqual([...themeNames].sort());

    for (const name of themeNames) {
      const swatches = themeSwatches[name];
      expect(swatches).toHaveLength(3);
      expect(new Set(swatches.map(color => color.toLowerCase())).size).toBe(3);
      for (const color of swatches) expect(color).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  it('uses colors drawn from each theme palette', () => {
    for (const name of themeNames) {
      const paletteColors = new Set(Object.values(themes[name]).map(value => value.toLowerCase()));
      for (const color of themeSwatches[name]) {
        expect(paletteColors.has(color.toLowerCase()), `${name} includes ${color}`).toBe(true);
      }
    }
  });
});
