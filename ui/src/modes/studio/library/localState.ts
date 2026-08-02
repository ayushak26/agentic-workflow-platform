// Favorites and "recently used"/"continue where you left off" are stored in
// localStorage only. This is a deliberate Phase 1 scope cut: the backend has
// no per-user account, org, or sharing model to hang a real cross-device,
// cross-user favorites/collections feature on (see the Library redesign
// plan) — so these are personal-device bookmarks, not a shared team feature.
// Every function here degrades to a no-op/empty-result if localStorage is
// unavailable (private browsing, etc.) rather than throwing.

const FAVORITES_KEY = 'eurskem.library.favorites';
const RECENT_KEY = 'eurskem.library.recent';
const MAX_RECENT = 10;

function readJsonArray(key: string): string[] {
  try {
    const raw = window.localStorage.getItem(key);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter(item => typeof item === 'string') : [];
  } catch {
    return [];
  }
}

function writeJsonArray(key: string, value: string[]): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Storage unavailable or full — favorites/recents are a convenience,
    // not load-bearing, so silently skip rather than breaking the page.
  }
}

export function getFavorites(): Set<string> {
  return new Set(readJsonArray(FAVORITES_KEY));
}

export function isFavorite(name: string): boolean {
  return getFavorites().has(name);
}

export function toggleFavorite(name: string): Set<string> {
  const current = getFavorites();
  if (current.has(name)) {
    current.delete(name);
  } else {
    current.add(name);
  }
  writeJsonArray(FAVORITES_KEY, [...current]);
  return current;
}

/** Most-recently-opened workflow names, newest first. */
export function getRecentlyOpened(): string[] {
  return readJsonArray(RECENT_KEY);
}

export function recordOpened(name: string): string[] {
  const withoutName = getRecentlyOpened().filter(item => item !== name);
  const next = [name, ...withoutName].slice(0, MAX_RECENT);
  writeJsonArray(RECENT_KEY, next);
  return next;
}

/** The single most recent workflow, for "Continue where you left off". */
export function getLastOpened(): string | null {
  return getRecentlyOpened()[0] ?? null;
}

/** Strips a deleted workflow's name out of both favorites and recently-
 * opened — otherwise it lingers as a dead entry (a favorite star with
 * nothing to unstar, a "recently used" card pointing at nothing) until it
 * ages out of the 10-slot recent list on its own. */
export function forgetWorkflow(name: string): void {
  writeJsonArray(FAVORITES_KEY, readJsonArray(FAVORITES_KEY).filter(item => item !== name));
  writeJsonArray(RECENT_KEY, readJsonArray(RECENT_KEY).filter(item => item !== name));
}
