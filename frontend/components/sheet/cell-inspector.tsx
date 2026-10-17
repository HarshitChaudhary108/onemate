"use client";

import { RotateCcw, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { SourceList } from "@/components/source-list";
import { StatusBadge } from "@/components/status-badge";
import { getCellDetail } from "@/lib/api/sheets";
import type { CellRevision } from "@/lib/schema/sheet";
import { cellKey, selectCell, selectColumn, selectRow, useSheetStore } from "@/lib/store/sheet-store";
import { cellStateLabel, formatCellValue, formatUsd } from "@/lib/utils/cell-format";
import { formatDateTime, safeExternalUrl } from "@/lib/utils/format";

/**
 * Right-hand cell inspector (P7 / plan §4.6): the value, the StatusBadge,
 * sources via SourceList, the rendered prompt, provider-reported cost,
 * timestamps, the append-only revision history and a retry control.
 * Everything shown comes from the cell record or the cell-detail endpoint —
 * nothing is filled in.
 */
export function CellInspector({
  sheetId,
  rowId,
  columnId,
  onClose,
  onRetryErrors,
}: {
  sheetId: string;
  rowId: string;
  columnId: string;
  onClose: () => void;
  onRetryErrors?: () => void;
}) {
  const cell = useSheetStore(selectCell(rowId, columnId));
  const column = useSheetStore(selectColumn(columnId));
  const row = useSheetStore(selectRow(rowId));
  const [revisions, setRevisions] = useState<CellRevision[] | null>(null);
  const panelRef = useRef<HTMLElement | null>(null);
  const key = cellKey(rowId, columnId);

  // Revision history lives on the detail endpoint — re-read it whenever the
  // inspected cell or its revision changes (a fresh write means new history).
  const revision = cell?.revision ?? -1;
  useEffect(() => {
    let cancelled = false;
    void getCellDetail(sheetId, rowId, columnId).then((result) => {
      if (!cancelled && result.kind === "ok") setRevisions(result.data.revisions);
    });
    return () => {
      cancelled = true;
    };
  }, [sheetId, rowId, columnId, revision]);

  // Move focus into the panel when it opens so Esc/keys behave predictably.
  useEffect(() => {
    panelRef.current?.focus();
  }, [key]);

  const result = cell?.result;
  const sources = result?.sources ?? [];
  const valueText = formatCellValue(result?.value, column?.output_type ?? "text");
  const url = typeof result?.value === "string" ? safeExternalUrl(result.value) : null;
  const rowName = row?.resolved_name ?? row?.input_name ?? "row";

  return (
    <aside
      ref={panelRef}
      tabIndex={-1}
      role="complementary"
      aria-label={`Cell inspector: ${column?.name ?? "column"} for ${rowName}`}
      data-testid="cell-inspector"
      className="flex h-full w-80 shrink-0 flex-col border-l border-line bg-surface outline-none"
    >
      <div className="flex items-start justify-between gap-2 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-ink-strong">
            {column?.name ?? "Column"}
          </p>
          <p className="truncate text-xs text-ink-muted">{rowName}</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close inspector"
          className="rounded p-1 text-ink-muted hover:bg-surface-muted hover:text-ink"
        >
          <X aria-hidden="true" className="h-4 w-4" />
        </button>
      </div>

      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-4 text-sm">
        <section>
          <p className="text-xs font-mono font-medium uppercase tracking-wide text-ink-muted">Value</p>
          {cell?.status === "done" && result ? (
            <div className="mt-1 space-y-2">
              {valueText !== null ? (
                url !== null ? (
                  <a
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="block break-all font-medium text-accent-strong hover:underline"
                  >
                    {valueText}
                  </a>
                ) : (
                  <p className="break-words text-ink">{valueText}</p>
                )
              ) : (
                <p className="text-ink-muted">—</p>
              )}
              <StatusBadge status={result.status} sources={sources} />
              {result.note ? (
                <p className="text-xs text-ink-muted">{result.note}</p>
              ) : null}
            </div>
          ) : (
            <p className="mt-1 flex items-center gap-2 text-ink-muted">
              {cellStateLabel(cell?.status ?? "idle")}
              {cell?.status === "error" && onRetryErrors ? (
                <button
                  type="button"
                  onClick={onRetryErrors}
                  className="inline-flex items-center gap-1 rounded-full border border-danger-line bg-danger-bg px-2 py-0.5 text-xs font-medium text-danger-ink hover:bg-danger-line/40"
                  title="Start a run that retries every error cell"
                >
                  <RotateCcw aria-hidden="true" className="h-3 w-3" />
                  Retry error cells
                </button>
              ) : null}
            </p>
          )}
          {cell?.error ? (
            <p className="mt-1 break-words text-xs text-danger-ink">{cell.error}</p>
          ) : null}
          {cell?.stale ? (
            <p className="mt-1 text-xs font-medium text-inferred-ink">
              Stale — the column prompt changed after this value was written.
            </p>
          ) : null}
        </section>

        <SourceList sources={sources} />

        {cell?.rendered_prompt ? (
          <section>
            <p className="text-xs font-mono font-medium uppercase tracking-wide text-ink-muted">
              Rendered prompt
            </p>
            <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-card bg-surface-muted p-2 font-mono text-xs text-ink">
              {cell.rendered_prompt}
            </pre>
          </section>
        ) : null}

        <section>
          <p className="text-xs font-mono font-medium uppercase tracking-wide text-ink-muted">Details</p>
          <dl className="mt-1 space-y-1 text-xs text-ink-muted">
            {typeof cell?.cost === "number" ? (
              <div className="flex justify-between gap-2">
                <dt>Provider cost</dt>
                <dd className="tabular-nums text-ink">{formatUsd(cell.cost)}</dd>
              </div>
            ) : null}
            <div className="flex justify-between gap-2">
              <dt>Revision</dt>
              <dd className="tabular-nums text-ink">{cell?.revision ?? 0}</dd>
            </div>
            {cell?.created_at ? (
              <div className="flex justify-between gap-2">
                <dt>Created</dt>
                <dd className="text-ink">{formatDateTime(cell.created_at) ?? cell.created_at}</dd>
              </div>
            ) : null}
            {cell?.updated_at ? (
              <div className="flex justify-between gap-2">
                <dt>Updated</dt>
                <dd className="text-ink">{formatDateTime(cell.updated_at) ?? cell.updated_at}</dd>
              </div>
            ) : null}
          </dl>
        </section>

        <section>
          <p className="text-xs font-mono font-medium uppercase tracking-wide text-ink-muted">
            Revision history
          </p>
          {revisions === null ? (
            <p className="mt-1 text-xs text-ink-muted">Loading…</p>
          ) : revisions.length === 0 ? (
            <p className="mt-1 text-xs text-ink-muted">No completed writes yet.</p>
          ) : (
            <ul className="mt-1 space-y-1.5">
              {revisions.map((rev) => (
                <li key={rev.id} className="text-xs text-ink-muted">
                  <span className="font-mono tabular-nums text-ink">r{rev.revision}</span>
                  {" · "}
                  {rev.result?.status ?? (rev.error ? "error" : "empty")}
                  {rev.error ? ` — ${rev.error}` : ""}
                  {" · "}
                  {formatDateTime(rev.created_at) ?? rev.created_at}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </aside>
  );
}
