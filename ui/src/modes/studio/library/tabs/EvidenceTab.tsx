import type { LibraryMetadata } from '../../../../api/types';
import type { YamlWorkflow } from '../../yaml-bridge';

// Deterministic node-type → evidence-source mapping. Not exhaustive of every
// node in the registry — only the ones that actually acquire or verify
// evidence — so a workflow's supported sources can be read straight off
// which of these node types it contains, without new metadata.
const EVIDENCE_SOURCE_BY_NODE_TYPE: Record<string, string> = {
  WorkflowFileLoader: 'Uploaded documents',
  MinIOEvidenceIngestion: 'Uploaded documents',
  WebSearchAgent: 'Websites and URLs',
  ScholarlyCandidateDiscoveryAgent: 'Research papers',
  ResearchSourceAcquirer: 'Research papers',
  PaperQAEvidenceSynthesizerAgent: 'Research papers',
  RAGAgent: 'Citation libraries',
  ClaimEvidenceVerifier: 'Citation libraries',
  CitationRegistryBuilder: 'Citation libraries',
  InternalProjectEvidenceRetrieverAgent: 'Internal project records',
  PriorProjectRetrieverAgent: 'Previous project records',
  StructuredDatasetRetrieverAgent: 'Structured databases',
  ExcelTableExtractor: 'Spreadsheets and datasets',
  KimiVisionAgent: 'Images',
  BoundedDeepResearchAgent: 'Deep research (context only)',
};

const ALL_SOURCE_LABELS = Array.from(new Set(Object.values(EVIDENCE_SOURCE_BY_NODE_TYPE)));

export function EvidenceTab({
  parsed,
  library,
}: {
  parsed: YamlWorkflow;
  library: LibraryMetadata;
}) {
  const presentTypes = new Set(parsed.nodes.map(node => node.type));
  const supported = new Set(
    [...presentTypes]
      .map(type => EVIDENCE_SOURCE_BY_NODE_TYPE[type])
      .filter((label): label is string => Boolean(label)),
  );

  return (
    <div className="library-tab-content">
      <section className="library-evidence-section">
        <h3>Supported evidence sources</h3>
        <ul className="library-evidence-list">
          {ALL_SOURCE_LABELS.map(label => (
            <li key={label} className={supported.has(label) ? 'is-supported' : 'is-unavailable'}>
              <span aria-hidden="true">{supported.has(label) ? '✓' : '—'}</span>
              {label}
              <span className="library-evidence-status">
                {supported.has(label) ? 'Supported' : 'Not available in this workflow'}
              </span>
            </li>
          ))}
        </ul>
      </section>

      <section className="library-evidence-section">
        <h3>Evidence policy</h3>
        {library.evidence_policy ? (
          <ul className="library-evidence-policy">
            <li>
              Final claims may use only verified evidence:{' '}
              <strong>{library.evidence_policy.drafting_requires_verified_evidence ? 'Yes' : 'No'}</strong>
            </li>
            <li>
              Deep-research findings remain contextual until sources are acquired and verified:{' '}
              <strong>{library.evidence_policy.deep_research_is_context_only ? 'Yes' : 'No'}</strong>
            </li>
          </ul>
        ) : (
          <p className="library-empty-note">
            This workflow hasn&apos;t declared an evidence policy. Do not assume
            drafted claims are automatically verified — check with the
            workflow owner or inspect Technical details before relying on
            unverified content for a final deliverable.
          </p>
        )}
      </section>
    </div>
  );
}
