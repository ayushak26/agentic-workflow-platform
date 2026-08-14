import { NavLink } from 'react-router-dom';
import type { ReactNode } from 'react';

const items = [
  { to: '/studio',   label: 'Studio',           hint: 'Build & run' },
  { to: '/eval',     label: 'Eval Lab',         hint: 'Grade & compare' },
  { to: '/cost',     label: 'Cost Management',  hint: 'Spend & budgets' },
];

export function ModeShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full">
      <aside className="w-60 border-r border-slate-200 bg-white flex flex-col">
        <div className="px-5 py-5 border-b border-slate-200">
          <div className="text-sm text-ink-500">Agentic Workflow Platform</div>
          <div className="text-lg font-semibold">Alex POC</div>
        </div>
        <nav className="flex-1 p-3 space-y-1">
          {items.map((it) => (
            <NavLink
              key={it.to}
              to={it.to}
              className={({ isActive }) =>
                `block rounded-md px-3 py-2 text-sm ${
                  isActive ? 'bg-accent-600 text-white' : 'text-ink-700 hover:bg-slate-100'
                }`
              }
            >
              <div>{it.label}</div>
              <div className={`text-xs opacity-70`}>{it.hint}</div>
            </NavLink>
          ))}
        </nav>
        <div className="px-5 py-3 border-t border-slate-200 text-xs text-ink-500">
          Local · Phase 9A
        </div>
      </aside>
      <main className="flex-1 overflow-auto">{children}</main>
    </div>
  );
}