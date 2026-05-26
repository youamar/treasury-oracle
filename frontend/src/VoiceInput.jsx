import React, { useState } from "react";
import { apiFetch as fetch } from "./Toast.jsx";

export default function VoiceInput({ onProof }) {
  const [transcript, setTranscript] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!transcript.trim()) return;
    setBusy(true);
    const fd = new FormData();
    fd.append("transcript", transcript);
    const r = await fetch("/api/voice", { method: "POST", body: fd });
    const j = await r.json();
    onProof?.(j);
    setTranscript("");
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
      <button onClick={submit} disabled={busy || !transcript.trim()}
              className="mt-2 w-full bg-purple-600 text-white py-1.5 rounded disabled:opacity-40 hover:bg-purple-700">
        {busy ? "Parsing..." : "Extract proof from voice"}
      </button>
    </div>
  );
}
