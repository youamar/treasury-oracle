import React, { useState } from "react";

export default function BossDocumentary({ match }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [speaking, setSpeaking] = useState(false);

  async function run() {
    setLoading(true);
    const today = new Date().toISOString().slice(0,10);
    // Need rates: call boss-chart-style endpoint first? Use FX series quick.
    const bc = await fetch("/api/boss-chart", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        amount: match.proof.amount, from_ccy: match.proof.currency, to_ccy: match.txn?.currency || "MYR",
        invoice_date: match.proof.date, payment_date: match.txn?.date || today,
        actual_local: match.conversion?.actual_received || match.actual || 0,
      }),
    }).then(r => r.json());

    const r = await fetch("/api/boss-documentary", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        amount: match.proof.amount, from_ccy: match.proof.currency, to_ccy: match.txn?.currency || "MYR",
        invoice_date: match.proof.date, payment_date: match.txn?.date || today,
        rate_invoice: bc.rate_invoice_date, rate_payment: bc.rate_payment_date,
        diff_local: bc.diff_local,
      }),
    }).then(r => r.json());
    setData(r);
    setLoading(false);
  }

  function narrate() {
    if (!data) return;
    if (speaking) { window.speechSynthesis.cancel(); setSpeaking(false); return; }
    const text = `${data.title}. ${data.paragraphs.join(" ")}`;
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 0.92; u.pitch = 0.95; u.onend = () => setSpeaking(false);
    setSpeaking(true);
    window.speechSynthesis.speak(u);
  }

  if (!data) {
    return (
      <button onClick={run} disabled={loading}
              className="text-xs px-3 py-1 bg-purple-100 text-purple-800 rounded hover:bg-purple-200">
        {loading ? "Writing the documentary..." : "🎬 Boss Documentary Mode"}
      </button>
    );
  }

  return (
    <div className="mt-2 bg-gradient-to-br from-slate-900 to-purple-900 text-slate-100 rounded p-4 shadow-inner">
      <div className="flex justify-between items-center mb-2">
        <h4 className="font-bold text-lg">🎬 {data.title}</h4>
        <button onClick={narrate} className="text-xs bg-purple-600 px-3 py-1 rounded hover:bg-purple-500">
          {speaking ? "⏸ Stop narration" : "▶ Play voice-over"}
        </button>
      </div>
      {data.paragraphs.map((p, i) => (
        <p key={i} className="text-sm leading-relaxed mb-2 italic text-slate-200">{p}</p>
      ))}
      <div className="mt-3 bg-purple-800/40 p-2 rounded text-sm">
        <b>TL;DR for the boss:</b> {data.tldr_for_boss}
      </div>
    </div>
  );
}
