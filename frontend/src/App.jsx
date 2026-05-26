import React, { useState } from "react";
import MagneticMatches from "./MagneticMatches.jsx";
import SwiftRoute from "./SwiftRoute.jsx";
import DunningModal from "./DunningModal.jsx";
import BossChart from "./BossChart.jsx";
import Inbox from "./Inbox.jsx";
import VoiceInput from "./VoiceInput.jsx";
import FXPeakAnalyzer from "./FXPeakAnalyzer.jsx";
import FXWatcher from "./FXWatcher.jsx";
import BossDocumentary from "./BossDocumentary.jsx";
import CampaignTracker from "./CampaignTracker.jsx";
import SalesValidator from "./SalesValidator.jsx";
import Settings from "./Settings.jsx";
import MemoryPanel from "./MemoryPanel.jsx";
import EvalPanel from "./EvalPanel.jsx";
import { apiFetch as fetch, pushToast } from "./Toast.jsx";

const API = "/api";

function FileDrop({ label, multiple, onFiles, accept }) {
  return (
    <label className="block border-2 border-dashed border-slate-300 rounded-xl p-5 text-center cursor-pointer hover:border-blue-500 hover:bg-blue-50 transition">
      <div className="text-slate-600 font-medium text-sm">{label}</div>
      <div className="text-[11px] text-slate-400 mt-1">Click to choose {multiple ? "files" : "a file"}</div>
      <input type="file" multiple={multiple} accept={accept} className="hidden"
             onChange={(e) => onFiles([...e.target.files])} />
    </label>
  );
}

function Pill({ children, color = "slate" }) {
  const map = {
    green: "bg-green-100 text-green-800", red: "bg-red-100 text-red-800",
    blue: "bg-blue-100 text-blue-800", slate: "bg-slate-100 text-slate-700",
    amber: "bg-amber-100 text-amber-800", purple: "bg-purple-100 text-purple-800",
  };
  return <span className={`px-2 py-0.5 rounded text-xs font-medium ${map[color]}`}>{children}</span>;
}

export default function App() {
  const [proofFiles, setProofFiles] = useState([]);
  const [stmtFile, setStmtFile] = useState(null);
  const [proofs, setProofs] = useState([]);
  const [txns, setTxns] = useState([]);
  const [parseInfo, setParseInfo] = useState(null);  // {skipped, columns_detected, warnings, row_count}
  const [result, setResult] = useState(null);
  const [bank, setBank] = useState("Maybank");
  const [busy, setBusy] = useState("");
  const [liveTrace, setLiveTrace] = useState([]);  // streaming agent events while busy
  const [dunningTarget, setDunningTarget] = useState(null);
  const [view, setView] = useState("recon"); // "recon" | "settings" | "memory"

  function appendProofs(newOnes) {
    setProofs((cur) => [...cur, ...newOnes]);
  }

  async function runOCR() {
    if (!proofFiles.length) return;
    setBusy("Vision LLM reading payment proofs...");
    try {
      const fd = new FormData();
      proofFiles.forEach((f) => fd.append("files", f));
      const r = await fetch(`${API}/extract-proofs`, { method: "POST", body: fd });
      const j = await r.json();
      appendProofs(j.proofs || []);
      const n = (j.proofs || []).length;
      if (n) pushToast({ kind: "ok", title: "OCR complete",
                         message: `Extracted ${n} proof${n !== 1 ? "s" : ""}.` });
    } catch (e) {
      pushToast({ kind: "error", title: "OCR failed", message: String(e) });
    }
    setBusy("");
  }

  async function runParse() {
    if (!stmtFile) return;
    setBusy("Parsing bank statement...");
    try {
      const fd = new FormData();
      fd.append("file", stmtFile);
      const r = await fetch(`${API}/parse-statement?bank=${encodeURIComponent(bank)}`,
                            { method: "POST", body: fd });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || "parse failed");
      setTxns(j.transactions || []);
      setParseInfo({
        skipped: j.skipped || [],
        columns_detected: j.columns_detected || {},
        warnings: j.warnings || [],
        row_count: j.row_count || 0,
        inbound_count: j.inbound_count || 0,
        outbound_count: j.outbound_count || 0,
        column_drift: j.column_drift || null,
      });
      if (j.column_drift?.severity === "fields_moved") {
        pushToast({
          kind: "warn", title: "Critical column drift",
          message: `Bank statement headers changed since last upload for ${bank}.`,
        });
      } else {
        pushToast({
          kind: "ok", title: "Statement parsed",
          message: `${(j.transactions || []).length} inbound transactions ready.`,
        });
      }
    } catch (e) {
      pushToast({ kind: "error", title: "Parse failed", message: String(e) });
    }
    setBusy("");
  }

  async function runReconcile() {
    if (!proofs.length || !txns.length) return;
    setBusy("Agent starting...");
    setLiveTrace([]);
    // Stable idempotency key for THIS set of inputs — a tab refresh that
    // re-submits the same proofs+txns will hit the cached recon_id instead
    // of spending tokens again.
    const idempKey = "recon-" + await sha256Short(
      JSON.stringify({ bank, proofs, txns })
    );
    // Client-generated session id so we can poll trace while the agent runs.
    const liveSid = "live-" + Math.random().toString(36).slice(2, 10);
    let polling = true;
    (async () => {
      while (polling) {
        await new Promise(r => setTimeout(r, 800));
        if (!polling) break;
        try {
          const tr = await fetch(`${API}/session/${liveSid}/trace`).then(r => r.json());
          setLiveTrace(tr.trace || []);
          if (tr.trace?.length) {
            const last = tr.trace[tr.trace.length - 1];
            setBusy(`Agent step ${last.step} — ${last.type}${
              last.payload?.name ? `: ${last.payload.name}` : ""}`);
          }
        } catch {}
      }
    })();
    try {
      const r = await fetch(`${API}/reconcile`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": idempKey,
        },
        body: JSON.stringify({ proofs, transactions: txns, bank, session_id: liveSid }),
      });
      const j = await r.json();
      setResult(j);
      if (j.idempotent_replay) {
        pushToast({ kind: "ok", title: "Restored from cache",
                    message: "Identical inputs — replayed without re-running the agent." });
      } else {
        const s = j.summary || {};
        pushToast({
          kind: "ok", title: "Reconciliation complete",
          message: `${s.matched || 0} matched · ${s.soft_matches || 0} soft · ${s.unmatched_proofs || 0} discrepancies`,
        });
      }
    } catch (e) {
      pushToast({ kind: "error", title: "Reconcile failed", message: String(e) });
    }
    polling = false;
    setBusy("");
  }

  async function sha256Short(s) {
    const buf = new TextEncoder().encode(s);
    const hash = await crypto.subtle.digest("SHA-256", buf);
    return Array.from(new Uint8Array(hash)).slice(0, 8)
      .map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  async function confirmSoftMatch(softMatch) {
    await fetch(`${API}/soft-match/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        canonical_payer: softMatch.proof.payer || "",
        observed_in_txn: softMatch.txn.description || "",
      }),
    });
    // Promote in UI: move from soft -> matches
    setResult((cur) => ({
      ...cur,
      matches: [...cur.matches, { ...softMatch, status: "matched_via_soft" }],
      soft_matches: cur.soft_matches.filter((s) => s !== softMatch),
      summary: { ...cur.summary,
        matched: cur.summary.matched + 1, soft_matches: cur.summary.soft_matches - 1 },
    }));
  }

  function downloadReport() {
    if (!result?.recon_id) return;
    window.open(`${API}/report/${result.recon_id}`, "_blank");
  }
  function downloadAuditPack(i) {
    if (!result?.recon_id) return;
    window.open(`${API}/audit-pack/${result.recon_id}/${i}`, "_blank");
  }
  async function monthEndClose() {
    setBusy("🌙 Month-end close: ingesting inbox, parsing, reconciling…");
    try {
      const r = await fetch(`${API}/month-end-close`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bank }),
      }).then(r => r.json());
      appendProofs(r.proofs || []);
      pushToast({
        kind: "ok",
        title: "🌙 Month-end close",
        message: `${r.ingested_proofs} proof${r.ingested_proofs !== 1 ? "s" : ""} ingested.`,
        detail: r.next_step,
      });
    } catch (e) {
      pushToast({ kind: "error", title: "Month-end close failed", message: String(e) });
    }
    setBusy("");
  }

  return (
    <div className="min-h-screen">
      <header className="bg-gradient-to-r from-blue-700 via-indigo-700 to-purple-700 text-white px-6 py-5 shadow">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold">🌍 Treasury Oracle <span className="text-blue-200 text-sm font-normal">— Skill Platform</span></h1>
            <p className="text-blue-100 text-sm">
              Composable treasury agent · Vision OCR · Fuzzy matching · SWIFT tracing · Multilingual dunning · Configurable per customer
            </p>
          </div>
          <nav className="flex bg-white/10 rounded-lg p-1 text-sm">
            <button
              onClick={() => setView("recon")}
              className={`px-3 py-1 rounded ${view === "recon" ? "bg-white text-blue-700 font-medium" : "text-blue-100 hover:text-white"}`}
            >Reconcile</button>
            <button
              onClick={() => setView("memory")}
              className={`px-3 py-1 rounded ${view === "memory" ? "bg-white text-blue-700 font-medium" : "text-blue-100 hover:text-white"}`}
            >Memory</button>
            <button
              onClick={() => setView("eval")}
              className={`px-3 py-1 rounded ${view === "eval" ? "bg-white text-blue-700 font-medium" : "text-blue-100 hover:text-white"}`}
            >Eval</button>
            <button
              onClick={() => setView("settings")}
              className={`px-3 py-1 rounded ${view === "settings" ? "bg-white text-blue-700 font-medium" : "text-blue-100 hover:text-white"}`}
            >Settings</button>
          </nav>
        </div>
      </header>
      {view === "settings" && <Settings />}
      {view === "memory" && <main className="max-w-6xl mx-auto p-6"><MemoryPanel /></main>}
      {view === "eval" && <EvalPanel />}
      {(view !== "recon") ? null : (

      <main className="max-w-6xl mx-auto p-6 space-y-6">
        <section className="grid md:grid-cols-2 gap-4">
          <div className="bg-white p-5 rounded-xl shadow">
            <h2 className="font-semibold mb-3">1. Payment Proofs</h2>
            <FileDrop
              label={proofFiles.length ? `${proofFiles.length} file(s) selected` : "Drop payment proof images / PDFs"}
              multiple accept="image/*,.pdf" onFiles={setProofFiles}
            />
            <button disabled={!proofFiles.length || busy} onClick={runOCR}
                    className="mt-3 w-full bg-blue-600 text-white py-2 rounded disabled:opacity-40 hover:bg-blue-700">
              Extract with Vision LLM
            </button>
            {proofs.length > 0 && (
              <ul className="mt-3 text-sm space-y-1 max-h-40 overflow-auto">
                {proofs.map((p, i) => {
                  const q = p.ocr_quality || {};
                  const lowQ = q.gate === "low_quality";
                  return (
                    <li key={i} className="border-b py-1">
                      <span className="font-mono text-xs text-slate-500">{p.source_file}</span>{" "}
                      {p.error ? <Pill color="red">error</Pill> : (
                        <>
                          <Pill color="blue">{p.currency}</Pill> {p.amount} · {p.date} · {p.payer}
                          {lowQ && (
                            <Pill color="amber">⚠ low quality {Math.round((q.completeness || 0) * 100)}%</Pill>
                          )}
                        </>
                      )}
                      {lowQ && q.missing_fields?.length > 0 && (
                        <div className="text-[11px] text-amber-700 ml-1">
                          missing: {q.missing_fields.join(", ")} · will be routed to review
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          <div className="bg-white p-5 rounded-xl shadow">
            <h2 className="font-semibold mb-3">2. Bank Statement</h2>
            <FileDrop
              label={stmtFile ? stmtFile.name : "Drop CSV / XLSX bank statement"}
              accept=".csv,.xlsx,.xls" onFiles={(f) => setStmtFile(f[0])}
            />
            <div className="mt-3 flex gap-2 items-center">
              <label className="text-sm text-slate-600">Bank:</label>
              <select value={bank} onChange={(e) => setBank(e.target.value)}
                      className="border rounded px-2 py-1 text-sm">
                <option>Maybank</option><option>CIMB</option><option>Public Bank</option>
                <option>HSBC</option><option>Wise</option><option>default</option>
              </select>
            </div>
            <button disabled={!stmtFile || busy} onClick={runParse}
                    className="mt-3 w-full bg-blue-600 text-white py-2 rounded disabled:opacity-40 hover:bg-blue-700">
              Parse Statement
            </button>
            {txns.length > 0 && (
              <div className="mt-3 text-sm text-slate-600">
                Parsed <b>{txns.length}</b> inbound transactions
                {parseInfo && parseInfo.row_count > 0 && (
                  <span className="text-slate-500"> of <b>{parseInfo.row_count}</b> rows</span>
                )}
                {parseInfo && parseInfo.outbound_count > 0 && (
                  <span className="text-slate-500"> · {parseInfo.outbound_count} outbound (kept, not matched)</span>
                )}.
                {parseInfo && parseInfo.skipped.length > 0 && (
                  <details className="mt-2 text-xs bg-amber-50 border border-amber-200 rounded p-2">
                    <summary className="cursor-pointer text-amber-800 font-medium">
                      ⚠ {parseInfo.skipped.length} row{parseInfo.skipped.length !== 1 ? "s" : ""} skipped — click to inspect
                    </summary>
                    <ul className="mt-1 ml-3 space-y-0.5 max-h-40 overflow-auto">
                      {parseInfo.skipped.slice(0, 50).map((s, i) => (
                        <li key={i} className="font-mono text-[11px] text-amber-900">
                          row {s.row_index}: {s.reason}
                          {s.values && Object.keys(s.values).length > 0 && (
                            <span className="text-amber-700"> — {JSON.stringify(s.values)}</span>
                          )}
                        </li>
                      ))}
                      {parseInfo.skipped.length > 50 && (
                        <li className="text-amber-700">…and {parseInfo.skipped.length - 50} more</li>
                      )}
                    </ul>
                  </details>
                )}
                {parseInfo && parseInfo.warnings.length > 0 && (
                  <div className="mt-2 text-xs text-amber-700">
                    {parseInfo.warnings.map((w, i) => <div key={i}>⚠ {w}</div>)}
                  </div>
                )}
                {parseInfo?.column_drift?.drift && (
                  <div className={`mt-2 text-xs rounded p-2 border ${
                    parseInfo.column_drift.severity === "fields_moved"
                      ? "bg-red-50 border-red-300 text-red-900"
                      : "bg-amber-50 border-amber-300 text-amber-900"
                  }`}>
                    <div className="font-semibold">
                      {parseInfo.column_drift.severity === "fields_moved"
                        ? "🚨 Critical column drift detected"
                        : "⚠ Column layout changed since last upload"}
                    </div>
                    <div className="mt-1 text-[11px]">
                      Last seen: {parseInfo.column_drift.previous_updated_at || "—"}
                    </div>
                    <ul className="mt-1 ml-3 space-y-0.5 font-mono text-[11px]">
                      {parseInfo.column_drift.changes.map((c, i) => (
                        <li key={i}>
                          <b>{c.field}</b>: <span className="line-through opacity-60">
                            {c.previous_column || "(none)"}
                          </span> → {c.current_column || "(none)"} <i>[{c.kind}]</i>
                        </li>
                      ))}
                    </ul>
                    {parseInfo.column_drift.severity === "fields_moved" && (
                      <div className="mt-1 text-[11px]">
                        A critical field (date/amount) moved. Verify the parse before reconciling.
                      </div>
                    )}
                  </div>
                )}
                {parseInfo && parseInfo.columns_detected && (
                  <details className="mt-2 text-xs">
                    <summary className="cursor-pointer text-slate-500">columns detected</summary>
                    <pre className="mt-1 bg-slate-50 p-2 rounded text-[11px]">{JSON.stringify(parseInfo.columns_detected, null, 2)}</pre>
                  </details>
                )}
              </div>
            )}
          </div>
        </section>

        <section className="grid md:grid-cols-2 gap-4">
          <Inbox onIngest={appendProofs} />
          <VoiceInput onProof={(p) => appendProofs([p])} />
        </section>

        <section className="grid md:grid-cols-2 gap-4">
          <SalesValidator />
          <FXWatcher />
        </section>

        <FXPeakAnalyzer />

        <section className="bg-white p-5 rounded-xl shadow">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <h2 className="font-semibold">3. Reconcile</h2>
            <div className="flex gap-2">
              <button onClick={monthEndClose} disabled={busy}
                      className="bg-slate-900 text-white px-4 py-2 rounded disabled:opacity-40 hover:bg-slate-700">
                🌙 Month-End Close
              </button>
              <button disabled={!proofs.length || !txns.length || busy} onClick={runReconcile}
                      className="bg-indigo-600 text-white px-5 py-2 rounded disabled:opacity-40 hover:bg-indigo-700">
                ⚡ Run Agent
              </button>
            </div>
          </div>
          {busy && (
            <div className="mt-3">
              <div className="text-sm text-blue-700 flex items-center gap-2">
                <span className="inline-block w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
                {busy}
              </div>
              {liveTrace.length > 0 && (
                <div className="mt-2 bg-slate-900 text-slate-100 rounded p-3 max-h-48 overflow-auto font-mono text-[11px] leading-relaxed">
                  {liveTrace.slice(-30).map((t, i) => {
                    const color = t.type === "decision" ? "text-green-300"
                              : t.type === "tool_call" ? "text-cyan-300"
                              : t.type === "tool_result" ? "text-slate-400"
                              : t.type === "error" ? "text-red-300"
                              : t.type === "verifier_downgrade" ? "text-amber-300"
                              : t.type === "verifier_confirm" ? "text-emerald-300"
                              : "text-slate-300";
                    const label = t.type === "tool_call"
                      ? `→ ${t.payload?.name}(${JSON.stringify(t.payload?.arguments || {}).slice(0, 60)})`
                      : t.type === "tool_result"
                      ? `← ${t.payload?.name}: ${JSON.stringify(t.payload?.result || {}).slice(0, 80)}`
                      : t.type === "decision"
                      ? `★ ${t.payload?.decision} (conf ${Math.round((t.payload?.confidence || 0)*100)}%)`
                      : t.type === "verifier_downgrade"
                      ? `⚠ verifier: ${(t.payload?.concerns || []).join("; ")}`
                      : t.type === "verifier_confirm"
                      ? `✓ verifier confirmed`
                      : t.type;
                    return (
                      <div key={`${t.step}-${i}`} className={color}>
                        <span className="text-slate-500">[{t.proof_source || "—"} step {t.step}]</span> {label}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </section>

        {result && (
          <section className="space-y-4">
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              {[
                ["Proofs", result.summary.total_proofs, "slate"],
                ["Txns", result.summary.total_txns, "slate"],
                ["Matched", result.summary.matched, "green"],
                ["Soft matches", result.summary.soft_matches || 0, "purple"],
                ["Discrepancies", result.summary.unmatched_proofs, "red"],
              ].map(([l, v, c]) => (
                <div key={l} className="bg-white p-4 rounded-xl shadow text-center">
                  <div className="text-2xl font-bold"><Pill color={c}>{v}</Pill></div>
                  <div className="text-xs text-slate-500 mt-1">{l}</div>
                </div>
              ))}
            </div>

            <div className="bg-white p-5 rounded-xl shadow">
              <div className="flex items-center justify-between mb-3">
                <h3 className="font-semibold">Reconciliation Results</h3>
                <button onClick={downloadReport}
                        className="bg-emerald-600 text-white px-4 py-1.5 rounded text-sm hover:bg-emerald-700">
                  ⬇ Download PDF Report
                </button>
              </div>

              <MagneticMatches matches={result.matches} />
              {result.matches.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {result.matches.map((m, i) => (
                    <button key={i} onClick={() => downloadAuditPack(i)}
                            className="text-xs px-3 py-1 bg-slate-200 text-slate-700 rounded hover:bg-slate-300">
                      📚 Audit Pack: {m.proof.reference || `match ${i+1}`}
                    </button>
                  ))}
                </div>
              )}

              {/* Soft matches awaiting confirmation */}
              {result.soft_matches?.length > 0 && (
                <div className="mt-6">
                  <h3 className="font-semibold text-purple-700 mb-2">
                    🔮 Soft matches — needs your blessing
                  </h3>
                  <ul className="space-y-2">
                    {result.soft_matches.map((s, i) => (
                      <li key={i} className="bg-purple-50 border border-purple-200 rounded p-3">
                        <div className="flex justify-between gap-3">
                          <div className="flex-1">
                            <b>{s.proof.amount} {s.proof.currency}</b> from {s.proof.payer || "(unknown)"}
                            {" → "}
                            <b>{s.txn.amount} {s.txn.currency}</b> ({s.txn.description?.slice(0, 30)})
                            <div className="text-xs text-purple-700 mt-1">
                              {s.signals.join(" · ")} — {(s.confidence * 100).toFixed(0)}% confident
                            </div>
                          </div>
                          <button onClick={() => confirmSoftMatch(s)}
                                  className="bg-purple-600 text-white px-3 py-1 rounded text-sm hover:bg-purple-700 whitespace-nowrap">
                            👍 Confirm
                          </button>
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Discrepancies with SWIFT trace + dunning + boss chart */}
              {result.unmatched_proofs.length > 0 && (
                <div className="mt-6">
                  <h3 className="font-semibold text-red-700 mb-2">⚠ Discrepancies</h3>
                  <ul className="space-y-3">
                    {result.unmatched_proofs.map((u, i) => (
                      <li key={i} className="bg-red-50 p-3 rounded border border-red-200">
                        <div className="flex justify-between items-start gap-3">
                          <div>
                            <b>{u.source_file}</b> — {u.amount} {u.currency} on {u.date}
                            <div className="text-xs text-red-700 mt-1">{u.reason}</div>
                          </div>
                          <div className="flex flex-col gap-1">
                            <button onClick={() => setDunningTarget({
                              proof: u, expected: u.expected_net, actual: u.actual,
                              localCcy: u.closest_txn?.currency || "MYR",
                            })}
                              className="text-xs px-3 py-1 bg-indigo-100 text-indigo-800 rounded hover:bg-indigo-200">
                              ✍️ Auto-dunning
                            </button>
                            {u.closest_txn && (
                              <BossChart match={{
                                proof: u,
                                txn: u.closest_txn,
                                conversion: { fx_rate: u.fx_rate, actual_received: u.actual },
                              }} />
                            )}
                            {u.closest_txn && (
                              <BossDocumentary match={{
                                proof: u,
                                txn: u.closest_txn,
                                conversion: { fx_rate: u.fx_rate, actual_received: u.actual },
                              }} />
                            )}
                          </div>
                        </div>
                        {u.swift_route && (
                          <div className="mt-3">
                            <SwiftRoute route={u.swift_route} />
                          </div>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="mt-6">
                <CampaignTracker seedProof={result.unmatched_proofs?.[0]} />
              </div>

              <details className="mt-5">
                <summary className="cursor-pointer text-sm text-slate-600">🧠 Agent reasoning trace</summary>
                <pre className="bg-slate-900 text-green-300 text-xs p-3 mt-2 rounded overflow-auto">
{result.trace.join("\n")}
                </pre>
              </details>
            </div>
          </section>
        )}
      </main>
      )}

      {dunningTarget && (
        <DunningModal {...dunningTarget} onClose={() => setDunningTarget(null)} />
      )}

      <footer className="text-center text-xs text-slate-400 py-6">
        AI Marathon 2026 · Track 3 · Treasury Oracle · Powered by Chutes
      </footer>
    </div>
  );
}
