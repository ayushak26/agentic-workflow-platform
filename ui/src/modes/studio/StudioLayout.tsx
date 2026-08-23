import { Suspense } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';

const tabs = [
  { to: 'chat', label: 'Chat' },
  { to: 'workflows', label: 'Workflows' },
  { to: 'builder', label: 'Builder' },
  { to: 'workflow-runs', label: 'Workflow runs' },
  { to: 'proposal-review', label: 'Proposal review' },
];

export function StudioLayout() {
  const location = useLocation();
  const chatRoute = location.pathname === '/chat' || location.pathname.startsWith('/chat/');
  return (
    <div className={`h-full flex flex-col ${chatRoute ? 'studio-layout--chat' : ''}`}>
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
      <main className={`flex-1 min-h-0 ${chatRoute ? 'overflow-hidden' : 'overflow-auto'}`}>
        <Suspense fallback={<div className="p-8 text-sm text-ink-500" role="status">Loading screen…</div>}>
          <Outlet />
        </Suspense>
      </main>
    </div>
  );
}
