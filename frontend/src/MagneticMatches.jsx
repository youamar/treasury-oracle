import React from "react";
import { motion, AnimatePresence } from "framer-motion";

/**
 * Magnetic-snap visualization of matched proof <-> txn pairs.
 * Each pair animates from separated cards into a "kissed" snapped state with a green flash.
 */
export default function MagneticMatches({ matches }) {
  if (!matches?.length) return null;
  return (
    <div className="space-y-3">
      <h3 className="font-semibold text-slate-700">⚡ Auto-snapped matches</h3>
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
                  className="flex-1 bg-white border-2 border-blue-300 rounded-lg p-3 shadow"
                >
                  <div className="text-[10px] uppercase text-blue-600 font-bold">Payment Proof</div>
                  <div className="font-mono text-xs text-slate-500">{m.proof.source_file}</div>
                  <div className="text-lg font-bold">{m.proof.amount} {m.proof.currency}</div>
                  <div className="text-xs text-slate-600">{m.proof.payer} · {m.proof.date}</div>
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
                  className="flex-1 bg-white border-2 border-emerald-300 rounded-lg p-3 shadow"
                >
                  <div className="text-[10px] uppercase text-emerald-600 font-bold">Bank Txn</div>
                  <div className="font-mono text-xs text-slate-500">{m.txn.id}</div>
                  <div className="text-lg font-bold">{m.txn.amount} {m.txn.currency}</div>
                  <div className="text-xs text-slate-600 truncate">{m.txn.description}</div>
                </motion.div>
              </div>
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: [0, 0.4, 0] }}
                transition={{ delay: i * 0.15 + 0.55, duration: 0.8 }}
                className="absolute inset-0 bg-green-400 rounded-lg pointer-events-none"
              />
              <div className="text-xs text-emerald-700 mt-1 ml-1">
                ✓ {(m.confidence * 100).toFixed(0)}% confidence · FX {m.conversion.fx_rate.toFixed(4)} ·
                fee {m.conversion.fee_amount} {m.txn.currency}
              </div>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}
