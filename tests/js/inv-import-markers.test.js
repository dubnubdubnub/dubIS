import { describe, it, expect, beforeEach } from 'vitest';
import state, {
  recordImportGeneration, clearImportGenerations,
  popImportGeneration, generationOpacityFor, MAX_IMPORT_GENERATIONS,
} from '../../js/inventory/inv-state.js';

describe('import generations', () => {
  beforeEach(() => clearImportGenerations());

  it('records newest-first and caps the list', () => {
    for (let i = 0; i < MAX_IMPORT_GENERATIONS + 2; i++) recordImportGeneration([`K${i}`]);
    expect(state.importGenerations.length).toBe(MAX_IMPORT_GENERATIONS);
    // newest is first
    expect(state.importGenerations[0].keys.has(`K${MAX_IMPORT_GENERATIONS + 1}`)).toBe(true);
  });

  it('generationOpacityFor returns 1 for newest and fades for older', () => {
    recordImportGeneration(['OLD']);
    recordImportGeneration(['NEW']);
    expect(generationOpacityFor('NEW')).toBe(1);
    expect(generationOpacityFor('OLD')).toBeLessThan(1);
    expect(generationOpacityFor('MISSING')).toBe(0);
  });

  it('a key in two generations uses the brightest (newest)', () => {
    recordImportGeneration(['DUP']);
    recordImportGeneration(['DUP']);
    expect(generationOpacityFor('DUP')).toBe(1);
  });

  it('popImportGeneration removes the newest', () => {
    recordImportGeneration(['A']);
    recordImportGeneration(['B']);
    popImportGeneration();
    expect(generationOpacityFor('B')).toBe(0);
    expect(generationOpacityFor('A')).toBe(1);
  });
});
