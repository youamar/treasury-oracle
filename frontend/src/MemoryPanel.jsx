import React, { useEffect, useState } from "react";
import { apiFetch as fetch } from "./Toast.jsx";

const API = "/api/memory";

function Badge({ children, color = "slate" }) {
  const map = {
    green: "bg-green-100 text-green-800",
    blue: "bg-blue-100 text-blue-800",
    slate: "bg-slate-100 text-slate-700",
    amber: "bg-amber-100 text-amber-800",
    purple: "bg-purple-100 text-purple-800",
    red: "bg-red-100 text-red-800",
  };
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${map[color]}`}>
      {children}
    </span>
  );
}

export default function MemoryPanel() {
  const [summary, setSummary] = useState(null);
  const [aliases, setAliases] = useState({});
  const [facts, setFacts] = useState([]);
  const [uploads, setUploads] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [errors, setErrors] = useState([]);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");

  // alias form
  const [aliasCanonical, setAliasCanonical] = useState("");
  const [aliasObserved, setAliasObserved] = useState("");

  // fact form
  const [fSub, setFSub] = useState("");
  const [fPred, setFPred] = useState("");
  const [fVal, setFVal] = useState("");

  async function load() {
    setBusy("Loading memory…");
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
      setErr(String(e));
    }
    setBusy("");
  }

  useEffect(() => {
    load();
  }, []);

  async function addAlias(e) {
    e.preventDefault();
    if (!aliasCanonical || !aliasObserved) return;
    await fetch(`${API}/aliases`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ canonical: aliasCanonical, observed: aliasObserved }),
    });
    setAliasCanonical("");
    setAliasObserved("");
    load();
  }

  async function delAlias(c) {
    await fetch(`${API}/aliases/${encodeURIComponent(c)}`, { method: "DELETE" });
    load();
  }

  async function addFact(e) {
    e.preventDefault();
    if (!fSub || !fPred || !fVal) return;
    await fetch(`${API}/facts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ subject: fSub, predicate: fPred, value: fVal, source: "user" }),
    });
    setFSub("");
    setFPred("");
    setFVal("");
    load();
  }

  async function delFact(id) {
    await fetch(`${API}/facts/${id}`, { method: "DELETE" });
    load();
  }

  return (
    <div className="space-y-6">
      {err && <div className="bg-red-100 text-red-800 p-3 rounded">{err}</div>}
      {busy && <div className="bg-blue-50 text-blue-800 p-2 rounded text-sm">{busy}</div>}

      {summary && (
        <section className="bg-white p-5 rounded-xl shadow">
          <h2 className="font-semibold text-lg mb-2">Tenant memory · <span className="text-slate-500 text-sm">{summary.tenant}</span></h2>
          <div className="flex flex-wrap gap-3 text-sm">
            <Badge color="purple">{Object.keys(summary.aliases || {}).length} payer aliases</Badge>
            <Badge color="blue">{summary.fact_count} facts</Badge>
            <Badge color="amber">{summary.upload_count} raw uploads</Badge>
            <Badge color="green">{summary.session_count} past sessions</Badge>
            <Badge color={summary.error_count ? "red" : "slate"}>{summary.error_count || 0} errors logged</Badge>
          </div>
          <p className="text-xs text-slate-500 mt-2">
            Everything below is scoped to this tenant. The agent reads and writes
            facts via the <code>remember_fact</code> / <code>recall_facts</code> skills.
          </p>
        </section>
      )}

      <section className="bg-white p-5 rounded-xl shadow">
        <h3 className="font-semibold mb-3">Learned payer aliases</h3>
        <form onSubmit={addAlias} className="flex gap-2 mb-3 text-sm">
          <input
            value={aliasCanonical}
            onChange={(e) => setAliasCanonical(e.target.value)}
            placeholder="Canonical (e.g. Acme Corp)"
            className="flex-1 border rounded px-2 py-1"
          />
          <input
            value={aliasObserved}
            onChange={(e) => setAliasObserved(e.target.value)}
            placeholder="Observed (e.g. ACME CRP USA)"
            className="flex-1 border rounded px-2 py-1"
          />
          <button className="px-3 py-1 bg-blue-600 text-white rounded">Add</button>
        </form>
        {Object.keys(aliases).length === 0 ? (
          <div className="text-xs text-slate-400">No aliases learned yet.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-xs uppercase text-slate-500">
              <tr><th className="text-left">Canonical</th><th className="text-left">Observed</th><th></th></tr>
            </thead>
            <tbody>
              {Object.entries(aliases).map(([k, v]) => (
                <tr key={k} className="border-t">
                  <td className="py-1 font-medium">{k}</td>
                  <td className="py-1">{v}</td>
                  <td className="text-right">
                    <button onClick={() => delAlias(k)} className="text-xs text-red-600 hover:underline">delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="bg-white p-5 rounded-xl shadow">
        <h3 className="font-semibold mb-1">Memory facts</h3>
        <p className="text-xs text-slate-500 mb-3">
          Free-form (subject, predicate, value) the agent remembers. Customer
          can add domain knowledge manually — the agent will recall it next run.
        </p>
        <form onSubmit={addFact} className="grid grid-cols-7 gap-2 mb-3 text-sm">
          <input value={fSub} onChange={(e) => setFSub(e.target.value)} placeholder="subject" className="col-span-2 border rounded px-2 py-1" />
          <input value={fPred} onChange={(e) => setFPred(e.target.value)} placeholder="predicate" className="col-span-2 border rounded px-2 py-1" />
          <input value={fVal} onChange={(e) => setFVal(e.target.value)} placeholder="value" className="col-span-2 border rounded px-2 py-1" />
          <button className="col-span-1 px-3 py-1 bg-blue-600 text-white rounded">Add</button>
        </form>
        {facts.length === 0 ? (
          <div className="text-xs text-slate-400">No facts yet.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-xs uppercase text-slate-500">
              <tr>
                <th className="text-left">Subject</th><th className="text-left">Predicate</th>
                <th className="text-left">Value</th><th className="text-left">Source</th>
                <th className="text-right"></th>
              </tr>
            </thead>
            <tbody>
              {facts.map((f) => (
                <tr key={f.id} className="border-t">
                  <td className="py-1 font-medium">{f.subject}</td>
                  <td className="py-1 text-slate-600">{f.predicate}</td>
                  <td className="py-1">{f.value}</td>
                  <td className="py-1 text-xs text-slate-500">{f.source}</td>
                  <td className="text-right">
                    <button onClick={() => delFact(f.id)} className="text-xs text-red-600 hover:underline">delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="bg-white p-5 rounded-xl shadow">
        <h3 className="font-semibold mb-3">Recent sessions</h3>
        {sessions.length === 0 ? (
          <div className="text-xs text-slate-400">No sessions yet.</div>
        ) : (
          <ul className="text-sm space-y-1">
            {sessions.map((s) => (
              <li key={s.id} className="flex justify-between border-b last:border-0 py-1">
                <span><code className="text-xs">{s.id}</code> · {s.bank}</span>
                <span className="text-xs text-slate-500">
                  {s.summary?.matched ?? 0} matched · {s.summary?.unmatched_proofs ?? 0} unmatched · {s.created_at}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="bg-white p-5 rounded-xl shadow">
        <h3 className="font-semibold mb-3">Raw uploaded files</h3>
        {uploads.length === 0 ? (
          <div className="text-xs text-slate-400">No raw uploads stored yet.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-xs uppercase text-slate-500">
              <tr>
                <th className="text-left">File</th><th className="text-left">Purpose</th>
                <th className="text-left">Size</th><th className="text-left">SHA-256</th>
                <th className="text-right">Download</th>
              </tr>
            </thead>
            <tbody>
              {uploads.map((u) => (
                <tr key={u.id} className="border-t">
                  <td className="py-1">{u.filename}</td>
                  <td className="py-1 text-xs">{u.purpose}</td>
                  <td className="py-1 text-xs">{Math.round((u.size || 0) / 1024)} KB</td>
                  <td className="py-1 text-[10px] font-mono text-slate-500">{u.sha256.slice(0, 16)}…</td>
                  <td className="text-right">
                    <a href={`${API}/uploads/${u.sha256}`} className="text-xs text-blue-600 hover:underline">download</a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="bg-white p-5 rounded-xl shadow">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold">Recent errors</h3>
          {errors.length > 0 && (
            <button
              onClick={async () => { await fetch(`${API}/errors`, { method: "DELETE" }); load(); }}
              className="text-xs px-3 py-1 rounded border border-slate-300 hover:bg-slate-50"
            >Clear all</button>
          )}
        </div>
        {errors.length === 0 ? (
          <div className="text-xs text-slate-400">No errors logged.</div>
        ) : (
          <ul className="space-y-2 text-sm">
            {errors.map((e) => (
              <li key={e.id} className="border border-red-100 rounded p-2 bg-red-50">
                <div className="flex justify-between text-xs text-slate-500">
                  <span><Badge color="red">{e.kind}</Badge> · <code>{e.source}</code></span>
                  <span>{e.created_at}</span>
                </div>
                <div className="mt-1 font-mono text-xs text-red-900 whitespace-pre-wrap break-words">{e.message}</div>
                {e.context && Object.keys(e.context).length > 0 && (
                  <details className="mt-1">
                    <summary className="text-[11px] text-slate-600 cursor-pointer">context</summary>
                    <pre className="text-[10px] bg-slate-900 text-slate-100 p-2 rounded mt-1 overflow-x-auto">{JSON.stringify(e.context, null, 2)}</pre>
                  </details>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
