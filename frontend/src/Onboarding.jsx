import React, { useState, useEffect } from "react";
import { apiFetch as fetch, pushToast } from "./Toast.jsx";
import { markOnboarded, getEmail } from "./Account.jsx";

const EXAMPLE_PROFILES = [
  {
    name: "Malaysian SME exporting to Asia",
    text: "We're a B2B exporter in Penang invoicing customers in USD, SGD, "
        + "EUR, JPY, CNY and getting paid into a Maybank MYR account. We "
        + "want strict matching after FX + bank fees, soft matches for known "
        + "payer aliases (e.g. when payments come from a holding company "
        + "instead of the invoiced entity), SWIFT route tracing for any "
        + "shortfall over 5%, and multilingual dunning emails — English for "
        + "most, Chinese for CN/HK clients.",
  },
  {
    name: "Singapore freelancer",
    text: "I'm a solo consultant invoicing US and EU clients in USD and "
        + "EUR, paid into a DBS SGD account via Wise. I mostly need clean "
        + "reconciliation + chase-payment emails. No campaigns, no SWIFT "
        + "complexity. Keep prompts terse and professional.",
  },
  {
    name: "Multi-entity holding group",
    text: "We have entities in Malaysia, Singapore, and Vietnam. Each entity "
        + "files its own books. Need strict per-tenant isolation, audit packs "
        + "for every match, and the workflow campaign for overdue invoices "
        + "with a 4-stage escalation. FX rates must be sourced from ECB only "
        + "— don't accept static fallbacks for strict matches.",
  },
];


export default function Onboarding({ onDone }) {
  const [step, setStep] = useState(0);
  const [profile, setProfile] = useState("");
  const [proposed, setProposed] = useState(null);
  const [busy, setBusy] = useState(false);
  // Elapsed-seconds counter — wizard call hits a reasoning model (30-60s
  // typical). Without a visible counter the user assumes the button is
  // stuck. Same pattern as DunningModal.
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!busy) return;
    setElapsed(0);
    const startedAt = Date.now();
    const t = setInterval(() => setElapsed(Math.floor((Date.now() - startedAt) / 1000)), 1000);
    return () => clearInterval(t);
  }, [busy]);
  const email = getEmail();

  async function runWizard() {
    if (profile.trim().length < 30) {
      pushToast({ kind: "warn", title: "Add a bit more detail",
                  message: "Describe your business in a sentence or two so the AI can tune the agent for you." });
      return;
    }
    setBusy(true);
    try {
      const r = await fetch("/api/platform/wizard", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ business_profile: profile, apply: false }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || "wizard failed");
      setProposed(j.proposed);
      setStep(2);
    } catch (e) {
      pushToast({ kind: "error", title: "Wizard failed",
                  message: String(e?.message || e) });
    }
    setBusy(false);
  }

  async function accept() {
    setBusy(true);
    try {
      const r = await fetch("/api/platform/wizard", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ business_profile: profile, apply: true, proposed }),
      });
      if (!r.ok) throw new Error(await r.text());
      markOnboarded();
      pushToast({ kind: "ok", title: "Agent configured",
                  message: "Your treasury AI is ready." });
      onDone?.();
    } catch (e) {
      pushToast({ kind: "error", title: "Could not save config",
                  message: String(e?.message || e) });
    }
    setBusy(false);
  }

  function skip() {
    markOnboarded();
    pushToast({ kind: "ok", title: "Skipped",
                message: "Using default agent config. You can edit it under Settings." });
    onDone?.();
  }

  // ----- step UI helpers -----
  const StepIndicator = () => (
    <div className="flex items-center justify-center gap-3 mb-8">
      {["Welcome", "Describe", "Confirm"].map((label, i) => (
        <React.Fragment key={label}>
          <div className="flex items-center gap-2">
            <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold border-2 ${
              i < step ? "bg-emerald-500 border-emerald-500 text-white"
              : i === step ? "bg-indigo-600 border-indigo-600 text-white"
              : "bg-white border-slate-300 text-slate-400"
            }`}>{i < step ? "✓" : i + 1}</div>
            <div className={`text-sm font-medium ${
              i === step ? "text-indigo-700" :
              i < step ? "text-emerald-700" : "text-slate-400"
            }`}>{label}</div>
          </div>
          {i < 2 && <div className={`w-10 h-0.5 ${i < step ? "bg-emerald-400" : "bg-slate-200"}`} />}
        </React.Fragment>
      ))}
    </div>
  );

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 via-indigo-50 to-purple-50 flex items-center justify-center p-6">
      <div className="bg-white rounded-2xl shadow-2xl max-w-2xl w-full p-8">
        <StepIndicator />

        {step === 0 && (
          <div>
            <h1 className="text-2xl font-bold text-slate-900 mb-1">
              Welcome{email ? `, ${email.split("@")[0]}` : ""} 👋
            </h1>
            <p className="text-slate-600 mb-6">
              Let's build the treasury agent that fits <em>your</em> business.
              It takes about a minute — describe what you do, and the AI
              configures the right skills, prompts, and matching policy for you.
            </p>
            <div className="bg-slate-50 border border-slate-200 rounded-lg p-4 mb-6 space-y-3 text-sm">
              <div className="font-medium text-slate-800">What you'll get:</div>
              <ul className="space-y-1.5 text-slate-700">
                <li>✅ A private workspace scoped to your account</li>
                <li>✅ An AI-tuned reconciliation agent with provenance + verifier</li>
                <li>✅ Multilingual dunning workflows you can edit anytime</li>
                <li>✅ Live evaluation harness to measure agent accuracy over time</li>
              </ul>
            </div>
            <div className="flex justify-between items-center">
              <button onClick={skip}
                className="text-sm text-slate-500 hover:text-slate-700 underline">
                Skip — use defaults
              </button>
              <button onClick={() => setStep(1)}
                className="bg-indigo-600 text-white px-6 py-2.5 rounded-lg font-medium hover:bg-indigo-700 shadow">
                Get started →
              </button>
            </div>
          </div>
        )}

        {step === 1 && (
          <div>
            <h1 className="text-2xl font-bold text-slate-900 mb-1">
              Tell us about your business
            </h1>
            <p className="text-slate-600 mb-4">
              Describe what you do, who pays you, in which currencies, and
              what reconciliation rules matter to you. The AI will translate
              this into a tuned agent.
            </p>

            <textarea
              value={profile}
              onChange={(e) => setProfile(e.target.value)}
              placeholder="e.g. We're a Malaysian SME exporting to Asia. Customers in USD/SGD/EUR pay into our Maybank MYR account. We need strict matching after FX + fees, multilingual dunning for late payers, and audit packs for our auditors."
              rows={7}
              className="w-full border border-slate-300 rounded-lg p-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
            <div className="text-[11px] text-slate-400 mt-1 mb-3">
              {profile.length} chars · {profile.length < 30 ? "needs more detail" : "looks good"}
            </div>

            <details className="mb-4">
              <summary className="text-sm text-indigo-700 hover:text-indigo-900 cursor-pointer">
                💡 Need inspiration? See example profiles
              </summary>
              <div className="mt-2 space-y-2">
                {EXAMPLE_PROFILES.map((ex, i) => (
                  <div key={i} className="bg-indigo-50 border border-indigo-200 rounded p-2">
                    <div className="font-medium text-xs text-indigo-900">{ex.name}</div>
                    <div className="text-xs text-indigo-800 mt-1">{ex.text}</div>
                    <button onClick={() => setProfile(ex.text)}
                      className="text-[11px] text-indigo-600 hover:text-indigo-800 underline mt-1">
                      use this →
                    </button>
                  </div>
                ))}
              </div>
            </details>

            <div className="flex justify-between items-center">
              <button onClick={() => setStep(0)}
                className="text-sm text-slate-500 hover:text-slate-700">
                ← Back
              </button>
              <button onClick={runWizard} disabled={busy || profile.trim().length < 30}
                className="bg-indigo-600 text-white px-6 py-2.5 rounded-lg font-medium hover:bg-indigo-700 disabled:opacity-50 shadow">
                {busy
                  ? `AI is thinking… (${elapsed}s — reasoning model, normally 30–60s)`
                  : "Configure my agent →"}
              </button>
            </div>
          </div>
        )}

        {step === 2 && proposed && (
          <div>
            <h1 className="text-2xl font-bold text-slate-900 mb-1">
              Here's your tuned agent
            </h1>
            <p className="text-slate-600 mb-4">
              Review what the AI recommends, then accept or adjust later under Settings.
            </p>

            {proposed.rationale && (
              <div className="bg-emerald-50 border border-emerald-200 rounded p-3 mb-4 text-sm text-emerald-900">
                <div className="font-medium mb-1">🎯 AI rationale</div>
                {proposed.rationale}
              </div>
            )}

            <div className="bg-slate-50 border border-slate-200 rounded p-3 mb-3">
              <div className="font-medium text-sm text-slate-800 mb-2">
                Enabled skills ({proposed.enabled_skills?.length || 0})
              </div>
              <div className="flex flex-wrap gap-1">
                {(proposed.enabled_skills || []).map((s) => (
                  <span key={s} className="bg-indigo-100 text-indigo-800 px-2 py-0.5 rounded text-xs font-mono">
                    {s}
                  </span>
                ))}
              </div>
            </div>

            {Object.keys(proposed.skill_overrides || {}).length > 0 && (
              <div className="bg-slate-50 border border-slate-200 rounded p-3 mb-4">
                <div className="font-medium text-sm text-slate-800 mb-2">
                  Customized prompts ({Object.keys(proposed.skill_overrides).length})
                </div>
                <ul className="text-xs space-y-1">
                  {Object.keys(proposed.skill_overrides).map((sid) => (
                    <li key={sid}>
                      <span className="font-mono text-indigo-700">{sid}</span>
                      <span className="text-slate-500"> — prompt tuned for your business</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div className="flex justify-between items-center">
              <button onClick={() => setStep(1)}
                className="text-sm text-slate-500 hover:text-slate-700">
                ← Edit profile
              </button>
              <button onClick={accept} disabled={busy}
                className="bg-emerald-600 text-white px-6 py-2.5 rounded-lg font-medium hover:bg-emerald-700 disabled:opacity-50 shadow">
                {busy ? "Saving…" : "✓ Looks good — start using →"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
