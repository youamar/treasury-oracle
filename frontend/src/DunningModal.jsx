import React, { useState } from "react";
import { apiFetch as fetch, pushToast } from "./Toast.jsx";

export default function DunningModal({ proof, expected, actual, localCcy, onClose }) {
  const [email, setEmail] = useState(null);
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);

  async function draft() {
    setLoading(true);
    try {
      // Guard against NaN — agent 'no_match' discrepancies often have null
      // expected_net / actual, in which case the dunning is about the
      // *whole* invoice rather than a percentage shortfall.
      let shortfall = 0;
      const exp = Number(expected), act = Number(actual), amt = Number(proof.amount);
      if (isFinite(exp) && isFinite(act) && exp > 0 && isFinite(amt)) {
        shortfall = +(amt * (1 - act / exp)).toFixed(2);
      } else if (isFinite(amt)) {
        shortfall = amt;  // nothing landed — full invoice is outstanding
      }

      const r = await fetch("/api/dunning", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          client_name: proof.payer || "Valued Client",
          invoice_ref: proof.reference || "",
          invoice_amount: isFinite(amt) ? amt : 0,
          invoice_ccy: proof.currency || "USD",
          received_local: isFinite(act) ? act : 0,
          local_ccy: localCcy || "MYR",
          shortfall_invoice: shortfall,
        }),
      });
      if (!r.ok) {
        const detail = await r.text();
        throw new Error(`${r.status}: ${detail.slice(0, 200)}`);
      }
      const j = await r.json();
      // Backend may return a fallback email with `error` set as metadata —
      // still show it, just warn the user it's the offline template.
      if (j.body && j.subject) {
        setEmail(j);
        if (j.fallback) {
          pushToast({
            kind: "warn", title: "Used offline template",
            message: "LLM unavailable; showing canned dunning copy.",
          });
        }
      } else {
        throw new Error(j.error || "no email body returned");
      }
    } catch (e) {
      pushToast({
        kind: "error",
        title: "Dunning draft failed",
        message: String(e?.message || e),
      });
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl max-w-2xl w-full p-6 shadow-2xl">
        <div className="flex justify-between items-center mb-3">
          <h3 className="text-lg font-bold">🗣️ Auto-Dunning Email</h3>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-800">✕</button>
        </div>
        <div className="text-xs text-slate-500 mb-3">
          For {proof.payer} · Invoice {proof.reference} · {proof.amount} {proof.currency}
        </div>

        {!email ? (
          <button onClick={draft} disabled={loading}
                  className="w-full bg-indigo-600 text-white py-2 rounded hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed">
            {loading ? "Drafting in payer's language..." : "✍️ Draft email with AI"}
          </button>
        ) : (
          <>
            <div className="bg-slate-50 border rounded p-3">
              <div className="text-xs text-slate-500">Language: {email.language}</div>
              <div className="font-semibold mt-1">{email.subject}</div>
              <pre className="whitespace-pre-wrap text-sm mt-2 font-sans">{email.body}</pre>
            </div>
            {sent ? (
              <div className="mt-3 text-green-700 text-sm">✓ Sent (simulated)</div>
            ) : (
              <div className="mt-3 flex gap-2">
                <button onClick={() => setSent(true)}
                        className="bg-emerald-600 text-white px-4 py-2 rounded hover:bg-emerald-700">
                  Send
                </button>
                <button onClick={() => setEmail(null)}
                        className="bg-slate-200 px-4 py-2 rounded hover:bg-slate-300">
                  Regenerate
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
