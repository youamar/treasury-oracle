import React, { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";


/** Per-match badges showing verifier verdict + every numeric's source.
 *  This is the UI surface of F1 (provenance) + F2 (verifier) — previously
 *  only visible in the audit-pack PDF. */
function ProvenanceBadges({ match }) {
  const [open, setOpen] = useState(false);
  const prov = match.conversion?.provenance || {};
  const verifier = prov.verifier;
  const allTrusted = prov.all_inputs_trusted;

  const verifierPill = verifier?.ran ? (
    verifier.verdict === "confirm" ? (
      <span className="px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200 text-[10px] font-medium">
        ✓ verifier confirmed
      </span>
    ) : (
      <span className="px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200 text-[10px] font-medium"
            title={verifier.concerns?.join("; ")}>
        ⚠ verifier downgraded
      </span>
    )
  ) : null;

  const trustPill = allTrusted === false ? (
    <span className="px-1.5 py-0.5 rounded bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200 text-[10px] font-medium">
      ⚠ untrusted inputs
    </span>
  ) : allTrusted === true ? (
    <span className="px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200 text-[10px] font-medium">
      🔒 all sources verified
    </span>
  ) : null;

  if (!verifierPill && !trustPill) return null;

  return (
    <div className="mt-1 ml-1">
      <div className="flex items-center gap-2 flex-wrap">
        {verifierPill}
        {trustPill}
        <button
          onClick={() => setOpen(o => !o)}
          className="text-[10px] text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 underline"
        >
          {open ? "hide" : "show"} provenance
        </button>
      </div>
      {open && (
        <div className="mt-1 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded p-2">
          <table className="text-[10px] w-full">
            <thead className="text-slate-500 dark:text-slate-400">
              <tr>
                <th className="text-left font-medium pb-1">field</th>
                <th className="text-left font-medium pb-1">value</th>
                <th className="text-left font-medium pb-1">source</th>
                <th className="text-left font-medium pb-1">trusted</th>
              </tr>
            </thead>
            <tbody className="font-mono text-slate-700 dark:text-slate-300">
              {["proof_amount", "fx_rate", "fee", "expected_net", "actual_received"].map((k) => {
                const e = prov[k];
                if (!e) return null;
                return (
                  <tr key={k} className="border-t border-slate-200 dark:border-slate-700">
                    <td className="py-0.5 pr-2">{k}</td>
                    <td className="py-0.5 pr-2">{String(e.value ?? "—").slice(0, 10)}</td>
                    <td className="py-0.5 pr-2 text-slate-600 dark:text-slate-400">{e.source || "—"}</td>
                    <td className="py-0.5">{e.trusted ? "✓" : "✗"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {verifier?.concerns?.length > 0 && (
            <div className="mt-1 text-[10px] text-amber-800 dark:text-amber-300">
              <b>verifier concerns:</b>
              <ul className="ml-3 list-disc">
                {verifier.concerns.map((c, i) => <li key={i}>{c}</li>)}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Magnetic-snap visualization of matched proof <-> txn pairs.
 * Each pair animates from separated cards into a "kissed" snapped state with a green flash.
 */
function DiffBar({ expected, actual }) {
  if (!expected) return null;
  // Bar centered at 0; spans ±50 bps. Position is proportional.
  const bps = ((actual - expected) / expected) * 10000;
  const clamped = Math.max(-50, Math.min(50, bps));
  const pct = 50 + clamped; // 0..100
  const col = Math.abs(bps) < 10 ? "bg-emerald-500"
            : Math.abs(bps) < 30 ? "bg-amber-500" : "bg-red-500";
  return (
    <div className="mt-1.5 px-1">
      <div className="relative h-1.5 bg-slate-200 dark:bg-slate-700 rounded-full">
        {/* Center line */}
        <div className="absolute top-0 bottom-0 left-1/2 w-px bg-slate-400 dark:bg-slate-500" />
        {/* Diff marker */}
        <div className={`absolute top-0 bottom-0 w-1.5 rounded-full ${col}`}
             style={{ left: `calc(${pct}% - 3px)` }} />
      </div>
      <div className="flex justify-between text-[9px] text-slate-400 dark:text-slate-500 mt-0.5">
        <span>-50 bps</span><span>0</span><span>+50 bps</span>
      </div>
    </div>
  );
}


export default function MagneticMatches({ matches }) {
  if (!matches?.length) return null;
  return (
    <div className="space-y-3">
      <h3 className="font-semibold text-slate-700 dark:text-slate-200">⚡ Auto-snapped matches</h3>
      <div className="space-y-3">
        <AnimatePresence>
          {matches.map((m, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ delay: i * 0.15 }}
              className="relative"
            >
              <div className="flex items-center gap-2">
                <motion.div
                  initial={{ x: -120, opacity: 0 }}
                  animate={{ x: 0, opacity: 1 }}
                  transition={{ delay: i * 0.15 + 0.2, type: "spring", stiffness: 300, damping: 20 }}
                  className="flex-1 bg-white dark:bg-slate-900 border-2 border-blue-300 dark:border-blue-700 rounded-lg p-3 shadow dark:shadow-slate-950/50"
                >
                  <div className="text-[10px] uppercase text-blue-600 dark:text-blue-400 font-bold">Payment Proof</div>
                  <div className="font-mono text-xs text-slate-500 dark:text-slate-400">{m.proof.source_file}</div>
                  <div className="text-lg font-bold text-slate-900 dark:text-slate-100">{m.proof.amount} {m.proof.currency}</div>
                  <div className="text-xs text-slate-600 dark:text-slate-400">{m.proof.payer} · {m.proof.date}</div>
                </motion.div>

                <motion.div
                  initial={{ scale: 0, rotate: 0 }}
                  animate={{ scale: [0, 1.4, 1], rotate: [0, 360, 360] }}
                  transition={{ delay: i * 0.15 + 0.55, duration: 0.6 }}
                  className="text-2xl"
                >
                  ⚡
                </motion.div>

                <motion.div
                  initial={{ x: 120, opacity: 0 }}
                  animate={{ x: 0, opacity: 1 }}
                  transition={{ delay: i * 0.15 + 0.2, type: "spring", stiffness: 300, damping: 20 }}
                  className="flex-1 bg-white dark:bg-slate-900 border-2 border-emerald-300 dark:border-emerald-700 rounded-lg p-3 shadow dark:shadow-slate-950/50"
                >
                  <div className="text-[10px] uppercase text-emerald-600 dark:text-emerald-400 font-bold">Bank Txn</div>
                  <div className="font-mono text-xs text-slate-500 dark:text-slate-400">{m.txn.id}</div>
                  <div className="text-lg font-bold text-slate-900 dark:text-slate-100">{m.txn.amount} {m.txn.currency}</div>
                  <div className="text-xs text-slate-600 dark:text-slate-400 truncate">{m.txn.description}</div>
                </motion.div>
              </div>
              <DiffBar expected={m.conversion.expected_net} actual={m.conversion.actual_received} />
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: [0, 0.4, 0] }}
                transition={{ delay: i * 0.15 + 0.55, duration: 0.8 }}
                className="absolute inset-0 bg-green-400 rounded-lg pointer-events-none"
              />
              <div className="text-xs text-emerald-700 dark:text-emerald-300 mt-1 ml-1 flex items-center gap-2 flex-wrap">
                <span>✓ {(m.confidence * 100).toFixed(0)}% confidence</span>
                <span className="text-slate-400 dark:text-slate-600">·</span>
                <span>FX {m.conversion.fx_rate.toFixed(4)}</span>
                <span className="text-slate-400 dark:text-slate-600">·</span>
                <span>fee {m.conversion.fee_amount} {m.txn.currency}</span>
                {(() => {
                  const exp = m.conversion.expected_net;
                  const act = m.conversion.actual_received;
                  if (!exp) return null;
                  const bps = ((act - exp) / exp) * 10000;
                  const col = Math.abs(bps) < 10 ? "text-slate-500 dark:text-slate-400"
                            : Math.abs(bps) < 30 ? "text-amber-600 dark:text-amber-400"
                            : "text-red-600 dark:text-red-400";
                  return <span className={col + " font-medium"}>diff {bps >= 0 ? "+" : ""}{bps.toFixed(1)} bps</span>;
                })()}
              </div>
              <ProvenanceBadges match={m} />
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}
