// Detect a downloadable object-store key inside a node's output.
// Looks for object-store keys (workflows/...) in *_key / pdf_key / file_key fields
// or any string value that looks like a workflow-scoped key.
export function fileKey(output: unknown): string | null {
  if (output == null) return null;
  if (typeof output === 'string') {
    return output.startsWith('workflows/') ? output : null;
  }
  if (typeof output === 'object') {
    const obj = output as Record<string, unknown>;
    for (const key of ['pdf_key', 'docx_key', 'file_key', 'output_key', 'key', 'minio_key']) {
      const v = obj[key];
      if (typeof v === 'string' && v.startsWith('workflows/')) return v;
    }
    // Any value that looks like a workflow key.
    for (const v of Object.values(obj)) {
      if (typeof v === 'string' && v.startsWith('workflows/')) return v;
    }
  }
  return null;
}

// Human-readable "PDF · 207 KB · ~22 pages" summary for a file-producing
// node's output, so a download link doesn't just say "Download" with no
// sense of what's behind it. Falls back gracefully when a field is absent —
// renderer nodes don't all report the same set (e.g. PowerPointToolNode has
// no page count).
export function artifactLabel(
  output: unknown,
  key: string,
  opts: { includeExtension?: boolean } = {},
): string {
  const ext = key.split('.').pop()?.toUpperCase() || 'FILE';
  const obj = (output && typeof output === 'object') ? output as Record<string, unknown> : {};
  const parts = opts.includeExtension === false ? [] : [ext];

  const size = obj.byte_size;
  if (typeof size === 'number') {
    parts.push(size >= 1_000_000 ? `${(size / 1_000_000).toFixed(1)} MB` : `${Math.max(1, Math.round(size / 1024))} KB`);
  }

  const pages = obj.page_count ?? obj.estimated_page_count;
  if (typeof pages === 'number') {
    const estimated = Boolean(obj.page_count_basis);
    parts.push(`${estimated ? '~' : ''}${pages} page${pages === 1 ? '' : 's'}`);
  }

  return parts.length > 0 ? parts.join(' · ') : ext;
}
