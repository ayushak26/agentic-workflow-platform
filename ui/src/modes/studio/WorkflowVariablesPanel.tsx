import { useMemo, useState } from 'react';

type VariableMap = Record<string, unknown>;

function visibleInputs(inputs: VariableMap): VariableMap {
  return Object.fromEntries(
    Object.entries(inputs).filter(([key]) => !key.startsWith('SYSTEM.')),
  );
}

function valueText(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value === undefined) return 'undefined';
  return JSON.stringify(value, null, 2) ?? String(value);
}

function templateReference(root: string, ...segments: string[]): string {
  return `{{${[root, ...segments].join('.')}}}`;
}

function VariableValue({
  reference,
  value,
  copied,
  onCopy,
}: {
  reference: string;
  value: unknown;
  copied: string | null;
  onCopy: (reference: string, text: string) => void;
}) {
  const rendered = valueText(value);
  return (
    <div className="rounded-lg border border-slate-200 bg-white overflow-hidden">
      <div className="flex items-center justify-between gap-3 bg-slate-50 px-3 py-2">
        <code className="min-w-0 break-all text-[11px] font-medium text-accent-700">
          {reference}
        </code>
        <div className="flex shrink-0 gap-1">
          <button
            type="button"
            onClick={() => onCopy(reference, reference)}
            className="rounded border border-slate-300 bg-white px-2 py-1 text-[10px] text-ink-700 hover:bg-slate-100"
          >
            {copied === reference ? 'Copied' : 'Copy reference'}
          </button>
          <button
            type="button"
            onClick={() => onCopy(`${reference}:value`, rendered)}
            className="rounded border border-slate-300 bg-white px-2 py-1 text-[10px] text-ink-700 hover:bg-slate-100"
          >
            {copied === `${reference}:value` ? 'Copied' : 'Copy value'}
          </button>
        </div>
      </div>
      <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words px-3 py-2 text-[11px] leading-relaxed text-ink-700">
        {rendered}
      </pre>
    </div>
  );
}

function VariableSection({
  title,
  note,
  values,
  root,
  copied,
  onCopy,
}: {
  title: string;
  note: string;
  values: VariableMap;
  root: 'inputs' | 'variables';
  copied: string | null;
  onCopy: (reference: string, text: string) => void;
}) {
  const entries = Object.entries(values);
  return (
    <section>
      <div className="mb-2">
        <div className="text-xs font-medium text-ink-700">{title}</div>
        <div className="text-[11px] text-ink-400">{note}</div>
      </div>
      {entries.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-200 px-3 py-3 text-xs text-ink-500">
          None available.
        </div>
      ) : (
        <div className="space-y-2">
          {entries.map(([name, value]) => (
            <VariableValue
              key={name}
              reference={templateReference(root, name)}
              value={value}
              copied={copied}
              onCopy={onCopy}
            />
          ))}
        </div>
      )}
    </section>
  );
}

export function WorkflowVariablesPanel({
  inputs = {},
  variables = {},
  outputs = {},
  live = false,
}: {
  inputs?: VariableMap;
  variables?: VariableMap;
  outputs?: VariableMap;
  live?: boolean;
}) {
  const [copied, setCopied] = useState<string | null>(null);
  const safeInputs = useMemo(() => visibleInputs(inputs), [inputs]);
  const outputEntries = Object.entries(outputs);

  async function copy(reference: string, text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(reference);
      window.setTimeout(() => setCopied(null), 1200);
    } catch {
      setCopied(null);
    }
  }

  return (
    <div className="space-y-6 p-4">
      <div className="rounded-lg border border-cyan-200 bg-cyan-50 px-3 py-2.5 text-xs text-cyan-900">
        <div className="font-medium">
          {live ? 'Live workflow variables' : 'Reusable workflow variables'}
        </div>
        <div className="mt-1 text-cyan-800">
          References resolve to exact values at runtime. Use explicit
          {' '}<code>{'{{outputs.node.field}}'}</code> paths in future node
          instructions; the older <code>{'{{node.field}}'}</code> shorthand
          remains supported.
        </div>
      </div>

      <VariableSection
        title="Inputs received"
        note="Names are defined by the workflow input schema."
        values={safeInputs}
        root="inputs"
        copied={copied}
        onCopy={copy}
      />

      <VariableSection
        title="Workflow variables"
        note="Fixed values defined under static_variables."
        values={variables}
        root="variables"
        copied={copied}
        onCopy={copy}
      />

      <section>
        <div className="mb-2">
          <div className="text-xs font-medium text-ink-700">
            Outputs received
          </div>
          <div className="text-[11px] text-ink-400">
            Created automatically when each node completes.
          </div>
        </div>
        {outputEntries.length === 0 ? (
          <div className="rounded-lg border border-dashed border-slate-200 px-3 py-3 text-xs text-ink-500">
            {live ? 'No node has completed yet.' : 'No outputs were recorded.'}
          </div>
        ) : (
          <div className="space-y-3">
            {outputEntries.map(([nodeId, output]) => {
              const fields = (
                output !== null
                && typeof output === 'object'
                && !Array.isArray(output)
              )
                ? Object.entries(output as VariableMap)
                : [];
              const nodeReference = templateReference('outputs', nodeId);
              return (
                <details
                  key={nodeId}
                  className="rounded-lg border border-slate-200 bg-white"
                >
                  <summary className="cursor-pointer px-3 py-2.5">
                    <div className="inline-flex max-w-[calc(100%-1rem)] items-center gap-2 align-middle">
                      <span className="font-mono text-xs font-medium text-ink-900">
                        {nodeId}
                      </span>
                      <code className="truncate text-[10px] text-accent-700">
                        {nodeReference}
                      </code>
                    </div>
                  </summary>
                  <div className="space-y-2 border-t border-slate-100 p-3">
                    <VariableValue
                      reference={nodeReference}
                      value={output}
                      copied={copied}
                      onCopy={copy}
                    />
                    {fields.map(([field, value]) => (
                      <VariableValue
                        key={field}
                        reference={templateReference('outputs', nodeId, field)}
                        value={value}
                        copied={copied}
                        onCopy={copy}
                      />
                    ))}
                  </div>
                </details>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
