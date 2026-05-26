import React, { useState } from "react";
import { apiFetch as fetch } from "./Toast.jsx";

export default function SalesValidator() {
  const [form, setForm] = useState({ amount: "", currency: "$", date: "", payer: "", reference: "" });
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);

  function set(k, v) { setForm(f => ({...f, [k]: v})); }

  async function check() {
    setLoading(true);
    const r = await fetch("/api/sales/validate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...form, amount: form.amount ? +form.amount : null }),
    }).then(r => r.json());
    setResult(r);
    setLoading(false);
  }

  const color = result?.verdict === "accept" ? "green" : "red";

  return (
    <div className="bg-white p-4 rounded-xl shadow border border-slate-200">
      <h3 className="font-semibold text-sm mb-2">🛡️ Sales Submission Validator</h3>
      <div className="text-[11px] text-slate-500 mb-2">
        Pre-flight check for sales team. Reject before it pollutes the books.
      </div>
      <div className="grid grid-cols-2 gap-2 text-sm">
        <input placeholder="amount" value={form.amount} onChange={e=>set("amount", e.target.value)}
               className="border rounded p-1.5" />
        <input placeholder="currency (USD/EUR/...)" value={form.currency} onChange={e=>set("currency", e.target.value)}
               className="border rounded p-1.5" />
        <input placeholder="date YYYY-MM-DD" value={form.date} onChange={e=>set("date", e.target.value)}
               className="border rounded p-1.5" />
        <input placeholder="payer" value={form.payer} onChange={e=>set("payer", e.target.value)}
               className="border rounded p-1.5" />
        <input placeholder="invoice ref" value={form.reference} onChange={e=>set("reference", e.target.value)}
               className="border rounded p-1.5 col-span-2" />
      </div>
      <button onClick={check} disabled={loading}
              className="mt-2 w-full bg-slate-700 text-white py-1.5 rounded hover:bg-slate-800 disabled:opacity-40">
        {loading ? "Auditing..." : "Validate submission"}
      </button>
      {result && (
        <div className={`mt-2 p-2 rounded text-sm bg-${color}-50 border border-${color}-200`}>
          <div className={`font-bold text-${color}-800`}>
            {result.verdict === "accept" ? "✓ Accepted" : "✗ Rejected"} ({result.severity})
          </div>
          {result.issues?.length > 0 && (
            <ul className="list-disc ml-4 text-xs text-red-700 mt-1">
              {result.issues.map((s,i)=><li key={i}>{s}</li>)}
            </ul>
          )}
          <div className="text-xs italic mt-1">💬 {result.message_to_sales}</div>
        </div>
      )}
    </div>
  );
}
