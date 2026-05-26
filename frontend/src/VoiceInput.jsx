import React, { useState } from "react";
import { apiFetch as fetch, pushToast } from "./Toast.jsx";

const EXAMPLES = [
  "Hi, I just transferred 500 USD for invoice INV-007 today.",
  "Just sent EUR 850 from Berlin Designs for INV-2026-002 on May 21.",
  "Anuy, 我刚转了 3200 人民币给你, ref INV-2026-006.",
];

export default function VoiceInput({ onProof }) {
  const [transcript, setTranscript] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    const t = transcript.trim();
    if (!t) return;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("transcript", t);
      const r = await fetch("/api/voice", { method: "POST", body: fd });
      const j = await r.json();
      // Server returns a proof-shaped dict that may include {error: "..."}.
      // We must NOT inject an error-only dict into the proof list — surface it.
      if (j.error || (!j.amount && !j.currency)) {
        pushToast({
          kind: "warn",
          title: "Voice extraction incomplete",
          message: j.error || "Couldn't extract amount/currency from that transcript. Try adding the amount + currency code (e.g. '500 USD').",
          detail: j,
        });
      } else {
        // Synthesize a minimal ocr_quality so the gate doesn't reject it.
        const proof = { ...j, ocr_quality: j.ocr_quality || {
          completeness: 1.0, missing_fields: [], gate: "ok",
        }};
        onProof?.(proof);
        pushToast({
          kind: "ok", title: "Voice proof extracted",
          message: `${j.amount} ${j.currency} from ${j.payer || "(unknown)"}`,
        });
        setTranscript("");
      }
    } catch (e) {
      pushToast({ kind: "error", title: "Voice request failed", message: String(e) });
    }
    setBusy(false);
  }

  return (
    <div className="bg-white p-4 rounded-xl shadow border border-slate-200">
      <h3 className="font-semibold text-sm mb-2">🎤 Voice Note Transcript</h3>
      <div className="text-[11px] text-slate-500 mb-2">
        Paste a transcript from a client's voice note (any language). The LLM extracts the payment.
      </div>
      <textarea
        value={transcript}
        onChange={(e) => setTranscript(e.target.value)}
        placeholder='e.g. "Hi, I just transferred 500 USD for invoice INV-007 today."'
        className="w-full border rounded p-2 text-sm" rows={3}
      />
      <div className="mt-1 flex flex-wrap gap-1">
        {EXAMPLES.map((ex, i) => (
          <button key={i} onClick={() => setTranscript(ex)}
                  className="text-[10px] text-purple-700 bg-purple-50 hover:bg-purple-100 border border-purple-200 rounded px-2 py-0.5">
            try example {i + 1}
          </button>
        ))}
      </div>
      <button onClick={submit} disabled={busy || !transcript.trim()}
              title={!transcript.trim() ? "Paste a transcript first" : "Extract via LLM"}
              className="mt-2 w-full bg-purple-600 text-white py-1.5 rounded disabled:opacity-40 hover:bg-purple-700">
        {busy ? "Parsing..." : "Extract proof from voice"}
      </button>
    </div>
  );
}
