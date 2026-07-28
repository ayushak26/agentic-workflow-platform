import { useEffect, useState } from "react";
import { api, type Scorecard } from "../../api/client";

const CRITERIA = ["faithfulness", "relevance", "completeness", "citation_accuracy"] as const;
const NAVY = "#0D1B2A";
const GOLD = "#C8A96E";

function ScoreBar({ value }: { value: number }) {
  const pct = (value / 5) * 100;
  const color = value >= 4 ? "#2e7d32" : value >= 3 ? GOLD : "#c0392b";
  return (
    <div style={{ background: "#eee", borderRadius: 6, height: 8, overflow: "hidden", flex: 1 }}>
      <div style={{ width: `${pct}%`, height: "100%", background: color }} />
    </div>
  );
}

export function EvalRoot() {
  const [setName, setSetName] = useState("document_qa");
  const [judgeModel, setJudgeModel] = useState("claude-sonnet-4-5");
  const [golden, setGolden] = useState<{
    n: number;
    examples: {
      id: string;
      question: string;
      context: string;
      reference: string;
    }[];
  } | null>(null);
  const [scorecard, setScorecard] = useState<Scorecard | null>(null);
  const [history, setHistory] = useState<Scorecard[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.goldenSet(setName).then(setGolden).catch(e => setError(String(e)));
  }, [setName]);

  useEffect(() => {
    api.evalHistory(20).then(r => setHistory(r.scorecards)).catch(() => {});
  }, []);

  async function runEval() {
    setRunning(true); setError(null); setScorecard(null);
    try {
      const card = await api.runEval(setName, judgeModel);
      setScorecard(card);
      api.evalHistory(20).then(r => setHistory(r.scorecards)).catch(() => {});
    } catch (e) {
      setError(String(e));
    } finally {
      setRunning(false);
    }
  }

  return (
    <div style={{ maxWidth: 1000 }}>
      <h1 style={{ fontSize: 24, fontWeight: 600, color: NAVY }}>Evaluation Lab</h1>
      <p style={{ color: "#667", fontSize: 13, marginTop: 4, marginBottom: 20 }}>
        LLM-as-a-Judge scoring on a golden set. Answers are generated grounded in the
        golden context, so scores reflect <strong>generation quality</strong>, isolated
        from retrieval.
      </p>

      {/* Controls */}
      <div style={{ display: "flex", gap: 12, alignItems: "flex-end", marginBottom: 20, flexWrap: "wrap" }}>
        <label style={{ fontSize: 12 }}>
          <div style={{ marginBottom: 4, color: "#667" }}>Golden set</div>
          <input value={setName} onChange={e => setSetName(e.target.value)}
            style={{ padding: "8px 10px", border: "1px solid #ccc", borderRadius: 8, fontSize: 13 }} />
        </label>
        <label style={{ fontSize: 12 }}>
          <div style={{ marginBottom: 4, color: "#667" }}>Judge model</div>
          <input value={judgeModel} onChange={e => setJudgeModel(e.target.value)}
            style={{ padding: "8px 10px", border: "1px solid #ccc", borderRadius: 8, fontSize: 13, width: 220 }} />
        </label>
        <button onClick={runEval} disabled={running}
          style={{ padding: "9px 18px", background: NAVY, color: "#fff", border: "none",
            borderRadius: 8, fontSize: 13, fontWeight: 500, cursor: "pointer", opacity: running ? 0.6 : 1 }}>
          {running ? "Running…" : "Run Eval"}
        </button>
        {golden && <span style={{ fontSize: 12, color: "#667" }}>{golden.n} examples loaded</span>}
      </div>

      {error && <div style={{ color: "#c0392b", fontSize: 13, marginBottom: 16 }}>{error}</div>}

      {/* Scorecard */}
      {scorecard && (
        <div style={{ border: "1px solid #e5e5e5", borderRadius: 12, padding: 20, marginBottom: 24 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <h2 style={{ fontSize: 18, fontWeight: 600, color: NAVY }}>
              Scorecard · overall {scorecard.overall_mean.toFixed(2)}/5
            </h2>
            <span style={{ fontSize: 11, color: "#667", background: "#f4f1ea", padding: "3px 8px", borderRadius: 6 }}>
              judge: {scorecard.judge_model} · prompt {scorecard.judge_prompt_version} · n={scorecard.n_examples}
            </span>
          </div>
          <div style={{ marginTop: 16, display: "grid", gap: 10 }}>
            {CRITERIA.map(c => (
              <div key={c} style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <div style={{ width: 140, fontSize: 13, textTransform: "capitalize" }}>{c.replace("_", " ")}</div>
                <ScoreBar value={scorecard.per_criterion_mean[c] ?? 0} />
                <div style={{ width: 40, fontSize: 13, textAlign: "right" }}>
                  {(scorecard.per_criterion_mean[c] ?? 0).toFixed(2)}
                </div>
              </div>
            ))}
          </div>

          {/* Per-example detail */}
          <details style={{ marginTop: 16 }}>
            <summary style={{ cursor: "pointer", fontSize: 13, color: NAVY }}>
              Per-example detail ({scorecard.results.length})
            </summary>
            <div style={{ marginTop: 12, display: "grid", gap: 12 }}>
              {scorecard.results.map((r, i) => (
                <div key={i} style={{ border: "1px solid #eee", borderRadius: 8, padding: 12, fontSize: 12 }}>
                  <div style={{ fontWeight: 600, marginBottom: 6 }}>{r.question ?? r.example_id ?? `Example ${i + 1}`}</div>
                  <div style={{ color: "#667", fontStyle: "italic", marginBottom: 6 }}>{r.generated_answer}</div>
                  {r.scores.map((s, k) => (
                    <div key={k} style={{ marginBottom: 4 }}>
                      <span style={{ textTransform: "capitalize", color: NAVY }}>{s.criterion?.replace("_", " ")}: </span>
                      <strong>{s.score}/5</strong> — <span style={{ color: "#667" }}>{s.reasoning}</span>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </details>
        </div>
      )}

      {/* History */}
      <h2 style={{ fontSize: 16, fontWeight: 600, color: NAVY, marginBottom: 10 }}>Eval History</h2>
      {history.length === 0 && <p style={{ fontSize: 13, color: "#667" }}>No past runs.</p>}
      <div style={{ display: "grid", gap: 8 }}>
        {history.map((h, i) => (
          <div key={i} style={{ border: "1px solid #eee", borderRadius: 8, padding: "10px 14px",
            display: "flex", justifyContent: "space-between", fontSize: 13 }}>
            <span>{h.workflow_name} · {h.judge_model} (prompt {h.judge_prompt_version})</span>
            <span style={{ color: NAVY, fontWeight: 600 }}>{h.overall_mean?.toFixed(2)}/5 · n={h.n_examples}</span>
            <span style={{ color: "#999", fontSize: 11 }}>{new Date(h.created_at).toLocaleString()}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
