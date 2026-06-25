// ui/src/modes/operator/CorpusInspector.tsx
//
// Read-only RAG inspector for the Operator Console. Two tabs:
//   "Ingested"  -> GET  /inspect/chunks  : what's in Weaviate, grouped by source
//   "Retrieve"  -> POST /inspect/retrieve : what a query actually pulls back
//
// No new dependencies. Uses fetch against the same API base the rest of the UI
// uses. If your project exposes an apiBase()/authHeader() helper, swap the two
// marked lines to use them; otherwise these env-var defaults work.
//
// Mount it in OperatorRoot, e.g. add an "Inspector" tab that renders
// <CorpusInspector />.

import { useState, useEffect } from "react";

// --- API base + auth. Swap these two if you have shared helpers. ------------
const API_BASE = (import.meta as any).env?.VITE_API_BASE ?? "http://localhost:8000";
const token = () => localStorage.getItem("token") ?? "";
const authHeaders = (): Record<string, string> => {
  const t = token();
  return t ? { Authorization: `Bearer ${t}` } : {};
};

// --- Types mirror the FastAPI response models -------------------------------
type ChunkView = {
  chunk_id: string; text: string; token_count: number; chunk_index: number;
  doc_type: string; industry: string; language: string;
  session_id: string; collection_id: string;
};
type SourceGroup = {
  source_path: string; source_format: string; doc_type: string;
  chunk_count: number; total_tokens: number; chunks: ChunkView[];
};
type ChunksResponse = {
  collection_id: string | null; session_id: string | null;
  total_chunks: number; source_count: number; sources: SourceGroup[];
};
type RetrievedView = {
  rank: number; chunk_id: string; text: string; score: number | null;
  doc_type: string; source_path: string; session_id: string; collection_id: string;
};
type RetrieveResponse = {
  query: string; collection_id: string; session_id: string;
  returned: number; results: RetrievedView[];
};

// --- palette (matches Eurskem branding) -------------------------------------
const NAVY = "#0D1B2A";
const GOLD = "#C8A96E";
const BORDER = "#E2E5EA";
const SUBTLE = "#6B7280";

const card: React.CSSProperties = {
  border: `1px solid ${BORDER}`, borderRadius: 10, background: "#fff",
  marginBottom: 12, overflow: "hidden",
};
const chip = (bg: string, fg: string): React.CSSProperties => ({
  display: "inline-block", padding: "1px 8px", borderRadius: 999,
  fontSize: 11, fontWeight: 600, background: bg, color: fg, marginRight: 6,
});
const input: React.CSSProperties = {
  padding: "8px 10px", border: `1px solid ${BORDER}`, borderRadius: 8,
  fontSize: 13, outline: "none",
};
const btn: React.CSSProperties = {
  padding: "8px 16px", borderRadius: 8, border: "none", cursor: "pointer",
  background: NAVY, color: "#fff", fontSize: 13, fontWeight: 600,
};

function fmtToken(n: number) {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`;
}

export function CorpusInspector() {
  const [tab, setTab] = useState<"ingested" | "retrieve">("ingested");
  return (
    <div style={{ padding: 24 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, color: NAVY, margin: 0 }}>
        Corpus Inspector
      </h1>
      <p style={{ color: SUBTLE, marginTop: 4, fontSize: 13 }}>
        See exactly what is in Weaviate and what a query retrieves against it.
      </p>

      <div style={{ display: "flex", gap: 8, margin: "16px 0" }}>
        {(["ingested", "retrieve"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            style={{
              ...btn,
              background: tab === t ? NAVY : "#fff",
              color: tab === t ? "#fff" : NAVY,
              border: `1px solid ${tab === t ? NAVY : BORDER}`,
            }}
          >
            {t === "ingested" ? "Ingested chunks" : "Retrieval preview"}
          </button>
        ))}
      </div>

      {tab === "ingested" ? <IngestedView /> : <RetrieveView />}
    </div>
  );
}

// =========================================================================== //
// View 1: what's ingested                                                     //
// =========================================================================== //
function IngestedView() {
  const [collectionId, setCollectionId] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [data, setData] = useState<ChunksResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [openSrc, setOpenSrc] = useState<string | null>(null);

  async function load() {
    setLoading(true); setErr(null); setData(null);
    try {
      const qs = new URLSearchParams();
      if (collectionId) qs.set("collection_id", collectionId);
      if (sessionId) qs.set("session_id", sessionId);
      const r = await fetch(`${API_BASE}/inspect/chunks?${qs}`, { headers: authHeaders() });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setData(await r.json());
    } catch (e: any) {
      setErr(e.message ?? String(e));
    } finally {
      setLoading(false);
    }
  }

  // Show the whole corpus on first open, no click required.
  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  return (
    <div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 16 }}>
        <label style={{ fontSize: 12, color: SUBTLE }}>collection_id</label>
        <input style={input} value={collectionId} onChange={(e) => setCollectionId(e.target.value)} />
        <label style={{ fontSize: 12, color: SUBTLE }}>session_id</label>
        <input style={input} value={sessionId} onChange={(e) => setSessionId(e.target.value)} />
        <button style={btn} onClick={load} disabled={loading}>
          {loading ? "Loading…" : "Load corpus"}
        </button>
        <span style={{ fontSize: 11, color: SUBTLE }}>
          (leave both blank to see everything)
        </span>
      </div>

      {err && <ErrorBox msg={err} />}

      {data && (
        <>
          <div style={{ display: "flex", gap: 16, marginBottom: 16 }}>
            <Stat label="Sources" value={data.source_count} />
            <Stat label="Total chunks" value={data.total_chunks} />
            <Stat label="Scope" value={`${data.collection_id ?? "all"} / ${data.session_id ?? "all"}`} small />
          </div>

          {data.sources.length === 0 && (
            <p style={{ color: SUBTLE, fontSize: 13 }}>
              No chunks match this scope. Either nothing is ingested under{" "}
              <code>{collectionId || "(any)"}</code> /<code>{sessionId || "(any)"}</code>,
              or the collection_id / session_id don't match what you ingested with.
            </p>
          )}

          {data.sources.map((s) => {
            const isOpen = openSrc === s.source_path;
            return (
              <div key={s.source_path} style={card}>
                <div
                  onClick={() => setOpenSrc(isOpen ? null : s.source_path)}
                  style={{
                    padding: "12px 14px", cursor: "pointer", display: "flex",
                    justifyContent: "space-between", alignItems: "center",
                    background: isOpen ? "#F8F9FB" : "#fff",
                  }}
                >
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontWeight: 600, color: NAVY, fontSize: 13, wordBreak: "break-all" }}>
                      {basename(s.source_path)}
                    </div>
                    <div style={{ marginTop: 4 }}>
                      <span style={chip("#EFE6D2", "#7A5C20")}>{s.doc_type || "—"}</span>
                      <span style={chip("#EEF1F5", NAVY)}>{s.source_format || "—"}</span>
                      <span style={{ fontSize: 11, color: SUBTLE }}>
                        {s.chunk_count} chunks · {fmtToken(s.total_tokens)} tokens
                      </span>
                    </div>
                  </div>
                  <span style={{ color: GOLD, fontSize: 18 }}>{isOpen ? "−" : "+"}</span>
                </div>

                {isOpen && (
                  <div style={{ borderTop: `1px solid ${BORDER}` }}>
                    {s.chunks.map((c) => (
                      <div key={c.chunk_id} style={{ padding: "10px 14px", borderBottom: `1px solid ${BORDER}` }}>
                        <div style={{ fontSize: 11, color: SUBTLE, marginBottom: 4 }}>
                          #{c.chunk_index} · {fmtToken(c.token_count)} tok ·{" "}
                          <span style={chip("#EEF1F5", NAVY)}>{c.collection_id}</span>
                          <span style={chip("#E7F0EA", "#1F5132")}>{c.session_id}</span>
                        </div>
                        <div style={{ fontSize: 13, color: "#1F2937", whiteSpace: "pre-wrap", lineHeight: 1.5 }}>
                          {c.text}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </>
      )}
    </div>
  );
}

// =========================================================================== //
// View 2: retrieval preview                                                   //
// =========================================================================== //
function RetrieveView() {
  const [query, setQuery] = useState("EU biomass supply and demand to 2050 and sustainability");
  const [collectionId, setCollectionId] = useState("biomass_monitoring");
  const [sessionId, setSessionId] = useState("demo-biomass");
  const [docTypes, setDocTypes] = useState("report, template");
  const [topK, setTopK] = useState(10);
  const [data, setData] = useState<RetrieveResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function run() {
    setLoading(true); setErr(null); setData(null);
    try {
      const body = {
        query, collection_id: collectionId, session_id: sessionId,
        doc_types: docTypes.split(",").map((s) => s.trim()).filter(Boolean),
        top_k: topK,
      };
      const r = await fetch(`${API_BASE}/inspect/retrieve`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setData(await r.json());
    } catch (e: any) {
      setErr(e.message ?? String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <div style={{ display: "grid", gap: 10, marginBottom: 16, maxWidth: 720 }}>
        <textarea
          style={{ ...input, minHeight: 56, resize: "vertical", fontFamily: "inherit" }}
          value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Retrieval query…"
        />
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <Field label="collection_id"><input style={input} value={collectionId} onChange={(e) => setCollectionId(e.target.value)} /></Field>
          <Field label="session_id"><input style={input} value={sessionId} onChange={(e) => setSessionId(e.target.value)} /></Field>
          <Field label="doc_types (csv)"><input style={input} value={docTypes} onChange={(e) => setDocTypes(e.target.value)} /></Field>
          <Field label="top_k">
            <input style={{ ...input, width: 64 }} type="number" value={topK}
              onChange={(e) => setTopK(parseInt(e.target.value || "10", 10))} />
          </Field>
        </div>
        <div>
          <button style={btn} onClick={run} disabled={loading}>
            {loading ? "Retrieving…" : "Run retrieval"}
          </button>
        </div>
      </div>

      {err && <ErrorBox msg={err} />}

      {data && (
        <>
          <div style={{ fontSize: 13, color: SUBTLE, marginBottom: 12 }}>
            <strong style={{ color: NAVY }}>{data.returned}</strong> chunks returned for{" "}
            <code>{data.collection_id}</code> / <code>{data.session_id}</code>
            {data.returned === 0 && " — nothing matched; check the scope or that the corpus is ingested."}
          </div>
          {data.results.map((r) => (
            <div key={r.rank} style={card}>
              <div style={{ padding: "10px 14px", display: "flex", gap: 10, alignItems: "baseline" }}>
                <span style={{
                  fontWeight: 700, color: "#fff", background: GOLD, borderRadius: 6,
                  padding: "2px 8px", fontSize: 12,
                }}>#{r.rank}</span>
                {r.score != null && (
                  <span style={chip("#EEF1F5", NAVY)}>score {r.score.toFixed(3)}</span>
                )}
                <span style={chip("#EFE6D2", "#7A5C20")}>{r.doc_type || "—"}</span>
                <span style={{ fontSize: 11, color: SUBTLE, wordBreak: "break-all" }}>
                  {basename(r.source_path)}
                </span>
              </div>
              <div style={{
                padding: "0 14px 12px", fontSize: 13, color: "#1F2937",
                whiteSpace: "pre-wrap", lineHeight: 1.5,
              }}>
                {r.text}
              </div>
            </div>
          ))}
        </>
      )}
    </div>
  );
}

// --- small presentational helpers -------------------------------------------
function Stat({ label, value, small }: { label: string; value: React.ReactNode; small?: boolean }) {
  return (
    <div style={{ border: `1px solid ${BORDER}`, borderRadius: 10, padding: "10px 16px", background: "#fff" }}>
      <div style={{ fontSize: 11, color: SUBTLE, textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: small ? 13 : 22, fontWeight: 700, color: NAVY, marginTop: 2 }}>{value}</div>
    </div>
  );
}
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
      <label style={{ fontSize: 11, color: SUBTLE }}>{label}</label>
      {children}
    </div>
  );
}
function ErrorBox({ msg }: { msg: string }) {
  return (
    <div style={{
      border: "1px solid #F1C4C4", background: "#FDF2F2", color: "#9B1C1C",
      borderRadius: 8, padding: "10px 14px", fontSize: 13, marginBottom: 12,
      whiteSpace: "pre-wrap", wordBreak: "break-word",
    }}>
      {msg}
    </div>
  );
}
function basename(p: string) {
  if (!p) return "(unknown source)";
  const parts = p.split("/");
  return parts[parts.length - 1] || p;
}