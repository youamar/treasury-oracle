import React, { useEffect, useState } from "react";
import { apiFetch as fetch, pushToast } from "./Toast.jsx";
import Page, { Card, Badge, EmptyState } from "./Page.jsx";

const API = "/api";


export default function Settings() {
  const [skills, setSkills] = useState([]);
  const [config, setConfig] = useState(null);
  const [profiles, setProfiles] = useState({});
  const [profile, setProfile] = useState("");
  const [wizardOut, setWizardOut] = useState(null);
  const [expanded, setExpanded] = useState({});
  const [drafts, setDrafts] = useState({});
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState("all"); // all | enabled | disabled | customized

  async function load() {
    setBusy(true);
    try {
      const [r, pr] = await Promise.all([
        fetch(`${API}/platform/skills`),
        fetch(`${API}/platform/model-profiles`).catch(() => null),
      ]);
      const j = await r.json();
      setSkills(j.skills || []);
      setConfig(j.config || null);
      setProfile(j.config?.business_profile || "");
      const next = {};
      (j.skills || []).forEach((s) => (next[s.id] = s.system_prompt));
      setDrafts(next);
      if (pr) setProfiles((await pr.json()).profiles || {});
    } catch (e) {
      pushToast({ kind: "error", title: "Failed to load settings",
                  message: String(e?.message || e) });
    }
    setBusy(false);
  }

  useEffect(() => { load(); }, []);

  async function toggle(id, enabled) {
    try {
      const r = await fetch(`${API}/platform/skills/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || "failed");
      await load();
      pushToast({ kind: "ok", title: enabled ? "Skill enabled" : "Skill disabled",
                  message: id });
    } catch (e) {
      pushToast({ kind: "error", title: "Update failed",
                  message: String(e?.message || e) });
    }
  }

  async function saveProfileFor(id, model_profile) {
    try {
      await fetch(`${API}/platform/skills/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model_profile }),
      });
      await load();
      pushToast({ kind: "ok", title: "Model routing updated",
                  message: `${id} → ${model_profile}` });
    } catch (e) {
      pushToast({ kind: "error", title: "Update failed",
                  message: String(e?.message || e) });
    }
  }

  async function savePrompt(id) {
    try {
      const r = await fetch(`${API}/platform/skills/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ system_prompt: drafts[id] }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || "failed");
      await load();
      pushToast({ kind: "ok", title: "Prompt saved", message: id });
    } catch (e) {
      pushToast({ kind: "error", title: "Save failed",
                  message: String(e?.message || e) });
    }
  }

  async function resetPrompt(id, def) {
    setDrafts((d) => ({ ...d, [id]: def }));
    await savePrompt(id);
  }

  async function resetAll() {
    if (!confirm("Reset every skill toggle and system prompt to defaults?")) return;
    try {
      await fetch(`${API}/platform/config/reset`, { method: "POST" });
      await load();
      pushToast({ kind: "ok", title: "Platform reset",
                  message: "All toggles and prompts back to defaults." });
    } catch (e) {
      pushToast({ kind: "error", title: "Reset failed",
                  message: String(e?.message || e) });
    }
  }

  async function runWizard(apply) {
    if (profile.trim().length < 30) {
      pushToast({ kind: "warn", title: "Describe the business",
                  message: "Add a sentence or two so the AI can tune skills for you." });
      return;
    }
    setWizardOut(null);
    setBusy(true);
    try {
      const r = await fetch(`${API}/platform/wizard`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ business_profile: profile, apply }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || "wizard failed");
      setWizardOut(j);
      if (apply) {
        await load();
        pushToast({ kind: "ok", title: "Wizard applied",
                    message: "Agent reconfigured for your business." });
      }
    } catch (e) {
      pushToast({ kind: "error", title: "Wizard failed",
                  message: String(e?.message || e) });
    }
    setBusy(false);
  }

  const filtered = skills.filter((s) => {
    if (filter === "enabled") return s.enabled;
    if (filter === "disabled") return !s.enabled;
    if (filter === "customized") return s.is_overridden;
    return true;
  });

  const byCategory = filtered.reduce((acc, s) => {
    (acc[s.category] = acc[s.category] || []).push(s);
    return acc;
  }, {});

  return (
    <Page
      icon="⚙️"
      title="Settings"
      subtitle="Configure your treasury agent: enable/disable skills, edit prompts, route to different models."
      actions={
        <button onClick={resetAll}
                className="text-sm px-3 py-1.5 rounded-lg border border-slate-300 hover:bg-slate-50">
          Reset to defaults
        </button>
      }
    >
      {/* Wizard */}
      <Card
        title="🪄 AI Setup Wizard"
        subtitle="Re-run the wizard anytime your business changes. The AI picks skills and tunes prompts for you."
      >
        <textarea
          value={profile}
          onChange={(e) => setProfile(e.target.value)}
          rows={3}
          className="w-full border border-slate-300 rounded-lg p-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
          placeholder='e.g. "Malaysian SME exporting to US and Singapore clients, mostly USD/SGD inbound, Maybank account, want strict month-end close with multilingual dunning."'
        />
        <div className="text-[11px] text-slate-400 mt-1 mb-3">
          {profile.length} chars · {profile.length < 30 ? "needs more detail" : "looks good"}
        </div>
        <div className="flex gap-2 flex-wrap">
          <button onClick={() => runWizard(false)} disabled={busy}
            className="px-4 py-2 rounded-lg border border-indigo-600 text-indigo-700 hover:bg-indigo-50 text-sm disabled:opacity-50">
            Preview proposal
          </button>
          <button onClick={() => runWizard(true)} disabled={busy}
            className="px-4 py-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 text-sm disabled:opacity-50">
            {busy ? "Working…" : "Apply with AI"}
          </button>
        </div>
        {wizardOut?.proposed && (
          <div className="mt-3 bg-emerald-50 border border-emerald-200 rounded p-3 text-sm space-y-2">
            {wizardOut.rationale && (
              <div><b className="text-emerald-900">Rationale:</b>{" "}
                   <span className="text-emerald-800">{wizardOut.rationale}</span></div>
            )}
            <div>
              <b className="text-emerald-900">Enabled skills:</b>{" "}
              <span className="text-xs">
                {(wizardOut.proposed.enabled_skills || []).map((s) => (
                  <span key={s} className="inline-block bg-white border border-emerald-300 text-emerald-800 px-1.5 py-0.5 rounded mr-1 mb-1 font-mono">{s}</span>
                ))}
              </span>
            </div>
            {Object.keys(wizardOut.proposed.skill_overrides || {}).length > 0 && (
              <div>
                <b className="text-emerald-900">Customized prompts:</b>{" "}
                <span className="text-xs">{Object.keys(wizardOut.proposed.skill_overrides).join(", ")}</span>
              </div>
            )}
          </div>
        )}
      </Card>

      {/* Skills */}
      <Card
        title={`🧩 Skills (${skills.filter((s) => s.enabled).length} of ${skills.length} enabled)`}
        subtitle="Each skill is a tool the agent can call (e.g. get_fx_rate) or a capability the platform exposes (e.g. dunning_email)."
        actions={
          <select value={filter} onChange={(e) => setFilter(e.target.value)}
                  className="text-xs border border-slate-300 rounded px-2 py-1 bg-white">
            <option value="all">All</option>
            <option value="enabled">Enabled only</option>
            <option value="disabled">Disabled only</option>
            <option value="customized">Customized only</option>
          </select>
        }
      >
        {filtered.length === 0 ? (
          <EmptyState icon="🧩" title="No skills match this filter"
                      hint="Switch the filter dropdown to see more." />
        ) : (
          Object.entries(byCategory).map(([cat, list]) => (
            <div key={cat} className="mb-4 last:mb-0">
              <div className="text-[11px] uppercase tracking-wider text-slate-500 mb-2 font-semibold">{cat}</div>
              <div className="space-y-2">
                {list.map((s) => {
                  const isOpen = !!expanded[s.id];
                  return (
                    <div key={s.id}
                         className={`border rounded-lg transition ${
                           s.enabled
                             ? "border-slate-200 bg-white"
                             : "border-slate-200 bg-slate-50 opacity-75"
                         }`}>
                      <div className="flex items-center justify-between p-3 gap-3">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-medium text-slate-900">{s.name}</span>
                            <Badge color={s.kind === "tool" ? "purple" : "blue"}>{s.kind}</Badge>
                            {s.is_overridden && <Badge color="amber">customized</Badge>}
                          </div>
                          <div className="text-xs text-slate-500 mt-0.5">
                            <code className="text-slate-400">{s.id}</code> — {s.description}
                          </div>
                        </div>
                        <div className="flex items-center gap-3 flex-shrink-0">
                          {s.kind === "tool" && Object.keys(profiles).length > 0 && (
                            <select
                              value={s.model_profile || "default"}
                              onChange={(e) => saveProfileFor(s.id, e.target.value)}
                              className="text-xs border border-slate-300 rounded px-1.5 py-0.5 bg-white"
                              title={`Model routing for this skill. Default: ${s.default_model_profile}`}
                            >
                              {Object.keys(profiles).map((p) => (
                                <option key={p} value={p}>{p}</option>
                              ))}
                            </select>
                          )}
                          <button
                            onClick={() => setExpanded((e) => ({ ...e, [s.id]: !isOpen }))}
                            className="text-xs text-indigo-700 hover:underline whitespace-nowrap"
                          >
                            {isOpen ? "Hide prompt" : "Edit prompt"}
                          </button>
                          <label className="inline-flex items-center cursor-pointer">
                            <input type="checkbox" checked={s.enabled}
                                   onChange={(e) => toggle(s.id, e.target.checked)}
                                   className="sr-only peer" />
                            <div className="w-10 h-5 bg-slate-300 rounded-full peer-checked:bg-emerald-500 relative transition">
                              <div className="absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full peer-checked:translate-x-5 transition" />
                            </div>
                          </label>
                        </div>
                      </div>
                      {isOpen && (
                        <div className="border-t border-slate-200 p-3 space-y-2 bg-slate-50 rounded-b-lg">
                          <label className="text-xs text-slate-600 font-medium">System prompt</label>
                          <textarea
                            value={drafts[s.id] ?? ""}
                            onChange={(e) => setDrafts((d) => ({ ...d, [s.id]: e.target.value }))}
                            rows={5}
                            className="w-full border border-slate-300 rounded p-2 text-xs font-mono bg-white"
                          />
                          <div className="flex gap-2">
                            <button onClick={() => savePrompt(s.id)}
                              className="px-3 py-1 rounded bg-indigo-600 text-white text-xs hover:bg-indigo-700">
                              Save
                            </button>
                            <button onClick={() => resetPrompt(s.id, s.default_system_prompt)}
                              className="px-3 py-1 rounded border border-slate-300 text-xs hover:bg-slate-100">
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
          ))
        )}
      </Card>

      {/* Raw config (hidden by default) */}
      {config && (
        <Card>
          <details>
            <summary className="cursor-pointer text-sm text-slate-500 hover:text-slate-700">
              🔧 Show raw config JSON (advanced)
            </summary>
            <pre className="text-[11px] bg-slate-900 text-slate-100 p-3 rounded overflow-x-auto mt-2">
              {JSON.stringify(config, null, 2)}
            </pre>
          </details>
        </Card>
      )}
    </Page>
  );
}
