import { type FC } from "react";

type Mode = "studio" | "eval" | "operator";

interface Props {
  mode: Mode;
  runCostUsd: number;
}

const TITLES: Record<Mode, string> = {
  studio:   "Workflow Studio",
  eval:     "Evaluation Lab",
  operator: "Operator Console",
};

export const Topbar: FC<Props> = ({ mode, runCostUsd }) => (
  <header style={{
    height: 52,
    background: "var(--eur-white)",
    borderBottom: "1px solid var(--eur-border)",
    display: "flex", alignItems: "center",
    padding: "0 20px",
    justifyContent: "space-between",
    position: "sticky", top: 0, zIndex: 50,
  }}>
    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <span style={{ fontSize: 13, fontWeight: 600, color: "var(--eur-text-primary)" }}>
        {TITLES[mode]}
      </span>
      <span style={{
        fontSize: 10, fontWeight: 500,
        background: "var(--eur-gold-pale)", color: "#7A5C1E",
        padding: "2px 8px", borderRadius: 20,
        border: "1px solid var(--eur-gold-light)",
      }}>
        Eurskem
      </span>
    </div>

    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      {/* Live cost badge */}
      <div style={{
        fontSize: 11, fontWeight: 500,
        background: runCostUsd > 0 ? "#FFF7ED" : "#F0FDF4",
        color: runCostUsd > 0 ? "#92400E" : "#166534",
        padding: "4px 10px", borderRadius: 20,
        border: `1px solid ${runCostUsd > 0 ? "#FDE68A" : "#BBF7D0"}`,
        display: "flex", alignItems: "center", gap: 5,
      }}>
        <i className="ti ti-coin" style={{ fontSize: 13 }} aria-hidden />
        ${runCostUsd.toFixed(4)} this run
      </div>

      {/* Notifications */}
      <button style={{
        background: "none", border: "none", cursor: "pointer",
        color: "var(--eur-text-muted)", padding: 4,
      }} aria-label="Notifications">
        <i className="ti ti-bell" style={{ fontSize: 16 }} aria-hidden />
      </button>
    </div>
  </header>
);