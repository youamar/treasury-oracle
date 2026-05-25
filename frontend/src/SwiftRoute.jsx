import React from "react";
import { motion } from "framer-motion";

export default function SwiftRoute({ route }) {
  if (!route) return null;
  const { nodes, source_currency, local_currency, sent_amount,
          actual_net_local, gap_local, explanation, unexplained_residual } = route;
  const W = 760, H = 280, padX = 60;
  const stepX = (W - padX * 2) / (nodes.length - 1);

  return (
    <div className="bg-slate-900 text-slate-100 rounded-xl p-5 shadow-lg">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="font-bold text-lg">🛰️ SWIFT Route Time-Machine</h3>
          <div className="text-xs text-slate-400">
            Sent {sent_amount} {source_currency} · Received {actual_net_local} {local_currency} ·{" "}
            <span className="text-amber-400">Gap {gap_local} {local_currency}</span>
          </div>
        </div>
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
        {/* wire */}
        <motion.line
          x1={padX} y1={H/2} x2={W-padX} y2={H/2}
          stroke="#475569" strokeWidth="3" strokeDasharray="6 6"
          initial={{ pathLength: 0 }} animate={{ pathLength: 1 }}
          transition={{ duration: 1.5 }}
        />
        {/* money packet */}
        <motion.circle
          r="10" fill="#22c55e"
          initial={{ cx: padX, cy: H/2 }}
          animate={{ cx: W - padX, cy: H/2 }}
          transition={{ duration: 3, ease: "linear", repeat: Infinity }}
        />
        {nodes.map((n, i) => {
          const cx = padX + i * stepX;
          const color = n.type === "originator" ? "#3b82f6"
                       : n.type === "beneficiary" ? "#22c55e" : "#f59e0b";
          return (
            <motion.g key={i}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.4, duration: 0.5 }}>
              <circle cx={cx} cy={H/2} r="22" fill={color} stroke="white" strokeWidth="3" />
              <text x={cx} y={H/2 + 5} textAnchor="middle" fontSize="14" fontWeight="bold" fill="white">
                {i + 1}
              </text>
              <text x={cx} y={H/2 - 38} textAnchor="middle" fontSize="11" fill="#cbd5e1">
                {n.name}
              </text>
              {n.bic && (
                <text x={cx} y={H/2 - 24} textAnchor="middle" fontSize="9" fill="#64748b">
                  {n.bic}
                </text>
              )}
              {n.fee > 0 && (
                <g>
                  <rect x={cx - 50} y={H/2 + 28} width="100" height="20" rx="4" fill="#7f1d1d" />
                  <text x={cx} y={H/2 + 42} textAnchor="middle" fontSize="11" fill="#fecaca">
                    − {n.fee} {n.fee_currency}
                  </text>
                </g>
              )}
              <text x={cx} y={H/2 + 70} textAnchor="middle" fontSize="10" fill="#94a3b8">
                {n.country}
              </text>
            </motion.g>
          );
        })}
      </svg>

      <div className="mt-3 text-sm text-slate-300 bg-slate-800 p-3 rounded">
        {explanation}
        {Math.abs(unexplained_residual) > 0.5 && (
          <div className="mt-1 text-amber-300 text-xs">
            ⚠ Unexplained residual: {unexplained_residual} {local_currency}
          </div>
        )}
      </div>
    </div>
  );
}
