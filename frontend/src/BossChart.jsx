import React, { useState } from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from "recharts";
import { apiFetch as fetch } from "./Toast.jsx";

export default function BossChart({ match }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  async function run() {
    setLoading(true);
    try {
      const today = new Date().toISOString().slice(0, 10);
      const r = await fetch("/api/boss-chart", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          amount: match.proof.amount,
          from_ccy: match.proof.currency,
          to_ccy: match.txn.currency,
          invoice_date: match.proof.date,
          payment_date: match.txn.date || today,
          actual_local: match.conversion.actual_received,
        }),
      });
      setData(await r.json());
    } catch (e) { console.error(e); }
    setLoading(false);
  }

  if (!data) {
    return (
      <button onClick={run} disabled={loading}
              className="text-xs px-3 py-1 bg-amber-100 text-amber-800 rounded hover:bg-amber-200">
        {loading ? "Generating..." : "📊 Blame the Fed (explain to boss)"}
      </button>
    );
  }

  const chartData = [
    { label: `On invoice date\n${data.invoice_date}`, rate: data.rate_invoice_date, fill: "#3b82f6" },
    { label: `On payment date\n${data.payment_date}`, rate: data.rate_payment_date, fill: "#f97316" },
  ];

  return (
    <div className="mt-2 bg-amber-50 border border-amber-200 rounded p-3">
      <div className="font-semibold text-amber-900">{data.headline}</div>
      <div className="text-xs text-amber-800 mt-1">{data.explanation}</div>
      <div style={{ width: "100%", height: 180 }} className="mt-2">
        <ResponsiveContainer>
          <BarChart data={chartData}>
            <XAxis dataKey="label" fontSize={10} />
            <YAxis domain={["auto", "auto"]} fontSize={10} />
            <Tooltip />
            <Bar dataKey="rate">
              {chartData.map((d, i) => <Cell key={i} fill={d.fill} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="text-xs text-amber-700">
        FX moved {data.rate_move_pct >= 0 ? "+" : ""}{data.rate_move_pct.toFixed(2)}% ·
        Diff: {data.diff_local} {data.to_ccy}
      </div>
    </div>
  );
}
