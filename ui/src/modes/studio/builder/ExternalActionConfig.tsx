import type { MCPOperationClass, OutputContract } from '../../../api/types';
import { ModeCard } from './RouterEditor';
import { OperationBadge } from './MCPToolConfig';
import { TemplateTextField } from './TemplateTextField';

/**
 * Configuring a call to a system this deployment has no MCP connection
 * for — a specific URL the author already knows.
 *
 * Kept visually and structurally separate from MCPToolConfig on purpose:
 * there is no server to discover, no tool list, no declared schema. What
 * this form asks for instead is exactly what an HTTP call needs (method,
 * URL, headers, body) plus the one thing that must never be silently
 * guessed — the safety class.
 */

type Config = Record<string, unknown>;

const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'] as const;
const SAFETY_CLASSES: { value: MCPOperationClass; label: string; hint: string }[] = [
  { value: 'read', label: 'Read', hint: 'Nothing changes on the other side.' },
  { value: 'write', label: 'Write', hint: 'A business record changes on the other side.' },
  {
    value: 'external_action',
    label: 'External action',
    hint: 'A generic outward effect — a notification, a trigger — that is neither a read nor a business-record write.',
  },
];

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

function headerEntries(config: Config): [string, string][] {
  const headers = config.headers as Record<string, unknown> | undefined;
  if (!headers || typeof headers !== 'object') return [];
  return Object.entries(headers).map(([key, value]) => [key, asString(value)]);
}

export function ExternalActionConfig({
  config,
  contract,
  onChange,
}: {
  config: Config;
  contract: OutputContract | null;
  onChange: (next: Config) => void;
}) {
  const actionType = config.action_type === 'webhook' ? 'webhook' : 'rest_api';
  const safetyClass = typeof config.safety_class === 'string' ? config.safety_class : '';
  const headers = headerEntries(config);
  const isWrite = safetyClass === 'write' || safetyClass === 'external_action';

  const setHeaders = (next: [string, string][]) => {
    const asObject: Record<string, string> = {};
    for (const [key, value] of next) {
      if (key) asObject[key] = value;
    }
    onChange({ ...config, headers: asObject });
  };

  return (
    <div>
      <section>
        <div className="builder-panel-heading">Mode</div>
        <div className="mt-2 grid grid-cols-2 gap-1.5">
          <ModeCard
            active={actionType === 'rest_api'}
            description="A request/response call — the response body is used by a later step."
            label="REST API"
            onSelect={() => onChange({ ...config, action_type: 'rest_api' })}
          />
          <ModeCard
            active={actionType === 'webhook'}
            description="A fire-and-forget outbound notification. Defaults to POST."
            label="Webhook"
            onSelect={() => onChange({
              ...config,
              action_type: 'webhook',
              method: config.method ?? 'POST',
            })}
          />
        </div>
      </section>

      <section className="mt-4">
        <div className="builder-panel-heading">
          Safety class <span className="text-red-500">*</span>
        </div>
        <p className="mt-1 text-[10px] leading-4 text-ink-500">
          How this call is classified. There is no default — state it, don&apos;t
          let the URL imply it.
        </p>
        <div className="mt-2 grid grid-cols-3 gap-1.5">
          {SAFETY_CLASSES.map(item => (
            <button
              className={`rounded-md border p-2 text-left transition ${
                safetyClass === item.value
                  ? 'border-accent-600 bg-accent-50'
                  : 'border-slate-200 hover:border-accent-400'
              }`}
              key={item.value}
              onClick={() => onChange({ ...config, safety_class: item.value })}
              type="button"
            >
              <div className="flex items-center gap-1.5">
                <OperationBadge operation={item.value} />
              </div>
              <div className="mt-1 text-[10px] leading-4 text-ink-500">{item.hint}</div>
            </button>
          ))}
        </div>
        {!safetyClass && (
          <div className="mt-2 rounded-md border border-amber-200 bg-amber-50 p-2 text-[11px] text-amber-900">
            Choose a safety class — this step cannot be saved without one.
          </div>
        )}
      </section>

      <section className="mt-4 grid grid-cols-[100px_1fr] gap-2">
        <label className="text-[11px] font-medium text-ink-700">
          Method
          <select
            className="builder-field mt-1"
            onChange={event => onChange({ ...config, method: event.target.value })}
            value={asString(config.method, 'POST')}
          >
            {METHODS.map(method => <option key={method} value={method}>{method}</option>)}
          </select>
        </label>
        <label className="text-[11px] font-medium text-ink-700">
          URL
          <TemplateTextField
            aria-label="URL"
            contract={contract}
            onChange={value => onChange({ ...config, url: value })}
            placeholder="https://example.com/api/… or {{outputs.…}}"
            rows={1}
            value={asString(config.url)}
          />
        </label>
      </section>

      <section className="mt-4">
        <div className="builder-panel-heading">Headers</div>
        <div className="mt-2 space-y-1.5">
          {headers.map(([key, value], index) => (
            <div className="flex items-center gap-1.5" key={index}>
              <input
                className="builder-field font-mono"
                onChange={event => {
                  const next = [...headers];
                  next[index] = [event.target.value, value];
                  setHeaders(next);
                }}
                placeholder="Header-Name"
                value={key}
              />
              <input
                className="builder-field font-mono"
                onChange={event => {
                  const next = [...headers];
                  next[index] = [key, event.target.value];
                  setHeaders(next);
                }}
                placeholder="value or {{outputs.…}}"
                value={value}
              />
              <button
                aria-label={`Remove header ${key || index + 1}`}
                className="ui-button ui-button--secondary px-2"
                onClick={() => setHeaders(headers.filter((_, i) => i !== index))}
                type="button"
              >
                ×
              </button>
            </div>
          ))}
          <button
            className="text-[11px] font-medium text-accent-700 hover:underline"
            onClick={() => setHeaders([...headers, ['', '']])}
            type="button"
          >
            + Add header
          </button>
        </div>
      </section>

      <section className="mt-4">
        <div className="builder-panel-heading">Body</div>
        <p className="mt-1 text-[10px] leading-4 text-ink-500">
          Sent as JSON. Map a single value from an earlier step to send its
          whole object, or write literal text.
        </p>
        <TemplateTextField
          aria-label="Body"
          contract={contract}
          onChange={value => onChange({ ...config, body: value })}
          placeholder="{{outputs.…}} or literal text"
          rows={4}
          value={asString(config.body)}
        />
      </section>

      <label className="mt-4 block text-[11px] font-medium text-ink-700">
        Timeout (seconds)
        <input
          className="builder-field mt-1"
          max={300}
          min={1}
          onChange={event => onChange({
            ...config,
            timeout_seconds: event.target.value === '' ? undefined : Number(event.target.value),
          })}
          type="number"
          value={typeof config.timeout_seconds === 'number' ? config.timeout_seconds : 30}
        />
      </label>

      {isWrite && (
        <section className="mt-4 rounded-md border border-amber-200 bg-amber-50 p-3">
          <div className="text-[11px] font-semibold text-amber-900">
            ⚠ This is a {safetyClass === 'write' ? 'write' : 'external'} action
          </div>
          <p className="mt-1 text-[11px] leading-4 text-amber-900">
            By default it will only run after a Human Review step has approved
            something on this path.
          </p>
          <label className="mt-2 flex items-start gap-2 text-[11px] text-amber-900">
            <input
              checked={Boolean(config.allow_unattended_write)}
              className="mt-0.5"
              onChange={event => onChange({
                ...config,
                allow_unattended_write: event.target.checked,
              })}
              type="checkbox"
            />
            <span>
              Allow this call without a human review
              <span className="block text-[10px] opacity-80">
                A deliberate override, not a default — make sure that&apos;s
                intended before turning it on.
              </span>
            </span>
          </label>
        </section>
      )}
    </div>
  );
}
