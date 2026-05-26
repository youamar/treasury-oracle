import React, { useEffect, useState } from "react";
import { apiFetch as fetch, pushToast } from "./Toast.jsx";
import Page, { Card, Badge, EmptyState } from "./Page.jsx";

const API = "/api/memory";


export default function MemoryPanel() {
  const [summary, setSummary] = useState(null);
  const [aliases, setAliases] = useState({});
  const [facts, setFacts] = useState([]);
  const [uploads, setUploads] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [errors, setErrors] = useState([]);
  const [tab, setTab] = useState("learned");

  // forms
  const [aliasCanonical, setAliasCanonical] = useState("");
  const [aliasObserved, setAliasObserved] = useState("");
  const [fSub, setFSub] = useState("");
  const [fPred, setFPred] = useState("");
  const [fVal, setFVal] = useState("");

  async function load() {
    try {
      const [s, a, f, u, ss, er] = await Promise.all([
        fetch(`${API}/summary`).then((r) => r.json()),
        fetch(`${API}/aliases`).then((r) => r.json()),
        fetch(`${API}/facts`).then((r) => r.json()),
        fetch(`${API}/uploads`).then((r) => r.json()),
        fetch(`${API}/sessions?limit=20`).then((r) => r.json()),
        fetch(`${API}/errors?limit=30`).then((r) => r.json()),
      ]);
      setSummary(s);
      setAliases(a.aliases || {});
      setFacts(f.facts || []);
      setUploads(u.uploads || []);
      setSessions(ss.sessions || []);
      setErrors(er.errors || []);
    } catch (e) {
      pushToast({ kind: "error", title: "Failed to load memory",
                  message: String(e?.message || e) });
    }
  }

  useEffect(() => { load(); }, []);

  async function addAlias(e) {
    e.preventDefault();
    if (!aliasCanonical || !aliasObserved) return;
    try {
      await fetch(`${API}/aliases`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ canonical: aliasCanonical, observed: aliasObserved }),
      });
      setAliasCanonical(""); setAliasObserved("");
      await load();
      pushToast({ kind: "ok", title: "Alias added" });
    } catch (e) {
      pushToast({ kind: "error", title: "Add failed",
                  message: String(e?.message || e) });
    }
  }

  async function delAlias(c) {
    await fetch(`${API}/aliases/${encodeURIComponent(c)}`, { method: "DELETE" });
    await load();
    pushToast({ kind: "ok", title: "Alias removed" });
  }

  async function addFact(e) {
    e.preventDefault();
    if (!fSub || !fPred || !fVal) return;
    try {
      await fetch(`${API}/facts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subject: fSub, predicate: fPred, value: fVal, source: "user" }),
      });
      setFSub(""); setFPred(""); setFVal("");
      await load();
      pushToast({ kind: "ok", title: "Fact remembered" });
    } catch (e) {
      pushToast({ kind: "error", title: "Add failed",
                  message: String(e?.message || e) });
    }
  }

  async function delFact(id) {
    await fetch(`${API}/facts/${id}`, { method: "DELETE" });
    await load();
  }

  const tabs = [
    { id: "learned", label: "🧠 Learned",
      count: Object.keys(aliases).length + facts.length },
    { id: "sessions", label: "📜 Sessions", count: sessions.length },
    { id: "uploads",  label: "🗂  Uploads",  count: uploads.length },
    { id: "errors",   label: "⚠ Errors",   count: errors.length,
      danger: errors.length > 0 },
  ];

  return (
    <Page
      icon="🧠"
      title="Memory"
      subtitle={summary ? `Everything below is private to tenant ${summary.tenant}` :
                          "Everything the agent has remembered for this tenant"}
    >
      {/* Summary chips */}
      {summary && (
        <Card>
          <div className="flex flex-wrap gap-2">
            <Badge color="purple">{Object.keys(summary.aliases || {}).length} payer aliases</Badge>
            <Badge color="blue">{summary.fact_count} facts</Badge>
            <Badge color="amber">{summary.upload_count} raw uploads</Badge>
            <Badge color="green">{summary.session_count} sessions</Badge>
            <Badge color={summary.error_count ? "red" : "slate"}>
              {summary.error_count || 0} errors
            </Badge>
          </div>
          <p className="text-xs text-slate-500 mt-3">
            The agent reads and writes facts via the{" "}
            <code className="bg-slate-100 px-1 rounded">remember_fact</code> /{" "}
            <code className="bg-slate-100 px-1 rounded">recall_facts</code> skills.
            Everything here scopes to your account — switching email gives a clean memory.
          </p>
        </Card>
      )}

      {/* Tab nav */}
      <div className="flex gap-1 border-b border-slate-200 -mb-2">
        {tabs.map((t) => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={`px-3 py-2 text-sm font-medium border-b-2 -mb-px transition ${
              tab === t.id
                ? "border-indigo-600 text-indigo-700"
                : t.danger
                ? "border-transparent text-red-600 hover:text-red-700"
                : "border-transparent text-slate-500 hover:text-slate-700"
            }`}>
            {t.label} {t.count > 0 && <span className="text-xs opacity-70">({t.count})</span>}
          </button>
        ))}
      </div>

      {tab === "learned" && (
        <>
          {/* Aliases */}
          <Card title="Learned payer aliases"
                subtitle="Each soft-match confirmation teaches the agent that 'observed' = 'canonical'. Add manually or let the agent learn from your operator's clicks.">
            <form onSubmit={addAlias} className="flex gap-2 mb-3 text-sm flex-wrap">
              <input value={aliasCanonical}
                     onChange={(e) => setAliasCanonical(e.target.value)}
                     placeholder="Canonical (e.g. Acme Corp)"
                     className="flex-1 min-w-[180px] border border-slate-300 rounded px-2 py-1" />
              <input value={aliasObserved}
                     onChange={(e) => setAliasObserved(e.target.value)}
                     placeholder="Observed (e.g. ACME CRP USA)"
                     className="flex-1 min-w-[180px] border border-slate-300 rounded px-2 py-1" />
              <button className="px-3 py-1 bg-indigo-600 text-white rounded hover:bg-indigo-700">
                Add
              </button>
            </form>
            {Object.keys(aliases).length === 0 ? (
              <EmptyState icon="🪪" title="No aliases learned yet"
                          hint="Confirm a soft match from the Reconcile view, and the alias is remembered here." />
            ) : (
              <div className="overflow-x-auto -mx-5 px-5">
                <table className="w-full text-sm">
                  <thead className="text-xs uppercase text-slate-500">
                    <tr><th className="text-left py-1">Canonical</th>
                        <th className="text-left">Observed</th>
                        <th></th></tr>
                  </thead>
                  <tbody>
                    {Object.entries(aliases).map(([k, v]) => (
                      <tr key={k} className="border-t border-slate-100">
                        <td className="py-2 font-medium">{k}</td>
                        <td className="py-2 text-slate-600">{v}</td>
                        <td className="text-right py-2">
                          <button onClick={() => delAlias(k)}
                                  className="text-xs text-red-600 hover:underline">delete</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          {/* Facts */}
          <Card title="Memory facts"
                subtitle="Free-form (subject, predicate, value). The agent reads these before each decision.">
            <form onSubmit={addFact} className="grid grid-cols-1 md:grid-cols-7 gap-2 mb-3 text-sm">
              <input value={fSub} onChange={(e) => setFSub(e.target.value)}
                     placeholder="subject (e.g. Acme Corp)"
                     className="md:col-span-2 border border-slate-300 rounded px-2 py-1" />
              <input value={fPred} onChange={(e) => setFPred(e.target.value)}
                     placeholder="predicate (e.g. pays_late_by_days)"
                     className="md:col-span-2 border border-slate-300 rounded px-2 py-1" />
              <input value={fVal} onChange={(e) => setFVal(e.target.value)}
                     placeholder="value (e.g. 5)"
                     className="md:col-span-2 border border-slate-300 rounded px-2 py-1" />
              <button className="md:col-span-1 px-3 py-1 bg-indigo-600 text-white rounded hover:bg-indigo-700">
                Add
              </button>
            </form>
            {facts.length === 0 ? (
              <EmptyState icon="📝" title="No facts yet"
                          hint="The agent writes facts via remember_fact during runs, or you can add them manually." />
            ) : (
              <div className="overflow-x-auto -mx-5 px-5">
                <table className="w-full text-sm">
                  <thead className="text-xs uppercase text-slate-500">
                    <tr><th className="text-left py-1">Subject</th>
                        <th className="text-left">Predicate</th>
                        <th className="text-left">Value</th>
                        <th className="text-left">Source</th>
                        <th></th></tr>
                  </thead>
                  <tbody>
                    {facts.map((f) => (
                      <tr key={f.id} className="border-t border-slate-100">
                        <td className="py-2 font-medium">{f.subject}</td>
                        <td className="py-2 text-slate-600 font-mono text-xs">{f.predicate}</td>
                        <td className="py-2">{f.value}</td>
                        <td className="py-2 text-xs text-slate-500">{f.source}</td>
                        <td className="text-right py-2">
                          <button onClick={() => delFact(f.id)}
                                  className="text-xs text-red-600 hover:underline">delete</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}

      {tab === "sessions" && (
        <Card title="Recent reconciliation sessions">
          {sessions.length === 0 ? (
            <EmptyState icon="📜" title="No sessions yet"
                        hint="Run a reconciliation from the Reconcile tab to see history here." />
          ) : (
            <ul className="text-sm space-y-1">
              {sessions.map((s) => (
                <li key={s.id} className="flex justify-between items-center border-b border-slate-100 last:border-0 py-2 hover:bg-slate-50 px-2 -mx-2 rounded">
                  <div>
                    <code className="text-xs bg-slate-100 px-1 py-0.5 rounded">{s.id}</code>{" "}
                    <span className="font-medium">{s.bank}</span>
                  </div>
                  <div className="text-xs text-slate-500 text-right">
                    <Badge color="green">{s.summary?.matched ?? 0} matched</Badge>{" "}
                    <Badge color="red">{s.summary?.unmatched_proofs ?? 0} unmatched</Badge>
                    <div className="mt-0.5 text-[11px]">{s.created_at}</div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}

      {tab === "uploads" && (
        <Card title="Raw uploaded files"
              subtitle="Every file you've uploaded is deduplicated by SHA-256 and tied to your account.">
          {uploads.length === 0 ? (
            <EmptyState icon="🗂" title="No uploads yet"
                        hint="Drop a proof or bank statement to see it captured here with a SHA-256 hash." />
          ) : (
            <div className="overflow-x-auto -mx-5 px-5">
              <table className="w-full text-sm">
                <thead className="text-xs uppercase text-slate-500">
                  <tr>
                    <th className="text-left py-1">File</th>
                    <th className="text-left">Purpose</th>
                    <th className="text-right">Size</th>
                    <th className="text-left">SHA-256</th>
                    <th className="text-right">Download</th>
                  </tr>
                </thead>
                <tbody>
                  {uploads.map((u) => (
                    <tr key={u.id} className="border-t border-slate-100">
                      <td className="py-2">{u.filename}</td>
                      <td className="py-2"><Badge color="slate">{u.purpose}</Badge></td>
                      <td className="py-2 text-right text-xs">{Math.round((u.size || 0) / 1024)} KB</td>
                      <td className="py-2 text-[10px] font-mono text-slate-500">{u.sha256.slice(0, 16)}…</td>
                      <td className="text-right py-2">
                        <a href={`${API}/uploads/${u.sha256}`}
                           className="text-xs text-indigo-600 hover:underline">download</a>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}

      {tab === "errors" && (
        <Card
          title="Recent errors"
          subtitle="Captured automatically via reliability.record_error — useful for debugging."
          actions={
            errors.length > 0 && (
              <button onClick={async () => {
                await fetch(`${API}/errors`, { method: "DELETE" });
                await load();
                pushToast({ kind: "ok", title: "Errors cleared" });
              }}
                className="text-xs px-3 py-1 rounded border border-slate-300 hover:bg-slate-50">
                Clear all
              </button>
            )
          }
        >
          {errors.length === 0 ? (
            <EmptyState icon="✨" title="No errors logged"
                        hint="That's a good thing." />
          ) : (
            <ul className="space-y-2 text-sm">
              {errors.map((e) => (
                <li key={e.id} className="border border-red-100 rounded p-2 bg-red-50">
                  <div className="flex justify-between text-xs text-slate-500 flex-wrap gap-1">
                    <span><Badge color="red">{e.kind}</Badge>{" "}
                          <code className="text-slate-600">{e.source}</code></span>
                    <span>{e.created_at}</span>
                  </div>
                  <div className="mt-1 font-mono text-xs text-red-900 whitespace-pre-wrap break-words">
                    {e.message}
                  </div>
                  {e.context && Object.keys(e.context).length > 0 && (
                    <details className="mt-1">
                      <summary className="text-[11px] text-slate-600 cursor-pointer">context</summary>
                      <pre className="text-[10px] bg-slate-900 text-slate-100 p-2 rounded mt-1 overflow-x-auto">
                        {JSON.stringify(e.context, null, 2)}
                      </pre>
                    </details>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}
    </Page>
  );
}
