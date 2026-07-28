import { type FC } from "react";

type Mode = "studio" | "eval" | "operator";

const NAV = [
  { id: "studio"   as Mode, label: "Workflow Studio",   icon: "ti-topology-star-3" },
  { id: "eval"     as Mode, label: "Evaluation Lab",    icon: "ti-test-pipe"        },
  { id: "operator" as Mode, label: "Operator Console",  icon: "ti-terminal-2"       },
];

const SECONDARY = [
  { label: "Cloud Map",  icon: "ti-cloud"      },
  { label: "Audit Log",  icon: "ti-list-check" },
  { label: "Settings",   icon: "ti-settings"   },
];

interface Props {
  mode: Mode;
  onModeChange: (m: Mode) => void;
  username?: string | null;
}

export const Sidebar: FC<Props> = ({ mode, onModeChange, username }) => (
  <aside style={{
    width: "var(--sidebar-width)",
    background: "var(--eur-navy)",
    height: "100vh",
    position: "fixed",
    left: 0, top: 0,
    display: "flex",
    flexDirection: "column",
    zIndex: 100,
    borderRight: "1px solid rgba(255,255,255,0.05)",
  }}>
    {/* Logo */}
    <div style={{ padding: "22px 20px 18px", borderBottom: "1px solid rgba(255,255,255,0.07)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <div style={{
          width: 32, height: 32,
          background: "var(--eur-gold)",
          borderRadius: 8,
          display: "flex", alignItems: "center", justifyContent: "center",
        }}>
          <i className="ti ti-hexagon-letter-e" style={{ fontSize: 18, color: "var(--eur-navy)" }} aria-hidden />
        </div>
        <div>
          <div style={{ fontSize: 15, fontWeight: 600, color: "#fff", letterSpacing: "-0.3px" }}>
            Eurskem <span style={{ color: "var(--eur-gold)" }}>AI</span>
          </div>
          <div style={{ fontSize: 9, color: "rgba(255,255,255,0.3)", letterSpacing: "1.4px", textTransform: "uppercase" }}>
            by Rukainnovation
          </div>
        </div>
      </div>
    </div>

    {/* Primary nav */}
    <nav style={{ padding: "12px 0", flex: 1 }}>
      <div style={{ padding: "0 12px 6px", fontSize: 9, color: "rgba(255,255,255,0.2)", letterSpacing: "1.2px", textTransform: "uppercase" }}>
        Workspace
      </div>
      {NAV.map(item => {
        const active = mode === item.id;
        return (
          <button
            key={item.id}
            onClick={() => onModeChange(item.id)}
            style={{
              width: "100%", textAlign: "left",
              padding: "9px 16px",
              background: active ? "rgba(200,169,110,0.12)" : "transparent",
              borderLeft: active ? "2px solid var(--eur-gold)" : "2px solid transparent",
              border: "none",
              color: active ? "#fff" : "rgba(255,255,255,0.45)",
              fontSize: 12, fontWeight: active ? 500 : 400,
              cursor: "pointer",
              display: "flex", alignItems: "center", gap: 10,
              transition: "all 0.15s",
            }}
          >
            <i className={`ti ${item.icon}`} style={{ fontSize: 15 }} aria-hidden />
            {item.label}
          </button>
        );
      })}

      <div style={{ padding: "16px 12px 6px", marginTop: 8, fontSize: 9, color: "rgba(255,255,255,0.2)", letterSpacing: "1.2px", textTransform: "uppercase", borderTop: "1px solid rgba(255,255,255,0.06)" }}>
        System
      </div>
      {SECONDARY.map(item => (
        <button key={item.label} style={{
          width: "100%", textAlign: "left",
          padding: "8px 16px",
          background: "transparent", border: "none", borderLeft: "2px solid transparent",
          color: "rgba(255,255,255,0.35)", fontSize: 12, cursor: "pointer",
          display: "flex", alignItems: "center", gap: 10,
        }}>
          <i className={`ti ${item.icon}`} style={{ fontSize: 15 }} aria-hidden />
          {item.label}
        </button>
      ))}
    </nav>

    {/* User footer */}
    <div style={{ padding: "14px 16px", borderTop: "1px solid rgba(255,255,255,0.07)", display: "flex", alignItems: "center", gap: 10 }}>
      <div style={{
        width: 28, height: 28, borderRadius: "50%",
        background: "var(--eur-gold)", color: "var(--eur-navy)",
        display: "flex", alignItems: "center", justifyContent: "center",
        fontSize: 10, fontWeight: 600,
      }}>
        {(username ?? "?").slice(0, 2).toUpperCase()}
      </div>
      <div>
        <div style={{ fontSize: 11, color: "#fff", fontWeight: 500 }}>{username}</div>
        <div style={{ fontSize: 9, color: "rgba(255,255,255,0.3)" }}>Rukainnovation</div>
      </div>
    </div>
  </aside>
);