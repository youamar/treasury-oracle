import React, { useEffect, useState } from "react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
         ReferenceDot, ReferenceLine } from "recharts";

const PAIRS = [
  ["USD","MYR"], ["EUR","MYR"], ["SGD","MYR"], ["GBP","MYR"], ["JPY","MYR"],
];

export default function FXPeakAnalyzer() {
  const [pair, setPair] = useState(PAIRS[0]);
  const [amount, setAmount] = useState(10000);
  const [data, setData] = useState(null);
  const [pickedDate, setPickedDate] = useState(null);

  useEffect(() => {
    fetch(`/api/fx/what-if?amount=${amount}&from_ccy=${pair[0]}&to_ccy=${pair[1]}&days=30`)
      .then(r => r.json()).then(setData);
  }, [pair, amount]);

  if (!data?.series) return <div className="text-sm text-slate-500">Loading FX history…</div>;

  const picked = pickedDate ? data.series.find(s => s.date === pickedDate) : data.peak;
  const at_picked = picked ? +(amount * picked.rate).toFixed(2) : 0;
  const delta_vs_avg = +(at_picked - data.at_average).toFixed(2);

  return (
    <div className="bg-white p-5 rounded-xl shadow">
      <div className="flex flex-wrap gap-2 items-center mb-3">
        <h3 className="font-semibold">⏰ Retroactive FX Peak Lock</h3>
        <select value={pair.join("-")} onChange={e => setPair(e.target.value.split("-"))}
                className="border rounded px-2 py-1 text-sm ml-auto">
          {PAIRS.map(p => <option key={p.join("-")} value={p.join("-")}>{p[0]} → {p[1]}</option>)}
        </select>
        <input type="number" value={amount} onChange={e => setAmount(+e.target.value || 0)}
               className="border rounded px-2 py-1 text-sm w-28" />
        <span className="text-xs text-slate-500">{pair[0]}</span>
      </div>

      <div style={{ width: "100%", height: 220 }}>
        <ResponsiveContainer>
          <LineChart data={data.series}
            onClick={e => e?.activeLabel && setPickedDate(e.activeLabel)}>
            <XAxis dataKey="date" fontSize={9} tick={{ fontSize: 9 }} />
            <YAxis domain={["auto", "auto"]} fontSize={9} />
            <Tooltip />
            <Line type="monotone" dataKey="rate" stroke="#1f6feb" dot={false} strokeWidth={2} />
            <ReferenceDot x={data.peak.date} y={data.peak.rate} r={6} fill="#22c55e" stroke="white" />
            <ReferenceDot x={data.trough.date} y={data.trough.rate} r={6} fill="#ef4444" stroke="white" />
            <ReferenceLine y={data.average} stroke="#94a3b8" strokeDasharray="4 4" />
            {pickedDate && picked && (
              <ReferenceDot x={picked.date} y={picked.rate} r={7} fill="#f59e0b" stroke="white" />
            )}
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="text-xs text-slate-500 mt-1">
        Click any point on the chart to retroactively "lock" that rate.
      </div>

      <div className="grid grid-cols-3 gap-2 mt-3 text-sm">
        <div className="bg-green-50 border border-green-200 rounded p-2">
          <div className="text-[10px] uppercase text-green-700">Peak ({data.peak.date})</div>
          <div className="font-bold">{data.at_peak} {pair[1]}</div>
          <div className="text-[10px] text-green-700">rate {data.peak.rate.toFixed(4)}</div>
        </div>
        <div className="bg-slate-50 border rounded p-2">
          <div className="text-[10px] uppercase text-slate-700">30d Average</div>
          <div className="font-bold">{data.at_average} {pair[1]}</div>
          <div className="text-[10px] text-slate-700">rate {data.average.toFixed(4)}</div>
        </div>
        <div className="bg-red-50 border border-red-200 rounded p-2">
          <div className="text-[10px] uppercase text-red-700">Trough ({data.trough.date})</div>
          <div className="font-bold">{data.at_trough} {pair[1]}</div>
          <div className="text-[10px] text-red-700">rate {data.trough.rate.toFixed(4)}</div>
        </div>
      </div>

      {pickedDate && (
        <div className="mt-3 p-3 bg-amber-50 border border-amber-200 rounded text-sm">
          <b>📦 Retroactive lock @ {picked.date}:</b> {at_picked} {pair[1]}
          {delta_vs_avg !== 0 && (
            <span className={delta_vs_avg > 0 ? "text-green-700 ml-2" : "text-red-700 ml-2"}>
              {delta_vs_avg > 0 ? "🎉" : "😢"} {delta_vs_avg > 0 ? "+" : ""}{delta_vs_avg} {pair[1]} vs 30-day average
            </span>
          )}
        </div>
      )}

      <div className="mt-2 text-xs text-slate-500">
        30-day spread: <b>{data.spread_pct}%</b> ·
        Missed profit vs avg (if locked at peak): <b>{data.missed_profit_vs_avg} {pair[1]}</b>
      </div>
    </div>
  );
}
