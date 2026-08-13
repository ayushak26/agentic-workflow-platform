import type { ProfileCreateRequest } from '../../api/knowledge';

export type IngestionPresetKey = 'technical' | 'general' | 'policies' | 'fast';

export const INGESTION_PRESETS: Record<IngestionPresetKey, {
  label: string; parser: string; chunker: string; target: number; max: number; overlap: number;
}> = {
  technical: { label: 'Technical Documentation', parser: 'layout_aware', chunker: 'parent_child', target: 420, max: 1600, overlap: 64 },
  general: { label: 'General Documents', parser: 'standard', chunker: 'recursive', target: 512, max: 1024, overlap: 64 },
  policies: { label: 'Policies / Contracts', parser: 'layout_aware', chunker: 'structure_aware', target: 650, max: 1200, overlap: 64 },
  fast: { label: 'Fast Demo', parser: 'standard', chunker: 'recursive', target: 384, max: 768, overlap: 32 },
};

export function ingestionProfileRequests(input: {
  preset: IngestionPresetKey; parser: string; chunker: string;
  target: number; max: number; overlap: number; stamp: string;
}): { parser: ProfileCreateRequest; chunking: ProfileCreateRequest } {
  const label = INGESTION_PRESETS[input.preset].label;
  return {
    parser: {
      profile_type: 'parser',
      name: `${label} Parser ${input.stamp}`,
      strategy: input.parser,
      config: { strategy: input.parser },
    },
    chunking: {
      profile_type: 'chunking',
      name: `${label} Chunking ${input.stamp}`,
      strategy: input.chunker,
      config: {
        strategy: input.chunker,
        target_tokens: input.target,
        max_tokens: input.max,
        overlap_tokens: input.overlap,
      },
    },
  };
}

export function comparisonExperiments(base: Record<string, unknown>): Array<Record<string, unknown>> {
  return [
    { ...base, strategy: 'dense', rerank: false, query_transform: 'none' },
    { ...base, strategy: 'hybrid', rerank: false },
    { ...base, strategy: 'hybrid_rerank', rerank: true },
  ];
}
