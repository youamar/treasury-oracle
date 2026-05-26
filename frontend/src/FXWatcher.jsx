import React, { useEffect, useState } from "react";
import { apiFetch as fetch } from "./Toast.jsx";

const PAIRS = [["USD","MYR"], ["EUR","MYR"], ["SGD","MYR"], ["GBP","MYR"]];

function jackpotSound() {
  // Web Audio synthesized "ding ding ding"
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    [880, 1109, 1318, 1760].forEach((freq, i) => {
      const o = ctx.createOscillator(); const g = ctx.createGain();
      o.frequency.value = freq; o.type = "sine"; o.connect(g); g.connect(ctx.destination);
      g.gain.setValueAtTime(0.0001, ctx.currentTime + i * 0.15);
      g.gain.exponentialRampToValueAtTime(0.3, ctx.currentTime + i * 0.15 + 0.01);
      g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + i * 0.15 + 0.25);
      o.start(ctx.currentTime + i * 0.15); o.stop(ctx.currentTime + i * 0.15 + 0.3);
    });
  } catch {}
}

export default function FXWatcher() {
  const [pair, setPair] = useState(PAIRS[0]);
  const [target, setTarget] = useState(4.80);
  const [watchers, setWatchers] = useState([]);
  const [jackpot, setJackpot] = useState(null);
  const firedIds = React.useRef(new Set());

  async function refresh() {
    const r = await fetch("/api/fx/watcher").then(r => r.json());
    setWatchers(r.watchers || []);
    r.watchers?.forEach(w => {
      if (w.hit && !firedIds.current.has(w.id)) {
        firedIds.current.add(w.id);
        setJackpot(w);
        jackpotSound();
        setTimeout(() => setJackpot(null), 5000);
      }
    });
  }

  useEffect(() => { refresh(); const t = setInterval(refresh, 4000); return () => clearInterval(t); }, []);

  async function add() {
    await fetch("/api/fx/watcher", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ from_ccy: pair[0], to_ccy: pair[1], target_rate: +target, note: "" }),
    });
    refresh();
  }
  async function del(id) {
    await fetch(`/api/fx/watcher/${id}`, { method: "DELETE" });
    refresh();
  }

  return (
    <div className="bg-white p-4 rounded-xl shadow border border-slate-200">
      <h3 className="font-semibold mb-2">🎰 FX Watcher (Jackpot Alerts)</h3>
      <div className="flex gap-2 mb-3">
        <select value={pair.join("-")} onChange={e => setPair(e.target.value.split("-"))}
                className="border rounded px-2 py-1 text-sm">
          {PAIRS.map(p => <option key={p.join("-")} value={p.join("-")}>{p[0]} → {p[1]}</option>)}
        </select>
        <input type="number" step="0.01" value={target} onChange={e => setTarget(e.target.value)}
               className="border rounded px-2 py-1 text-sm w-24" placeholder="target" />
        <button onClick={add} className="bg-amber-500 text-white px-3 py-1 rounded text-sm hover:bg-amber-600">
          + Watch
        </button>
        <button onClick={() => { firedIds.current.clear(); setJackpot(watchers[0]); jackpotSound(); setTimeout(()=>setJackpot(null),5000); }}
                className="text-xs text-slate-500 underline">demo trigger</button>
      </div>
      <ul className="text-xs space-y-1">
        {watchers.map(w => (
          <li key={w.id} className="flex justify-between items-center p-1.5 bg-slate-50 rounded">
            <span>
              {w.from_ccy}→{w.to_ccy} @ {w.target_rate}
              {w.latest && <span className="text-slate-500 ml-2">now {w.latest.rate.toFixed(4)}</span>}
              {w.hit && <span className="ml-2 text-green-700 font-bold">🎰 HIT</span>}
            </span>
            <button onClick={() => del(w.id)} className="text-red-500 text-xs">✕</button>
          </li>
        ))}
        {!watchers.length && <li className="text-slate-400 italic">No watchers yet</li>}
      </ul>

      {jackpot && (
        <div className="fixed top-4 right-4 z-50 bg-gradient-to-r from-yellow-400 to-amber-500 text-white p-4 rounded-xl shadow-2xl animate-bounce">
          <div className="text-3xl">🎰 JACKPOT!</div>
          <div className="text-sm">
            {jackpot.from_ccy}→{jackpot.to_ccy} hit {jackpot.latest?.rate.toFixed(4)} (target {jackpot.target_rate})
          </div>
        </div>
      )}
    </div>
  );
}
