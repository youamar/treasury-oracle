import React, { useEffect, useState } from "react";
import { apiFetch as fetch, pushToast } from "./Toast.jsx";
import Page, { Card, Badge, EmptyState } from "./Page.jsx";

const API = "/api";


function BanksEditor() {
  const [banks, setBanks] = useState([]);
  const [draftRow, setDraftRow] = useState(null);

  async function load() {
    const r = await fetch(`${API}/banks`);
    if (!r.ok) return;
    setBanks((await r.json()).banks || []);
  }
  useEffect(() => { load(); }, []);

  async function save(b) {
    try {
      const r = await fetch(`${API}/banks/${encodeURIComponent(b.id)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(b),
      });
      if (!r.ok) throw new Error(await r.text());
      await load();
      setDraftRow(null);
      pushToast({ kind: "ok", title: "Bank saved", message: b.name || b.id });
    } catch (e) {
      pushToast({ kind: "error", title: "Save failed", message: String(e?.message || e) });
    }
  }

  async function del(id) {
    if (!confirm(`Delete bank "${id}"?`)) return;
    await fetch(`${API}/banks/${encodeURIComponent(id)}`, { method: "DELETE" });
    await load();
    pushToast({ kind: "ok", title: "Bank deleted" });
  }

  return (
    <Card
      title="🏦 Banks"
      subtitle="Customer-editable bank registry. Each row drives the bank dropdown, the inbound-fee math, and optional per-bank match tolerance."
      actions={
        <button onClick={() => setDraftRow({ id: "", name: "", inbound_fee_pct: 0.005,
                                             match_tolerance: null, currency: "MYR",
                                             swift_bic: "", notes: "" })}
                className="text-xs px-3 py-1.5 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700">
          + New bank
        </button>
      }
    >
      <div className="overflow-x-auto -mx-5 px-5">
        <table className="w-full text-sm">
          <thead className="text-xs uppercase text-slate-500">
            <tr>
              <th className="text-left py-1">ID</th>
              <th className="text-left">Name</th>
              <th className="text-right">Fee %</th>
              <th className="text-right">Match tol.</th>
              <th className="text-left">Currency</th>
              <th className="text-left">BIC</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {banks.map((b) => (
              <tr key={b.id} className="border-t border-slate-100">
                <td className="py-2 font-mono text-xs">{b.id}</td>
                <td className="py-2">{b.name}</td>
                <td className="py-2 text-right tabular-nums">{(b.inbound_fee_pct * 100).toFixed(2)}%</td>
                <td className="py-2 text-right tabular-nums text-slate-500">
                  {b.match_tolerance != null ? `${(b.match_tolerance*100).toFixed(2)}%` : "—"}
                </td>
                <td className="py-2 text-xs">{b.currency || "—"}</td>
                <td className="py-2 font-mono text-[10px]">{b.swift_bic || "—"}</td>
                <td className="text-right py-2 space-x-2">
                  <button onClick={() => setDraftRow({ ...b })}
                          className="text-xs text-indigo-700 hover:underline">edit</button>
                  <button onClick={() => del(b.id)}
                          className="text-xs text-red-600 hover:underline">delete</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {draftRow && (
        <div className="mt-4 border-2 border-indigo-300 bg-indigo-50 rounded-lg p-3">
          <div className="font-medium text-sm mb-2">
            {banks.find(x => x.id === draftRow.id) ? "Edit" : "New"} bank
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-2 text-sm">
            {[
              ["id", "ID (slug)", "text"],
              ["name", "Display name", "text"],
              ["inbound_fee_pct", "Fee % (0.005 = 0.5%)", "number"],
              ["match_tolerance", "Match tolerance (override)", "number"],
              ["currency", "Currency", "text"],
              ["swift_bic", "SWIFT BIC", "text"],
            ].map(([k, label, type]) => (
              <label key={k} className="block">
                <span className="text-[11px] text-slate-600">{label}</span>
                <input type={type} step={type === "number" ? "0.001" : undefined}
                       value={draftRow[k] ?? ""}
                       onChange={(e) => setDraftRow({
                         ...draftRow,
                         [k]: type === "number"
                           ? (e.target.value === "" ? null : Number(e.target.value))
                           : e.target.value,
                       })}
                       className="mt-0.5 w-full border border-slate-300 rounded px-2 py-1 text-sm bg-white" />
              </label>
            ))}
          </div>
          <textarea
            value={draftRow.notes || ""}
            onChange={(e) => setDraftRow({ ...draftRow, notes: e.target.value })}
            placeholder="optional notes (e.g. 'check inbound fee schedule quarterly')"
            rows={2}
            className="mt-2 w-full border border-slate-300 rounded p-2 text-xs"
          />
          <div className="mt-2 flex gap-2 justify-end">
            <button onClick={() => setDraftRow(null)}
                    className="px-3 py-1 rounded border border-slate-300 text-sm hover:bg-slate-100">
              Cancel
            </button>
            <button onClick={() => save(draftRow)}
                    disabled={!draftRow.id || !(draftRow.inbound_fee_pct >= 0)}
                    className="px-3 py-1 rounded bg-indigo-600 text-white text-sm hover:bg-indigo-700 disabled:opacity-50">
              Save bank
            </button>
          </div>
        </div>
      )}
    </Card>
  );
}


function ProvidersPanel() {
  const [chain, setChain] = useState([]);
  const [busy, setBusy] = useState(false);

  async function load() {
    setBusy(true);
    try {
      const r = await fetch(`${API}/providers`);
      if (r.ok) setChain((await r.json()).chain || []);
    } finally { setBusy(false); }
  }
  useEffect(() => {
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, []);

  const stateColor = (s) => s === "open" ? "red" : s === "half_open" ? "amber" : "green";
  const stateLabel = (s) => s === "open" ? "tripped" : s === "half_open" ? "probing" : "healthy";

  return (
    <Card
      title="🧠 LLM Providers"
      subtitle="Failover chain. If the top provider trips its circuit breaker, the agent automatically falls through to the next one — narrative, dunning, and OCR keep working even when Chutes is down."
      actions={
        <button onClick={load} disabled={busy}
                className="text-xs px-3 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 dark:hover:bg-slate-700 disabled:opacity-50">
          {busy ? "refreshing…" : "refresh"}
        </button>
      }
    >
      {chain.length === 0 ? (
        <EmptyState>
          No providers configured. Set <code>CHUTES_API_KEY</code>, <code>OPENAI_API_KEY</code>, or <code>ANTHROPIC_API_KEY</code> in <code>.env</code>.
        </EmptyState>
      ) : (
        <ol className="space-y-2">
          {chain.map((p, i) => (
            <li key={p.name} className="flex items-center justify-between gap-3 p-3 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900">
              <div className="flex items-center gap-3 min-w-0">
                <div className="font-mono text-xs text-slate-400 w-6">#{i + 1}</div>
                <div className="min-w-0">
                  <div className="font-semibold text-slate-900 dark:text-slate-100">{p.name}</div>
                  {p.last_error && (
                    <div className="text-[11px] text-red-600 dark:text-red-400 truncate font-mono" title={p.last_error}>
                      last error: {p.last_error}
                    </div>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <Badge color={stateColor(p.breaker_state)}>{stateLabel(p.breaker_state)}</Badge>
                {p.failures > 0 && <Badge color="amber">{p.failures} fail</Badge>}
                {p.remaining_cooldown_seconds > 0 && (
                  <span className="text-[11px] text-slate-500">cools in {Math.round(p.remaining_cooldown_seconds)}s</span>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
      <div className="mt-3 text-[11px] text-slate-500 dark:text-slate-400">
        Order is left-to-right: the agent tries provider #1 first, then falls through. Reorder by setting <code>LLM_PROVIDER_CHAIN</code>.
      </div>
    </Card>
  );
}


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
  const [knobs, setKnobs] = useState(null);  // {defaults, current, default_base_prompt}
  const [knobDrafts, setKnobDrafts] = useState({});

  async function load() {
    setBusy(true);
    try {
      const [r, pr, kr] = await Promise.all([
        fetch(`${API}/platform/skills`),
        fetch(`${API}/platform/model-profiles`).catch(() => null),
        fetch(`${API}/platform/agent-knobs`).catch(() => null),
      ]);
      const j = await r.json();
      setSkills(j.skills || []);
      setConfig(j.config || null);
      setProfile(j.config?.business_profile || "");
      const next = {};
      (j.skills || []).forEach((s) => (next[s.id] = s.system_prompt));
      setDrafts(next);
      if (pr) setProfiles((await pr.json()).profiles || {});
      if (kr) {
        const k = await kr.json();
        setKnobs(k);
        setKnobDrafts({ ...k.defaults, ...k.current });
      }
    } catch (e) {
      pushToast({ kind: "error", title: "Failed to load settings",
                  message: String(e?.message || e) });
    }
    setBusy(false);
  }

  async function saveKnobs() {
    try {
      const r = await fetch(`${API}/platform/agent-knobs`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(knobDrafts),
      });
      if (!r.ok) throw new Error(await r.text());
      await load();
      pushToast({ kind: "ok", title: "Agent knobs saved",
                  message: "Changes take effect on the next reconcile." });
    } catch (e) {
      pushToast({ kind: "error", title: "Save failed",
                  message: String(e?.message || e) });
    }
  }

  async function resetKnobs() {
    if (!confirm("Reset every agent knob and base prompt to defaults?")) return;
    try {
      await fetch(`${API}/platform/agent-knobs/reset`, { method: "POST" });
      await load();
      pushToast({ kind: "ok", title: "Knobs reset" });
    } catch (e) {
      pushToast({ kind: "error", title: "Reset failed",
                  message: String(e?.message || e) });
    }
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

      {/* Agent knobs */}
      {knobs && (
        <Card
          title="🎛 Agent knobs"
          subtitle="Per-tenant overrides for the agent loop, reflection, verifier, and base prompt. Empty = use built-in default."
          actions={
            <button onClick={resetKnobs}
              className="text-xs px-3 py-1 rounded border border-slate-300 hover:bg-slate-50">
              Reset all
            </button>
          }
        >
          <div className="grid md:grid-cols-2 gap-3 text-sm">
            {[
              ["max_steps", "Max tool-loop steps", "int", "How many LLM↔tool cycles per proof before fallback."],
              ["match_tolerance", "Match tolerance", "pct", "Strict-match upper bound on diff % (after FX + fee)."],
              ["date_window_days", "Date window (days)", "int", "Candidate txns must be within ±N days of the proof."],
              ["agent_temperature", "Agent temperature", "float", "0 = deterministic. Eval forces 0 separately."],
              ["reflection_max_cycles", "Reflection cycles", "int", "How many re-plan loops the agent gets per proof."],
              ["reflection_confidence_threshold", "Reflection conf. threshold", "float", "Below this confidence, reflection nudges recall_facts."],
              ["verifier_strict_diff_pct", "Verifier diff ceiling", "pct", "Tighter than match_tolerance — strict must beat this."],
              ["verifier_strict_days_off", "Verifier max days off", "int", "Date gap above this auto-downgrades strict to soft."],
              ["verifier_min_tool_calls_for_strict", "Verifier min tool calls", "int", "Strict claim needs ≥N substantive tool calls."],
              ["verifier_llm_enabled", "LLM verifier enabled", "bool", "Enables the second-pass independent auditor."],
              ["verifier_model_profile", "Verifier model profile", "profile", "Which model profile the LLM verifier uses."],
            ].map(([key, label, type, hint]) => {
              const val = knobDrafts[key];
              const defaultVal = knobs.defaults[key];
              const isOverridden = (knobs.current || {})[key] !== undefined;
              const onChange = (v) => setKnobDrafts((d) => ({ ...d, [key]: v }));
              return (
                <div key={key} className={`border rounded-lg p-3 ${isOverridden ? "border-amber-300 bg-amber-50" : "border-slate-200"}`}>
                  <div className="flex justify-between items-center gap-2">
                    <label className="font-medium text-slate-800 text-xs">
                      {label}
                      {isOverridden && <Badge color="amber">overridden</Badge>}
                    </label>
                    {type === "bool" ? (
                      <input type="checkbox" checked={Boolean(val)}
                             onChange={(e) => onChange(e.target.checked)}
                             className="w-4 h-4" />
                    ) : type === "profile" ? (
                      <select value={val || "cheap"} onChange={(e) => onChange(e.target.value)}
                              className="text-xs border border-slate-300 rounded px-2 py-1 bg-white">
                        {Object.keys(profiles).length > 0
                          ? Object.keys(profiles).map((p) => <option key={p} value={p}>{p}</option>)
                          : ["default", "cheap", "strong"].map((p) => <option key={p} value={p}>{p}</option>)}
                      </select>
                    ) : (
                      <input type="number"
                             step={type === "float" || type === "pct" ? "0.01" : "1"}
                             value={val ?? ""}
                             onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
                             className="text-xs border border-slate-300 rounded px-2 py-1 w-24 text-right bg-white" />
                    )}
                  </div>
                  <div className="text-[10px] text-slate-500 mt-1">{hint}</div>
                  <div className="text-[10px] text-slate-400 mt-0.5 font-mono">
                    default: {String(defaultVal)}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Base prompt — own card-within-card because it needs a textarea */}
          <div className={`mt-4 border rounded-lg p-3 ${(knobs.current || {}).base_prompt ? "border-amber-300 bg-amber-50" : "border-slate-200"}`}>
            <div className="flex justify-between items-center gap-2 mb-1">
              <div>
                <label className="font-medium text-slate-800 text-sm">
                  🪄 Base agent prompt
                  {(knobs.current || {}).base_prompt && <Badge color="amber">customized</Badge>}
                </label>
                <div className="text-[11px] text-slate-500">
                  The highest-leverage prompt in the system. Leave empty to use the built-in.
                </div>
              </div>
              <button onClick={() => setKnobDrafts((d) => ({ ...d, base_prompt: knobs.default_base_prompt }))}
                      className="text-[11px] text-indigo-700 hover:underline">
                ↺ load default
              </button>
            </div>
            <textarea
              value={knobDrafts.base_prompt ?? ""}
              onChange={(e) => setKnobDrafts((d) => ({ ...d, base_prompt: e.target.value }))}
              rows={8}
              placeholder="(empty — uses built-in)"
              className="w-full border border-slate-300 rounded p-2 text-[11px] font-mono bg-white"
            />
          </div>

          <div className="mt-3 flex gap-2 justify-end">
            <button onClick={saveKnobs}
                    className="px-4 py-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 text-sm font-medium">
              Save knobs
            </button>
          </div>
        </Card>
      )}

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

      {/* LLM Providers */}
      <ProvidersPanel />

      {/* Banks */}
      <BanksEditor />

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
