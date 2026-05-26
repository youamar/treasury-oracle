import React, { useEffect, useState } from "react";
import { apiFetch as fetch } from "./Toast.jsx";

const API = "/api/eval";

function Stat({ label, value, delta, suffix = "" }) {
  const color =
    delta == null ? "text-slate-500" :
    delta === 0 ? "text-slate-500" :
    delta > 0 ? "text-green-700" : "text-red-700";
  const arrow = delta == null ? "" : delta > 0 ? "▲" : delta < 0 ? "▼" : "•";
  return (
    <div className="bg-white border border-slate-200 rounded-lg p-3">
      <div className="text-[11px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className="text-2xl font-semibold mt-1">{value}{suffix}</div>
      {delta != null && (
        <div className={`text-xs mt-0.5 ${color}`}>{arrow} {delta > 0 ? "+" : ""}{delta}{suffix} vs prev</div>
      )}
    </div>
  );
}

function ClassRow({ name, m, prev }) {
  const f1Delta = prev ? Number((m.f1 - prev.f1).toFixed(3)) : null;
  const color = f1Delta == null ? "" : f1Delta > 0 ? "text-green-700" : f1Delta < 0 ? "text-red-700" : "text-slate-500";
  return (
    <tr className="border-t">
      <td className="py-1 font-medium">{name}</td>
      <td className="py-1 text-right">{m.precision.toFixed(3)}</td>
      <td className="py-1 text-right">{m.recall.toFixed(3)}</td>
      <td className="py-1 text-right">{m.f1.toFixed(3)}</td>
      <td className="py-1 text-right text-xs text-slate-500">{m.support}</td>
      <td className={`py-1 text-right text-xs ${color}`}>{f1Delta != null ? `${f1Delta > 0 ? "+" : ""}${f1Delta}` : ""}</td>
    </tr>
  );
}

export default function EvalPanel() {
  const [runs, setRuns] = useState([]);
  const [selected, setSelected] = useState(null);
  const [diff, setDiff] = useState(null);
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");

  async function loadRuns() {
    const r = await fetch(`${API}/runs?limit=20`);
    if (!r.ok) return;
    const j = await r.json();
    setRuns(j.runs || []);
    if ((j.runs || []).length > 0 && !selected) {
      loadRun(j.runs[0].id);
    }
  }

  async function loadRun(id) {
    setBusy("Loading run…");
    const [r1, r2] = await Promise.all([
      fetch(`${API}/runs/${id}`).then((r) => r.json()),
      fetch(`${API}/diff/${id}`).then((r) => r.json()),
    ]);
    setSelected(r1);
    setDiff(r2);
    setBusy("");
  }

  useEffect(() => { loadRuns(); }, []);

  async function runNow() {
    setBusy("Running eval — this calls the live agent…");
    setErr("");
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
    } catch (e) {
      setErr(String(e));
    }
    setBusy("");
  }

  const m = selected?.metrics;
  const dm = diff?.deltas;

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-6">
      {err && <div className="bg-red-100 text-red-800 p-3 rounded">{err}</div>}

      <section className="bg-white p-5 rounded-xl shadow space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="font-semibold text-lg">Evaluation Harness</h2>
            <p className="text-sm text-slate-600">
              Run the agent against a labeled fixture set. Every change to a
              skill prompt, model, or platform config is measured against
              the same cases. Score, calibrate, diff vs previous.
            </p>
          </div>
          <div className="flex gap-2">
            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="optional label"
              className="border rounded px-2 py-1 text-sm"
            />
            <button
              disabled={!!busy}
              onClick={runNow}
              className="px-4 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-700 text-sm disabled:opacity-50"
            >
              ▶ Run eval
            </button>
          </div>
        </div>
        {busy && <div className="text-xs text-blue-700">{busy}</div>}
      </section>

      <section className="bg-white p-5 rounded-xl shadow">
        <h3 className="font-semibold mb-3">Past runs</h3>
        {runs.length === 0 ? (
          <div className="text-xs text-slate-400">No eval runs yet — click ▶ Run eval above.</div>
        ) : (
          <ul className="text-sm space-y-1">
            {runs.map((r) => (
              <li key={r.id}
                  onClick={() => loadRun(r.id)}
                  className={`flex justify-between cursor-pointer py-1 px-2 rounded ${
                    selected?.id === r.id ? "bg-blue-50" : "hover:bg-slate-50"
                  }`}>
                <span><code className="text-xs">#{r.id}</code> {r.label || "(no label)"}</span>
                <span className="text-xs text-slate-500">
                  acc {(r.metrics.overall_accuracy * 100).toFixed(1)}% · {r.n_cases} cases · {r.created_at}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {selected && m && (
        <>
          <section className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Stat label="Overall accuracy"
                  value={`${(m.overall_accuracy * 100).toFixed(1)}`}
                  suffix="%"
                  delta={dm?.overall_accuracy != null ? Number((dm.overall_accuracy * 100).toFixed(1)) : null} />
            <Stat label="Decision accuracy"
                  value={`${(m.decision_accuracy * 100).toFixed(1)}`}
                  suffix="%"
                  delta={dm?.decision_accuracy != null ? Number((dm.decision_accuracy * 100).toFixed(1)) : null} />
            <Stat label="Mean tool calls"
                  value={m.mean_tool_calls}
                  delta={dm?.mean_tool_calls ?? null} />
            <Stat label="Mean latency"
                  value={Math.round(m.mean_latency_ms)}
                  suffix=" ms"
                  delta={dm?.mean_latency_ms != null ? Math.round(dm.mean_latency_ms) : null} />
            <Stat label="Tokens in"
                  value={m.total_tokens_in.toLocaleString()}
                  delta={dm?.total_tokens_in ?? null} />
            <Stat label="Tokens out"
                  value={m.total_tokens_out.toLocaleString()}
                  delta={dm?.total_tokens_out ?? null} />
            <Stat label="Brier score"
                  value={m.brier_score != null ? m.brier_score.toFixed(3) : "—"}
                  delta={dm?.brier_score ?? null} />
            <Stat label="Cases" value={m.n_cases} />
          </section>

          <section className="bg-white p-5 rounded-xl shadow">
            <h3 className="font-semibold mb-3">Per-class metrics</h3>
            <table className="w-full text-sm">
              <thead className="text-xs uppercase text-slate-500">
                <tr><th className="text-left">Class</th>
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
          </section>

          {m.confidence_buckets && m.confidence_buckets.length > 0 && (
            <section className="bg-white p-5 rounded-xl shadow">
              <h3 className="font-semibold mb-1">Confidence calibration</h3>
              <p className="text-xs text-slate-500 mb-2">
                If calibrated, accuracy per bucket ≈ mean confidence.
                Big gaps = overconfident model.
              </p>
              <table className="w-full text-sm">
                <thead className="text-xs uppercase text-slate-500">
                  <tr><th className="text-left">Bucket</th><th className="text-right">n</th>
                      <th className="text-right">Mean conf</th><th className="text-right">Accuracy</th>
                      <th className="text-right">Gap</th></tr>
                </thead>
                <tbody>
                  {m.confidence_buckets.map((b) => {
                    const gap = (b.accuracy - b.mean_confidence).toFixed(3);
                    return (
                      <tr key={b.range} className="border-t">
                        <td className="py-1">{b.range}</td>
                        <td className="py-1 text-right">{b.n}</td>
                        <td className="py-1 text-right">{b.mean_confidence.toFixed(3)}</td>
                        <td className="py-1 text-right">{b.accuracy.toFixed(3)}</td>
                        <td className={`py-1 text-right ${Number(gap) < -0.1 ? "text-red-700" : Number(gap) > 0.1 ? "text-amber-700" : ""}`}>{gap}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </section>
          )}

          <section className="bg-white p-5 rounded-xl shadow">
            <h3 className="font-semibold mb-3">Cases</h3>
            <table className="w-full text-sm">
              <thead className="text-xs uppercase text-slate-500">
                <tr><th className="text-left">Case</th>
                    <th className="text-left">Expected</th>
                    <th className="text-left">Predicted</th>
                    <th className="text-right">Confidence</th>
                    <th className="text-right">Tools</th>
                    <th className="text-center">✓</th></tr>
              </thead>
              <tbody>
                {selected.cases.map((c) => (
                  <tr key={c.id} className={`border-t ${c.correct ? "" : "bg-red-50"}`}>
                    <td className="py-1 font-mono text-xs">{c.id}</td>
                    <td className="py-1">{c.expected_decision}</td>
                    <td className="py-1">{c.predicted_decision}</td>
                    <td className="py-1 text-right">{c.confidence?.toFixed(2)}</td>
                    <td className="py-1 text-right">{c.tool_call_count}</td>
                    <td className="py-1 text-center">{c.correct ? "✓" : "✗"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section className="bg-white p-5 rounded-xl shadow">
            <h3 className="font-semibold mb-2">Prompt version hashes (at run time)</h3>
            <pre className="text-[11px] bg-slate-900 text-slate-100 p-3 rounded overflow-x-auto">{JSON.stringify(selected.prompt_versions, null, 2)}</pre>
          </section>
        </>
      )}
    </div>
  );
}
