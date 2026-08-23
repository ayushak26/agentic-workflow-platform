import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';

import { api } from '../../api/client';
import type {
  ConceptAlternative,
  HorizonEvaluation,
  ProposalApproval,
  ProposalReview as ProposalReviewData,
} from '../../api/types';

const statusClass: Record<string, string> = {
  ADDRESSED: 'bg-emerald-100 text-emerald-800',
  PARTIAL: 'bg-amber-100 text-amber-800',
  MISSING: 'bg-rose-100 text-rose-800',
};

export function ProposalReview() {
  const params = useParams();
  const [runId, setRunId] = useState(params.runId ?? '');
  const [review, setReview] = useState<ProposalReviewData | null>(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [sourceId, setSourceId] = useState('');
  const [sourceTitle, setSourceTitle] = useState('');
  const [sourceContent, setSourceContent] = useState('');
  const [conceptNote, setConceptNote] = useState('');
  const [selectedConcept, setSelectedConcept] = useState('');
  const [proposalText, setProposalText] = useState('');
  const [generatorModel, setGeneratorModel] = useState('claude-opus-5');
  const [evaluation, setEvaluation] = useState<HorizonEvaluation | null>(null);

  const concepts = useMemo(
    () => Object.values(
      (review?.graph?.concept_alternatives ?? {}) as Record<
        string,
        ConceptAlternative
      >,
    ),
    [review],
  );

  async function load(id = runId) {
    if (!id.trim()) return;
    setBusy('load');
    setError(null);
    try {
      setReview(await api.proposalReview(id.trim()));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy('');
    }
  }

  useEffect(() => {
    // Fetching route-owned data is the external synchronization for this effect.
     
    if (params.runId) void load(params.runId);
    // Route id is the load trigger; form edits should not auto-fetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.runId]);

  async function addSource() {
    if (!review || !sourceId || !sourceTitle || !sourceContent) return;
    setBusy('source');
    setError(null);
    try {
      await api.registerSourceVersion(review.proposal_id, sourceId, {
        content: sourceContent,
        title: sourceTitle,
      });
      setSourceContent('');
      await load(review.proposal_id);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy('');
    }
  }

  async function verifyClaims() {
    if (!review) return;
    setBusy('verify');
    setError(null);
    try {
      const result = await api.verifyProposalClaims(
        review.proposal_id,
        review.graph,
      );
      setReview({
        ...review,
        graph: result.graph,
        coverage: result.coverage,
      });
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy('');
    }
  }

  async function generateConcepts() {
    if (!review) return;
    setBusy('concepts');
    setError(null);
    try {
      const result = await api.generateConceptAlternatives(
        review.proposal_id,
        review.graph,
        conceptNote,
      );
      setReview({ ...review, graph: result.graph });
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy('');
    }
  }

  async function requestApproval() {
    if (!review) return;
    setBusy('approval');
    setError(null);
    try {
      const approval = await api.requestProposalApproval(
        review.proposal_id,
        review.graph,
        concepts.length ? 'concept_freeze' : 'call_coverage',
        selectedConcept || undefined,
      );
      setReview({
        ...review,
        approvals: [approval, ...review.approvals],
      });
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy('');
    }
  }

  async function decide(
    approval: ProposalApproval,
    decision: 'approved' | 'rejected' | 'changes_requested',
  ) {
    if (!review) return;
    setBusy(approval.approval_id);
    setError(null);
    try {
      const updated = await api.decideProposalApproval(
        review.proposal_id,
        approval.approval_id,
        decision,
      );
      setReview({
        ...review,
        approvals: review.approvals.map(item => (
          item.approval_id === updated.approval_id ? updated : item
        )),
      });
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy('');
    }
  }

  async function evaluate() {
    if (!review || !proposalText.trim()) return;
    setBusy('evaluate');
    setError(null);
    try {
      setEvaluation(await api.evaluateHorizonProposal(review.proposal_id, {
        graph: review.graph,
        proposal_text: proposalText,
        generator_model: generatorModel,
        evaluator_models: ['claude-sonnet-4-5', 'gpt-5'],
      }));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy('');
    }
  }

  return (
    <div className="mx-auto max-w-[1500px] space-y-5 p-4 md:p-6">
      <section className="ui-card p-5">
        <div className="ui-kicker">Quality assurance</div>
        <h2 className="ui-page-title mt-1">Proposal evidence review</h2>
        <p className="text-sm text-ink-500 mt-1">
          Load a workflow run to inspect call coverage, sources, claim evidence,
          concept alternatives, approvals, and the independent evaluator.
        </p>
        <div className="mt-4 flex flex-col gap-2 sm:flex-row">
          <input
            value={runId}
            onChange={event => setRunId(event.target.value)}
            placeholder="Run ID"
            className="flex-1 rounded-md border border-ink-200 px-3 py-2 text-sm"
          />
          <button
            onClick={() => void load()}
            className="ui-button ui-button--primary"
          >
            {busy === 'load' ? 'Loading…' : 'Load review'}
          </button>
        </div>
        {error && <div role="alert" className="mt-3 rounded-md border border-bad/20 bg-bad/10 p-3 text-sm text-bad">{error}</div>}
      </section>

      {review && (
        <>
          <section className="ui-card overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-3 p-4">
              <div>
                <h3 className="font-semibold">Call-coverage matrix</h3>
                <div className="text-xs text-ink-500">
                  {review.coverage.coverage_percent}% evidence-weighted coverage
                  {' · '}{review.coverage.addressed} addressed
                  {' · '}{review.coverage.partial} partial
                  {' · '}{review.coverage.missing} missing
                </div>
              </div>
              <span className={`px-2 py-1 rounded text-xs ${
                review.coverage.submission_blocked
                  ? 'bg-rose-100 text-rose-800'
                  : 'bg-emerald-100 text-emerald-800'
              }`}>
                {review.coverage.submission_blocked
                  ? 'Approval blocked'
                  : 'Ready for approval'}
              </span>
            </div>
            <div className="ui-table-wrap rounded-none border-x-0 border-b-0">
              <table className="w-full text-xs">
                <thead className="text-left">
                  <tr>
                    <th className="p-3">Requirement</th>
                    <th className="p-3">Status</th>
                    <th className="p-3">Section</th>
                    <th className="p-3">Mappings</th>
                    <th className="p-3">Evidence</th>
                    <th className="p-3">Missing</th>
                  </tr>
                </thead>
                <tbody>
                  {review.coverage.rows.map(row => (
                    <tr key={row.requirement_id} className="border-t border-ink-100 align-top hover:bg-brand-softer">
                      <td className="p-3 max-w-md">
                        <div className="font-mono">{row.requirement_id}</div>
                        <div className="text-ink-700 mt-1">{row.requirement}</div>
                        <div className="text-ink-400 mt-1">{row.kind}</div>
                      </td>
                      <td className="p-3">
                        <span className={`px-2 py-1 rounded ${statusClass[row.status]}`}>
                          {row.status}
                        </span>
                      </td>
                      <td className="p-3">{row.section || '—'}</td>
                      <td className="p-3">{row.mapped_object_ids.join(', ') || '—'}</td>
                      <td className="p-3">
                        {row.verified_claim_count}/{row.evidence_claim_ids.length}
                      </td>
                      <td className="p-3 text-rose-700">
                        {row.missing_items.join('; ') || '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="grid lg:grid-cols-2 gap-4">
            <div className="ui-card p-4">
              <h3 className="font-semibold">Versioned sources</h3>
              <div className="text-xs text-ink-500 mt-1">
                Same content and metadata deduplicates; every real change creates
                an immutable version and SHA-256 record.
              </div>
              <div className="space-y-2 mt-3">
                <input value={sourceId} onChange={e => setSourceId(e.target.value)}
                  placeholder="Source ID, e.g. SRC-038"
                  className="w-full rounded-md border border-ink-200 px-3 py-2 text-sm" />
                <input value={sourceTitle} onChange={e => setSourceTitle(e.target.value)}
                  placeholder="Source title"
                  className="w-full rounded-md border border-ink-200 px-3 py-2 text-sm" />
                <textarea value={sourceContent} onChange={e => setSourceContent(e.target.value)}
                  placeholder="Exact source text"
                  className="h-28 w-full rounded-md border border-ink-200 px-3 py-2 text-sm" />
                <div className="flex flex-wrap gap-2">
                  <button onClick={() => void addSource()}
                    className="ui-button ui-button--primary">
                    {busy === 'source' ? 'Saving…' : 'Save version'}
                  </button>
                  <button onClick={() => void verifyClaims()}
                    className="ui-button ui-button--secondary">
                    {busy === 'verify' ? 'Verifying…' : 'Verify claim passages'}
                  </button>
                </div>
              </div>
              <ul className="mt-4 space-y-2 text-xs">
                {review.source_versions.map((source, index) => (
                  <li key={`${source.version_id}-${index}`} className="rounded-md border border-ink-200 bg-ink-50 p-2">
                    <div className="font-medium">{source.source_id} · v{source.version}</div>
                    <div>{source.title}</div>
                    <div className="font-mono text-ink-400">{source.content_sha256}</div>
                  </li>
                ))}
              </ul>
            </div>

            <div className="ui-card p-4">
              <h3 className="font-semibold">Concept alternatives</h3>
              <textarea value={conceptNote} onChange={e => setConceptNote(e.target.value)}
                placeholder="Optional concept-note emphasis"
                className="mt-3 h-20 w-full rounded-md border border-ink-200 px-3 py-2 text-sm" />
              <button onClick={() => void generateConcepts()}
                className="ui-button ui-button--primary mt-2">
                {busy === 'concepts' ? 'Generating…' : 'Generate three alternatives'}
              </button>
              <div className="space-y-2 mt-4">
                {concepts.map(concept => (
                  <label key={concept.id}
                    className={`block cursor-pointer rounded-md border p-3 transition-colors hover:border-accent-400 hover:bg-brand-softer ${selectedConcept === concept.id ? 'border-accent-500 bg-brand-soft' : 'border-ink-200'}`}>
                    <div className="flex items-center justify-between">
                      <span className="font-medium">
                        <input type="radio" name="concept" value={concept.id}
                          checked={selectedConcept === concept.id}
                          onChange={() => setSelectedConcept(concept.id)}
                          className="mr-2" />
                        {concept.posture}: {concept.title}
                      </span>
                      <span className="text-xs font-mono">
                        {concept.evidence_weighted_score}/100
                      </span>
                    </div>
                    <p className="text-xs text-ink-600 mt-2">{concept.summary}</p>
                  </label>
                ))}
              </div>
            </div>
          </section>

          <section className="ui-card p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="font-semibold">Versioned approval workflow</h3>
                <p className="text-xs text-ink-500">
                  Each request freezes a proposal snapshot and its coverage matrix.
                </p>
              </div>
              <button onClick={() => void requestApproval()}
                className="ui-button ui-button--primary">
                {busy === 'approval' ? 'Requesting…' : 'Request approval'}
              </button>
            </div>
            <div className="grid md:grid-cols-2 gap-3 mt-4">
              {review.approvals.map(approval => (
                <div key={approval.approval_id} className="rounded-md border border-ink-200 bg-ink-50 p-3 text-xs">
                  <div className="flex justify-between">
                    <span className="font-medium">{approval.stage}</span>
                    <span>{approval.status}</span>
                  </div>
                  <div className="font-mono text-ink-400 mt-1">
                    snapshot v{approval.snapshot_version} · {approval.snapshot_sha256.slice(0, 12)}
                  </div>
                  {approval.status === 'pending' && (
                    <div className="flex gap-2 mt-3">
                      <button onClick={() => void decide(approval, 'approved')}
                        className="px-2 py-1 bg-emerald-600 text-white rounded">
                        Approve
                      </button>
                      <button onClick={() => void decide(approval, 'changes_requested')}
                        className="px-2 py-1 bg-amber-500 text-white rounded">
                        Changes
                      </button>
                      <button onClick={() => void decide(approval, 'rejected')}
                        className="px-2 py-1 bg-rose-600 text-white rounded">
                        Reject
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </section>

          <section className="ui-card p-4">
            <h3 className="font-semibold">Independent Horizon evaluation</h3>
            <p className="text-xs text-ink-500 mt-1">
              Claude and GPT score Excellence, Impact, and Implementation
              independently; deterministic evidence and coverage blockers remain
              separate from model opinion.
            </p>
            <div className="grid md:grid-cols-[220px_1fr] gap-3 mt-3">
              <input value={generatorModel}
                onChange={e => setGeneratorModel(e.target.value)}
                className="rounded-md border border-ink-200 px-3 py-2 text-sm"
                placeholder="Generator model" />
              <textarea value={proposalText}
                onChange={e => setProposalText(e.target.value)}
                className="h-32 rounded-md border border-ink-200 px-3 py-2 text-sm"
                placeholder="Paste the proposal text to evaluate" />
            </div>
            <button onClick={() => void evaluate()}
              className="ui-button ui-button--primary mt-2">
              {busy === 'evaluate' ? 'Evaluating…' : 'Run independent panel'}
            </button>
            {evaluation && (
              <div className="mt-4">
                <div className="flex gap-3 items-center">
                  <span className="text-lg font-semibold">
                    {evaluation.total_score}/15
                  </span>
                  <span className={`px-2 py-1 rounded text-xs ${
                    evaluation.threshold_passed
                      ? 'bg-emerald-100 text-emerald-800'
                      : 'bg-rose-100 text-rose-800'
                  }`}>
                    {evaluation.threshold_passed ? 'Threshold passed' : 'Blocked / below threshold'}
                  </span>
                </div>
                <div className="grid md:grid-cols-3 gap-3 mt-3">
                  {evaluation.criteria.map(item => (
                    <div key={item.criterion} className="rounded-md border border-ink-200 bg-ink-50 p-3 text-xs">
                      <div className="font-medium capitalize">{item.criterion}</div>
                      <div className="text-xl mt-1">{item.mean_score}/5</div>
                      <div className="text-ink-500">
                        judge spread: {item.disagreement}
                      </div>
                    </div>
                  ))}
                </div>
                {evaluation.deterministic_blockers.length > 0 && (
                  <ul className="mt-3 list-disc pl-5 text-xs text-rose-700">
                    {evaluation.deterministic_blockers.map(item => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
