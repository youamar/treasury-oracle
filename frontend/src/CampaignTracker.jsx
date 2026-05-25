import React, { useEffect, useState } from "react";

const STATUS_COLORS = {
  active: "bg-amber-100 text-amber-800",
  paid: "bg-green-100 text-green-800",
  exhausted: "bg-red-100 text-red-800",
};

export default function CampaignTracker({ seedProof }) {
  const [campaigns, setCampaigns] = useState([]);
  const [expanded, setExpanded] = useState(null);

  async function refresh() {
    const r = await fetch("/api/campaign").then(r => r.json());
    setCampaigns(r.campaigns || []);
  }
  useEffect(() => { refresh(); }, []);

  async function start() {
    if (!seedProof) return;
    const shortfall = +(seedProof.amount * 0.1).toFixed(2);
    await fetch("/api/campaign", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        client_name: seedProof.payer || "Client",
        invoice_ref: seedProof.reference || "INV-???",
        invoice_amount: seedProof.amount,
        invoice_ccy: seedProof.currency,
        outstanding: shortfall,
      }),
    });
    refresh();
  }
  async function advance(cid) {
    await fetch(`/api/campaign/${cid}/advance`, { method: "POST" });
    refresh();
  }
  async function paid(cid) {
    await fetch(`/api/campaign/${cid}/paid`, { method: "POST" });
    refresh();
  }

  return (
    <div className="bg-white p-5 rounded-xl shadow">
      <div className="flex justify-between items-center mb-3">
        <h3 className="font-semibold">📣 Dunning Escalation Campaigns</h3>
        {seedProof && (
          <button onClick={start} className="text-xs bg-indigo-600 text-white px-3 py-1 rounded hover:bg-indigo-700">
            + Start campaign from discrepancy
          </button>
        )}
      </div>

      {!campaigns.length && (
        <div className="text-xs text-slate-400 italic">No campaigns. Trigger from a discrepancy below.</div>
      )}

      <ul className="space-y-2">
        {campaigns.map(c => {
          const stage = c.history[c.history.length - 1];
          return (
            <li key={c.id} className="border rounded p-3">
              <div className="flex justify-between items-start gap-2">
                <div className="flex-1">
                  <div className="font-semibold">
                    {c.client_name} · {c.invoice_ref}
                    <span className={`ml-2 px-2 py-0.5 rounded text-[10px] ${STATUS_COLORS[c.status]}`}>
                      {c.status}
                    </span>
                  </div>
                  <div className="text-xs text-slate-500">
                    Outstanding {c.outstanding} {c.invoice_ccy} · Stage {c.current_stage + 1}/4 ·
                    Day {stage?.stage_day} · {stage?.language}
                  </div>
                </div>
                <div className="flex gap-1">
                  <button onClick={() => setExpanded(expanded === c.id ? null : c.id)}
                          className="text-xs px-2 py-1 bg-slate-200 rounded hover:bg-slate-300">
                    {expanded === c.id ? "Hide" : "View"}
                  </button>
                  {c.status === "active" && (
                    <>
                      <button onClick={() => advance(c.id)}
                              className="text-xs px-2 py-1 bg-amber-500 text-white rounded hover:bg-amber-600">
                        Escalate →
                      </button>
                      <button onClick={() => paid(c.id)}
                              className="text-xs px-2 py-1 bg-green-600 text-white rounded hover:bg-green-700">
                        ✓ Paid
                      </button>
                    </>
                  )}
                </div>
              </div>
              {/* stage dots */}
              <div className="flex gap-1 mt-2">
                {[0,1,2,3].map(i => (
                  <div key={i} className={`h-1.5 flex-1 rounded ${
                    i < c.current_stage ? "bg-red-500" :
                    i === c.current_stage ? "bg-amber-400" : "bg-slate-200"
                  }`} />
                ))}
              </div>
              {expanded === c.id && (
                <div className="mt-3 space-y-2">
                  {c.history.map((h, i) => (
                    <div key={i} className={`p-2 rounded text-sm ${
                      h.sent ? "bg-slate-100" : "bg-amber-50 border border-amber-200"
                    }`}>
                      <div className="text-[10px] uppercase text-slate-500">
                        Day {h.stage_day} · {h.language} · {h.sent ? "sent" : "draft"}
                      </div>
                      <div className="font-semibold">{h.subject}</div>
                      <pre className="whitespace-pre-wrap text-xs font-sans mt-1">{h.body}</pre>
                    </div>
                  ))}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
