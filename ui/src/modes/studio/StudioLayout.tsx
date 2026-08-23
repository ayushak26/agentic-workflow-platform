import { NavLink, Outlet } from 'react-router-dom';

const tabs = [
  { to: 'chat', label: 'Chat' },
  { to: 'workflows', label: 'Workflows' },
  { to: 'builder', label: 'Builder' },
  { to: 'workflow-runs', label: 'Workflow runs' },
  { to: 'pipelines', label: 'Pipelines' },
  { to: 'proposal-review', label: 'Proposal review' },
];

export function StudioLayout() {
  return (
    <div className="h-full flex flex-col">
      <header className="border-b border-slate-200 bg-white px-6 py-4">
        <h1 className="text-xl font-semibold">Studio</h1>
        <nav className="mt-3 flex gap-1">
          {tabs.map(t => (
            <NavLink
              key={t.to}
              to={t.to}
              className={({ isActive }) =>
                `px-3 py-1.5 text-sm rounded-md ${
                  isActive
                    ? 'bg-accent-600 text-white'
                    : 'text-ink-700 hover:bg-slate-100'
                }`
              }
            >
              {t.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main className="flex-1 overflow-auto">
        <Outlet />
      </main>
    </div>
  );
}
