import React, { useEffect, useState } from "react";
import { apiFetch as fetch, pushToast } from "./Toast.jsx";
import Page, { Card, Badge, EmptyState } from "./Page.jsx";

const API = "/api/eval";


function Stat({ label, value, delta, suffix = "", invert = false }) {
  // `invert`: for metrics where smaller = better (latency, tokens, brier),
  // green/red is reversed.
  const sign = invert ? -1 : 1;
  const color =
    delta == null ? "text-slate-500" :
    delta === 0 ? "text-slate-500" :
    delta * sign > 0 ? "text-emerald-700" : "text-red-700";
  const arrow = delta == null ? "" : delta > 0 ? "▲" : delta < 0 ? "▼" : "•";
  return (
    <div className="bg-white border border-slate-200 rounded-lg p-3 shadow-sm">
      <div className="text-[11px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className="text-2xl font-semibold mt-1 text-slate-900">{value}{suffix}</div>
      {delta != null && (
        <div className={`text-xs mt-0.5 ${color}`}>
          {arrow} {delta > 0 ? "+" : ""}{delta}{suffix} vs prev
        </div>
      )}
    </div>
  );
}


function ClassRow({ name, m, prev }) {
  const f1Delta = prev ? Number((m.f1 - prev.f1).toFixed(3)) : null;
  const color = f1Delta == null ? "" :
                f1Delta > 0 ? "text-emerald-700" :
                f1Delta < 0 ? "text-red-700" : "text-slate-500";
  return (
    <tr className="border-t border-slate-100">
      <td className="py-2 font-medium">{name}</td>
      <td className="py-2 text-right tabular-nums">{m.precision.toFixed(3)}</td>
      <td className="py-2 text-right tabular-nums">{m.recall.toFixed(3)}</td>
      <td className="py-2 text-right tabular-nums font-semibold">{m.f1.toFixed(3)}</td>
      <td className="py-2 text-right text-xs text-slate-500">{m.support}</td>
      <td className={`py-2 text-right text-xs tabular-nums ${color}`}>
        {f1Delta != null ? `${f1Delta > 0 ? "+" : ""}${f1Delta}` : ""}
      </td>
    </tr>
  );
}


export default function EvalPanel() {
  const [runs, setRuns] = useState([]);
  const [selected, setSelected] = useState(null);
  const [diff, setDiff] = useState(null);
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);

  async function loadRuns() {
    try {
      const r = await fetch(`${API}/runs?limit=20`);
      if (!r.ok) return;
      const j = await r.json();
      setRuns(j.runs || []);
      if ((j.runs || []).length > 0 && !selected) {
        loadRun(j.runs[0].id);
      }
    } catch (e) {
      pushToast({ kind: "error", title: "Failed to load runs",
                  message: String(e?.message || e) });
    }
  }

  async function loadRun(id) {
    try {
      const [r1, r2] = await Promise.all([
        fetch(`${API}/runs/${id}`).then((r) => r.json()),
        fetch(`${API}/diff/${id}`).then((r) => r.json()),
      ]);
      setSelected(r1);
      setDiff(r2);
    } catch (e) {
      pushToast({ kind: "error", title: "Failed to load run",
                  message: String(e?.message || e) });
    }
  }

  useEffect(() => { loadRuns(); }, []);

  async function runNow() {
    setBusy(true);
    try {
      const r = await fetch(`${API}/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label: label || `run-${new Date().toISOString().slice(0, 16)}` }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      setLabel("");
      await loadRuns();
      loadRun(j.run_id);
      pushToast({
        kind: "ok", title: "Eval complete",
        message: `${j.metrics?.n_cases || 0} cases · ${((j.metrics?.overall_accuracy || 0) * 100).toFixed(1)}% accuracy`,
      });
    } catch (e) {
      pushToast({ kind: "error", title: "Eval failed",
                  message: String(e?.message || e) });
    }
    setBusy(false);
  }

  const m = selected?.metrics;
  const dm = diff?.deltas;

  return (
    <Page
      icon="🧪"
      title="Evaluation"
      subtitle="Run the agent against labeled fixtures. Every prompt edit, model swap, or skill toggle is measured against the same cases — diff vs previous tells you whether it helped."
      actions={
        <div className="flex gap-2">
          <input value={label} onChange={(e) => setLabel(e.target.value)}
                 placeholder="optional label"
                 className="border border-slate-300 rounded px-2 py-1 text-sm" />
          <button disabled={busy} onClick={runNow}
                  className="px-4 py-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 text-sm disabled:opacity-50 shadow">
            {busy ? "Running…" : "▶ Run eval"}
          </button>
        </div>
      }
    >
      {/* Run list */}
      <Card title="Past runs"
            subtitle="Eval runs default to temperature=0 so reruns are reproducible.">
        {runs.length === 0 ? (
          <EmptyState icon="🧪" title="No eval runs yet"
                      hint="Click ▶ Run eval above to score the agent against the labeled fixture set."
                      cta="▶ Run eval now"
                      onCtaClick={runNow} />
        ) : (
          <ul className="text-sm space-y-1">
            {runs.map((r) => (
              <li key={r.id} onClick={() => loadRun(r.id)}
                  className={`flex justify-between items-center cursor-pointer py-2 px-2 -mx-2 rounded transition ${
                    selected?.id === r.id
                      ? "bg-indigo-50 border border-indigo-200"
                      : "hover:bg-slate-50"
                  }`}>
                <span>
                  <code className="text-xs bg-slate-100 px-1 py-0.5 rounded">#{r.id}</code>{" "}
                  <span className="font-medium">{r.label || "(no label)"}</span>
                </span>
                <span className="text-xs text-slate-500 text-right">
                  <Badge color={r.metrics.overall_accuracy >= 0.8 ? "green"
                              : r.metrics.overall_accuracy >= 0.6 ? "amber" : "red"}>
                    {(r.metrics.overall_accuracy * 100).toFixed(1)}% acc
                  </Badge>{" "}
                  <span className="text-[11px]">· {r.n_cases} cases · {r.created_at}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {selected && m && (
        <>
          {/* Stat grid — fixed: 4 cols on desktop, properly wraps */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Stat label="Overall accuracy" suffix="%"
                  value={(m.overall_accuracy * 100).toFixed(1)}
                  delta={dm?.overall_accuracy != null
                    ? Number((dm.overall_accuracy * 100).toFixed(1)) : null} />
            <Stat label="Decision accuracy" suffix="%"
                  value={(m.decision_accuracy * 100).toFixed(1)}
                  delta={dm?.decision_accuracy != null
                    ? Number((dm.decision_accuracy * 100).toFixed(1)) : null} />
            <Stat label="Brier score (lower = better)" invert
                  value={m.brier_score != null ? m.brier_score.toFixed(3) : "—"}
                  delta={dm?.brier_score ?? null} />
            <Stat label="Cases" value={m.n_cases} />

            <Stat label="Mean tool calls" invert
                  value={m.mean_tool_calls}
                  delta={dm?.mean_tool_calls ?? null} />
            <Stat label="Mean latency" suffix=" ms" invert
                  value={Math.round(m.mean_latency_ms)}
                  delta={dm?.mean_latency_ms != null
                    ? Math.round(dm.mean_latency_ms) : null} />
            <Stat label="Tokens in" invert
                  value={m.total_tokens_in.toLocaleString()}
                  delta={dm?.total_tokens_in ?? null} />
            <Stat label="Tokens out" invert
                  value={m.total_tokens_out.toLocaleString()}
                  delta={dm?.total_tokens_out ?? null} />
          </div>

          {/* Per-class */}
          <Card title="Per-class metrics">
            <div className="overflow-x-auto -mx-5 px-5">
              <table className="w-full text-sm">
                <thead className="text-xs uppercase text-slate-500">
                  <tr><th className="text-left py-1">Class</th>
                      <th className="text-right">Precision</th>
                      <th className="text-right">Recall</th>
                      <th className="text-right">F1</th>
                      <th className="text-right">Support</th>
                      <th className="text-right">ΔF1</th></tr>
                </thead>
                <tbody>
                  {Object.entries(m.per_class).map(([name, c]) => (
                    <ClassRow key={name} name={name} m={c}
                              prev={diff?.previous?.metrics?.per_class?.[name]} />
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          {/* Calibration buckets */}
          {m.confidence_buckets && m.confidence_buckets.length > 0 && (
            <Card title="Confidence calibration"
                  subtitle="If well-calibrated, accuracy per bucket ≈ mean confidence. Negative gap = overconfident.">
              <div className="overflow-x-auto -mx-5 px-5">
                <table className="w-full text-sm">
                  <thead className="text-xs uppercase text-slate-500">
                    <tr><th className="text-left py-1">Bucket</th>
                        <th className="text-right">n</th>
                        <th className="text-right">Mean conf</th>
                        <th className="text-right">Accuracy</th>
                        <th className="text-right">Gap</th></tr>
                  </thead>
                  <tbody>
                    {m.confidence_buckets.map((b) => {
                      const gap = Number((b.accuracy - b.mean_confidence).toFixed(3));
                      const color = gap < -0.1 ? "text-red-700" :
                                    gap > 0.1 ? "text-amber-700" :
                                    "text-emerald-700";
                      return (
                        <tr key={b.range} className="border-t border-slate-100">
                          <td className="py-2">{b.range}</td>
                          <td className="py-2 text-right">{b.n}</td>
                          <td className="py-2 text-right tabular-nums">{b.mean_confidence.toFixed(3)}</td>
                          <td className="py-2 text-right tabular-nums">{b.accuracy.toFixed(3)}</td>
                          <td className={`py-2 text-right tabular-nums font-medium ${color}`}>
                            {gap >= 0 ? "+" : ""}{gap}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </Card>
          )}

          {/* Cases */}
          <Card title="Cases"
                subtitle={`${selected.cases.filter(c => c.correct).length} of ${selected.cases.length} correct`}>
            <div className="overflow-x-auto -mx-5 px-5">
              <table className="w-full text-sm">
                <thead className="text-xs uppercase text-slate-500">
                  <tr><th className="text-left py-1">Case</th>
                      <th className="text-left">Expected</th>
                      <th className="text-left">Predicted</th>
                      <th className="text-right">Conf</th>
                      <th className="text-right">Tools</th>
                      <th className="text-center">✓</th></tr>
                </thead>
                <tbody>
                  {selected.cases.map((c) => (
                    <tr key={c.id}
                        className={`border-t border-slate-100 ${c.correct ? "" : "bg-red-50"}`}>
                      <td className="py-2 font-mono text-xs">{c.id}</td>
                      <td className="py-2"><Badge color="slate">{c.expected_decision}</Badge></td>
                      <td className="py-2">
                        <Badge color={c.correct ? "green" : "red"}>{c.predicted_decision}</Badge>
                      </td>
                      <td className="py-2 text-right tabular-nums">{c.confidence?.toFixed(2)}</td>
                      <td className="py-2 text-right">{c.tool_call_count}</td>
                      <td className="py-2 text-center">{c.correct ? "✓" : "✗"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          {/* Prompt versions (advanced) */}
          <Card>
            <details>
              <summary className="cursor-pointer text-sm text-slate-500 hover:text-slate-700">
                🔧 Prompt version hashes at run time (advanced)
              </summary>
              <pre className="text-[11px] bg-slate-900 text-slate-100 p-3 rounded overflow-x-auto mt-2">
                {JSON.stringify(selected.prompt_versions, null, 2)}
              </pre>
            </details>
          </Card>
        </>
      )}
    </Page>
  );
}
