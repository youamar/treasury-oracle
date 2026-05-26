import React, { useEffect, useState } from "react";
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
import { SAMPLE_PROOFS, SAMPLE_TXNS, SAMPLE_PARSE_INFO } from "./sampleData.js";
import Account, { getTenant, getEmail, isOnboarded, signOut, onAccountChange } from "./Account.jsx";
import Onboarding from "./Onboarding.jsx";

const API = "/api";

function FileDrop({ label, multiple, onFiles, accept }) {
  return (
    <label className="block border-2 border-dashed border-slate-300 dark:border-slate-700 rounded-xl p-5 text-center cursor-pointer hover:border-blue-500 hover:bg-blue-50 dark:hover:bg-blue-950/30 transition">
      <div className="text-slate-600 dark:text-slate-300 font-medium text-sm">{label}</div>
      <div className="text-[11px] text-slate-400 dark:text-slate-500 mt-1">Click to choose {multiple ? "files" : "a file"}</div>
      <input type="file" multiple={multiple} accept={accept} className="hidden"
             onChange={(e) => onFiles([...e.target.files])} />
    </label>
  );
}

function Pill({ children, color = "slate" }) {
  const map = {
    green: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-200",
    red:   "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200",
    blue:  "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-200",
    slate: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200",
    amber: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200",
    purple:"bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-200",
  };
  return <span className={`px-2 py-0.5 rounded text-xs font-medium ${map[color]}`}>{children}</span>;
}

/** Small avatar + email + dropdown for sign-out in the header. */
function AccountBadge() {
  const [open, setOpen] = useState(false);
  const email = getEmail() || "";
  const tenant = getTenant() || "";
  const initial = email ? email[0].toUpperCase() : "?";
  return (
    <div className="relative">
      <button onClick={() => setOpen((o) => !o)}
              className="bg-white/10 hover:bg-white/20 rounded-lg px-2 py-1 flex items-center gap-2 transition">
        <span className="bg-white text-indigo-700 w-7 h-7 rounded-full flex items-center justify-center font-bold text-sm">
          {initial}
        </span>
        <span className="hidden sm:inline text-sm text-blue-100">{email}</span>
        <span className="text-xs text-blue-200">▼</span>
      </button>
      {open && (
        <div className="absolute right-0 mt-2 bg-white text-slate-900 rounded-lg shadow-xl min-w-[220px] py-1 text-sm z-50">
          <div className="px-3 py-2 border-b border-slate-100">
            <div className="font-medium truncate">{email}</div>
            <div className="text-[11px] text-slate-500 font-mono truncate">tenant: {tenant}</div>
          </div>
          <button onClick={() => { setOpen(false); signOut(); }}
            className="w-full text-left px-3 py-2 hover:bg-slate-100 text-red-600">
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}


/** 3-step progress stepper showing where the user is in the flow. */
function Stepper({ proofsCount, txnsCount, hasResult }) {
  const steps = [
    { n: 1, label: "Payment Proofs", done: proofsCount > 0, count: proofsCount },
    { n: 2, label: "Bank Statement", done: txnsCount > 0, count: txnsCount },
    { n: 3, label: "Reconcile", done: hasResult, active: proofsCount > 0 && txnsCount > 0 && !hasResult },
  ];
  return (
    <div className="flex items-center gap-2 sm:gap-4 mb-4">
      {steps.map((s, i) => (
        <React.Fragment key={s.n}>
          <div className="flex items-center gap-2">
            <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold border-2 ${
              s.done ? "bg-emerald-500 border-emerald-500 text-white"
              : s.active ? "bg-indigo-500 border-indigo-500 text-white animate-pulse"
              : "bg-white dark:bg-slate-800 border-slate-300 dark:border-slate-600 text-slate-500 dark:text-slate-400"
            }`}>
              {s.done ? "✓" : s.n}
            </div>
            <div className="hidden sm:block">
              <div className={`text-xs font-medium ${
                s.done ? "text-emerald-700 dark:text-emerald-300"
                : s.active ? "text-indigo-700 dark:text-indigo-300"
                : "text-slate-500 dark:text-slate-400"
              }`}>{s.label}</div>
              {s.count !== undefined && (
                <div className="text-[10px] text-slate-400 dark:text-slate-500">{s.count} loaded</div>
              )}
            </div>
          </div>
          {i < steps.length - 1 && (
            <div className={`flex-1 h-0.5 ${s.done ? "bg-emerald-400" : "bg-slate-200 dark:bg-slate-700"}`} />
          )}
        </React.Fragment>
      ))}
    </div>
  );
}

/** Top-level gate: not signed in → Account screen; signed in but not
 *  onboarded → Onboarding wizard; otherwise → the existing workspace. */
export default function App() {
  const [tenant, setTenant] = useState(getTenant());
  const [onboarded, setOnboarded] = useState(isOnboarded());

  useEffect(() => {
    return onAccountChange(() => {
      setTenant(getTenant());
      setOnboarded(isOnboarded());
    });
  }, []);

  if (!tenant) return <Account onAuthed={setTenant} />;
  if (!onboarded) return <Onboarding onDone={() => setOnboarded(true)} />;
  return <Workspace />;
}


function Workspace() {
  const [proofFiles, setProofFiles] = useState([]);
  const [stmtFile, setStmtFile] = useState(null);
  const [proofs, setProofs] = useState([]);
  const [txns, setTxns] = useState([]);
  const [parseInfo, setParseInfo] = useState(null);  // {skipped, columns_detected, warnings, row_count}
  const [result, setResult] = useState(null);
  const [bank, setBank] = useState("Maybank");
  const [busy, setBusy] = useState("");
  const [liveTrace, setLiveTrace] = useState([]);  // streaming agent events while busy
  const [toolsOpen, setToolsOpen] = useState(false);
  const [narrative, setNarrative] = useState(null);
  const [narrativeBusy, setNarrativeBusy] = useState(false);
  const [dunningTarget, setDunningTarget] = useState(null);
  const [view, setView] = useState("recon"); // "recon" | "settings" | "memory"

  function appendProofs(newOnes) {
    setProofs((cur) => [...cur, ...newOnes]);
  }

  function loadSampleData() {
    setProofs(SAMPLE_PROOFS);
    setTxns(SAMPLE_TXNS);
    setParseInfo(SAMPLE_PARSE_INFO);
    setBank("Maybank");
    setResult(null);
    pushToast({
      kind: "ok",
      title: "Sample data loaded",
      message: `${SAMPLE_PROOFS.length} proofs · ${SAMPLE_TXNS.length} bank rows. Hit Run Agent.`,
    });
  }

  function clearAll() {
    setProofs([]); setTxns([]); setParseInfo(null); setResult(null);
    setProofFiles([]); setStmtFile(null);
    pushToast({ kind: "ok", title: "Cleared", message: "Workspace reset." });
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
      // Kick off the narrative in the background; UI shows a skeleton.
      if (j.recon_id) loadNarrative(j.recon_id, false);
    } catch (e) {
      pushToast({ kind: "error", title: "Reconcile failed", message: String(e) });
    }
    polling = false;
    setBusy("");
  }

  async function loadNarrative(reconId, refresh) {
    if (!reconId) return;
    setNarrativeBusy(true);
    if (refresh) setNarrative(null);
    try {
      const r = await fetch(`${API}/session/${reconId}/narrative${refresh ? "?refresh=true" : ""}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      setNarrative(j);
    } catch (e) {
      pushToast({ kind: "warn", title: "Narrative unavailable",
                  message: String(e?.message || e) });
    }
    setNarrativeBusy(false);
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
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="min-w-0">
            <h1 className="text-2xl font-bold">🌍 Treasury Oracle</h1>
            <p className="text-blue-100 text-sm">
              Build your own treasury AI agent — Vision OCR · Tool-calling reconciliation · Provenance-tracked decisions · Multilingual dunning
            </p>
          </div>
          <div className="flex items-center gap-3">
            <nav className="flex bg-white/10 rounded-lg p-1 text-sm">
              {[["recon","Reconcile"],["memory","Memory"],["eval","Eval"],["settings","Settings"]].map(([k,l]) => (
                <button key={k} onClick={() => setView(k)}
                  className={`px-3 py-1 rounded transition ${view === k ? "bg-white text-blue-700 font-medium" : "text-blue-100 hover:text-white hover:bg-white/10"}`}
                >{l}</button>
              ))}
            </nav>
            <AccountBadge />
          </div>
        </div>
      </header>
      {view === "settings" && <Settings />}
      {view === "memory" && <MemoryPanel />}
      {view === "eval" && <EvalPanel />}
      {(view !== "recon") ? null : (

      <main className="max-w-6xl mx-auto p-6 space-y-6">
        <Stepper proofsCount={proofs.length} txnsCount={txns.length} hasResult={!!result} />

        {/* First-run helper banner — only shown when workspace is empty */}
        {proofs.length === 0 && txns.length === 0 && (
          <div className="bg-gradient-to-r from-indigo-50 to-purple-50 dark:from-indigo-950/40 dark:to-purple-950/40 border border-indigo-200 dark:border-indigo-800 rounded-xl p-4 flex items-center justify-between gap-4 flex-wrap">
            <div>
              <div className="font-semibold text-indigo-900 dark:text-indigo-200">👋 First time here?</div>
              <div className="text-sm text-indigo-700 dark:text-indigo-300">
                Click <b>Try sample data</b> to skip the upload steps and see the agent in action with 8 pre-built payment proofs + a sample bank statement.
              </div>
            </div>
            <button onClick={loadSampleData}
                    className="bg-indigo-600 text-white px-4 py-2 rounded-lg hover:bg-indigo-700 font-medium whitespace-nowrap shadow">
              🎬 Try sample data
            </button>
          </div>
        )}
        {(proofs.length > 0 || txns.length > 0) && (
          <div className="flex items-center justify-end gap-2 text-sm">
            <button onClick={loadSampleData}
                    className="text-indigo-700 dark:text-indigo-300 hover:text-indigo-900 dark:hover:text-indigo-100 underline">
              reload sample data
            </button>
            <span className="text-slate-400 dark:text-slate-600">·</span>
            <button onClick={clearAll}
                    className="text-slate-500 dark:text-slate-400 hover:text-red-600 dark:hover:text-red-400 underline">
              clear workspace
            </button>
          </div>
        )}

        <section className="grid md:grid-cols-2 gap-4">
          <div className="bg-white dark:bg-slate-900 dark:border dark:border-slate-800 p-5 rounded-xl shadow dark:shadow-slate-950/50">
            <h2 className="font-semibold mb-3 text-slate-900 dark:text-slate-100">1. Payment Proofs</h2>
            <FileDrop
              label={proofFiles.length ? `${proofFiles.length} file(s) selected` : "Drop payment proof images / PDFs"}
              multiple accept="image/*,.pdf" onFiles={setProofFiles}
            />
            <button disabled={!proofFiles.length || busy} onClick={runOCR}
                    className="mt-3 w-full bg-blue-600 text-white py-2 rounded disabled:opacity-40 hover:bg-blue-700">
              Extract with Vision LLM
            </button>
            {proofs.length > 0 && (
              <ul className="mt-3 text-sm space-y-1 max-h-40 overflow-auto text-slate-700 dark:text-slate-300">
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

          <div className="bg-white dark:bg-slate-900 dark:border dark:border-slate-800 p-5 rounded-xl shadow dark:shadow-slate-950/50">
            <h2 className="font-semibold mb-3 text-slate-900 dark:text-slate-100">2. Bank Statement</h2>
            <FileDrop
              label={stmtFile ? stmtFile.name : "Drop CSV / XLSX bank statement"}
              accept=".csv,.xlsx,.xls" onFiles={(f) => setStmtFile(f[0])}
            />
            <details className="mt-2 text-xs text-slate-600">
              <summary className="cursor-pointer hover:text-slate-900">
                what should the file look like?
              </summary>
              <div className="mt-2 bg-slate-50 border border-slate-200 rounded p-2">
                <div className="text-[11px] mb-1">Headers we recognize (any of):</div>
                <ul className="ml-4 list-disc text-[11px] space-y-0.5">
                  <li><b>Date</b> · Transaction Date · Posting Date · Value Date</li>
                  <li><b>Amount</b> · Credit · Credit Amount · Deposit</li>
                  <li>Currency · CCY <span className="text-slate-400">(optional, defaults to MYR)</span></li>
                  <li>Description · Narrative · Memo <span className="text-slate-400">(optional)</span></li>
                  <li>Reference · Ref · Txn ID <span className="text-slate-400">(optional)</span></li>
                </ul>
                <div className="text-[11px] mt-2">Sample CSV:</div>
                <pre className="mt-1 bg-white border border-slate-200 rounded p-1 text-[10px] overflow-x-auto">
{`Date,Amount,Currency,Description,Reference
2026-05-20,4700.33,MYR,INWARD TT ACME CORP,INV-2026-001
2026-05-21,4301.03,MYR,INWARD TT BERLIN DESIGNS,INV-2026-002`}
                </pre>
                <button onClick={loadSampleData}
                        className="mt-2 text-indigo-600 hover:text-indigo-800 underline text-[11px]">
                  Don't have a file? Load the sample data instead →
                </button>
              </div>
            </details>
            <div className="mt-3 flex gap-2 items-center">
              <label className="text-sm text-slate-600 dark:text-slate-400">Bank:</label>
              <select value={bank} onChange={(e) => setBank(e.target.value)}
                      className="border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 dark:text-slate-100 rounded px-2 py-1 text-sm">
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

        {/* Secondary tools — collapsed by default so the main flow isn't cluttered */}
        <section className="bg-white dark:bg-slate-900 dark:border dark:border-slate-800 rounded-xl shadow">
          <button onClick={() => setToolsOpen(o => !o)}
                  className="w-full px-5 py-3 flex items-center justify-between hover:bg-slate-50 dark:hover:bg-slate-800/50 rounded-xl transition">
            <div className="flex items-center gap-3">
              <span className="text-lg">🧰</span>
              <div>
                <div className="font-semibold text-left">Tools</div>
                <div className="text-xs text-slate-500 dark:text-slate-400 text-left">
                  Live inbox · Voice ingest · Sales validator · FX watcher · FX peak analyzer
                </div>
              </div>
            </div>
            <span className={`text-slate-400 transition-transform ${toolsOpen ? "rotate-180" : ""}`}>▼</span>
          </button>
          {toolsOpen && (
            <div className="px-5 pb-5 space-y-4 border-t border-slate-200 dark:border-slate-800">
              <div className="grid md:grid-cols-2 gap-4 pt-4">
                <Inbox onIngest={appendProofs} />
                <VoiceInput onProof={(p) => appendProofs([p])} />
              </div>
              <div className="grid md:grid-cols-2 gap-4">
                <SalesValidator />
                <FXWatcher />
              </div>
              <FXPeakAnalyzer />
            </div>
          )}
        </section>

        <section className="bg-white dark:bg-slate-900 dark:border dark:border-slate-800 p-5 rounded-xl shadow dark:shadow-slate-950/50">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <h2 className="font-semibold">3. Reconcile</h2>
            <div className="flex gap-2 items-center">
              <button onClick={monthEndClose} disabled={busy}
                      className="bg-slate-900 text-white px-4 py-2 rounded disabled:opacity-40 hover:bg-slate-700">
                🌙 Month-End Close
              </button>
              <button disabled={!proofs.length || !txns.length || busy} onClick={runReconcile}
                      title={
                        !proofs.length && !txns.length ? "Load proofs and a bank statement first (or click Try sample data)" :
                        !proofs.length ? "Extract payment proofs first" :
                        !txns.length ? "Parse a bank statement first" :
                        busy ? "Running…" : "Run the agent"
                      }
                      className="bg-indigo-600 text-white px-5 py-2 rounded disabled:opacity-40 hover:bg-indigo-700">
                ⚡ Run Agent
              </button>
            </div>
          </div>
          {/* Inline checklist — replaces the silent disabled state */}
          {(!proofs.length || !txns.length) && !busy && (
            <div className="mt-3 text-sm bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-900 rounded p-2">
              <div className="font-medium text-amber-900 dark:text-amber-200 mb-1">
                Before you can run the agent:
              </div>
              <ul className="text-xs space-y-0.5">
                <li className={proofs.length ? "text-emerald-700 dark:text-emerald-300" : "text-amber-800 dark:text-amber-300"}>
                  {proofs.length ? "✓" : "○"} Payment proofs ({proofs.length} loaded)
                </li>
                <li className={txns.length ? "text-emerald-700 dark:text-emerald-300" : "text-amber-800 dark:text-amber-300"}>
                  {txns.length ? "✓" : "○"} Bank statement transactions ({txns.length} loaded)
                </li>
              </ul>
              <button onClick={loadSampleData}
                      className="mt-2 text-xs text-indigo-700 dark:text-indigo-300 hover:text-indigo-900 dark:hover:text-indigo-100 underline">
                or skip the upload steps →
              </button>
            </div>
          )}
          {busy && (
            <div className="mt-3">
              <div className="text-sm text-blue-700 dark:text-blue-300 flex items-center gap-2">
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
            {/* AI narrative — what just happened, in plain English */}
            {(narrative || narrativeBusy) && (
              <div className="bg-gradient-to-br from-indigo-50 to-purple-50 border border-indigo-200 rounded-xl p-5">
                <div className="flex items-start justify-between gap-3 mb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-xl">📝</span>
                    <div>
                      <div className="text-[10px] uppercase tracking-wider text-indigo-600 font-bold">
                        AI summary
                      </div>
                      {narrative?.headline && (
                        <div className="font-semibold text-slate-900">{narrative.headline}</div>
                      )}
                    </div>
                  </div>
                  <button onClick={() => loadNarrative(result?.recon_id, true)}
                          disabled={narrativeBusy}
                          className="text-xs text-indigo-700 hover:text-indigo-900 underline disabled:opacity-50">
                    {narrativeBusy ? "regenerating…" : "regenerate"}
                  </button>
                </div>
                {narrativeBusy && !narrative ? (
                  <div className="space-y-2 mt-2">
                    <div className="h-3 bg-indigo-100 rounded animate-pulse w-3/4" />
                    <div className="h-3 bg-indigo-100 rounded animate-pulse w-full" />
                    <div className="h-3 bg-indigo-100 rounded animate-pulse w-5/6" />
                  </div>
                ) : narrative && (
                  <>
                    <div className="space-y-2 text-sm text-slate-800">
                      {(narrative.paragraphs || []).map((p, i) => <p key={i}>{p}</p>)}
                    </div>
                    {(narrative.action_items || []).length > 0 && (
                      <div className="mt-3 pt-3 border-t border-indigo-200">
                        <div className="text-[11px] uppercase tracking-wider text-indigo-700 font-bold mb-1">
                          Next steps
                        </div>
                        <ul className="text-sm space-y-1">
                          {narrative.action_items.map((a, i) => (
                            <li key={i} className="text-slate-700">→ {a}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {narrative.generated === "fallback" && (
                      <div className="mt-2 text-[10px] text-amber-700">
                        ⚠ LLM unavailable — using deterministic template
                      </div>
                    )}
                  </>
                )}
              </div>
            )}

            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              {[
                ["Proofs", result.summary.total_proofs, "slate"],
                ["Txns", result.summary.total_txns, "slate"],
                ["Matched", result.summary.matched, "green"],
                ["Soft matches", result.summary.soft_matches || 0, "purple"],
                ["Discrepancies", result.summary.unmatched_proofs, "red"],
              ].map(([l, v, c]) => (
                <div key={l} className="bg-white dark:bg-slate-900 dark:border dark:border-slate-800 p-4 rounded-xl shadow dark:shadow-slate-950/50 text-center">
                  <div className="text-2xl font-bold"><Pill color={c}>{v}</Pill></div>
                  <div className="text-xs text-slate-500 dark:text-slate-400 mt-1">{l}</div>
                </div>
              ))}
            </div>

            <div className="bg-white dark:bg-slate-900 dark:border dark:border-slate-800 p-5 rounded-xl shadow dark:shadow-slate-950/50">
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
                            className="text-xs px-3 py-1 bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-200 rounded hover:bg-slate-300 dark:hover:bg-slate-700">
                      📚 Audit Pack: {m.proof.reference || `match ${i+1}`}
                    </button>
                  ))}
                </div>
              )}

              {/* Soft matches awaiting confirmation */}
              {result.soft_matches?.length > 0 && (
                <div className="mt-6">
                  <h3 className="font-semibold text-purple-700 dark:text-purple-300 mb-2">
                    🔮 Soft matches — needs your blessing
                  </h3>
                  <ul className="space-y-2">
                    {result.soft_matches.map((s, i) => (
                      <li key={i} className="bg-purple-50 dark:bg-purple-950/30 border border-purple-200 dark:border-purple-900 rounded p-3">
                        <div className="flex justify-between gap-3">
                          <div className="flex-1">
                            <b>{s.proof.amount} {s.proof.currency}</b> from {s.proof.payer || "(unknown)"}
                            {" → "}
                            <b>{s.txn.amount} {s.txn.currency}</b> ({s.txn.description?.slice(0, 30)})
                            <div className="text-xs text-purple-700 dark:text-purple-300 mt-1">
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
                  <h3 className="font-semibold text-red-700 dark:text-red-300 mb-2">⚠ Discrepancies</h3>
                  <ul className="space-y-3">
                    {result.unmatched_proofs.map((u, i) => (
                      <li key={i} className="bg-red-50 dark:bg-red-950/30 p-3 rounded border border-red-200 dark:border-red-900">
                        <div className="flex justify-between items-start gap-3">
                          <div>
                            <b>{u.source_file}</b> — {u.amount} {u.currency} on {u.date}
                            <div className="text-xs text-red-700 dark:text-red-300 mt-1">{u.reason}</div>
                          </div>
                          <div className="flex flex-col gap-1">
                            <button onClick={() => setDunningTarget({
                              proof: u, expected: u.expected_net, actual: u.actual,
                              localCcy: u.closest_txn?.currency || "MYR",
                            })}
                              className="text-xs px-3 py-1 bg-indigo-100 text-indigo-800 dark:bg-indigo-900/50 dark:text-indigo-200 rounded hover:bg-indigo-200 dark:hover:bg-indigo-900">
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
