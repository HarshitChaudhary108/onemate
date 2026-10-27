"use client";

import { X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { listHubSpotLinks, pushToHubSpot } from "@/lib/api/integrations";
import type { PushApprovalInput, PushItemResult } from "@/lib/schema/integration";
import { cellKey, useSheetStore } from "@/lib/store/sheet-store";
import { formatCellValue } from "@/lib/utils/cell-format";

/**
 * Push approval dialog (I4 / plan §3.4): linked company rows × their done
 * cells → checked cells are written to `outmate_*` properties on the same
 * remote records. The dialog sends WHICH cells and WHICH property — never
 * the values; the backend re-reads them server-side.
 *
 * Each row comes from `listHubSpotLinks` (company links only — contacts
 * are pull-only snapshots in v1); each property is chosen inside
 * `ALLOWED_PUSH_PROPERTIES`, the four writable `outmate_*` properties —
 * standard (sales-owned) properties are never offered.
 */

/** Mirrors `ALLOWED_PUSH_PROPERTIES` in backend/app/integrations/hubspot/mapping.py. */
const ALLOWED_PUSH_PROPERTIES = [
  "outmate_tech_stack",
  "outmate_description",
  "outmate_growth_signals",
  "outmate_leadership",
] as const;

/** Mirrors `slugify_column`: lowercase, every `[^a-z0-9]` run → `_`,
 *  edges trimmed, capped at 40 chars. */
function slugifyColumn(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 40);
}

/** The `outmate_<slug>` prefill — advisory only; values outside the
 *  allowlist fall back to the first writable property (the backend would
 *  reject them anyway). */
function initialProperty(columnName: string): string {
  const suggested = `outmate_${slugifyColumn(columnName)}`;
  return (ALLOWED_PUSH_PROPERTIES as readonly string[]).includes(suggested)
    ? suggested
    : ALLOWED_PUSH_PROPERTIES[0];
}

type PushCell = { columnId: string; columnName: string; valueText: string };
type LinkedRow = { rowId: string; remoteId: string; label: string; cells: PushCell[] };

type LinksState =
  | { kind: "loading" }
  | { kind: "ready"; links: { rowId: string; remoteId: string }[] }
  | { kind: "error"; message: string };

const NETWORK_MESSAGE = "Could not reach the integrations service. Check that the backend is running.";

export function PushHubSpotDialog({
  sheetId,
  onClose,
}: {
  sheetId: string;
  onClose: () => void;
}) {
  const cells = useSheetStore((state) => state.cells);
  const rowsById = useSheetStore((state) => state.rowsById);
  const columnsById = useSheetStore((state) => state.columnsById);

  const [linksState, setLinksState] = useState<LinksState>({ kind: "loading" });
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [properties, setProperties] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<PushItemResult[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    void listHubSpotLinks(sheetId).then((result) => {
      if (cancelled) return;
      if (result.kind === "ok") {
        setLinksState({
          kind: "ready",
          links: result.data.links.flatMap((link) =>
            link.object_type === "company" && link.row_id !== null
              ? [{ rowId: link.row_id, remoteId: link.remote_id }]
              : [],
          ),
        });
      } else {
        setLinksState({
          kind: "error",
          message:
            result.kind === "api_error"
              ? result.message
              : result.kind === "network"
                ? NETWORK_MESSAGE
                : "The links response could not be read.",
        });
      }
    });
    return () => {
      cancelled = true;
    };
  }, [sheetId]);

  /** Linked company rows × their done, non-null cells — recomputed as the
   *  store's cells map changes (a finishing cell becomes pushable live). */
  const rows = useMemo<LinkedRow[]>(() => {
    if (linksState.kind !== "ready") return [];
    return linksState.links.map((link) => {
      const row = rowsById[link.rowId];
      const label =
        row?.resolved_name ?? row?.input_name ?? row?.input_domain ?? link.rowId;
      const done: PushCell[] = [];
      for (const cell of Object.values(cells)) {
        if (cell.row_id !== link.rowId) continue;
        if (cell.status !== "done" || cell.result?.value == null) continue;
        const column = columnsById[cell.column_id];
        done.push({
          columnId: cell.column_id,
          columnName: column?.name ?? cell.column_id,
          valueText:
            formatCellValue(cell.result.value, column?.output_type ?? "text") ??
            String(cell.result.value),
        });
      }
      done.sort((a, b) => a.columnName.localeCompare(b.columnName));
      return { rowId: link.rowId, remoteId: link.remoteId, label, cells: done };
    });
  }, [linksState, cells, rowsById, columnsById]);

  const propFor = (key: string, columnName: string) =>
    properties[key] ?? initialProperty(columnName);

  const selectedCount = useMemo(
    () =>
      rows.reduce(
        (n, row) =>
          n + row.cells.filter((cell) => selected.has(cellKey(row.rowId, cell.columnId))).length,
        0,
      ),
    [rows, selected],
  );

  function toggleCell(key: string) {
    const next = new Set(selected);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setSelected(next);
  }

  async function handlePush() {
    if (selectedCount === 0 || submitting) return;
    setSubmitting(true);
    setError(null);
    // Re-pushing after results replaces them — a retry after fixing a
    // skipped/error item must never be locked out of its own dialog.
    setResults(null);
    const approvals: PushApprovalInput[] = [];
    for (const row of rows) {
      for (const cell of row.cells) {
        const key = cellKey(row.rowId, cell.columnId);
        if (!selected.has(key)) continue;
        approvals.push({
          row_id: row.rowId,
          column_id: cell.columnId,
          property: propFor(key, cell.columnName),
        });
      }
    }
    const result = await pushToHubSpot(sheetId, approvals);
    setSubmitting(false);
    if (result.kind !== "ok") {
      setError(
        result.kind === "api_error"
          ? result.message
          : result.kind === "network"
            ? NETWORK_MESSAGE
            : result.kind === "validation"
              ? "The backend rejected the push request."
              : "The push response could not be read.",
      );
      return;
    }
    setResults(result.data.results);
  }

  const cellName = (rowId: string, columnId: string) =>
    columnsById[columnId]?.name ?? columnId;
  const rowLabel = (rowId: string) =>
    rows.find((row) => row.rowId === rowId)?.label ?? rowId;

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-ink-strong/30 p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="push-hubspot-title"
        data-testid="push-hubspot-dialog"
        className="flex max-h-[85vh] w-full max-w-xl flex-col rounded-card border border-line bg-surface p-5 shadow-card"
      >
        <div className="flex items-start justify-between gap-2">
          <div>
            <h2 id="push-hubspot-title" className="text-base font-semibold text-ink-strong">
              Push to HubSpot
            </h2>
            <p className="mt-1 text-xs text-ink-muted">
              Checked cells are written to the linked company&apos;s{" "}
              <span className="font-mono">outmate_*</span> property — approved
              findings only, standard properties are never touched.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close push dialog"
            className="rounded p-1 text-ink-muted hover:bg-surface-muted hover:text-ink"
          >
            <X aria-hidden="true" className="h-4 w-4" />
          </button>
        </div>

        <div className="mt-3 min-h-0 flex-1 overflow-y-auto">
          {linksState.kind === "loading" ? (
            <p role="status" className="text-xs text-ink-muted">
              Loading linked companies…
            </p>
          ) : null}

          {linksState.kind === "error" ? (
            <p role="alert" className="text-xs font-medium text-danger-ink">
              {linksState.message}
            </p>
          ) : null}

          {linksState.kind === "ready" && rows.length === 0 ? (
            <p className="text-xs text-ink-muted">
              No companies linked to this sheet — import companies from HubSpot on
              the integrations page first.
            </p>
          ) : null}

          {rows.map((row) => (
            <section key={row.rowId} className="mt-3">
              <h3 className="text-xs font-semibold text-ink-strong">
                {row.label}{" "}
                <span className="font-mono font-normal text-ink-muted">
                  → {row.remoteId}
                </span>
              </h3>
              {row.cells.length === 0 ? (
                <p className="mt-1 text-xs text-ink-muted">
                  No completed cells to push.
                </p>
              ) : (
                <ul className="mt-1 space-y-1.5">
                  {row.cells.map((cell) => {
                    const key = cellKey(row.rowId, cell.columnId);
                    return (
                      <li
                        key={key}
                        onClick={() => toggleCell(key)}
                        className="flex cursor-pointer items-center gap-2 rounded-card border border-line px-2 py-1.5 hover:bg-surface-muted"
                      >
                        <input
                          type="checkbox"
                          aria-label={`Push ${cell.columnName} for ${row.label}`}
                          checked={selected.has(key)}
                          onChange={() => toggleCell(key)}
                          onClick={(event) => event.stopPropagation()}
                        />
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-xs font-medium text-ink">
                            {cell.columnName}
                          </p>
                          <p
                            className="truncate text-xs text-ink-muted"
                            title={cell.valueText}
                          >
                            {cell.valueText}
                          </p>
                        </div>
                        <label className="sr-only" htmlFor={`prop-${key}`}>
                          HubSpot property for {cell.columnName}
                        </label>
                        <select
                          id={`prop-${key}`}
                          value={propFor(key, cell.columnName)}
                          onClick={(event) => event.stopPropagation()}
                          onChange={(event) =>
                            setProperties((prev) => ({
                              ...prev,
                              [key]: event.target.value,
                            }))
                          }
                          className="rounded-card border border-line-strong bg-surface px-2 py-1 font-mono text-xs text-ink"
                        >
                          {ALLOWED_PUSH_PROPERTIES.map((property) => (
                            <option key={property} value={property}>
                              {property}
                            </option>
                          ))}
                        </select>
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>
          ))}
        </div>

        {error !== null ? (
          <p role="alert" className="mt-2 text-xs font-medium text-danger-ink">
            {error}
          </p>
        ) : null}

        {results !== null ? (
          <div role="status" className="mt-3 max-h-40 overflow-y-auto">
            <p className="text-xs font-medium text-ink">
              Push results ({results.length}{" "}
              {results.length === 1 ? "item" : "items"}):
            </p>
            <ul className="mt-1 space-y-1">
              {results.map((item, index) => (
                <li
                  key={`${item.row_id}:${item.column_id}:${item.property}:${index}`}
                  className="text-xs"
                >
                  <span
                    className={
                      item.status === "pushed"
                        ? "font-medium text-verified-ink"
                        : item.status === "skipped"
                          ? "font-medium text-unavailable-ink"
                          : "font-medium text-danger-ink"
                    }
                  >
                    {item.status}
                  </span>{" "}
                  <span className="text-ink">
                    {rowLabel(item.row_id)} · {cellName(item.row_id, item.column_id)} →{" "}
                    <span className="font-mono">{item.property}</span>
                  </span>
                  {item.detail != null ? (
                    <span className="text-ink-muted"> ({item.detail})</span>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-card border border-line-strong bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface-muted"
          >
            {results !== null ? "Done" : "Cancel"}
          </button>
          <button
            type="button"
            onClick={() => void handlePush()}
            disabled={submitting || selectedCount === 0}
            title={
              selectedCount === 0 && !submitting
                ? "Tick at least one cell to push"
                : undefined
            }
            className="rounded-card bg-accent px-3 py-1.5 text-xs font-semibold text-white hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-60"
          >
            {submitting
              ? "Pushing…"
              : `Push ${selectedCount || ""} ${selectedCount === 1 ? "cell" : "cells"}`}
          </button>
        </div>
      </div>
    </div>
  );
}
