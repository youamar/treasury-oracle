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
  const [result, setResult] = useState(null);
  const [bank, setBank] = useState("Maybank");
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [dunningTarget, setDunningTarget] = useState(null);

  function appendProofs(newOnes) {
    setProofs((cur) => [...cur, ...newOnes]);
  }

  async function runOCR() {
    if (!proofFiles.length) return;
    setBusy("Vision LLM reading payment proofs...");
    setErr("");
    try {
      const fd = new FormData();
      proofFiles.forEach((f) => fd.append("files", f));
      const r = await fetch(`${API}/extract-proofs`, { method: "POST", body: fd });
      const j = await r.json();
      appendProofs(j.proofs || []);
    } catch (e) { setErr(String(e)); }
    setBusy("");
  }

  async function runParse() {
    if (!stmtFile) return;
    setBusy("Parsing bank statement...");
    setErr("");
    try {
      const fd = new FormData();
      fd.append("file", stmtFile);
      const r = await fetch(`${API}/parse-statement`, { method: "POST", body: fd });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || "parse failed");
      setTxns(j.transactions || []);
    } catch (e) { setErr(String(e)); }
    setBusy("");
  }

  async function runReconcile() {
    if (!proofs.length || !txns.length) return;
    setBusy("Agent reconciling: FX lookups, fee calc, fuzzy matching, SWIFT trace...");
    setErr("");
    try {
      const r = await fetch(`${API}/reconcile`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ proofs, transactions: txns, bank }),
      });
      setResult(await r.json());
    } catch (e) { setErr(String(e)); }
    setBusy("");
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
      alert(`Month-end close: ${r.ingested_proofs} proof(s) ingested. ${r.next_step}`);
    } catch (e) { setErr(String(e)); }
    setBusy("");
  }

  return (
    <div className="min-h-screen">
      <header className="bg-gradient-to-r from-blue-700 via-indigo-700 to-purple-700 text-white px-6 py-5 shadow">
        <h1 className="text-2xl font-bold">🌍 Treasury Oracle</h1>
        <p className="text-blue-100 text-sm">
          Autonomous cross-border reconciliation · Vision OCR · Fuzzy matching · SWIFT tracing · Multilingual dunning
        </p>
      </header>

      <main className="max-w-6xl mx-auto p-6 space-y-6">
        {err && <div className="bg-red-100 text-red-800 p-3 rounded">{err}</div>}

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
                {proofs.map((p, i) => (
                  <li key={i} className="border-b py-1">
                    <span className="font-mono text-xs text-slate-500">{p.source_file}</span>{" "}
                    {p.error ? <Pill color="red">error</Pill> : (
                      <><Pill color="blue">{p.currency}</Pill> {p.amount} · {p.date} · {p.payer}</>
                    )}
                  </li>
                ))}
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
                Parsed <b>{txns.length}</b> inbound transactions.
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
          {busy && <div className="mt-3 text-sm text-blue-700 animate-pulse">{busy}</div>}
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

      {dunningTarget && (
        <DunningModal {...dunningTarget} onClose={() => setDunningTarget(null)} />
      )}

      <footer className="text-center text-xs text-slate-400 py-6">
        AI Marathon 2026 · Track 3 · Treasury Oracle · Powered by Chutes
      </footer>
    </div>
  );
}
