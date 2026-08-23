import { useRef, useState, type FC } from "react";
import { login } from "../../api/client";

interface Props {
  onLogin: (username: string) => void;
  /** Informational banner (e.g. "session expired") shown above the form. */
  notice?: string;
}

export const LoginPage: FC<Props> = ({ onLogin, notice }) => {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError]       = useState("");
  const [loading, setLoading]   = useState(false);
  // A ref lock, not just `loading` state: state updates are async, so two
  // keydown events in the same tick (Enter key-repeat) could both still see
  // stale `loading === false` before React re-renders.
  const submittingRef = useRef(false);

  const handleLogin = async () => {
    if (submittingRef.current) return;
    submittingRef.current = true;
    setLoading(true); setError("");
    try {
      const result = await login(username, password);
      onLogin(result.username);
    } catch {
      setError("Invalid credentials or cannot reach server");
    } finally {
      setLoading(false);
      submittingRef.current = false;
    }
  };


  return (
    <div style={{
      minHeight: "100vh",
      background: "var(--eur-navy)",
      display: "flex", alignItems: "center", justifyContent: "center",
    }}>
      <div style={{
        background: "var(--eur-white)",
        borderRadius: 14, padding: "40px 36px",
        width: 380,
        border: "1px solid var(--eur-border)",
      }}>
        {/* Brand */}
        <div style={{ textAlign: "center", marginBottom: 32 }}>
          <div style={{
            width: 48, height: 48, background: "var(--eur-navy)",
            borderRadius: 12, display: "flex", alignItems: "center",
            justifyContent: "center", margin: "0 auto 14px",
          }}>
            <i className="ti ti-hexagon-letter-e" style={{ fontSize: 26, color: "var(--eur-gold)" }} aria-hidden />
          </div>
          <div style={{ fontSize: 22, fontWeight: 600, color: "var(--eur-navy)", letterSpacing: "-0.5px" }}>
            Eurskem <span style={{ color: "var(--eur-gold)" }}>AI</span>
          </div>
          <div style={{ fontSize: 10, color: "var(--eur-text-muted)", letterSpacing: "1.4px", textTransform: "uppercase", marginTop: 4 }}>
            Agentic Workflow Platform · Rukainnovation
          </div>
        </div>

        {/* Fields */}
        {notice && (
          <div role="status" style={{
            fontSize: 12, color: "var(--eur-navy)", background: "#f2f5f8",
            border: "1px solid var(--eur-border-mid)", borderRadius: 8,
            padding: "8px 12px", marginBottom: 14, textAlign: "center",
          }}>
            {notice}
          </div>
        )}
        <div style={{ marginBottom: 12 }}>
          <label htmlFor="login-username" style={{ fontSize: 11, fontWeight: 500, color: "var(--eur-text-secondary)", display: "block", marginBottom: 5 }}>
            Username
          </label>
          <input
            id="login-username"
            name="username"
            autoComplete="username"
            value={username}
            onChange={e => setUsername(e.target.value)}
            onKeyDown={e => e.key === "Enter" && handleLogin()}
            style={{ width: "100%", padding: "9px 12px", border: "1px solid var(--eur-border-mid)", borderRadius: 8, fontSize: 13, outline: "none" }}
          />
        </div>
        <div style={{ marginBottom: 20 }}>
          <label htmlFor="login-password" style={{ fontSize: 11, fontWeight: 500, color: "var(--eur-text-secondary)", display: "block", marginBottom: 5 }}>
            Password
          </label>
          <input
            id="login-password"
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            onKeyDown={e => e.key === "Enter" && handleLogin()}
            style={{ width: "100%", padding: "9px 12px", border: "1px solid var(--eur-border-mid)", borderRadius: 8, fontSize: 13, outline: "none" }}
          />
        </div>

        {error && (
          <div style={{ fontSize: 12, color: "var(--eur-red)", marginBottom: 12, textAlign: "center" }}>
            {error}
          </div>
        )}

        <button
          onClick={handleLogin}
          disabled={loading}
          style={{
            width: "100%", padding: "10px",
            background: "var(--eur-navy)", color: "#fff",
            border: "none", borderRadius: 8, fontSize: 13, fontWeight: 500,
            cursor: "pointer", marginBottom: 10,
            opacity: loading ? 0.7 : 1,
          }}
        >
          {loading ? "Signing in..." : "Sign in"}
        </button>

        <button style={{
          width: "100%", padding: "10px",
          background: "#fff", color: "var(--eur-text-primary)",
          border: "1px solid var(--eur-border-mid)", borderRadius: 8,
          fontSize: 13, cursor: "pointer",
          display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
        }}>
          <i className="ti ti-brand-windows" style={{ fontSize: 15, color: "#0078D4" }} aria-hidden />
          Sign in with Microsoft
        </button>

        <p style={{ textAlign: "center", fontSize: 10, color: "var(--eur-text-muted)", marginTop: 14 }}>
          Microsoft SSO requires Azure AD configuration
        </p>
      </div>
    </div>
  );
};