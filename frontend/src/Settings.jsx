import React, { useEffect, useState } from "react";
import { apiFetch as fetch } from "./Toast.jsx";

const API = "/api";

function Badge({ children, color = "slate" }) {
  const map = {
    green: "bg-green-100 text-green-800",
    red: "bg-red-100 text-red-800",
    blue: "bg-blue-100 text-blue-800",
    slate: "bg-slate-100 text-slate-700",
    amber: "bg-amber-100 text-amber-800",
    purple: "bg-purple-100 text-purple-800",
  };
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${map[color]}`}>{children}</span>
  );
}

export default function Settings() {
  const [skills, setSkills] = useState([]);
  const [config, setConfig] = useState(null);
  const [profiles, setProfiles] = useState({});
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [profile, setProfile] = useState("");
  const [wizardOut, setWizardOut] = useState(null);
  const [expanded, setExpanded] = useState({});
  const [drafts, setDrafts] = useState({});

  async function load() {
    setBusy("Loading platform…");
    setErr("");
    try {
      const r = await fetch(`${API}/platform/skills`);
      const j = await r.json();
      setSkills(j.skills || []);
      try {
        const pr = await fetch(`${API}/platform/model-profiles`);
        const pj = await pr.json();
        setProfiles(pj.profiles || {});
      } catch {}
      setConfig(j.config || null);
      setProfile(j.config?.business_profile || "");
      const next = {};
      (j.skills || []).forEach((s) => (next[s.id] = s.system_prompt));
      setDrafts(next);
    } catch (e) {
      setErr(String(e));
    }
    setBusy("");
  }

  useEffect(() => {
    load();
  }, []);

  async function toggle(id, enabled) {
    setBusy(`Updating ${id}…`);
    try {
      const r = await fetch(`${API}/platform/skills/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || "failed");
      await load();
    } catch (e) {
      setErr(String(e));
    }
    setBusy("");
  }

  async function saveProfileFor(id, model_profile) {
    setBusy(`Routing ${id} to ${model_profile}…`);
    try {
      await fetch(`${API}/platform/skills/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model_profile }),
      });
      await load();
    } catch (e) { setErr(String(e)); }
    setBusy("");
  }

  async function savePrompt(id) {
    setBusy(`Saving prompt for ${id}…`);
    try {
      const r = await fetch(`${API}/platform/skills/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ system_prompt: drafts[id] }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || "failed");
      await load();
    } catch (e) {
      setErr(String(e));
    }
    setBusy("");
  }

  async function resetPrompt(id, def) {
    setDrafts((d) => ({ ...d, [id]: def }));
    await savePrompt(id);
  }

  async function resetAll() {
    if (!confirm("Reset every skill toggle and system prompt to defaults?")) return;
    setBusy("Resetting platform…");
    try {
      await fetch(`${API}/platform/config/reset`, { method: "POST" });
      await load();
    } catch (e) {
      setErr(String(e));
    }
    setBusy("");
  }

  async function runWizard(apply) {
    if (!profile.trim()) {
      setErr("Describe the business first.");
      return;
    }
    setBusy("AI wizard configuring…");
    setErr("");
    setWizardOut(null);
    try {
      const r = await fetch(`${API}/platform/wizard`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ business_profile: profile, apply }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || "wizard failed");
      setWizardOut(j);
      if (apply) await load();
    } catch (e) {
      setErr(String(e));
    }
    setBusy("");
  }

  const byCategory = skills.reduce((acc, s) => {
    (acc[s.category] = acc[s.category] || []).push(s);
    return acc;
  }, {});

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-6">
      {err && <div className="bg-red-100 text-red-800 p-3 rounded">{err}</div>}
      {busy && <div className="bg-blue-50 text-blue-800 p-2 rounded text-sm">{busy}</div>}

      <section className="bg-white p-5 rounded-xl shadow space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="font-semibold text-lg">AI Setup Wizard</h2>
            <p className="text-sm text-slate-600">
              Describe your treasury operation in plain English. The AI picks which
              skills to enable and tunes each skill's system prompt for you.
            </p>
          </div>
          <button onClick={resetAll} className="text-xs px-3 py-1 rounded border border-slate-300 hover:bg-slate-50">
            Reset to defaults
          </button>
        </div>
        <textarea
          value={profile}
          onChange={(e) => setProfile(e.target.value)}
          rows={4}
          className="w-full border rounded-lg p-3 text-sm"
          placeholder='e.g. "Malaysian SME exporting to US and Singapore clients, mostly USD/SGD inbound, Maybank account, no SWIFT visibility, want strict month-end close with multilingual dunning."'
        />
        <div className="flex gap-2">
          <button
            onClick={() => runWizard(false)}
            className="px-4 py-2 rounded-lg border border-blue-600 text-blue-700 hover:bg-blue-50 text-sm"
          >
            Preview proposal
          </button>
          <button
            onClick={() => runWizard(true)}
            className="px-4 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-700 text-sm"
          >
            Apply with AI
          </button>
        </div>
        {wizardOut && (
          <div className="bg-slate-50 border border-slate-200 rounded p-3 text-xs space-y-2">
            {wizardOut.rationale && (
              <div><strong>Rationale:</strong> {wizardOut.rationale}</div>
            )}
            {wizardOut.proposed && (
              <>
                <div>
                  <strong>Enabled:</strong>{" "}
                  {(wizardOut.proposed.enabled_skills || []).join(", ") || "(none)"}
                </div>
                <div>
                  <strong>Overrides:</strong>{" "}
                  {Object.keys(wizardOut.proposed.skill_overrides || {}).join(", ") || "(none)"}
                </div>
              </>
            )}
          </div>
        )}
      </section>

      <section className="bg-white p-5 rounded-xl shadow">
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-semibold text-lg">Skills</h2>
          <div className="text-xs text-slate-500">
            {skills.filter((s) => s.enabled).length} of {skills.length} enabled
          </div>
        </div>

        {Object.entries(byCategory).map(([cat, list]) => (
          <div key={cat} className="mb-5">
            <div className="text-xs uppercase tracking-wider text-slate-500 mb-2">{cat}</div>
            <div className="space-y-2">
              {list.map((s) => {
                const isOpen = !!expanded[s.id];
                return (
                  <div
                    key={s.id}
                    className={`border rounded-lg ${s.enabled ? "border-slate-200" : "border-slate-200 opacity-70"}`}
                  >
                    <div className="flex items-center justify-between p-3">
                      <div className="flex-1">
                        <div className="flex items-center gap-2">
                          <span className="font-medium">{s.name}</span>
                          <Badge color={s.kind === "tool" ? "purple" : "blue"}>{s.kind}</Badge>
                          {s.is_overridden && <Badge color="amber">customized</Badge>}
                        </div>
                        <div className="text-xs text-slate-500 mt-0.5">{s.id} — {s.description}</div>
                      </div>
                      <div className="flex items-center gap-3">
                        {s.kind === "tool" && Object.keys(profiles).length > 0 && (
                          <select
                            value={s.model_profile || "default"}
                            onChange={(e) => saveProfileFor(s.id, e.target.value)}
                            className="text-xs border rounded px-1 py-0.5 bg-white"
                            title={`Model routing for this skill. Default: ${s.default_model_profile}`}
                          >
                            {Object.keys(profiles).map((p) => (
                              <option key={p} value={p}>{p}</option>
                            ))}
                          </select>
                        )}
                        <button
                          onClick={() => setExpanded((e) => ({ ...e, [s.id]: !isOpen }))}
                          className="text-xs text-blue-700 hover:underline"
                        >
                          {isOpen ? "Hide prompt" : "Edit prompt"}
                        </button>
                        <label className="inline-flex items-center cursor-pointer">
                          <input
                            type="checkbox"
                            checked={s.enabled}
                            onChange={(e) => toggle(s.id, e.target.checked)}
                            className="sr-only peer"
                          />
                          <div className="w-10 h-5 bg-slate-300 rounded-full peer-checked:bg-green-500 relative transition">
                            <div className="absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full peer-checked:translate-x-5 transition" />
                          </div>
                        </label>
                      </div>
                    </div>
                    {isOpen && (
                      <div className="border-t border-slate-200 p-3 space-y-2 bg-slate-50">
                        <label className="text-xs text-slate-600">System prompt</label>
                        <textarea
                          value={drafts[s.id] ?? ""}
                          onChange={(e) => setDrafts((d) => ({ ...d, [s.id]: e.target.value }))}
                          rows={5}
                          className="w-full border rounded p-2 text-xs font-mono bg-white"
                        />
                        <div className="flex gap-2">
                          <button
                            onClick={() => savePrompt(s.id)}
                            className="px-3 py-1 rounded bg-blue-600 text-white text-xs hover:bg-blue-700"
                          >
                            Save
                          </button>
                          <button
                            onClick={() => resetPrompt(s.id, s.default_system_prompt)}
                            className="px-3 py-1 rounded border border-slate-300 text-xs hover:bg-slate-100"
                          >
                            Restore default
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </section>

      {config && (
        <section className="bg-white p-5 rounded-xl shadow">
          <h2 className="font-semibold text-lg mb-2">Raw config</h2>
          <pre className="text-[11px] bg-slate-900 text-slate-100 p-3 rounded overflow-x-auto">
            {JSON.stringify(config, null, 2)}
          </pre>
        </section>
      )}
    </div>
  );
}
