import type { FC } from "react";
import { Icon, type IconName } from "../ui/Icon";
import { BrandMark } from "../ui/BrandMark";

type Mode = "studio" | "eval" | "operator";

const NAV: { id: Mode; label: string; icon: IconName }[] = [
  { id: "studio", label: "Workflow Studio", icon: "topology" },
  { id: "eval", label: "Evaluation Lab", icon: "flask" },
  { id: "operator", label: "Operator Console", icon: "terminal" },
];

const SECONDARY: { label: string; icon: IconName }[] = [
  { label: "Cloud Map", icon: "cloud" },
  { label: "Audit Log", icon: "checklist" },
  { label: "Settings", icon: "settings" },
];

interface Props {
  mode: Mode;
  onModeChange: (m: Mode) => void;
  username?: string | null;
  collapsed: boolean;
  mobileOpen: boolean;
  onCloseMobile: () => void;
  onCollapsedChange: (collapsed: boolean) => void;
}

export const Sidebar: FC<Props> = ({
  mode, onModeChange, username, collapsed, mobileOpen, onCloseMobile, onCollapsedChange,
}) => (
  <>
    {mobileOpen && (
      <button
        type="button"
        className="mobile-backdrop"
        aria-label="Close navigation"
        onClick={onCloseMobile}
      />
    )}
    <aside
      className={`app-sidebar ${collapsed ? 'app-sidebar--collapsed' : ''} ${mobileOpen ? 'app-sidebar--mobile-open' : ''}`}
    >
      <div className="sidebar-brand">
        <BrandMark size="sm" />
        <div className="sidebar-brand-copy">
          <div className="sidebar-product-name">
            Eurskem <span style={{ color: 'var(--brand-teal-400)' }}>AI</span>
          </div>
        </div>
        <button
          type="button"
          className="sidebar-collapse"
          onClick={() => onCollapsedChange(!collapsed)}
          title={collapsed ? 'Expand navigation' : 'Collapse navigation'}
        >
          <Icon name={collapsed ? 'chevron-right' : 'chevron-left'} size={16} />
        </button>
      </div>

      <nav className="sidebar-nav">
        <div className="sidebar-section-label">Workspace</div>
        {NAV.map((item) => {
          const active = mode === item.id;
          return (
            <button
              key={item.id}
              type="button"
              className={`sidebar-nav-item ${active ? 'sidebar-nav-item--active' : ''}`}
              onClick={() => onModeChange(item.id)}
            >
              <Icon name={item.icon} size={16} />
              <span className="sidebar-nav-copy">{item.label}</span>
            </button>
          );
        })}

        <div className="sidebar-section-label">System</div>
        {SECONDARY.map((item) => (
          <button key={item.label} type="button" className="sidebar-nav-item" disabled>
            <Icon name={item.icon} size={16} />
            <span className="sidebar-nav-copy">{item.label}</span>
          </button>
        ))}
      </nav>

      <div className="sidebar-footer">
        <div className="sidebar-avatar">{(username ?? "?").slice(0, 2).toUpperCase()}</div>
        <div className="sidebar-footer-copy">
          <div style={{ fontSize: 11, color: 'var(--text-on-dark)', fontWeight: 500 }}>{username}</div>
          <div style={{ fontSize: 9, color: 'var(--text-on-dark-muted)' }}>Rukainnovation</div>
        </div>
      </div>
    </aside>
  </>
);
