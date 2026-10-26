"use client";

import { X } from "lucide-react";
import { useMemo, useRef, useState } from "react";

import { importRows, type SheetResult } from "@/lib/api/sheets";
import type { ImportRowInput, ImportRowsResponse } from "@/lib/schema/sheet";
import { parseImportLine } from "@/lib/utils/cell-format";

/**
 * Paste-import dialog (P7 / D8): one pasted line per company —
 * `name<TAB>domain`, `name,domain`, or a lone name/domain. Up to 100 rows go
 * per request (the API limit); longer pastes are sent in sequential batches
 * and the true created/skipped counts are reported back. Nothing is
 * invented: an unparseable line is skipped and counted.
 */

const BATCH = 100;

export function parseImportText(text: string): {
  rows: ImportRowInput[];
  skippedLines: number;
} {
  const rows: ImportRowInput[] = [];
  let skippedLines = 0;
  for (const line of text.split(/\r?\n/)) {
    if (!line.trim()) continue;
    const parsed = parseImportLine(line);
    if (parsed === null) skippedLines += 1;
    else rows.push(parsed);
  }
  return { rows, skippedLines };
}

export function ImportRowsDialog({
  sheetId,
  onClose,
  onImported,
}: {
  sheetId: string;
  onClose: () => void;
  onImported: () => void;
}) {
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<{ created: number; skipped: number } | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);

  const parsed = useMemo(() => parseImportText(text), [text]);

  async function handleImport() {
    if (parsed.rows.length === 0 || submitting) return;
    setSubmitting(true);
    setError(null);
    let created = 0;
    let skipped = 0;
    for (let offset = 0; offset < parsed.rows.length; offset += BATCH) {
      const batch = parsed.rows.slice(offset, offset + BATCH);
      const result: SheetResult<ImportRowsResponse> = await importRows(sheetId, batch);
      if (result.kind !== "ok") {
        setError(
          result.kind === "api_error"
            ? result.message
            : result.kind === "network"
              ? "Could not reach the sheets service. Check that the backend is running."
              : "The import response could not be read.",
        );
        setSubmitting(false);
        if (created > 0) onImported();
        return;
      }
      created += result.data.created;
      skipped += result.data.skipped;
    }
    setSubmitting(false);
    setSummary({ created, skipped });
    onImported();
  }

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-ink-strong/30 p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="import-rows-title"
        data-testid="import-rows-dialog"
        className="w-full max-w-lg rounded-card border border-line bg-surface p-5 shadow-card"
      >
        <div className="flex items-start justify-between gap-2">
          <div>
            <h2 id="import-rows-title" className="text-base font-semibold text-ink-strong">
              Import rows
            </h2>
            <p className="mt-1 text-xs text-ink-muted">
              One company per line: <span className="font-mono">name⇥domain</span>,{" "}
              <span className="font-mono">name,domain</span>, or a lone name or domain.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close import dialog"
            className="rounded p-1 text-ink-muted hover:bg-surface-muted hover:text-ink"
          >
            <X aria-hidden="true" className="h-4 w-4" />
          </button>
        </div>

        <label htmlFor="import-rows-text" className="sr-only">
          Companies to import
        </label>
        <textarea
          id="import-rows-text"
          value={text}
          onChange={(event) => setText(event.target.value)}
          rows={8}
          autoFocus
          placeholder={"Acme Corp\tacme.com\nGlobex,globex.io\nstartup.dev"}
          className="mt-3 w-full rounded-card border border-line-strong bg-surface px-3 py-2 font-mono text-xs text-ink placeholder:text-ink-muted"
        />

        <p className="mt-1 text-xs text-ink-muted" aria-live="polite">
          {parsed.rows.length > 0
            ? `${parsed.rows.length} ${parsed.rows.length === 1 ? "row" : "rows"} ready` +
              (parsed.rows.length > BATCH ? ` (sent in batches of ${BATCH})` : "") +
              (parsed.skippedLines > 0 ? ` · ${parsed.skippedLines} unparseable` : "")
            : "Paste at least one company."}
        </p>

        {error !== null ? (
          <p role="alert" className="mt-2 text-xs font-medium text-danger-ink">
            {error}
          </p>
        ) : null}
        {summary !== null ? (
          <p role="status" className="mt-2 text-xs font-medium text-verified-ink">
            Imported {summary.created} {summary.created === 1 ? "row" : "rows"}
            {summary.skipped > 0 ? ` · ${summary.skipped} skipped as duplicates` : ""}.
          </p>
        ) : null}

        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-card border border-line-strong bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface-muted"
          >
            {summary !== null ? "Done" : "Cancel"}
          </button>
          <button
            type="button"
            onClick={() => void handleImport()}
            disabled={submitting || parsed.rows.length === 0 || summary !== null}
            className="rounded-card bg-accent px-3 py-1.5 text-xs font-semibold text-white hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-60"
          >
            {submitting ? "Importing…" : `Import ${parsed.rows.length || ""} rows`}
          </button>
        </div>
      </div>
    </div>
  );
}
