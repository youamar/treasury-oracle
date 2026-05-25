import React, { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";

const ICONS = { image: "🖼️", pdf: "📄", audio: "🎤", other: "📎" };

/** Mock 'live inbox' — polls backend, animates newly-discovered files. */
export default function Inbox({ onIngest }) {
  const [items, setItems] = useState([]);
  const [active, setActive] = useState(true);
  const seen = useRef(new Set());

  useEffect(() => {
    if (!active) return;
    let timer;
    async function poll() {
      try {
        const r = await fetch("/api/inbox/poll");
        const j = await r.json();
        const fresh = j.items.filter((it) => !seen.current.has(it.filename));
        fresh.forEach((f) => seen.current.add(f.filename));
        if (fresh.length) setItems((cur) => [...fresh.map(f => ({...f, isNew: true})), ...cur]);
      } catch {}
      timer = setTimeout(poll, 2500);
    }
    poll();
    return () => clearTimeout(timer);
  }, [active]);

  async function ingest(item) {
    const r = await fetch(`/api/inbox/ingest/${encodeURIComponent(item.filename)}`, { method: "POST" });
    const j = await r.json();
    onIngest?.(j.proofs || []);
    setItems((cur) => cur.map((x) => x.filename === item.filename ? {...x, ingested: true} : x));
  }

  return (
    <div className="bg-white p-4 rounded-xl shadow border border-slate-200">
      <div className="flex justify-between items-center mb-2">
        <h3 className="font-semibold text-sm">📨 Live Inbox (WhatsApp / Email simulation)</h3>
        <label className="text-xs flex items-center gap-1">
          <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
          watching
        </label>
      </div>
      <div className="text-[11px] text-slate-500 mb-2">
        Drop files into <code>backend/data/inbox/</code> to simulate clients pinging you.
      </div>
      {items.length === 0 && <div className="text-xs text-slate-400 italic">No messages yet…</div>}
      <ul className="space-y-1 max-h-48 overflow-auto">
        <AnimatePresence>
          {items.map((it) => (
            <motion.li
              key={it.filename}
              initial={{ opacity: 0, x: -30, backgroundColor: "#fef9c3" }}
              animate={{ opacity: 1, x: 0, backgroundColor: it.ingested ? "#dcfce7" : "#ffffff" }}
              transition={{ duration: 0.5 }}
              className="flex justify-between items-center text-sm p-2 rounded border"
            >
              <span>{ICONS[it.kind]} <span className="font-mono text-xs">{it.filename}</span></span>
              <button
                disabled={it.ingested}
                onClick={() => ingest(it)}
                className="text-xs bg-blue-600 text-white px-2 py-0.5 rounded disabled:opacity-40 hover:bg-blue-700"
              >
                {it.ingested ? "✓ ingested" : "Ingest"}
              </button>
            </motion.li>
          ))}
        </AnimatePresence>
      </ul>
    </div>
  );
}
