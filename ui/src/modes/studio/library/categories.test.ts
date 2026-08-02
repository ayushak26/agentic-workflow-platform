import { describe, expect, it } from 'vitest';
import type { LibraryMetadata } from '../../../api/types';
import { categoriesForWorkflow } from './categories';

function metadata(purpose: string[]): LibraryMetadata {
  return {
    title: 'x',
    summary: 'x',
    purpose,
    suitable_for: [],
    not_suitable_for: [],
    outputs: [],
    input_types: [],
    typical_duration: null,
    human_reviews: { count: 0, labels: [] },
    evidence_policy: null,
    visibility_status: 'draft',
    owner_team: null,
    declared: true,
  };
}

describe('categoriesForWorkflow', () => {
  it('uses declared purpose tags when they match a known category', () => {
    const categories = categoriesForWorkflow({
      library: metadata(['research-evidence', 'deep-research']),
      description: 'irrelevant',
      name: 'irrelevant',
    });
    expect(categories).toEqual(['research-evidence', 'deep-research']);
  });

  it('falls back to keyword inference when no library metadata is declared', () => {
    const categories = categoriesForWorkflow({
      library: null,
      description: 'Drafts a literature review from acquired papers.',
      name: 'literature_review_synthesis',
    });
    expect(categories).toContain('literature-review');
  });

  it('a workflow can belong to more than one inferred category', () => {
    const categories = categoriesForWorkflow({
      library: null,
      description: 'Reviews evidence and drafts a Horizon proposal.',
      name: 'horizon_proposal',
    });
    expect(categories).toEqual(
      expect.arrayContaining(['proposal-development', 'research-evidence']),
    );
  });

  it('falls back to "custom-workflows" when nothing matches', () => {
    const categories = categoriesForWorkflow({
      library: null,
      description: 'Does something entirely unrelated to any known purpose.',
      name: 'zzz_mystery',
    });
    expect(categories).toEqual(['custom-workflows']);
  });

  it('ignores declared purpose tags that do not match any known category id', () => {
    const categories = categoriesForWorkflow({
      library: metadata(['not-a-real-category']),
      description: 'Drafts a literature review.',
      name: 'lit_review',
    });
    // Falls through to keyword inference since no declared tag matched.
    expect(categories).toContain('literature-review');
  });
});
