import React, { useEffect, useMemo, useState } from "react";
import { apiFetch as fetch, pushToast, downloadAuthed } from "./Toast.jsx";
import Page, { Card, Badge, EmptyState } from "./Page.jsx";

const API = "/api";


/** Past reconciliations browser.
 *
 * Day-by-day operators don't want to re-run the agent to see what happened
 * yesterday. This view lists every recon, lets you search by bank or recon-id,
 * click into a session, and inspect the full agent trace step-by-step. */
export default function History() {
  const [sessions, setSessions] = useState([]);
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState("");
  const [active, setActive] = useState(null);  // recon_id we're inspecting
  const [detail, setDetail] = useState(null);  // full session payload

  async function load() {
    setBusy(true);
    try {
      const r = await fetch(`${API}/sessions?limit=200`);
      if (!r.ok) throw new Error(await r.text());
      setSessions((await r.json()).sessions || []);
    } catch (e) {
      pushToast({ kind: "error", title: "Failed to load history",
                  message: String(e?.message || e) });
    }
    setBusy(false);
  }
  useEffect(() => { load(); }, []);

  async function openDetail(id) {
    setActive(id);
    setDetail(null);
    try {
      const r = await fetch(`${API}/session/${encodeURIComponent(id)}`);
      if (!r.ok) throw new Error(await r.text());
      setDetail(await r.json());
    } catch (e) {
      pushToast({ kind: "error", title: "Could not load session",
                  message: String(e?.message || e) });
      setActive(null);
    }
  }

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return sessions;
    return sessions.filter((s) =>
      (s.id || "").toLowerCase().includes(q) ||
      (s.bank || "").toLowerCase().includes(q)
    );
  }, [sessions, filter]);

  return (
    <Page
      icon="📚"
      title="Reconciliation History"
      subtitle="Every recon this tenant has run. Click any row to inspect its full agent trace, matches, and discrepancies — no need to re-run."
      actions={
        <button onClick={load} disabled={busy}
          className="text-xs px-3 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 disabled:opacity-50">
          {busy ? "refreshing…" : "refresh"}
        </button>
      }
    >
      <Card>
        <div className="flex items-center gap-2 mb-3">
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter by recon-id or bank…"
            className="flex-1 border border-slate-300 rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
          />
          <span className="text-xs text-slate-500 shrink-0">
            {filtered.length} of {sessions.length}
          </span>
        </div>
        {filtered.length === 0 ? (
          <EmptyState
            icon="🗂"
            title={sessions.length === 0 ? "No reconciliations yet" : "No matches for that filter"}
            hint={sessions.length === 0 ? "Run your first recon — it'll appear here." : "Try a different search term."}
          />
        ) : (
          <ul className="divide-y divide-slate-200">
            {filtered.map((s) => (
              <SessionRow key={s.id} session={s} onClick={() => openDetail(s.id)} />
            ))}
          </ul>
        )}
      </Card>

      {active && (
        <DetailDrawer id={active} detail={detail} onClose={() => { setActive(null); setDetail(null); }} />
      )}
    </Page>
  );
}


function SessionRow({ session, onClick }) {
  const s = session.summary || {};
  const created = session.created_at ? new Date(session.created_at) : null;
  const matched = s.matched || 0;
  const soft = s.soft_matches || 0;
  const disc = s.unmatched_proofs || 0;
  const total = s.total_proofs || (matched + soft + disc);
  const status =
    disc > 0 ? "needs-review"
    : soft > 0 ? "soft-pending"
    : matched > 0 ? "clean"
    : "empty";
  const statusBadge = {
    "clean":          <Badge color="green">✓ clean</Badge>,
    "soft-pending":   <Badge color="purple">soft pending</Badge>,
    "needs-review":   <Badge color="red">needs review</Badge>,
    "empty":          <Badge color="slate">empty</Badge>,
  }[status];
  return (
    <li onClick={onClick}
        className="py-3 px-1 cursor-pointer hover:bg-slate-50 rounded transition flex items-center justify-between gap-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-mono text-xs text-slate-700">{session.id}</span>
          <Badge color="blue">{session.bank}</Badge>
          {statusBadge}
        </div>
        <div className="text-xs text-slate-500 mt-1">
          {created ? created.toLocaleString() : "—"}
          {" · "}
          <span className="text-emerald-700">{matched} matched</span>
          {soft > 0 && <> · <span className="text-purple-700">{soft} soft</span></>}
          {disc > 0 && <> · <span className="text-red-700">{disc} flagged</span></>}
          <span className="text-slate-400"> · {total} proof{total !== 1 ? "s" : ""}</span>
        </div>
      </div>
      <span className="text-slate-400 text-sm shrink-0">inspect →</span>
    </li>
  );
}


function DetailDrawer({ id, detail, onClose }) {
  return (
    <div className="fixed inset-0 z-40 flex" onClick={onClose}>
      <div className="flex-1 bg-slate-900/40 backdrop-blur-sm" />
      <div className="w-full max-w-3xl bg-white shadow-2xl overflow-y-auto"
           onClick={(e) => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-slate-200 px-5 py-3 flex items-center justify-between">
          <div className="min-w-0">
            <div className="font-mono text-xs text-slate-500">recon_id</div>
            <div className="font-semibold truncate">{id}</div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => downloadAuthed(
                `/api/report/${encodeURIComponent(id)}`,
                `reconciliation_${id}.pdf`,
              ).catch(() => {})}
              className="text-xs px-3 py-1.5 rounded bg-emerald-600 text-white hover:bg-emerald-700">
              ⬇ PDF report
            </button>
            <button onClick={onClose}
                    className="text-slate-500 hover:text-slate-900 text-xl px-2">×</button>
          </div>
        </div>
        {!detail ? (
          <div className="p-8 text-center text-slate-500">Loading…</div>
        ) : (
          <DetailBody d={detail} />
        )}
      </div>
    </div>
  );
}


function DetailBody({ d }) {
  const s = d.summary || {};
  const [tab, setTab] = useState("trace"); // trace | matches | flagged
  return (
    <div className="p-5 space-y-5">
      <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
        {[
          ["Proofs", s.total_proofs || 0],
          ["Txns", s.total_txns || 0],
          ["Matched", s.matched || 0],
          ["Soft", s.soft_matches || 0],
          ["Flagged", s.unmatched_proofs || 0],
        ].map(([l, v]) => (
          <div key={l} className="bg-slate-50 rounded p-2 text-center">
            <div className="text-lg font-bold">{v}</div>
            <div className="text-[10px] text-slate-500 uppercase tracking-wider">{l}</div>
          </div>
        ))}
      </div>

      <div className="flex gap-1 border-b border-slate-200">
        {[
          ["trace",   `🧠 Agent trace (${(d.agent_trace || []).length})`],
          ["matches", `✓ Matches (${(d.matches || []).length})`],
          ["flagged", `⚠ Flagged (${(d.unmatched_proofs || []).length})`],
        ].map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)}
                  className={`px-3 py-2 text-sm border-b-2 transition ${
                    tab === k ? "border-indigo-500 text-indigo-700 font-medium"
                              : "border-transparent text-slate-500 hover:text-slate-900"
                  }`}>
            {l}
          </button>
        ))}
      </div>

      {tab === "trace" && <TraceList trace={d.agent_trace || []} />}
      {tab === "matches" && <MatchesList matches={d.matches || []} />}
      {tab === "flagged" && <FlaggedList items={d.unmatched_proofs || []} />}
    </div>
  );
}


function ProviderBadge({ name }) {
  if (!name) return null;
  const color = name.startsWith("chutes") ? "blue"
              : name === "openai" ? "emerald"
              : name === "anthropic" ? "amber"
              : "slate";
  return <Badge color={color}>{name}</Badge>;
}


function TraceList({ trace }) {
  if (!trace.length) return <EmptyState icon="—" title="No trace recorded" />;
  const grouped = [];
  let bucket = null;
  for (const t of trace) {
    const src = t.proof_source || "—";
    if (!bucket || bucket.src !== src) {
      bucket = { src, items: [] };
      grouped.push(bucket);
    }
    bucket.items.push(t);
  }
  return (
    <div className="space-y-3">
      {grouped.map((g, i) => (
        <details key={i} open className="rounded border border-slate-200 bg-slate-50">
          <summary className="cursor-pointer px-3 py-2 text-xs font-mono text-slate-700 hover:bg-slate-100">
            📄 {g.src} <span className="text-slate-400">({g.items.length} events)</span>
          </summary>
          <div className="px-3 pb-3 font-mono text-[11px] leading-relaxed bg-slate-900 text-slate-100 mx-2 my-2 rounded p-2 max-h-96 overflow-auto">
            {g.items.map((t, j) => <TraceLine key={j} t={t} />)}
          </div>
        </details>
      ))}
    </div>
  );
}


function TraceLine({ t }) {
  const color =
      t.type === "decision" ? "text-green-300"
    : t.type === "tool_call" ? "text-cyan-300"
    : t.type === "tool_result" ? "text-slate-400"
    : t.type === "error" ? "text-red-300"
    : t.type === "provider" ? "text-blue-300"
    : t.type === "verifier_downgrade" ? "text-amber-300"
    : t.type === "verifier_confirm" ? "text-emerald-300"
    : t.type === "reflection" ? "text-purple-300"
    : "text-slate-300";
  const p = t.payload || {};
  const label =
      t.type === "tool_call" ? `→ ${p.name}(${JSON.stringify(p.arguments || {}).slice(0, 80)})`
    : t.type === "tool_result" ? `← ${p.name}: ${JSON.stringify(p.result || {}).slice(0, 100)}`
    : t.type === "decision" ? `★ ${p.decision} (conf ${Math.round((p.confidence || 0) * 100)}%)`
    : t.type === "provider" ? `🧠 provider=${p.provider} model=${p.model || "?"}${
        (p.fallback_from || []).length ? ` (fell through: ${(p.fallback_from || []).join(", ")})` : ""}`
    : t.type === "verifier_downgrade" ? `⚠ verifier: ${(p.concerns || []).join("; ")}`
    : t.type === "verifier_confirm" ? `✓ verifier confirmed`
    : t.type === "reflection" ? `↻ re-plan cycle ${p.cycle}`
    : t.type === "error" ? `⚠ ${p.message || ""}`
    : `${t.type}: ${JSON.stringify(p).slice(0, 100)}`;
  return (
    <div className={color}>
      <span className="text-slate-500">[step {t.step}]</span> {label}
    </div>
  );
}


function MatchesList({ matches }) {
  if (!matches.length) return <EmptyState icon="—" title="No matches" />;
  return (
    <ul className="space-y-2">
      {matches.map((m, i) => (
        <li key={i} className="border border-emerald-200 bg-emerald-50 rounded p-3 text-sm">
          <div className="flex justify-between gap-2 flex-wrap">
            <div>
              <b>{m.proof.amount} {m.proof.currency}</b> · {m.proof.payer || "(unknown)"}
              {m.proof.reference && <> · <span className="font-mono text-xs">{m.proof.reference}</span></>}
            </div>
            <Badge color="green">{Math.round((m.confidence || 0) * 100)}%</Badge>
          </div>
          <div className="text-xs text-slate-600 mt-1">→ {m.txn?.description?.slice(0, 80)}</div>
        </li>
      ))}
    </ul>
  );
}


function FlaggedList({ items }) {
  if (!items.length) return <EmptyState icon="✓" title="Nothing flagged" hint="Every proof matched a transaction." />;
  return (
    <ul className="space-y-2">
      {items.map((u, i) => (
        <li key={i} className="border border-red-200 bg-red-50 rounded p-3 text-sm">
          <div className="font-mono text-xs text-red-700">{u.source_file}</div>
          <div>{u.amount} {u.currency} · {u.payer || "(unknown)"} · {u.date}</div>
          <div className="text-xs text-red-700 mt-1">{u.reason}</div>
        </li>
      ))}
    </ul>
  );
}
