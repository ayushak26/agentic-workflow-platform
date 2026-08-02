import type { LibraryMetadata } from '../../../api/types';

// Fixed purpose taxonomy for Library discovery. Declared `library.purpose`
// tags on a workflow are matched against these ids/labels first; workflows
// with no declared purpose (every pre-existing workflow) fall back to a
// deterministic keyword match on name+description — same "matcher list,
// keyword fallback" shape already proven for Guided Run's stage inference
// (see guided/runtime-model.ts's DEFAULT_STAGES).
export type LibraryCategoryId =
  | 'research-evidence'
  | 'deep-research'
  | 'literature-review'
  | 'data-analysis'
  | 'proposal-development'
  | 'scientific-drafting'
  | 'reports-deliverables'
  | 'document-review'
  | 'citation-fact-checking'
  | 'figures-visualisation'
  | 'presentations'
  | 'data-database-retrieval'
  | 'quality-compliance'
  | 'custom-workflows';

export type LibraryCategory = {
  id: LibraryCategoryId;
  label: string;
  // null for the catch-all category — it's never matched by keyword, only
  // used as the fallback when nothing else matches.
  matcher: RegExp | null;
};

export const LIBRARY_CATEGORIES: LibraryCategory[] = [
  { id: 'research-evidence', label: 'Research and Evidence', matcher: /evidence|research_source|verif/i },
  { id: 'deep-research', label: 'Deep Research', matcher: /deep.?research/i },
  { id: 'literature-review', label: 'Literature Review', matcher: /literature|paper.?qa|scholarly/i },
  { id: 'data-analysis', label: 'Data Analysis', matcher: /data.?analysis|dataset|analy(s|z)e/i },
  { id: 'proposal-development', label: 'Proposal Development', matcher: /proposal|horizon|concept.?note|blueprint/i },
  { id: 'scientific-drafting', label: 'Scientific Drafting', matcher: /draft|methodology|scientific/i },
  { id: 'reports-deliverables', label: 'Reports and Deliverables', matcher: /report|deliverable|memo/i },
  { id: 'document-review', label: 'Document Review', matcher: /review|editor|human.?in.?loop|hitl/i },
  { id: 'citation-fact-checking', label: 'Citation and Fact Checking', matcher: /citation|fact.?check|claim.?verif/i },
  { id: 'figures-visualisation', label: 'Figures and Visualisation', matcher: /figure|chart|visuali[sz]/i },
  { id: 'presentations', label: 'Presentations', matcher: /powerpoint|pptx|slide|presentation/i },
  { id: 'data-database-retrieval', label: 'Data and Database Retrieval', matcher: /database|lookup|structured.?dataset/i },
  { id: 'quality-compliance', label: 'Quality and Compliance', matcher: /complian|quality|consisten|red.?team|peer.?review/i },
  { id: 'custom-workflows', label: 'Custom Workflows', matcher: null },
];

export const CATEGORY_LABEL: Record<LibraryCategoryId, string> = Object.fromEntries(
  LIBRARY_CATEGORIES.map(category => [category.id, category.label]),
) as Record<LibraryCategoryId, string>;

function slugToId(tag: string): string {
  return tag.trim().toLowerCase().replace(/[\s_]+/g, '-');
}

/** Every category a workflow belongs to. Never empty — falls back to
 * 'custom-workflows' when nothing declared or inferred matches. */
export function categoriesForWorkflow(workflow: {
  library: LibraryMetadata | null;
  description: string;
  name: string;
}): LibraryCategoryId[] {
  const declaredTags = workflow.library?.purpose ?? [];
  if (declaredTags.length > 0) {
    const bySlug = new Map(LIBRARY_CATEGORIES.map(category => [category.id, category]));
    const matched = declaredTags
      .map(tag => bySlug.get(slugToId(tag) as LibraryCategoryId))
      .filter((category): category is LibraryCategory => Boolean(category))
      .map(category => category.id);
    if (matched.length > 0) return matched;
  }

  const searchable = `${workflow.name} ${workflow.description}`;
  const inferred = LIBRARY_CATEGORIES
    .filter(category => category.matcher?.test(searchable))
    .map(category => category.id);
  return inferred.length > 0 ? inferred : ['custom-workflows'];
}
