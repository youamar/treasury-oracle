import React, { useState } from "react";

export default function DunningModal({ proof, expected, actual, localCcy, onClose }) {
  const [email, setEmail] = useState(null);
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);

  async function draft() {
    setLoading(true);
    const shortfall = +(proof.amount * (1 - actual / expected)).toFixed(2);
    const r = await fetch("/api/dunning", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        client_name: proof.payer || "Valued Client",
        invoice_ref: proof.reference || "",
        invoice_amount: proof.amount,
        invoice_ccy: proof.currency,
        received_local: actual,
        local_ccy: localCcy,
        shortfall_invoice: shortfall,
      }),
    });
    setEmail(await r.json());
    setLoading(false);
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
                  className="w-full bg-indigo-600 text-white py-2 rounded hover:bg-indigo-700">
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
