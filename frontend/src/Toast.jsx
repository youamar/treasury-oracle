import React, { createContext, useCallback, useContext, useEffect, useState } from "react";

const ToastCtx = createContext({ push: () => {}, dismiss: () => {} });

let _externalPush = null;

export function useToast() {
  return useContext(ToastCtx);
}

/** Push a toast from outside the React tree (e.g. plain fetch helpers). */
export function pushToast(t) {
  if (_externalPush) _externalPush(t);
  else if (typeof console !== "undefined") console.warn("toast not mounted:", t);
}

let _id = 0;

export function ToastProvider({ children, max = 4, ttlMs = 6000 }) {
  const [toasts, setToasts] = useState([]);

  const dismiss = useCallback((id) => {
    setToasts((cur) => cur.filter((t) => t.id !== id));
  }, []);

  const push = useCallback((t) => {
    const id = ++_id;
    const toast = {
      id,
      kind: t.kind || "error",
      title: t.title || "Error",
      message: t.message || "",
      source: t.source || "",
      detail: t.detail || null,
      ts: Date.now(),
    };
    setToasts((cur) => {
      const next = [...cur, toast];
      return next.length > max ? next.slice(next.length - max) : next;
    });
    if (ttlMs > 0) {
      setTimeout(() => dismiss(id), ttlMs);
    }
    return id;
  }, [max, ttlMs, dismiss]);

  useEffect(() => {
    _externalPush = push;
    return () => { _externalPush = null; };
  }, [push]);

  return (
    <ToastCtx.Provider value={{ push, dismiss }}>
      {children}
      <div className="fixed top-3 right-3 z-50 flex flex-col gap-2 max-w-sm w-full pointer-events-none">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`pointer-events-auto rounded-lg shadow-lg border px-3 py-2 text-sm ${
              t.kind === "error"
                ? "bg-red-50 border-red-300 text-red-900"
                : t.kind === "warn"
                ? "bg-amber-50 border-amber-300 text-amber-900"
                : t.kind === "ok"
                ? "bg-green-50 border-green-300 text-green-900"
                : "bg-slate-50 border-slate-300 text-slate-900"
            }`}
          >
            <div className="flex justify-between items-start gap-2">
              <div className="flex-1 min-w-0">
                <div className="font-semibold flex items-center gap-2">
                  {t.kind === "error" ? "⚠️" : t.kind === "ok" ? "✓" : t.kind === "warn" ? "⚠" : "ℹ"}
                  <span>{t.title}</span>
                  {t.source && <span className="text-[10px] font-mono opacity-70">{t.source}</span>}
                </div>
                {t.message && (
                  <div className="mt-0.5 text-xs whitespace-pre-wrap break-words">{t.message}</div>
                )}
                {t.detail && (
                  <details className="mt-1 text-[10px]">
                    <summary className="cursor-pointer opacity-70">details</summary>
                    <pre className="mt-1 bg-white/60 p-1 rounded overflow-x-auto">{
                      typeof t.detail === "string" ? t.detail : JSON.stringify(t.detail, null, 2)
                    }</pre>
                  </details>
                )}
              </div>
              <button onClick={() => dismiss(t.id)} className="opacity-50 hover:opacity-100 text-xs">✕</button>
            </div>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

/** Wrap fetch — surface non-2xx responses + network errors as toasts. */
export async function apiFetch(input, init = {}) {
  try {
    const r = await fetch(input, init);
    if (!r.ok) {
      let detail = "";
      try {
        const j = await r.clone().json();
        detail = j.detail || j.error || JSON.stringify(j);
      } catch {
        try { detail = await r.clone().text(); } catch {}
      }
      pushToast({
        kind: "error",
        title: `${r.status} ${r.statusText || ""}`.trim(),
        message: typeof input === "string" ? input : "",
        detail: detail || undefined,
      });
    }
    return r;
  } catch (e) {
    pushToast({
      kind: "error",
      title: "Network error",
      message: typeof input === "string" ? input : "request failed",
      detail: String(e),
    });
    throw e;
  }
}
