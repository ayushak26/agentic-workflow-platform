/* Node outputs are runtime-defined by independently registered node plugins. */
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../../api/client';
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
    const [artifact, setArtifact] = useState<{
        key: string;
        url?: string;
        error?: string;
    } | null>(null);

    const nodeOutputs = state?.node_outputs ?? {};
    const workflowInputs = state?.inputs ?? {};
    const workflowVariables = state?.variables ?? {};

    // Renderer-agnostic: pick whichever document renderer ran. Either node type
    // (PDFProposalRenderer / DOCXProposalRenderer) writes { minio_key, byte_size,
    // template_used }, so the viewer only needs the key + the file extension.
    // We find the producing node by looking for a node output that carries a
    // minio_key, rather than hard-coding a single node id, so renaming or
    // swapping the final node never breaks the download again.
    const RENDERER_IDS = ['generate_docx', 'generate_pdf'];
    let docNodeId: string | undefined = RENDERER_IDS.find((id) => nodeOutputs[id]?.minio_key);
    if (!docNodeId) {
        docNodeId = Object.keys(nodeOutputs).find((id) => nodeOutputs[id]?.minio_key);
    }
    const doc = docNodeId ? nodeOutputs[docNodeId] : undefined;
    const minioKey: string | undefined = doc?.minio_key;

    // Derive the file kind from the key's extension (e.g. proposal.docx -> DOCX).
    const ext = (minioKey?.split('.').pop() ?? '').toLowerCase();
    const fileKind = ext ? ext.toUpperCase() : 'FILE';
    // Only PDFs render inside an <iframe>; .docx cannot preview in-browser, so
    // for non-PDF documents we show a download card instead of a broken iframe.
    const canPreviewInline = ext === 'pdf';
    const artifactUrl =
        artifact && artifact.key === minioKey ? artifact.url ?? null : null;
    const artifactError =
        artifact && artifact.key === minioKey ? artifact.error ?? null : null;

    useEffect(() => {
        if (!minioKey || !canPreviewInline) return;
        let cancelled = false;
        let objectUrl: string | null = null;
        api.artifactBlobUrl(minioKey)
            .then(url => {
                objectUrl = url;
                if (!cancelled) setArtifact({ key: minioKey, url });
            })
            .catch(error => {
                if (!cancelled) setArtifact({ key: minioKey, error: String(error) });
            });
        return () => {
            cancelled = true;
            if (objectUrl) URL.revokeObjectURL(objectUrl);
        };
    }, [canPreviewInline, minioKey]);

    const citations = nodeOutputs.knowledge_retrieval?.citations ?? [];
    const auditLog: any[] = state?.audit_log ?? [];

    const answerText: string =
        nodeOutputs.compile_and_qa?.raw ??
        nodeOutputs.knowledge_retrieval?.answer ??
        Object.values(nodeOutputs).map((o: any) => o?.raw).filter(Boolean).join('\n\n') ??
        '';
    const sourcesText: string = citations
        .map((c: any) => `[${c.label}] ${c.source_doc}: ${c.snippet}`)
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
                <div className="p-4">
                    {citations.length === 0 ? (
                        <div className="text-sm text-ink-500">No citations recorded.</div>
                    ) : (
                        <ul className="space-y-2 text-xs">
                            {citations.map((c: any) => (
                                <li key={c.label} className="border border-slate-200 rounded-md p-2">
                                    <div className="font-medium">[{c.label}] {c.source_doc}</div>
                                    <div className="text-ink-500 mt-1">{c.snippet}</div>
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
                                        <div className="font-mono text-sm font-medium mb-1">{nid}</div>
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
    return (
        <div className="h-full flex flex-col">
            <header className="px-6 py-3 border-b border-slate-200 flex items-center justify-between bg-white">
                <div>
                    <h2 className="font-semibold">{workflowName ?? 'Proposal'} — completed</h2>
                    <div className="text-xs text-ink-500">
                        {doc.template_used ? `Template: ${doc.template_used} · ` : ''}
                        {typeof doc.byte_size === 'number' ? `${(doc.byte_size / 1024).toFixed(0)} KB · ` : ''}
                        {fileKind}
                    </div>
                </div>
                <div className="flex gap-2">
                    <button
                        type="button"
                        onClick={() => void api.downloadArtifact(minioKey)}
                        className="px-4 py-2 rounded-md bg-accent-600 text-white text-sm"
                    >
                        Download {fileKind}
                    </button>
                    {canPreviewInline && artifactUrl && (
                        <a href={artifactUrl} target="_blank" rel="noreferrer"
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
                    {canPreviewInline && artifactUrl ? (
                        <iframe title="Proposal" src={artifactUrl}
                            className="w-full h-full bg-white border border-slate-200 rounded-md" />
                    ) : canPreviewInline ? (
                        <div className="w-full h-full bg-white border border-slate-200 rounded-md flex items-center justify-center text-sm text-ink-500">
                            {artifactError ?? 'Loading secure preview…'}
                        </div>
                    ) : (
                        // .docx and other office formats cannot render in an <iframe>;
                        // offer a download card instead of a blank/broken preview.
                        <div className="w-full h-full bg-white border border-slate-200 rounded-md flex items-center justify-center">
                            <div className="text-center space-y-3">
                                <div className="text-sm text-ink-500">
                                    {fileKind} documents can&rsquo;t preview in the browser.
                                </div>
                                <button
                                    type="button"
                                    onClick={() => void api.downloadArtifact(minioKey)}
                                    className="inline-block px-4 py-2 rounded-md bg-accent-600 text-white text-sm">
                                    Download {fileKind}
                                </button>
                            </div>
                        </div>
                    )}
                </div>
                {sidePanel}
            </div>
        </div>
    );
}
