import { useEffect, useState } from "react";
import { api, type ModelComparisonResult, type Scorecard, type WorkflowCompareResponse } from "../../api/client";

const CRITERIA = ["faithfulness", "relevance", "completeness", "citation_accuracy"] as const;
const NAVY = "#0D1B2A";
const GOLD = "#C8A96E";

const CANDIDATE_MODELS: { id: string; label: string }[] = [
  { id: "claude-sonnet-4-5", label: "Claude Sonnet 4.5" },
  { id: "claude-haiku-4-5", label: "Claude Haiku 4.5" },
  { id: "claude-opus-5", label: "Claude Opus 5" },
  { id: "gpt-5-mini", label: "GPT-5 Mini" },
];

function ScoreBar({ value }: { value: number }) {
  const pct = (value / 5) * 100;
  const color = value >= 4 ? "#2e7d32" : value >= 3 ? GOLD : "#c0392b";
  return (
    <div style={{ background: "#eee", borderRadius: 6, height: 8, overflow: "hidden", flex: 1 }}>
      <div style={{ width: `${pct}%`, height: "100%", background: color }} />
    </div>
  );
}

function labelFor(modelId: string): string {
  return CANDIDATE_MODELS.find(m => m.id === modelId)?.label ?? modelId;
}

function ModelComparisonRow({
  result,
  isRecommended,
}: {
  result: ModelComparisonResult;
  isRecommended: boolean;
}) {
  const passPct = Math.round(result.pass_rate * 100);
  const passColor = result.pass_rate === 1 ? "#2e7d32" : result.pass_rate >= 0.5 ? GOLD : "#c0392b";
  return (
    <div style={{ border: `1px solid ${isRecommended ? GOLD : "#e5e5e5"}`, borderRadius: 10, padding: 14 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontWeight: 600, color: NAVY, fontSize: 14 }}>{labelFor(result.model)}</span>
          {isRecommended && (
            <span style={{ fontSize: 10, fontWeight: 700, color: "#fff", background: GOLD, borderRadius: 5, padding: "2px 7px", letterSpacing: 0.3 }}>
              RECOMMENDED
            </span>
          )}
        </div>
        <div style={{ display: "flex", gap: 18, fontSize: 12, color: "#667" }}>
          <span>
            Pass rate <strong style={{ color: passColor }}>{passPct}%</strong> ({result.passed_cases}/{result.total_cases})
          </span>
          <span>
            Avg cost <strong style={{ color: NAVY }}>{result.avg_cost_usd != null ? `$${result.avg_cost_usd.toFixed(4)}` : "—"}</strong>
          </span>
          <span>
            Avg latency <strong style={{ color: NAVY }}>{result.avg_latency_ms != null ? `${Math.round(result.avg_latency_ms)} ms` : "—"}</strong>
          </span>
        </div>
      </div>
      <details style={{ marginTop: 10 }}>
        <summary style={{ cursor: "pointer", fontSize: 12, color: NAVY }}>Per-case detail</summary>
        <div style={{ marginTop: 8, display: "grid", gap: 6 }}>
          {result.cases.map(c => (
            <div key={c.case_id} style={{ border: "1px solid #eee", borderRadius: 6, padding: 8, fontSize: 12 }}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ fontWeight: 600 }}>{c.passed ? "✓" : "✗"} {c.label}</span>
              </div>
              {c.error && <div style={{ color: "#c0392b", marginTop: 4 }}>{c.error}</div>}
              {!c.error && c.checks.filter(chk => !chk.passed).length > 0 && (
                <div style={{ marginTop: 4, color: "#667" }}>
                  {c.checks.filter(chk => !chk.passed).map(chk => (
                    <div key={chk.field}>
                      {chk.field}: expected <code>{JSON.stringify(chk.expected)}</code>, got <code>{JSON.stringify(chk.actual)}</code>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}

function WorkflowModelComparison() {
  const [selected, setSelected] = useState<string[]>(["claude-sonnet-4-5", "claude-haiku-4-5"]);
  const [result, setResult] = useState<WorkflowCompareResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [caseCount, setCaseCount] = useState<number | null>(null);

  useEffect(() => {
    api.workflowGoldenSet("verder_customer_triage").then(r => setCaseCount(r.n)).catch(() => {});
  }, []);

  function toggle(id: string) {
    setSelected(prev => (prev.includes(id) ? prev.filter(m => m !== id) : [...prev, id]));
  }

  async function runComparison() {
    if (selected.length === 0) return;
    setRunning(true); setError(null); setResult(null);
    try {
      const r = await api.workflowCompare("verder_customer_triage", selected);
      setResult(r);
    } catch (e) {
      setError(String(e));
    } finally {
      setRunning(false);
    }
  }

  return (
    <div style={{ marginBottom: 32 }}>
      <h1 style={{ fontSize: 24, fontWeight: 600, color: NAVY }}>Model Comparison — Verder Customer Triage</h1>
      <p style={{ color: "#667", fontSize: 13, marginTop: 4, marginBottom: 20 }}>
        Runs the same {caseCount ?? "4"} real customer messages (standard / technical / complex / ambiguous)
        through the full triage workflow once per model, and checks whether each one reached the right
        business outcome — intent, complexity tier, CRM lookup, and whether it correctly asked a person for
        help. This is a pass/fail check against a known-right answer, not a judged quality score.
      </p>

      <div style={{ display: "flex", gap: 16, alignItems: "flex-end", marginBottom: 16, flexWrap: "wrap" }}>
        <div style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>
          {CANDIDATE_MODELS.map(m => (
            <label key={m.id} style={{ fontSize: 13, display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
              <input type="checkbox" checked={selected.includes(m.id)} onChange={() => toggle(m.id)} />
              {m.label}
            </label>
          ))}
        </div>
        <button onClick={runComparison} disabled={running || selected.length === 0}
          style={{ padding: "9px 18px", background: NAVY, color: "#fff", border: "none",
            borderRadius: 8, fontSize: 13, fontWeight: 500, cursor: "pointer", opacity: running || selected.length === 0 ? 0.6 : 1 }}>
          {running ? "Running…" : "Run comparison"}
        </button>
      </div>

      {error && <div style={{ color: "#c0392b", fontSize: 13, marginBottom: 16 }}>{error}</div>}

      {result && (
        <div style={{ display: "grid", gap: 12 }}>
          {result.recommendation && (
            <div style={{ border: `1px solid ${GOLD}`, background: "#faf6ee", borderRadius: 10, padding: 12, fontSize: 13, color: NAVY }}>
              <strong>{labelFor(result.recommendation.model)}</strong> recommended — {result.recommendation.reason}
            </div>
          )}
          {!result.recommendation && (
            <div style={{ fontSize: 13, color: "#c0392b" }}>No candidate model passed a single golden case.</div>
          )}
          {result.comparisons.map(c => (
            <ModelComparisonRow key={c.model} result={c} isRecommended={result.recommendation?.model === c.model} />
          ))}
        </div>
      )}
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
      <WorkflowModelComparison />
      <hr style={{ border: "none", borderTop: "1px solid #eee", margin: "0 0 32px" }} />

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
