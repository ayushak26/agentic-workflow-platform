import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../../api/client';

export function OutputViewer({ state, workflowName }: { state: any; workflowName?: string }) {
    const navigate = useNavigate();
    const [tab, setTab] = useState<'sources' | 'audit'>('sources');

    const nodeOutputs = state?.node_outputs ?? {};
    const pdf = nodeOutputs.generate_pdf;
    const minioKey = pdf?.minio_key;
    const citations = nodeOutputs.knowledge_retrieval?.citations ?? [];
    const auditLog: any[] = state?.audit_log ?? [];

    // Diagnostic — remove once confirmed working.
    console.log('[OutputViewer] audit_log length:', auditLog.length,
        'node_outputs keys:', Object.keys(nodeOutputs),
        'state keys:', Object.keys(state ?? {}));

    // Fallback: if the audit_log channel is somehow empty, derive a timeline from
    // node_outputs so the tab is never blank when the run clearly executed.
    const auditEntries = auditLog.length > 0
        ? auditLog
        : Object.keys(nodeOutputs).map((nid) => ({ node_id: nid, type_name: '(derived from node_outputs)' }));

    const decisionBadge = (d: string) =>
        d === 'reject' ? 'bg-warn text-white'
            : d === 'edit' ? 'bg-accent-600 text-white'
                : 'bg-ok text-white';

    if (!minioKey) {
        const entries = Object.entries(nodeOutputs);
        return (
            <div className="h-full flex flex-col">
                <header className="px-6 py-3 border-b border-slate-200 flex items-center justify-between bg-white">
                    <div>
                        <h2 className="font-semibold">{workflowName ?? 'Workflow'} — completed</h2>
                        <div className="text-xs text-ink-500">
                            {entries.length} node{entries.length === 1 ? '' : 's'} executed · no PDF output
                        </div>
                    </div>
                    <button onClick={() => navigate('/studio/library')}
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

                    <aside className="w-72 border-l border-slate-200 bg-white overflow-y-auto p-4">
                        <div className="text-sm font-medium mb-2">Audit log ({auditEntries.length})</div>
                        {auditEntries.length === 0 ? (
                            <div className="text-xs text-ink-500">No audit entries.</div>
                        ) : (
                            <ul className="space-y-1.5 text-xs">
                                {auditEntries.map((e: any, i: number) => (
                                    <li key={i} className="border border-slate-200 rounded-md p-2">
                                        <div className="flex items-center justify-between">
                                            <span className="font-mono font-medium">{e.node_id}</span>
                                            {typeof e.duration_s === 'number' && (
                                                <span className="text-ink-400">{e.duration_s.toFixed(2)}s</span>
                                            )}
                                        </div>
                                        <div className="text-ink-500">{e.type_name}</div>
                                    </li>
                                ))}
                            </ul>
                        )}
                    </aside>
                </div>
            </div>
        );
    }

    const viewUrl = api.fileUrl(minioKey);
    const downloadUrl = api.fileUrl(minioKey, true);

    return (
        <div className="h-full flex flex-col">
            <header className="px-6 py-3 border-b border-slate-200 flex items-center justify-between bg-white">
                <div>
                    <h2 className="font-semibold">{workflowName ?? 'Proposal'} — completed</h2>
                    <div className="text-xs text-ink-500">
                        Template: {pdf.template_used} · {(pdf.byte_size / 1024).toFixed(0)} KB
                    </div>
                </div>
                <div className="flex gap-2">
                    <a href={downloadUrl} className="px-4 py-2 rounded-md bg-accent-600 text-white text-sm">
                        Download PDF
                    </a>
                    <a href={viewUrl} target="_blank" rel="noreferrer"
                        className="px-4 py-2 rounded-md border border-slate-300 text-sm hover:bg-slate-50">
                        Open in new tab
                    </a>
                    <button
                        onClick={() => navigate('/studio/library')}
                        className="px-4 py-2 rounded-md border border-slate-300 text-sm hover:bg-slate-50"
                    >
                        Back
                    </button>
                </div>
            </header>

            <div className="flex-1 flex min-h-0">
                <div className="flex-1 bg-slate-100 p-4">
                    <iframe title="Proposal" src={viewUrl}
                        className="w-full h-full bg-white border border-slate-200 rounded-md" />
                </div>

                <aside className="w-80 border-l border-slate-200 bg-white overflow-y-auto">
                    <div className="flex border-b border-slate-200">
                        <button onClick={() => setTab('sources')}
                            className={`flex-1 px-4 py-2 text-sm ${tab === 'sources' ? 'border-b-2 border-accent-600 font-medium' : 'text-ink-500'}`}>
                            Sources ({citations.length})
                        </button>
                        <button onClick={() => setTab('audit')}
                            className={`flex-1 px-4 py-2 text-sm ${tab === 'audit' ? 'border-b-2 border-accent-600 font-medium' : 'text-ink-500'}`}>
                            Audit log ({auditEntries.length})
                        </button>
                    </div>

                    {tab === 'sources' ? (
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
                    ) : (
                        <div className="p-4">
                            {auditEntries.length === 0 ? (
                                <div className="text-sm text-ink-500">No audit entries.</div>
                            ) : (
                                <ul className="space-y-1.5 text-xs">
                                    {auditEntries.map((e: any, i: number) => {
                                        const out = nodeOutputs[e.node_id] ?? {};
                                        const decision = out.decision;            // present on HITL nodes
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
                </aside>
            </div>
        </div>
    );
}