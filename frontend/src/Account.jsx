import React, { useState } from "react";
import { pushToast } from "./Toast.jsx";

// localStorage keys
const LS_TENANT = "to_tenant_id";
const LS_EMAIL  = "to_email";
const LS_ONBOARDED = "to_onboarded";

export function getTenant() {
  return localStorage.getItem(LS_TENANT) || null;
}
export function getEmail() {
  return localStorage.getItem(LS_EMAIL) || null;
}
export function isOnboarded() {
  return localStorage.getItem(LS_ONBOARDED) === "1";
}
export function markOnboarded() {
  localStorage.setItem(LS_ONBOARDED, "1");
  window.dispatchEvent(new Event("to-account-changed"));
}
export function signOut() {
  localStorage.removeItem(LS_TENANT);
  localStorage.removeItem(LS_EMAIL);
  localStorage.removeItem(LS_ONBOARDED);
  window.dispatchEvent(new Event("to-account-changed"));
}

/** Subscribe to account changes (sign-in, sign-out, onboarded). */
export function onAccountChange(cb) {
  window.addEventListener("to-account-changed", cb);
  return () => window.removeEventListener("to-account-changed", cb);
}


/** Login / sign-up screen. Email-only — backend creates a tenant scoped to
 *  the email's slug. No password yet; this is the demo path. */
export default function Account({ onAuthed }) {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);

  function tenantIdFromEmail(e) {
    // Slug the email so tenant ids are URL-safe + readable.
    return e.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  }

  async function submit(e) {
    e?.preventDefault();
    const trimmed = email.trim();
    if (!trimmed || !/.+@.+\..+/.test(trimmed)) {
      pushToast({ kind: "error", title: "Invalid email",
                  message: "Enter a real email address." });
      return;
    }
    setBusy(true);
    const tenant = tenantIdFromEmail(trimmed);
    try {
      // Upsert tenant on the backend. Header is sent so the new tenant
      // is also scoped correctly from the start.
      const r = await fetch("/api/memory/tenants", {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-tenant-id": tenant },
        body: JSON.stringify({ id: tenant, name: trimmed.split("@")[0] }),
      });
      if (!r.ok) throw new Error(await r.text());
      localStorage.setItem(LS_TENANT, tenant);
      localStorage.setItem(LS_EMAIL, trimmed);
      window.dispatchEvent(new Event("to-account-changed"));
      pushToast({ kind: "ok", title: "Signed in",
                  message: `Welcome, ${trimmed}` });
      onAuthed?.(tenant);
    } catch (err) {
      pushToast({ kind: "error", title: "Sign-in failed",
                  message: String(err?.message || err) });
    }
    setBusy(false);
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 via-indigo-50 to-purple-50 flex items-center justify-center p-6">
      <div className="bg-white rounded-2xl shadow-2xl max-w-md w-full p-8">
        <div className="text-center mb-6">
          <div className="text-5xl mb-2">🌍</div>
          <h1 className="text-2xl font-bold text-slate-900">Treasury Oracle</h1>
          <div className="text-sm text-slate-500 mt-1">
            Build your own treasury AI agent
          </div>
        </div>

        <form onSubmit={submit} className="space-y-3">
          <label className="block">
            <span className="text-sm font-medium text-slate-700">Email</span>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
              autoFocus
              className="mt-1 w-full border border-slate-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              required
            />
          </label>
          <button
            type="submit"
            disabled={busy}
            className="w-full bg-indigo-600 text-white py-2.5 rounded-lg font-medium hover:bg-indigo-700 disabled:opacity-50 shadow"
          >
            {busy ? "Signing you in…" : "Sign in / Sign up →"}
          </button>
        </form>

        <div className="mt-6 text-xs text-slate-500 text-center leading-relaxed">
          No password needed for the hackathon demo. Your email maps to a
          private tenant; every reconciliation, calibration, and learned
          fact is scoped to your account.
        </div>

        <div className="mt-4 pt-4 border-t border-slate-200">
          <div className="text-[11px] text-slate-400 text-center">
            Already a user with a different email?{" "}
            <button onClick={signOut}
              className="text-indigo-600 hover:underline">
              Clear local state
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
