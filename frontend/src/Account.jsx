import React, { useState } from "react";
import { pushToast } from "./Toast.jsx";

// localStorage keys
const LS_TENANT = "to_tenant_id";
const LS_EMAIL  = "to_email";
const LS_TOKEN  = "to_token";
const LS_ONBOARDED = "to_onboarded";

export function getTenant() { return localStorage.getItem(LS_TENANT) || null; }
export function getEmail()  { return localStorage.getItem(LS_EMAIL)  || null; }
export function getToken()  { return localStorage.getItem(LS_TOKEN)  || null; }
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
  localStorage.removeItem(LS_TOKEN);
  localStorage.removeItem(LS_ONBOARDED);
  window.dispatchEvent(new Event("to-account-changed"));
}

export function onAccountChange(cb) {
  window.addEventListener("to-account-changed", cb);
  return () => window.removeEventListener("to-account-changed", cb);
}


/** Real-auth sign-in / sign-up screen. Single 'submit' tries login first;
 *  on 401, offers to register with the same credentials. Issues a signed
 *  bearer token that apiFetch sends on every subsequent request. */
export default function Account({ onAuthed }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mode, setMode] = useState("login");  // "login" | "register"
  const [busy, setBusy] = useState(false);

  function persist(result) {
    localStorage.setItem(LS_TENANT, result.tenant_id);
    localStorage.setItem(LS_EMAIL, result.email);
    localStorage.setItem(LS_TOKEN, result.token);
    window.dispatchEvent(new Event("to-account-changed"));
    onAuthed?.(result.tenant_id);
  }

  async function submit(e) {
    e?.preventDefault();
    const em = email.trim();
    if (!/.+@.+\..+/.test(em)) {
      pushToast({ kind: "error", title: "Invalid email",
                  message: "Enter a real email address." });
      return;
    }
    if (password.length < 8) {
      pushToast({ kind: "error", title: "Password too short",
                  message: "Use at least 8 characters." });
      return;
    }
    setBusy(true);
    try {
      const r = await fetch(`/api/auth/${mode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: em, password }),
      });
      const j = await r.json();
      if (!r.ok) {
        // Helpful nudge: failed login often means "you haven't registered yet".
        if (mode === "login" && r.status === 401) {
          pushToast({
            kind: "warn", title: "Login failed",
            message: "No account yet? Switch to Sign up below.",
          });
        } else {
          pushToast({ kind: "error", title: "Auth failed",
                      message: j.detail || `HTTP ${r.status}` });
        }
        setBusy(false);
        return;
      }
      persist(j);
      pushToast({
        kind: "ok",
        title: mode === "register" ? "Account created" : "Signed in",
        message: `Welcome, ${j.email}`,
      });
    } catch (err) {
      pushToast({ kind: "error", title: "Network error",
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

        {/* mode toggle */}
        <div className="flex bg-slate-100 rounded-lg p-1 mb-4 text-sm">
          {["login", "register"].map((m) => (
            <button key={m} type="button" onClick={() => setMode(m)}
              className={`flex-1 py-1.5 rounded transition ${
                mode === m ? "bg-white text-indigo-700 font-medium shadow"
                           : "text-slate-500 hover:text-slate-700"}`}>
              {m === "login" ? "Sign in" : "Sign up"}
            </button>
          ))}
        </div>

        <form onSubmit={submit} className="space-y-3">
          <label className="block">
            <span className="text-sm font-medium text-slate-700">Email</span>
            <input
              type="email" value={email} onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com" autoFocus required
              className="mt-1 w-full border border-slate-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </label>
          <label className="block">
            <span className="text-sm font-medium text-slate-700">Password</span>
            <input
              type="password" value={password} onChange={(e) => setPassword(e.target.value)}
              placeholder="at least 8 characters" required
              className="mt-1 w-full border border-slate-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
            <span className="text-[11px] text-slate-400 mt-0.5 inline-block">
              Hashed with bcrypt server-side. Never stored in plaintext.
            </span>
          </label>
          <button type="submit" disabled={busy}
            className="w-full bg-indigo-600 text-white py-2.5 rounded-lg font-medium hover:bg-indigo-700 disabled:opacity-50 shadow">
            {busy ? (mode === "register" ? "Creating account…" : "Signing in…")
                  : (mode === "register" ? "Sign up →" : "Sign in →")}
          </button>
        </form>

        <div className="mt-6 text-xs text-slate-500 text-center leading-relaxed">
          Your email maps to a private tenant. Every reconciliation,
          calibration, learned fact, and account note is scoped to your
          login — bearer tokens are Ed25519-signed by the same key that
          attests our audit packs.
        </div>
      </div>
    </div>
  );
}
