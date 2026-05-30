import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../../api/client';

export function OutputViewer({ state, workflowName }: { state: any; workflowName?: string }) {
  const navigate = useNavigate();
  const [tab, setTab] = useState<'sources' | 'audit'>('sources');

  const pdf = state?.node_outputs?.generate_pdf;
  const minioKey = pdf?.minio_key;
  const citations = state?.node_outputs?.knowledge_retrieval?.citations ?? [];
  const auditLog = state?.audit_log ?? [];

  if (!minioKey) {
    return (
      <div className="p-8">
        <div className="text-ok font-medium">Workflow completed</div>
        <div className="text-ink-500 text-sm mt-1">No proposal PDF was produced.</div>
        <button
          onClick={() => navigate('/studio/library')}
          className="mt-4 px-4 py-2 rounded-md bg-accent-600 text-white text-sm"
        >
          Back to Library
        </button>
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
          <iframe title="Proposal" src={viewUrl} className="w-full h-full bg-white border border-slate-200 rounded-md" />
        </div>

        <aside className="w-80 border-l border-slate-200 bg-white overflow-y-auto">
          <div className="flex border-b border-slate-200">
            <button onClick={() => setTab('sources')}
              className={`flex-1 px-4 py-2 text-sm ${tab === 'sources' ? 'border-b-2 border-accent-600 font-medium' : 'text-ink-500'}`}>
              Sources
            </button>
            <button onClick={() => setTab('audit')}
              className={`flex-1 px-4 py-2 text-sm ${tab === 'audit' ? 'border-b-2 border-accent-600 font-medium' : 'text-ink-500'}`}>
              Audit log
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
              {auditLog.length === 0 ? (
                <div className="text-sm text-ink-500">No audit entries.</div>
              ) : (
                <ul className="space-y-1.5 text-xs">
                  {auditLog.map((e: any, i: number) => (
                    <li key={i} className="border border-slate-200 rounded-md p-2">
                      <div className="font-medium">{e.node_id}</div>
                      <div className="text-ink-500">{e.type_name}</div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}