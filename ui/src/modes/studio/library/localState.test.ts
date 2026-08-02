import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// This project's vitest runs in the default 'node' test environment (no
// jsdom) — so `window`/`localStorage` don't exist unless stubbed. A minimal
// in-memory Map-backed stub is enough to exercise the real read/write logic
// without pulling in a full DOM environment for the whole test suite.
function stubLocalStorage() {
  const store = new Map<string, string>();
  vi.stubGlobal('window', {
    localStorage: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => { store.set(key, value); },
      removeItem: (key: string) => { store.delete(key); },
    },
  });
}

describe('library/localState', () => {
  beforeEach(() => {
    stubLocalStorage();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('has no favorites initially', async () => {
    const { getFavorites, isFavorite } = await import('./localState');
    expect(getFavorites().size).toBe(0);
    expect(isFavorite('abm_playbook')).toBe(false);
  });

  it('toggles a favorite on and back off', async () => {
    const { toggleFavorite, isFavorite } = await import('./localState');
    toggleFavorite('abm_playbook');
    expect(isFavorite('abm_playbook')).toBe(true);
    toggleFavorite('abm_playbook');
    expect(isFavorite('abm_playbook')).toBe(false);
  });

  it('records recently opened workflows newest-first and dedupes', async () => {
    const { recordOpened, getRecentlyOpened } = await import('./localState');
    recordOpened('a');
    recordOpened('b');
    recordOpened('a'); // re-opening moves it back to the front, no duplicate
    expect(getRecentlyOpened()).toEqual(['a', 'b']);
  });

  it('caps recently opened at the configured maximum', async () => {
    const { recordOpened, getRecentlyOpened } = await import('./localState');
    for (let i = 0; i < 15; i += 1) recordOpened(`wf-${i}`);
    expect(getRecentlyOpened()).toHaveLength(10);
    expect(getRecentlyOpened()[0]).toBe('wf-14');
  });

  it('getLastOpened returns the most recent workflow or null', async () => {
    const { recordOpened, getLastOpened } = await import('./localState');
    expect(getLastOpened()).toBeNull();
    recordOpened('only-one');
    expect(getLastOpened()).toBe('only-one');
  });

  it('degrades to a no-op instead of throwing when localStorage is unavailable', async () => {
    vi.unstubAllGlobals();
    vi.stubGlobal('window', {
      localStorage: {
        getItem: () => { throw new Error('storage disabled'); },
        setItem: () => { throw new Error('storage disabled'); },
      },
    });
    const { toggleFavorite, getFavorites } = await import('./localState');
    expect(() => toggleFavorite('x')).not.toThrow();
    expect(getFavorites()).toEqual(new Set());
  });
});
