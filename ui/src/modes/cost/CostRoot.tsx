import { useEffect, useMemo, useState } from 'react';

import { api } from '../../api/client';
import type {
  BudgetsResponse,
  CacheSummary,
  CostBreakdownRow,
  CostOverview,
  DirectPricingEntry,
  InfraAllocationEntry,
  OpenRouterPricingEntry,
  PricingResponse,
} from '../../api/types';
import { Icon, type IconName } from '../../components/ui/Icon';

type Tab = 'overview' | 'pricing' | 'infra' | 'cache' | 'budgets';

const TABS: { id: Tab; label: string; icon: IconName }[] = [
  { id: 'overview', label: 'Overview', icon: 'grid' },
  { id: 'pricing', label: 'Pricing', icon: 'coin' },
  { id: 'infra', label: 'Private Infra', icon: 'terminal' },
  { id: 'cache', label: 'Prompt Cache', icon: 'refresh' },
  { id: 'budgets', label: 'Budgets', icon: 'checklist' },
];

function usd(value: number): string {
  if (value > 0 && value < 0.0001) return '<$0.0001';
  return `$${value.toFixed(value < 1 ? 4 : 2)}`;
}

function Card({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`rounded-lg border border-slate-200 bg-white p-4 ${className}`}>
      {children}
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[11px] font-semibold uppercase tracking-wide text-ink-500 mb-2">
      {children}
    </div>
  );
}

function BreakdownList({ rows, emptyLabel }: { rows: CostBreakdownRow[]; emptyLabel: string }) {
  if (rows.length === 0) {
    return <div className="text-xs text-ink-500">{emptyLabel}</div>;
  }
  const max = Math.max(...rows.map((r) => r.cost_usd), 0.0001);
  return (
    <div className="space-y-1.5">
      {rows.slice(0, 10).map((row) => (
        <div key={row.label} className="text-xs">
          <div className="flex items-center justify-between mb-0.5">
            <span className="truncate text-ink-700" title={row.label}>{row.label}</span>
            <span className="ml-2 shrink-0 font-medium text-ink-900">{usd(row.cost_usd)}</span>
          </div>
          <div className="h-1.5 rounded-full bg-slate-100 overflow-hidden">
            <div
              className="h-full rounded-full bg-accent-500"
              style={{ width: `${Math.max(2, (row.cost_usd / max) * 100)}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

function OverviewTab() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<CostOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.costAdminOverview(days)
      .then((result) => { if (!cancelled) setData(result); })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : String(err)); });
    return () => { cancelled = true; };
  }, [days]);

  if (error) return <div className="text-sm text-bad">{error}</div>;
  if (!data) return <div className="text-sm text-ink-500">Loading…</div>;

  const maxDaily = Math.max(...data.daily_trend.map((d) => d.cost_usd), 0.0001);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex gap-4">
          <Card className="min-w-[140px]">
            <SectionTitle>Total ({days}d)</SectionTitle>
            <div className="text-2xl font-semibold text-ink-900">{usd(data.total_usd)}</div>
            <div className="text-[11px] text-ink-500 mt-0.5">{data.call_count} calls</div>
          </Card>
          <Card className="min-w-[140px]">
            <SectionTitle>Allocated infra</SectionTitle>
            <div className="text-2xl font-semibold text-ink-900">{usd(data.allocated_infra_usd)}</div>
            <div className="text-[11px] text-ink-500 mt-0.5">estimated, not billed</div>
          </Card>
        </div>
        <select
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-xs"
        >
          <option value={7}>Last 7 days</option>
          <option value={30}>Last 30 days</option>
          <option value={90}>Last 90 days</option>
          <option value={365}>Last 365 days</option>
        </select>
      </div>

      <Card>
        <SectionTitle>Daily spend</SectionTitle>
        {data.daily_trend.length === 0 ? (
          <div className="text-xs text-ink-500">No spend recorded in this window.</div>
        ) : (
          <div className="flex items-end gap-0.5 h-24">
            {data.daily_trend.map((d) => (
              <div
                key={d.date}
                className="flex-1 bg-accent-400 rounded-t hover:bg-accent-600 transition-colors"
                style={{ height: `${Math.max(2, (d.cost_usd / maxDaily) * 100)}%` }}
                title={`${d.date}: ${usd(d.cost_usd)}`}
              />
            ))}
          </div>
        )}
      </Card>

      <div className="grid grid-cols-2 gap-4">
        <Card>
          <SectionTitle>By model</SectionTitle>
          <BreakdownList rows={data.by_model} emptyLabel="No model spend yet." />
        </Card>
        <Card>
          <SectionTitle>By provider</SectionTitle>
          <BreakdownList rows={data.by_provider} emptyLabel="No provider spend yet." />
        </Card>
        <Card>
          <SectionTitle>By workflow</SectionTitle>
          <BreakdownList rows={data.by_workflow} emptyLabel="No workflow spend yet." />
        </Card>
        <Card>
          <SectionTitle>By knowledge collection</SectionTitle>
          <BreakdownList rows={data.by_collection} emptyLabel="No collection-scoped spend yet." />
        </Card>
      </div>
    </div>
  );
}

function PricingTab() {
  const [data, setData] = useState<PricingResponse | null>(null);
  const [query, setQuery] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Record<string, { input: string; output: string }>>({});

  const refresh = (q = query) => {
    api.costAdminPricing(q, 25)
      .then(setData)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  };

  useEffect(() => { refresh(''); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const timer = window.setTimeout(() => refresh(query), 300);
    return () => window.clearTimeout(timer);
  }, [query]); // eslint-disable-line react-hooks/exhaustive-deps

  const saveOverride = async (model: string) => {
    const draft = editing[model];
    if (!draft) return;
    const input_usd_per_1k = Number(draft.input);
    const output_usd_per_1k = Number(draft.output);
    if (!Number.isFinite(input_usd_per_1k) || !Number.isFinite(output_usd_per_1k)) return;
    await api.setPricingOverride(model, { input_usd_per_1k, output_usd_per_1k });
    setEditing((prev) => { const next = { ...prev }; delete next[model]; return next; });
    refresh();
  };

  const revert = async (model: string) => {
    await api.clearPricingOverride(model);
    refresh();
  };

  if (error) return <div className="text-sm text-bad">{error}</div>;
  if (!data) return <div className="text-sm text-ink-500">Loading…</div>;

  return (
    <div className="space-y-6">
      <Card>
        <SectionTitle>Direct &amp; local models — editable</SectionTitle>
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-ink-500 border-b border-slate-100">
              <th className="pb-1.5 font-medium">Model</th>
              <th className="pb-1.5 font-medium">Input $/1k</th>
              <th className="pb-1.5 font-medium">Output $/1k</th>
              <th className="pb-1.5 font-medium">Source</th>
              <th className="pb-1.5 font-medium" />
            </tr>
          </thead>
          <tbody>
            {data.direct.map((row: DirectPricingEntry) => {
              const draft = editing[row.model];
              return (
                <tr key={row.model} className="border-b border-slate-50">
                  <td className="py-1.5 text-ink-900">{row.model}</td>
                  <td className="py-1.5">
                    <input
                      type="number"
                      step="0.0001"
                      value={draft?.input ?? String(row.input_usd_per_1k)}
                      onChange={(e) => setEditing((prev) => ({
                        ...prev,
                        [row.model]: { input: e.target.value, output: draft?.output ?? String(row.output_usd_per_1k) },
                      }))}
                      className="w-20 rounded border border-slate-300 px-1.5 py-0.5"
                    />
                  </td>
                  <td className="py-1.5">
                    <input
                      type="number"
                      step="0.0001"
                      value={draft?.output ?? String(row.output_usd_per_1k)}
                      onChange={(e) => setEditing((prev) => ({
                        ...prev,
                        [row.model]: { input: draft?.input ?? String(row.input_usd_per_1k), output: e.target.value },
                      }))}
                      className="w-20 rounded border border-slate-300 px-1.5 py-0.5"
                    />
                  </td>
                  <td className="py-1.5">
                    <span className={row.source === 'override' ? 'text-accent-700 font-medium' : 'text-ink-500'}>
                      {row.source}
                    </span>
                  </td>
                  <td className="py-1.5 text-right space-x-2">
                    {draft && (
                      <button type="button" onClick={() => saveOverride(row.model)} className="text-accent-700 font-medium">
                        Save
                      </button>
                    )}
                    {row.source === 'override' && (
                      <button type="button" onClick={() => revert(row.model)} className="text-ink-500">
                        Revert
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>

      <Card>
        <SectionTitle>OpenRouter — live, read-only</SectionTitle>
        <p className="text-[11px] text-ink-500 mb-2">
          OpenRouter prices and bills its own ~400-500 models directly (reported per-call via
          usage.cost) — nothing here is overridable.
        </p>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search OpenRouter models…"
          className="mb-2 w-full rounded-md border border-slate-300 px-2 py-1.5 text-xs"
        />
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-ink-500 border-b border-slate-100">
              <th className="pb-1.5 font-medium">Model</th>
              <th className="pb-1.5 font-medium">Input $/1M</th>
              <th className="pb-1.5 font-medium">Output $/1M</th>
            </tr>
          </thead>
          <tbody>
            {data.openrouter.map((row: OpenRouterPricingEntry) => (
              <tr key={row.model} className="border-b border-slate-50">
                <td className="py-1.5 text-ink-900">{row.display_name}</td>
                <td className="py-1.5 text-ink-700">
                  {row.input_usd_per_million != null ? `$${row.input_usd_per_million.toFixed(2)}` : '—'}
                </td>
                <td className="py-1.5 text-ink-700">
                  {row.output_usd_per_million != null ? `$${row.output_usd_per_million.toFixed(2)}` : '—'}
                </td>
              </tr>
            ))}
            {data.openrouter.length === 0 && (
              <tr><td colSpan={3} className="py-2 text-ink-500">No matching models.</td></tr>
            )}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function InfraTab() {
  const [data, setData] = useState<InfraAllocationEntry[] | null>(null);
  const [form, setForm] = useState<Record<string, { type: 'per_call' | 'monthly_amortized'; value: string; calls: string }>>({});

  const refresh = () => api.costAdminInfraAllocations().then((r) => setData(r.models));
  useEffect(() => { refresh(); }, []);

  const save = async (model: string) => {
    const draft = form[model];
    if (!draft) return;
    const value_usd = Number(draft.value);
    if (!Number.isFinite(value_usd)) return;
    await api.setInfraAllocation(model, {
      allocation_type: draft.type,
      value_usd,
      expected_monthly_calls: draft.type === 'monthly_amortized' ? Number(draft.calls) || null : null,
    });
    refresh();
  };

  if (!data) return <div className="text-sm text-ink-500">Loading…</div>;

  return (
    <Card>
      <SectionTitle>Local model infra allocation</SectionTitle>
      <p className="text-[11px] text-ink-500 mb-3">
        Local models are $0 API-metered but run on real GPU/infrastructure. Configure an
        estimated cost so reporting can show it — this never changes the real (accurate) $0
        billed figures, it's an overlay applied only when reading the Overview tab.
      </p>
      <div className="space-y-4">
        {data.map((entry) => {
          const draft = form[entry.model] ?? {
            type: entry.allocation?.allocation_type ?? 'per_call',
            value: entry.allocation ? String(entry.allocation.value_usd) : '',
            calls: entry.allocation?.expected_monthly_calls ? String(entry.allocation.expected_monthly_calls) : '',
          };
          return (
            <div key={entry.model} className="rounded-md border border-slate-200 p-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-medium text-ink-900">{entry.model}</span>
                {entry.effective_usd_per_call != null && (
                  <span className="text-xs text-ink-500">
                    ≈ {usd(entry.effective_usd_per_call)}/call today
                  </span>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <select
                  value={draft.type}
                  onChange={(e) => setForm((prev) => ({ ...prev, [entry.model]: { ...draft, type: e.target.value as 'per_call' | 'monthly_amortized' } }))}
                  className="rounded border border-slate-300 px-1.5 py-1"
                >
                  <option value="per_call">Per call ($)</option>
                  <option value="monthly_amortized">Monthly amortized</option>
                </select>
                <input
                  type="number"
                  step="0.0001"
                  placeholder={draft.type === 'per_call' ? '$ per call' : 'monthly $ total'}
                  value={draft.value}
                  onChange={(e) => setForm((prev) => ({ ...prev, [entry.model]: { ...draft, value: e.target.value } }))}
                  className="w-32 rounded border border-slate-300 px-1.5 py-1"
                />
                {draft.type === 'monthly_amortized' && (
                  <input
                    type="number"
                    placeholder="expected calls/month"
                    value={draft.calls}
                    onChange={(e) => setForm((prev) => ({ ...prev, [entry.model]: { ...draft, calls: e.target.value } }))}
                    className="w-36 rounded border border-slate-300 px-1.5 py-1"
                  />
                )}
                <button
                  type="button"
                  onClick={() => save(entry.model)}
                  className="rounded-md bg-accent-600 px-2.5 py-1 text-white font-medium"
                >
                  Save
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

function CacheTab() {
  const [data, setData] = useState<CacheSummary | null>(null);
  useEffect(() => { api.costAdminCacheSummary(30).then(setData); }, []);
  if (!data) return <div className="text-sm text-ink-500">Loading…</div>;

  return (
    <div className="space-y-4">
      <div className="flex gap-4">
        <Card className="min-w-[160px]">
          <SectionTitle>Estimated savings ({data.since_days}d)</SectionTitle>
          <div className="text-2xl font-semibold text-emerald-700">{usd(data.estimated_total_savings_usd)}</div>
        </Card>
        <Card className="min-w-[160px]">
          <SectionTitle>Cache read tokens</SectionTitle>
          <div className="text-2xl font-semibold text-ink-900">{data.total_cache_read_tokens.toLocaleString()}</div>
        </Card>
        <Card className="min-w-[160px]">
          <SectionTitle>Cache write tokens</SectionTitle>
          <div className="text-2xl font-semibold text-ink-900">{data.total_cache_creation_tokens.toLocaleString()}</div>
        </Card>
      </div>
      <Card>
        <SectionTitle>By model</SectionTitle>
        {data.by_model.length === 0 ? (
          <div className="text-xs text-ink-500">No cache activity in this window.</div>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-ink-500 border-b border-slate-100">
                <th className="pb-1.5 font-medium">Model</th>
                <th className="pb-1.5 font-medium">Read tokens</th>
                <th className="pb-1.5 font-medium">Write tokens</th>
                <th className="pb-1.5 font-medium">Est. savings</th>
              </tr>
            </thead>
            <tbody>
              {data.by_model.map((row) => (
                <tr key={row.model} className="border-b border-slate-50">
                  <td className="py-1.5 text-ink-900">{row.model}</td>
                  <td className="py-1.5 text-ink-700">{row.cache_read_tokens.toLocaleString()}</td>
                  <td className="py-1.5 text-ink-700">{row.cache_creation_tokens.toLocaleString()}</td>
                  <td className="py-1.5 text-emerald-700 font-medium">{usd(row.estimated_savings_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

function BudgetsTab() {
  const [data, setData] = useState<BudgetsResponse | null>(null);
  const [globalDraft, setGlobalDraft] = useState('');
  const [sessionDrafts, setSessionDrafts] = useState<Record<string, string>>({});

  const refresh = () => api.costAdminBudgets().then(setData);
  useEffect(() => { refresh(); }, []);

  const saveGlobal = async () => {
    const value = Number(globalDraft);
    if (!Number.isFinite(value)) return;
    await api.setGlobalBudget(value);
    setGlobalDraft('');
    refresh();
  };

  const saveSession = async (sessionId: string) => {
    const value = Number(sessionDrafts[sessionId]);
    if (!Number.isFinite(value)) return;
    await api.setSessionBudget(sessionId, value);
    setSessionDrafts((prev) => { const next = { ...prev }; delete next[sessionId]; return next; });
    refresh();
  };

  if (!data) return <div className="text-sm text-ink-500">Loading…</div>;

  return (
    <div className="space-y-4">
      <Card>
        <SectionTitle>Global daily budget</SectionTitle>
        <div className="flex items-center gap-3">
          <div>
            <div className="text-xl font-semibold text-ink-900">
              {usd(data.global.spend_today_usd)} <span className="text-xs font-normal text-ink-500">today</span>
            </div>
            <div className="text-xs text-ink-500">
              Limit: {data.global.daily_limit_usd != null ? usd(data.global.daily_limit_usd) : 'not set'}
              {data.global.exceeded && (
                <span className="ml-2 rounded-full bg-red-100 px-2 py-0.5 text-red-700 font-medium">exceeded</span>
              )}
            </div>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <input
              type="number"
              step="0.01"
              placeholder="new daily limit ($)"
              value={globalDraft}
              onChange={(e) => setGlobalDraft(e.target.value)}
              className="w-36 rounded border border-slate-300 px-1.5 py-1 text-xs"
            />
            <button type="button" onClick={saveGlobal} className="rounded-md bg-accent-600 px-2.5 py-1 text-xs text-white font-medium">
              Save
            </button>
          </div>
        </div>
      </Card>

      <Card>
        <SectionTitle>Per-session spend today</SectionTitle>
        <p className="text-[11px] text-ink-500 mb-2">
          Scoped by session_id (the same identity a run is scoped to), not necessarily one row
          per human user.
        </p>
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-ink-500 border-b border-slate-100">
              <th className="pb-1.5 font-medium">Session</th>
              <th className="pb-1.5 font-medium">Spend today</th>
              <th className="pb-1.5 font-medium">Limit</th>
              <th className="pb-1.5 font-medium" />
            </tr>
          </thead>
          <tbody>
            {data.by_session.map((row) => (
              <tr key={row.session_id} className="border-b border-slate-50">
                <td className="py-1.5 text-ink-900 truncate max-w-[160px]" title={row.session_id}>{row.session_id}</td>
                <td className="py-1.5 text-ink-700">
                  {usd(row.spend_today_usd)}
                  {row.exceeded && <span className="ml-1.5 rounded-full bg-red-100 px-1.5 py-0.5 text-red-700">exceeded</span>}
                </td>
                <td className="py-1.5 text-ink-700">{row.daily_limit_usd != null ? usd(row.daily_limit_usd) : '—'}</td>
                <td className="py-1.5 text-right">
                  <div className="flex items-center justify-end gap-1.5">
                    <input
                      type="number"
                      step="0.01"
                      placeholder="$"
                      value={sessionDrafts[row.session_id] ?? ''}
                      onChange={(e) => setSessionDrafts((prev) => ({ ...prev, [row.session_id]: e.target.value }))}
                      className="w-20 rounded border border-slate-300 px-1.5 py-0.5"
                    />
                    <button type="button" onClick={() => saveSession(row.session_id)} className="text-accent-700 font-medium">
                      Set
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

export function CostRoot() {
  const [tab, setTab] = useState<Tab>('overview');
  const activeTab = useMemo(() => {
    switch (tab) {
      case 'overview': return <OverviewTab />;
      case 'pricing': return <PricingTab />;
      case 'infra': return <InfraTab />;
      case 'cache': return <CacheTab />;
      case 'budgets': return <BudgetsTab />;
    }
  }, [tab]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex-none border-b border-slate-200 bg-white px-4">
        <div className="flex gap-1">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
              className={`flex items-center gap-1.5 border-b-2 px-3 py-2.5 text-xs font-medium transition-colors ${
                tab === t.id
                  ? 'border-accent-600 text-accent-700'
                  : 'border-transparent text-ink-500 hover:text-ink-900'
              }`}
            >
              <Icon name={t.icon} size={14} />
              {t.label}
            </button>
          ))}
        </div>
      </div>
      <div className="flex-1 overflow-y-auto p-4">{activeTab}</div>
    </div>
  );
}
