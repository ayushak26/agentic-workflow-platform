/* Node outputs are runtime-defined by independently registered node plugins. */
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../../api/client';
import { CopyButton } from '../../components/CopyButton';
import { WorkflowVariablesPanel } from './WorkflowVariablesPanel';

export function OutputViewer({
    state,
    workflowName,
}: {
    runId?: string;
    state: any;
    projectedOutput?: Record<string, unknown>;
    workflowName?: string;
}) {
    const navigate = useNavigate();
    const [tab, setTab] = useState<'variables' | 'sources' | 'audit' | 'score'>('variables');
    const [reference, setReference] = useState('');
    const [scores, setScores] = useState<{ criterion: string; score: number; reasoning: string }[] | null>(null);
    const [scoring, setScoring] = useState(false);
    const [scoreErr, setScoreErr] = useState<string | null>(null);

    const nodeOutputs = state?.node_outputs ?? {};
    const workflowInputs = state?.inputs ?? {};
    const workflowVariables = state?.variables ?? {};

    // A proposal workflow can now publish PDF, editable DOCX, and its sanitised
    // HTML source in the same run. Collect every renderer-owned artifact and
    // deduplicate `minio_key` aliases rather than hiding all but one output.
    type OutputArtifact = {
        key: string;
        extension: string;
        nodeId: string;
        output: any;
    };
    const rendererEntries = Object.entries(nodeOutputs).filter(([, output]: [string, any]) => (
        output?.pdf_key
        || output?.docx_key
        || output?.html_key
        || (
            typeof output?.minio_key === 'string'
            && ['pdf', 'docx', 'pptx', 'xlsx'].includes(
                output.minio_key.split('.').pop()?.toLowerCase() ?? '',
            )
        )
    ));
    const artifactKeys = new Set<string>();
    const artifacts: OutputArtifact[] = [];
    for (const [nodeId, output] of rendererEntries as [string, any][]) {
        for (const field of ['pdf_key', 'docx_key', 'html_key', 'minio_key']) {
            const key = output?.[field];
            if (typeof key !== 'string' || artifactKeys.has(key)) continue;
            const extension = key.split('.').pop()?.toLowerCase() ?? '';
            if (!['pdf', 'docx', 'html', 'pptx', 'xlsx'].includes(extension)) continue;
            artifactKeys.add(key);
            artifacts.push({ key, extension, nodeId, output });
        }
    }
    const artifactOrder: Record<string, number> = {
        pdf: 0,
        docx: 1,
        html: 2,
        pptx: 3,
        xlsx: 4,
    };
    artifacts.sort((left, right) => (
        (artifactOrder[left.extension] ?? 99)
        - (artifactOrder[right.extension] ?? 99)
    ));
    const primaryArtifact = artifacts[0];
    const previewArtifact = artifacts.find(item => item.extension === 'pdf');
    const doc = primaryArtifact?.output;
    const minioKey = primaryArtifact?.key;
    const fileKinds = artifacts.map(item => item.extension.toUpperCase()).join(' + ');
    const rendererWarnings = Array.from(new Set(
        rendererEntries.flatMap(([, output]: [string, any]) => (
            Array.isArray(output?.warnings) ? output.warnings : []
        )),
    ));
    const pageCountOutput = (
        previewArtifact?.output
        ?? artifacts.find(item => typeof item.output?.page_count === 'number')?.output
    );
    const pageCount = pageCountOutput?.page_count;
    const pageCountEstimated = Boolean(pageCountOutput?.page_count_basis);

    const evidenceOutput: any = Object.values(nodeOutputs).find(
        (output: any) => Array.isArray(output?.citation_registry) && output?.qa_report,
    );
    const ragCitations = nodeOutputs.knowledge_retrieval?.citations ?? [];
    const evidenceCitations = evidenceOutput?.citation_registry ?? [];
    const citations = evidenceCitations.length > 0 ? evidenceCitations : ragCitations;
    const evidenceBlockers: string[] = evidenceOutput?.blocking_issues ?? [];
    const evidenceQa: any = evidenceOutput?.qa_report;
    const auditLog: any[] = state?.audit_log ?? [];

    const answerText: string =
        nodeOutputs.compile_and_qa?.raw ??
        nodeOutputs.knowledge_retrieval?.answer ??
        Object.values(nodeOutputs).map((o: any) => o?.raw).filter(Boolean).join('\n\n') ??
        '';
    const sourcesText: string = citations
        .map((c: any) => c.formatted_citation
            ? `[${c.display_number}] ${c.formatted_citation}`
            : `[${c.label}] ${c.source_doc}: ${c.snippet}`)
        .join('\n');

    async function runScoring() {
        setScoring(true); setScoreErr(null); setScores(null);
        try {
            const res = await api.scoreOutput({
                answer: answerText,
                sources: sourcesText,
                question: `Proposal for ${workflowName ?? 'workflow'}`,
                reference: reference.trim() || undefined,
            });
            setScores(res.scores);
        } catch (e) {
            setScoreErr(String(e));
        } finally {
            setScoring(false);
        }
    }

    const auditEntries = auditLog.length > 0
        ? auditLog
        : Object.keys(nodeOutputs).map((nid) => ({ node_id: nid, type_name: '(derived from node_outputs)' }));

    const decisionBadge = (d: string) =>
        d === 'reject' ? 'bg-warn text-white'
            : d === 'edit' ? 'bg-accent-600 text-white'
                : 'bg-ok text-white';

    // Reusable Score panel — used in both the document and no-document layouts.
    const scorePanel = (
        <div className="p-4 space-y-3">
            <div className="text-xs text-ink-500">
                The judge scores this output against its sources. Paste an ideal proposal
                below to score <strong>completeness</strong> against it (optional, never
                stored). Without it, faithfulness and citation accuracy still apply.
            </div>
            <textarea
                value={reference}
                onChange={e => setReference(e.target.value)}
                placeholder="Optional: paste an ideal proposal as the completeness reference…"
                className="w-full h-24 border border-slate-300 rounded-md p-2 text-xs"
            />
            <button onClick={runScoring} disabled={scoring || !answerText}
                className="px-4 py-2 rounded-md bg-accent-600 text-white text-sm disabled:opacity-50">
                {scoring ? 'Scoring…' : 'Score this output'}
            </button>
            {!answerText && (
                <div className="text-xs text-ink-400">No output text found to score.</div>
            )}
            {scoreErr && <div className="text-xs text-red-600">{scoreErr}</div>}
            {scores && (
                <ul className="space-y-2 text-xs mt-2">
                    {scores.map(s => (
                        <li key={s.criterion} className="border border-slate-200 rounded-md p-2">
                            <div className="flex justify-between">
                                <span className="capitalize font-medium">{s.criterion.replace('_', ' ')}</span>
                                <span className="font-semibold">{s.score}/5</span>
                            </div>
                            <div className="text-ink-500 mt-1">{s.reasoning}</div>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );

    // Shared right-hand panel — identical in document and no-document layouts.
    const sidePanel = (
        <aside className="w-96 border-l border-slate-200 bg-white overflow-y-auto">
            <div className="flex border-b border-slate-200">
                <button onClick={() => setTab('variables')}
                    className={`flex-1 px-2 py-2 text-xs ${tab === 'variables' ? 'border-b-2 border-accent-600 font-medium' : 'text-ink-500'}`}>
                    Variables
                </button>
                <button onClick={() => setTab('sources')}
                    className={`flex-1 px-2 py-2 text-xs ${tab === 'sources' ? 'border-b-2 border-accent-600 font-medium' : 'text-ink-500'}`}>
                    Sources ({citations.length})
                </button>
                <button onClick={() => setTab('audit')}
                    className={`flex-1 px-2 py-2 text-xs ${tab === 'audit' ? 'border-b-2 border-accent-600 font-medium' : 'text-ink-500'}`}>
                    Audit ({auditEntries.length})
                </button>
                <button onClick={() => setTab('score')}
                    className={`flex-1 px-2 py-2 text-xs ${tab === 'score' ? 'border-b-2 border-accent-600 font-medium' : 'text-ink-500'}`}>
                    Score
                </button>
            </div>

            {tab === 'variables' && (
                <WorkflowVariablesPanel
                    inputs={workflowInputs}
                    variables={workflowVariables}
                    outputs={nodeOutputs}
                />
            )}

            {tab === 'sources' && (
                <div className="p-4 space-y-3">
                    {evidenceQa && (
                        <div className={`rounded-md border p-3 text-xs ${evidenceBlockers.length > 0
                            ? 'border-red-300 bg-red-50 text-red-800'
                            : 'border-emerald-300 bg-emerald-50 text-emerald-800'}`}>
                            <div className="font-semibold">
                                {evidenceBlockers.length > 0
                                    ? `Evidence gate blocked (${evidenceBlockers.length})`
                                    : 'Evidence gate passed'}
                            </div>
                            <div className="mt-1">
                                {evidenceQa.verified_claims ?? 0}/{evidenceQa.claims_examined ?? 0} claims verified ·
                                {' '}{Math.round((evidenceQa.exact_locator_rate ?? 0) * 100)}% exact locators
                            </div>
                            {evidenceBlockers.length > 0 && (
                                <ul className="mt-2 list-disc pl-4 space-y-1">
                                    {evidenceBlockers.map((item, index) => (
                                        <li key={index}>{item}</li>
                                    ))}
                                </ul>
                            )}
                        </div>
                    )}
                    {citations.length === 0 ? (
                        <div className="text-sm text-ink-500">No citations recorded.</div>
                    ) : (
                        <ul className="space-y-2 text-xs">
                            {citations.map((c: any) => (
                                <li key={c.citation_id ?? c.label} className="border border-slate-200 rounded-md p-2">
                                    <div className="font-medium">
                                        [{c.display_number ?? c.label}] {c.title ?? c.source_doc}
                                    </div>
                                    <div className="text-ink-500 mt-1">
                                        {c.formatted_citation ?? c.snippet}
                                    </div>
                                    {c.version_id && (
                                        <div className="text-ink-400 mt-1 font-mono">
                                            {c.version_id} · {c.retraction_status}
                                        </div>
                                    )}
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            )}

            {tab === 'audit' && (
                <div className="p-4">
                    {auditEntries.length === 0 ? (
                        <div className="text-xs text-ink-500">No audit entries.</div>
                    ) : (
                        <ul className="space-y-1.5 text-xs">
                            {auditEntries.map((e: any, i: number) => {
                                const out = nodeOutputs[e.node_id] ?? {};
                                const decision = out.decision;
                                return (
                                    <li key={i} className="border border-slate-200 rounded-md p-2">
                                        <div className="flex items-center justify-between">
                                            <span className="font-medium font-mono">{e.node_id}</span>
                                            {typeof e.duration_s === 'number' && (
                                                <span className="text-ink-400">{e.duration_s.toFixed(2)}s</span>
                                            )}
                                        </div>
                                        <div className="text-ink-500">{e.type_name}</div>
                                        {e.started_at && (
                                            <div className="text-ink-400">
                                                {new Date(e.started_at).toLocaleTimeString()}
                                            </div>
                                        )}
                                        {decision && (
                                            <div className="mt-1">
                                                <span className={`px-1.5 py-0.5 rounded text-[10px] ${decisionBadge(decision)}`}>
                                                    {decision}
                                                </span>
                                                {out.reason ? <span className="text-ink-500 ml-1">{out.reason}</span> : null}
                                            </div>
                                        )}
                                        {Array.isArray(e.output_keys) && e.output_keys.length > 0 && (
                                            <div className="text-ink-400 mt-1">→ {e.output_keys.join(', ')}</div>
                                        )}
                                    </li>
                                );
                            })}
                        </ul>
                    )}
                </div>
            )}

            {tab === 'score' && scorePanel}
        </aside>
    );

    // ── No-document layout (e.g. rag_test, document_qa) ─────────────────────
    if (!minioKey) {
        const entries = Object.entries(nodeOutputs);
        return (
            <div className="h-full flex flex-col">
                <header className="px-6 py-3 border-b border-slate-200 flex items-center justify-between bg-white">
                    <div>
                        <h2 className="font-semibold">{workflowName ?? 'Workflow'} — completed</h2>
                        <div className="text-xs text-ink-500">
                            {entries.length} node{entries.length === 1 ? '' : 's'} executed · no document output
                        </div>
                    </div>
                    <button onClick={() => navigate('/library')}
                        className="px-4 py-2 rounded-md border border-slate-300 text-sm hover:bg-slate-50">
                        Back to Library
                    </button>
                </header>

                <div className="flex-1 flex min-h-0">
                    <div className="flex-1 overflow-y-auto p-6 space-y-3">
                        {entries.length === 0 ? (
                            <div className="text-sm text-ink-500">This run produced no node outputs.</div>
                        ) : (
                            entries.map(([nid, out]: [string, any]) => {
                                const o = out ?? {};
                                return (
                                    <div key={nid} className="border border-slate-200 rounded-md p-3 bg-white">
                                        <div className="flex items-center justify-between mb-1">
                                            <div className="font-mono text-sm font-medium">{nid}</div>
                                            <CopyButton
                                                text={
                                                    typeof o.raw === 'string' && o.raw
                                                        ? o.raw
                                                        : JSON.stringify(o.parsed ?? o, null, 2)
                                                }
                                            />
                                        </div>
                                        {typeof o.raw === 'string' && o.raw ? (
                                            <div className="text-sm whitespace-pre-wrap text-ink-700">{o.raw}</div>
                                        ) : o.decision ? (
                                            <div className="text-sm">
                                                decision: <span className="font-medium">{o.decision}</span>
                                                {o.reason ? ` — ${o.reason}` : ''}
                                            </div>
                                        ) : o.parsed ? (
                                            <pre className="text-xs bg-slate-50 rounded p-2 overflow-x-auto">
                                                {JSON.stringify(o.parsed, null, 2)}
                                            </pre>
                                        ) : (
                                            <pre className="text-xs bg-slate-50 rounded p-2 overflow-x-auto">
                                                {JSON.stringify(o, null, 2)}
                                            </pre>
                                        )}
                                        {o != null && typeof o === 'object' && !Array.isArray(o) && (
                                            <div className="mt-2 flex flex-wrap gap-1.5">
                                                {Object.entries(o as Record<string, unknown>).map(([field, fieldValue]) => (
                                                    <CopyButton
                                                        key={field}
                                                        text={
                                                            typeof fieldValue === 'string'
                                                                ? fieldValue
                                                                : JSON.stringify(fieldValue, null, 2)
                                                        }
                                                        label={`Copy "${field}" field`}
                                                        className="text-[9px]"
                                                    />
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                );
                            })
                        )}
                    </div>
                    {sidePanel}
                </div>
            </div>
        );
    }

    // ── Document layout (flagship proposal_generation, biomass, etc.) ───────
    const viewUrl = previewArtifact ? api.fileUrl(previewArtifact.key) : undefined;

    return (
        <div className="h-full flex flex-col">
            <header className="px-6 py-3 border-b border-slate-200 flex items-center justify-between bg-white">
                <div>
                    <h2 className="font-semibold">{workflowName ?? 'Proposal'} — completed</h2>
                    <div className="text-xs text-ink-500">
                        {doc.template_used ? `Template: ${doc.template_used} · ` : ''}
                        {typeof pageCount === 'number'
                            ? `${pageCountEstimated ? 'approximately ' : ''}${pageCount} pages · `
                            : ''}
                        {typeof doc.byte_size === 'number' ? `${(doc.byte_size / 1024).toFixed(0)} KB · ` : ''}
                        {fileKinds || 'FILE'}
                    </div>
                    {rendererWarnings.length > 0 && (
                        <div className="text-xs text-amber-700 mt-1">
                            {rendererWarnings.join(' · ')}
                        </div>
                    )}
                </div>
                <div className="flex gap-2">
                    {artifacts.map((artifact, index) => (
                        <a
                            key={artifact.key}
                            href={api.fileUrl(artifact.key, true)}
                            className={`px-4 py-2 rounded-md text-sm ${
                                index === 0
                                    ? 'bg-accent-600 text-white'
                                    : 'border border-slate-300 hover:bg-slate-50'
                            }`}
                        >
                            Download {artifact.extension.toUpperCase()}
                        </a>
                    ))}
                    {previewArtifact && viewUrl && (
                        <a href={viewUrl} target="_blank" rel="noreferrer"
                            className="px-4 py-2 rounded-md border border-slate-300 text-sm hover:bg-slate-50">
                            Open in new tab
                        </a>
                    )}
                    <button
                        onClick={() => navigate('/library')}
                        className="px-4 py-2 rounded-md border border-slate-300 text-sm hover:bg-slate-50"
                    >
                        Back
                    </button>
                </div>
            </header>

            <div className="flex-1 flex min-h-0">
                <div className="flex-1 bg-slate-100 p-4">
                    {previewArtifact && viewUrl ? (
                        <iframe title="Proposal" src={viewUrl}
                            className="w-full h-full bg-white border border-slate-200 rounded-md" />
                    ) : (
                        <div className="w-full h-full bg-white border border-slate-200 rounded-md flex items-center justify-center">
                            <div className="text-center space-y-3">
                                <div className="text-sm text-ink-500">
                                    {fileKinds || 'Office'} documents can&rsquo;t preview in the browser.
                                </div>
                                <div className="flex flex-wrap justify-center gap-2">
                                    {artifacts.map(artifact => (
                                        <a
                                            key={artifact.key}
                                            href={api.fileUrl(artifact.key, true)}
                                            className="inline-block px-4 py-2 rounded-md bg-accent-600 text-white text-sm"
                                        >
                                            Download {artifact.extension.toUpperCase()}
                                        </a>
                                    ))}
                                </div>
                            </div>
                        </div>
                    )}
                </div>
                {sidePanel}
            </div>
        </div>
    );
}
