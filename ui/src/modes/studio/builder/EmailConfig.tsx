import { useState } from 'react';

import type {
  ContractField,
  EmailConnectionInfo,
  NodePreset,
  OutputContract,
} from '../../../api/types';
import { FieldPicker } from './FieldPicker';

/**
 * The Email capability's configuration.
 *
 * One connection plus one operation — the shape that replaces a node type per
 * provider and verb. The connection dropdown lists mailboxes configured for the
 * deployment; the workflow only ever stores the connection's name, so it can be
 * exported and shared without carrying mailbox access.
 *
 * Operations that act outside the platform are marked as such here, not buried
 * in a description, because deciding what may happen automatically is the
 * author's call and has to be visible while they make it.
 */

type Config = Record<string, unknown>;

const WRITE_OPERATIONS = new Set(['create_draft', 'reply', 'send']);
const SENDING_OPERATIONS = new Set(['reply', 'send']);

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

type Recipient = { email: string; name?: string };

function recipientsOf(value: unknown): Recipient[] {
  return Array.isArray(value) ? (value as Recipient[]) : [];
}

export function EmailConfig({
  config,
  connections,
  contract,
  onChange,
  presets,
}: {
  config: Config;
  connections: EmailConnectionInfo[];
  contract: OutputContract | null;
  onChange: (next: Config) => void;
  presets: NodePreset[];
}) {
  const operation = asString(config.operation, 'search');
  const connectionId = asString(config.connection);
  const connection = connections.find(item => item.id === connectionId);
  const set = (patch: Config) => onChange({ ...config, ...patch });

  return (
    <div>
      <section>
        <div className="builder-panel-heading">Mailbox connection</div>
        {connections.length === 0 ? (
          <div className="mt-2 rounded-md border border-amber-200 bg-amber-50 p-2 text-[11px] leading-4 text-amber-900">
            No mailbox is configured for this deployment. A workflow can still be
            built — preflight will block the run until a connection exists.
            <input
              className="builder-field mt-2 font-mono"
              onChange={event => set({ connection: event.target.value })}
              placeholder="support_inbox"
              value={connectionId}
            />
          </div>
        ) : (
          <select
            className="builder-field mt-2"
            onChange={event => set({ connection: event.target.value })}
            value={connectionId}
          >
            <option value="">Choose a mailbox…</option>
            {connections.map(item => (
              <option key={item.id} value={item.id}>
                {item.display_name} · {item.provider}
                {item.address ? ` · ${item.address}` : ''}
                {item.allow_send ? '' : ' (read only)'}
              </option>
            ))}
          </select>
        )}
        {connection && (
          <p className="mt-1 text-[10px] text-ink-500">
            Provider differences are handled below this step. Switching from
            Gmail to Microsoft is a connection change, not a workflow change.
          </p>
        )}
      </section>

      <section className="mt-4">
        <div className="builder-panel-heading">Operation</div>
        <div className="mt-2 grid grid-cols-2 gap-1.5">
          {presets.map(preset => {
            const value = String(preset.config?.operation ?? preset.id);
            const active = operation === value;
            return (
              <button
                className={`rounded-md border p-2 text-left transition ${
                  active ? 'border-accent-600 bg-accent-50' : 'border-slate-200 hover:border-accent-400'
                }`}
                key={preset.id}
                onClick={() => set({ operation: value })}
                type="button"
              >
                <div className="flex items-center gap-1">
                  <span className="text-[11px] font-semibold text-ink-900">
                    {preset.label}
                  </span>
                  {preset.external_action && (
                    <span className="rounded bg-amber-100 px-1 text-[9px] text-amber-800">
                      acts outside
                    </span>
                  )}
                </div>
                <div className="mt-0.5 text-[10px] leading-4 text-ink-500">
                  {preset.summary}
                </div>
              </button>
            );
          })}
        </div>
      </section>

      {connection && SENDING_OPERATIONS.has(operation) && !connection.allow_send && (
        <div className="mt-3 rounded-md border border-red-200 bg-red-50 p-2 text-[11px] leading-4 text-red-800">
          <span className="font-mono">{connection.display_name}</span> is not
          permitted to send. Choose a different mailbox, or use Create Draft so a
          person sends it.
        </div>
      )}

      {SENDING_OPERATIONS.has(operation) && (
        <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-2 text-[11px] leading-4 text-amber-900">
          This reaches the recipient immediately. Put a Human Review step in front
          of it unless you have decided this may happen unattended — preflight
          warns if there is none.
        </div>
      )}

      {operation === 'search' && (
        <SearchFields config={config} onChange={onChange} />
      )}

      {(operation === 'read' || operation === 'reply') && (
        <MessageReference
          config={config}
          contract={contract}
          label={operation === 'read' ? 'Message to read' : 'Message being replied to'}
          onChange={onChange}
        />
      )}

      {WRITE_OPERATIONS.has(operation) && (
        <MessageFields config={config} onChange={onChange} showRecipients={operation !== 'reply'} />
      )}
    </div>
  );
}

function SearchFields({
  config,
  onChange,
}: {
  config: Config;
  onChange: (next: Config) => void;
}) {
  const set = (patch: Config) => onChange({ ...config, ...patch });
  return (
    <section className="mt-4 space-y-2">
      <div className="builder-panel-heading">Search criteria</div>
      <label className="block text-[11px] font-medium text-ink-700">
        Text to look for
        <input
          className="builder-field mt-1"
          onChange={event => set({ query: event.target.value })}
          value={asString(config.query)}
        />
      </label>
      <label className="block text-[11px] font-medium text-ink-700">
        From address
        <input
          className="builder-field mt-1"
          onChange={event => set({ from_address: event.target.value })}
          value={asString(config.from_address)}
        />
      </label>
      <label className="block text-[11px] font-medium text-ink-700">
        Subject contains
        <input
          className="builder-field mt-1"
          onChange={event => set({ subject_contains: event.target.value })}
          value={asString(config.subject_contains)}
        />
      </label>
      <div className="flex gap-3">
        <label className="flex items-center gap-1.5 text-[11px] text-ink-700">
          <input
            checked={Boolean(config.unread_only)}
            onChange={event => set({ unread_only: event.target.checked })}
            type="checkbox"
          />
          Unread only
        </label>
        <label className="flex items-center gap-1.5 text-[11px] text-ink-700">
          <input
            checked={Boolean(config.has_attachments)}
            onChange={event => set({ has_attachments: event.target.checked })}
            type="checkbox"
          />
          Has attachments
        </label>
      </div>
      <div className="flex gap-2">
        <label className="flex-1 text-[11px] font-medium text-ink-700">
          From the last (days)
          <input
            className="builder-field mt-1"
            min={1}
            onChange={event => set({
              newer_than_days: event.target.value === '' ? null : Number(event.target.value),
            })}
            type="number"
            value={typeof config.newer_than_days === 'number' ? config.newer_than_days : ''}
          />
        </label>
        <label className="flex-1 text-[11px] font-medium text-ink-700">
          Most results
          <input
            className="builder-field mt-1"
            max={100}
            min={1}
            onChange={event => set({ max_results: Number(event.target.value) })}
            type="number"
            value={typeof config.max_results === 'number' ? config.max_results : 10}
          />
        </label>
      </div>
    </section>
  );
}

function MessageReference({
  config,
  contract,
  label,
  onChange,
}: {
  config: Config;
  contract: OutputContract | null;
  label: string;
  onChange: (next: Config) => void;
}) {
  const [picking, setPicking] = useState(false);
  return (
    <section className="mt-4">
      <div className="flex items-center justify-between">
        <label className="text-[11px] font-medium text-ink-700">{label}</label>
        <button
          className="text-[11px] font-medium text-accent-700 hover:underline"
          onClick={() => setPicking(value => !value)}
          type="button"
        >
          {picking ? 'Close picker' : 'Pick a value'}
        </button>
      </div>
      <input
        className="builder-field mt-1 font-mono"
        onChange={event => onChange({ ...config, message_id: event.target.value })}
        placeholder="{{outputs.find_email.message.id}}"
        value={asString(config.message_id)}
      />
      {picking && (
        <div className="mt-2 rounded border border-slate-200 p-2">
          <FieldPicker
            contract={contract}
            onPick={(field: ContractField) => {
              onChange({ ...config, message_id: field.reference });
              setPicking(false);
            }}
            selectedReference={asString(config.message_id)}
          />
        </div>
      )}
    </section>
  );
}

function MessageFields({
  config,
  onChange,
  showRecipients,
}: {
  config: Config;
  onChange: (next: Config) => void;
  showRecipients: boolean;
}) {
  const recipients = recipientsOf(config.to);
  const set = (patch: Config) => onChange({ ...config, ...patch });

  return (
    <section className="mt-4 space-y-2">
      <div className="builder-panel-heading">Message</div>

      {showRecipients && (
        <div>
          <div className="text-[11px] font-medium text-ink-700">To</div>
          <div className="mt-1 space-y-1">
            {recipients.map((recipient, index) => (
              <div className="flex gap-1" key={index}>
                <input
                  aria-label={`Recipient ${index + 1}`}
                  className="builder-field font-mono"
                  onChange={event => {
                    const next = [...recipients];
                    next[index] = { ...recipient, email: event.target.value };
                    set({ to: next });
                  }}
                  placeholder="{{outputs.understand.result.customer.email}}"
                  value={recipient.email ?? ''}
                />
                <button
                  aria-label="Remove recipient"
                  className="px-1 text-ink-400 hover:text-red-600"
                  onClick={() => set({
                    to: recipients.filter((_, position) => position !== index),
                  })}
                  type="button"
                >×</button>
              </div>
            ))}
          </div>
          <button
            className="mt-1 text-[11px] font-medium text-accent-700 hover:underline"
            onClick={() => set({ to: [...recipients, { email: '' }] })}
            type="button"
          >
            + Add recipient
          </button>
        </div>
      )}

      <label className="block text-[11px] font-medium text-ink-700">
        Subject
        <input
          className="builder-field mt-1"
          onChange={event => set({ subject: event.target.value })}
          value={asString(config.subject)}
        />
      </label>

      <label className="block text-[11px] font-medium text-ink-700">
        Body
        <textarea
          className="builder-field mt-1"
          onChange={event => set({ body: event.target.value })}
          placeholder="{{outputs.draft_reply.text}}"
          rows={6}
          value={asString(config.body)}
        />
      </label>
      <p className="text-[10px] text-ink-500">
        Usually mapped from a drafting step&apos;s output, reviewed by a person
        before it is sent.
      </p>
    </section>
  );
}
