export function slugify(displayName: string): string {
  return displayName
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_\- ]/g, '')
    .replace(/\s+/g, '_');
}

// Mirrors the backend's defensive check in app/api/workflows.py:save_workflow —
// alphanumeric plus underscore/hyphen only. Surfacing the same rule here means
// the user finds out before submitting, not after a 400.
export function isValidSlug(slug: string): boolean {
  return slug.length > 0 && /^[a-zA-Z0-9_-]+$/.test(slug);
}

// Disambiguates a candidate slug against already-taken names by appending
// `_2`, `_3`, ... — used when auto-saving on the user's behalf, where there's
// no name-collision dialog to surface the conflict through.
export function uniqueSlug(base: string, existing: Set<string>): string {
  const root = isValidSlug(base) ? base : 'workflow';
  if (!existing.has(root)) return root;
  let i = 2;
  while (existing.has(`${root}_${i}`)) i++;
  return `${root}_${i}`;
}
