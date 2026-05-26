import React, { useEffect, useState } from "react";
import { apiFetch as fetch } from "./Toast.jsx";

const STATUS_COLORS = {
  active: "bg-amber-100 text-amber-800",
  paid: "bg-green-100 text-green-800",
  exhausted: "bg-red-100 text-red-800",
};

export default function CampaignTracker({ seedProof }) {
  const [campaigns, setCampaigns] = useState([]);
  const [expanded, setExpanded] = useState(null);
  const [workflows, setWorkflows] = useState({}); // cid -> snapshot

  async function refresh() {
    const r = await fetch("/api/campaign").then(r => r.json());
    setCampaigns(r.campaigns || []);
    // Refresh known workflow snapshots
    const next = { ...workflows };
    await Promise.all((r.campaigns || []).map(async (c) => {
      try {
        const s = await fetch(`/api/campaign/${c.id}/workflow/state`);
        if (s.ok) next[c.id] = await s.json();
      } catch {}
    }));
    setWorkflows(next);
  }
  useEffect(() => { refresh(); }, []);

  async function startWorkflow(cid) {
    const r = await fetch(`/api/campaign/${cid}/workflow/start`, { method: "POST" });
    if (r.ok) {
      const snap = await r.json();
      setWorkflows((w) => ({ ...w, [cid]: snap }));
    }
    refresh();
  }
  async function tickWorkflow(cid) {
    await fetch(`/api/campaign/${cid}/workflow/tick`, { method: "POST" });
    refresh();
  }
  async function stopWorkflow(cid) {
    await fetch(`/api/campaign/${cid}/workflow/stop`, { method: "POST" });
    refresh();
  }
  async function recoverWorkflow(cid) {
    await fetch(`/api/campaign/${cid}/workflow/recover`, { method: "POST" });
    refresh();
  }

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
                  {workflows[c.id] ? (
                    <>
                      {!workflows[c.id].done && (
                        <button onClick={() => tickWorkflow(c.id)}
                                className="text-xs px-2 py-1 bg-purple-600 text-white rounded hover:bg-purple-700">
                          ⏵ Tick
                        </button>
                      )}
                      <button onClick={() => stopWorkflow(c.id)}
                              className="text-xs px-2 py-1 bg-slate-500 text-white rounded hover:bg-slate-600">
                        ⏹ Stop
                      </button>
                    </>
                  ) : (
                    c.status === "active" && (
                      <button onClick={() => startWorkflow(c.id)}
                              className="text-xs px-2 py-1 bg-purple-600 text-white rounded hover:bg-purple-700"
                              title="Autonomous LangGraph workflow with SQLite checkpointer">
                        🤖 Auto-run
                      </button>
                    )
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
              {workflows[c.id] && (
                <div className={`mt-2 border rounded p-2 text-[11px] ${
                  workflows[c.id].status === "error"
                    ? "bg-red-50 border-red-300"
                    : "bg-purple-50 border-purple-200"
                }`}>
                  <div className="flex justify-between items-center">
                    <span className={`font-semibold ${workflows[c.id].status === "error" ? "text-red-800" : "text-purple-800"}`}>
                      🤖 Autonomous workflow
                      {workflows[c.id].status === "error"
                        ? " · ERROR"
                        : workflows[c.id].done
                          ? " · finished"
                          : ` · paused before ${(workflows[c.id].interrupted_before || []).join(", ") || "next step"}`}
                    </span>
                    <span className={workflows[c.id].status === "error" ? "text-red-700" : "text-purple-600"}>
                      iter {workflows[c.id].iterations} · stage {workflows[c.id].current_stage}
                    </span>
                  </div>
                  {workflows[c.id].error_message && (
                    <div className="mt-1 text-red-900 font-mono text-[10px]">{workflows[c.id].error_message}</div>
                  )}
                  {workflows[c.id].status === "error" && (
                    <button onClick={() => recoverWorkflow(c.id)}
                            className="mt-1 text-[10px] px-2 py-0.5 bg-red-600 text-white rounded hover:bg-red-700">
                      🔄 Recover & retry
                    </button>
                  )}
                  {workflows[c.id].log && workflows[c.id].log.length > 0 && (
                    <ul className="mt-1 text-purple-700 list-disc list-inside">
                      {workflows[c.id].log.slice(-4).map((l, i) => <li key={i}>{l}</li>)}
                    </ul>
                  )}
                </div>
              )}
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
