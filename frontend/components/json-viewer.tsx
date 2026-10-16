"use client";

import { Copy } from "lucide-react";
import { useState } from "react";

import type { Report } from "@/lib/schema/report";

/**
 * Raw JSON: collapsed `<details>` with pretty-printed report JSON and a
 * "Copy JSON" action. The copy result (or failure) is announced in an
 * `aria-live` region; a missing/unavailable clipboard API is handled
 * gracefully.
 */
export function JsonViewer({ report }: { report: Report }) {
  const [status, setStatus] = useState<string | null>(null);
  const json = JSON.stringify(report, null, 2);

  async function copyJson() {
    try {
      await navigator.clipboard.writeText(json);
      setStatus("Copied");
    } catch {
      setStatus("Copy failed — select the JSON text and copy it manually.");
    }
  }

  return (
    <details
      data-testid="json-viewer"
      className="rounded-card border border-line bg-surface p-5 shadow-card"
    >
      <summary className="cursor-pointer text-lg font-semibold text-ink-strong">
        Raw JSON
      </summary>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={copyJson}
          className="inline-flex items-center gap-2 rounded-card border border-line-strong bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:bg-surface-muted"
        >
          <Copy aria-hidden="true" className="h-4 w-4" />
          Copy JSON
        </button>
        <p aria-live="polite" className="text-xs text-ink-muted">
          {status}
        </p>
      </div>
      <pre className="mt-3 max-h-96 overflow-auto rounded-card bg-surface-muted p-3 font-mono text-xs text-ink">
        {json}
      </pre>
    </details>
  );
}
