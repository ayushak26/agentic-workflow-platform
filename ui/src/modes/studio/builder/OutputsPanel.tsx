import { useEffect, useState } from 'react';

import { api } from '../../../api/client';
import type { ContractNode, OutputContract } from '../../../api/types';
import { typeLabel } from './FieldPicker';

/**
 * The Outputs tab (§40).
 *
 * Shows exactly what this step guarantees to the steps after it, as typed
 * paths. That contract is what the next node's rule editor and mapping picker
 * are driven by, so seeing it here is seeing the real interface — not
 * documentation about it.
 *
 * Fetched from the backend rather than derived in the browser, because the
 * backend derives it from the same index preflight uses. Two derivations would
 * eventually disagree, and the one the author reads would be the wrong one.
 */

export function OutputsPanel({
  workflowYaml,
  nodeId,
}: {
  workflowYaml: string;
  nodeId: string;
}) {
  const [contract, setContract] = useState<ContractNode | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    // No node_id filter: this asks for the whole workflow's contract and picks
    // this node out of it, because a node's own outputs are excluded from the
    // upstream-filtered view (which exists to answer "what can I read here?").
    api.outputContract(workflowYaml)
      .then((result: OutputContract) => {
        if (cancelled) return;
        setContract(result.nodes.find(item => item.node_id === nodeId) ?? null);
        setError(null);
      })
      .catch(reason => {
        if (cancelled) return;
        setError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [nodeId, workflowYaml]);

  if (loading) {
    return <div className="p-5 text-[11px] text-ink-500">Reading the contract…</div>;
  }

  if (error) {
    return (
      <div className="p-4">
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-[11px] text-red-800">
          {error}
        </div>
      </div>
    );
  }

  if (!contract || contract.fields.length === 0) {
    return (
      <div className="p-5">
        <div className="rounded-lg border border-dashed border-ink-200 p-5 text-center">
          <div className="text-sm font-semibold text-ink-800">
            Nothing declared yet
          </div>
          <div className="mt-1 text-xs leading-5 text-ink-500">
            Once this step has an output schema, everything it guarantees to
            later steps is listed here.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="builder-inspector-scroll p-4">
      <div className="builder-panel-heading">This step guarantees</div>
      <p className="mt-1 text-[11px] leading-4 text-ink-500">
        Later steps address these paths directly. The rule editor and mapping
        picker offer exactly this list, and preflight rejects anything else.
      </p>

      {!contract.typed && (
        <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-2 text-[11px] text-amber-900">
          This step&apos;s output is not typed, so references into it cannot be
          checked before a run.
        </div>
      )}

      <div className="mt-3 overflow-hidden rounded-md border border-slate-200">
        <table className="w-full text-left">
          <thead className="bg-slate-50 text-[10px] uppercase tracking-wide text-ink-500">
            <tr>
              <th className="px-2 py-1.5 font-semibold">Field</th>
              <th className="px-2 py-1.5 font-semibold">Type</th>
              <th className="px-2 py-1.5 font-semibold">Availability</th>
            </tr>
          </thead>
          <tbody>
            {contract.fields.map(field => (
              <tr className="border-t border-slate-100 align-top" key={field.path}>
                <td className="px-2 py-1.5">
                  <div className="font-mono text-[11px] text-ink-800">{field.path}</div>
                  {field.description && (
                    <div className="mt-0.5 text-[10px] leading-4 text-ink-500">
                      {field.description}
                    </div>
                  )}
                  {field.enum_values.length > 0 && (
                    <div className="mt-0.5 text-[10px] text-ink-500">
                      One of: {field.enum_values.join(', ')}
                    </div>
                  )}
                </td>
                <td className="px-2 py-1.5 text-[10px] text-ink-600">
                  {typeLabel(field)}
                </td>
                <td className="px-2 py-1.5 text-[10px]">
                  <span
                    className={
                      field.may_be_unavailable ? 'text-amber-600' : 'text-emerald-700'
                    }
                  >
                    {field.may_be_unavailable ? 'May be empty' : 'Always set'}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
