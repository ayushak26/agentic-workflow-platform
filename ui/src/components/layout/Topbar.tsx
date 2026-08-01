import type { FC } from "react";
import { Icon } from "../ui/Icon";

type Mode = "studio" | "eval" | "operator";

interface Props {
  mode: Mode;
  runCostUsd: number;
  onOpenNavigation: () => void;
}

const TITLES: Record<Mode, string> = {
  studio: "Workflow Studio",
  eval: "Evaluation Lab",
  operator: "Operator Console",
};

export const Topbar: FC<Props> = ({ mode, runCostUsd, onOpenNavigation }) => (
  <header className="app-topbar">
    <div className="flex items-center gap-3 min-w-0">
      <button
        type="button"
        className="mobile-menu-button ui-icon-button"
        onClick={onOpenNavigation}
        aria-label="Open navigation"
      >
        <Icon name="menu" size={18} />
      </button>
      <span className="text-sm font-semibold truncate" style={{ color: 'var(--text-primary)' }}>
        {TITLES[mode]}
      </span>
      <span
        className="topbar-brand-badge flex-none rounded-full px-2 py-0.5 text-[10px] font-medium"
        style={{
          background: 'var(--surface-brand-soft)',
          color: 'var(--text-brand)',
          border: '1px solid var(--brand-teal-400)',
        }}
      >
        Eurskem
      </span>
    </div>

    <div className="flex items-center gap-2.5">
      <div
        className="flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium"
        style={{
          background: runCostUsd > 0 ? 'var(--status-warning-soft)' : 'var(--status-success-soft)',
          color: runCostUsd > 0 ? 'var(--status-warning)' : 'var(--status-success)',
          border: `1px solid ${runCostUsd > 0 ? 'var(--status-warning)' : 'var(--status-success)'}`,
        }}
      >
        <Icon name="coin" size={13} />
        ${runCostUsd.toFixed(4)} this run
      </div>

      <button type="button" className="ui-icon-button" aria-label="Notifications">
        <Icon name="bell" size={16} />
      </button>
    </div>
  </header>
);
