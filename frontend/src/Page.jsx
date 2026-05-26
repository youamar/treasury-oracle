import React from "react";

/** Consistent page wrapper used by Memory / Eval / Settings views. */
export default function Page({ icon, title, subtitle, actions, children }) {
  return (
    <main className="max-w-6xl mx-auto p-6 space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="flex items-start gap-3">
          {icon && (
            <div className="bg-gradient-to-br from-indigo-500 to-purple-600 text-white w-12 h-12 rounded-xl flex items-center justify-center text-2xl shadow">
              {icon}
            </div>
          )}
          <div>
            <h1 className="text-2xl font-bold text-slate-900">{title}</h1>
            {subtitle && <p className="text-sm text-slate-600 mt-0.5">{subtitle}</p>}
          </div>
        </div>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
      {children}
    </main>
  );
}


/** Empty-state card with an icon, title, hint, and optional CTA. */
export function EmptyState({ icon = "📭", title, hint, cta, onCtaClick }) {
  return (
    <div className="bg-slate-50 border-2 border-dashed border-slate-200 rounded-xl p-8 text-center">
      <div className="text-4xl mb-2 opacity-60">{icon}</div>
      <div className="font-medium text-slate-700">{title}</div>
      {hint && <div className="text-sm text-slate-500 mt-1">{hint}</div>}
      {cta && (
        <button onClick={onCtaClick}
                className="mt-3 px-4 py-1.5 bg-indigo-600 text-white text-sm rounded-lg hover:bg-indigo-700">
          {cta}
        </button>
      )}
    </div>
  );
}


/** Shared pill / badge — duplicates removed from individual panels. */
export function Badge({ children, color = "slate" }) {
  const map = {
    green: "bg-green-100 text-green-800",
    red:   "bg-red-100 text-red-800",
    blue:  "bg-blue-100 text-blue-800",
    slate: "bg-slate-100 text-slate-700",
    amber: "bg-amber-100 text-amber-800",
    purple:"bg-purple-100 text-purple-800",
    emerald:"bg-emerald-100 text-emerald-800",
    indigo:"bg-indigo-100 text-indigo-800",
  };
  return <span className={`px-2 py-0.5 rounded text-xs font-medium ${map[color]}`}>{children}</span>;
}


/** Card with optional title/subtitle and standard padding. */
export function Card({ title, subtitle, actions, children, className = "" }) {
  return (
    <section className={`bg-white rounded-xl shadow ${className}`}>
      {(title || actions) && (
        <div className="flex items-center justify-between px-5 pt-4 pb-2 gap-2">
          <div>
            {title && <h3 className="font-semibold text-slate-900">{title}</h3>}
            {subtitle && <div className="text-xs text-slate-500 mt-0.5">{subtitle}</div>}
          </div>
          {actions && <div className="flex gap-2 items-center">{actions}</div>}
        </div>
      )}
      <div className={title ? "px-5 pb-5" : "p-5"}>{children}</div>
    </section>
  );
}
