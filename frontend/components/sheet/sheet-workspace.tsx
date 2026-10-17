"use client";

import { TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { CellInspector } from "@/components/sheet/cell-inspector";
import { ImportRowsDialog } from "@/components/sheet/import-rows-dialog";
import { PromptBar, type PromptBarApi } from "@/components/sheet/prompt-bar";
import { RowProfileSheet } from "@/components/sheet/row-profile-sheet";
import { SheetGrid, type SheetGridApi } from "@/components/sheet/sheet-grid";
import { SheetToolbar } from "@/components/sheet/sheet-toolbar";
import { ErrorAlert } from "@/components/error-alert";
import {
  cancelRun,
  createColumns,
  createRun,
  planColumns,
  type SheetResult,
} from "@/lib/api/sheets";
import type { ColumnSpecInput, PlanResult } from "@/lib/schema/sheet";
import { cellKey, useSheetStore } from "@/lib/store/sheet-store";

/**
 * The sheets workspace (P7): toolbar → grid → docked prompt bar, with the
 * right-hand inspector and the deep-profile sheet as side panels. The store
 * owns the SSE subscription (`connect` is called once per sheet, in an
 * effect — no component ever manages a subscription itself, plan risk #8).
 */
export function SheetWorkspace({ sheetId }: { sheetId: string }) {
  const hydrated = useSheetStore((state) => state.hydrated);
  const connection = useSheetStore((state) => state.connection);
  const storeError = useSheetStore((state) => state.error);
  const cells = useSheetStore((state) => state.cells);

  const [inspected, setInspected] = useState<{ rowId: string; columnId: string } | null>(
    null,
  );
  const [profileRowId, setProfileRowId] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [planning, setPlanning] = useState(false);
  const [plan, setPlan] = useState<PlanResult | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const gridApi = useRef<SheetGridApi>(null);
  const promptApi = useRef<PromptBarApi>(null);

  useEffect(() => {
    useSheetStore.getState().connect(sheetId);
    return () => {
      useSheetStore.getState().disconnect();
    };
  }, [sheetId]);

  // Ctrl/⌘+K focuses the prompt bar — the one global shortcut in the spec.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        promptApi.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Esc closes the inspector wherever focus sits — unless something closer
  // to the keypress already handled it (e.g. the @ autocomplete).
  useEffect(() => {
    if (inspected === null) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented) {
        setInspected(null);
        gridApi.current?.focusActiveCell();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [inspected]);

  const handleInspectCell = useCallback((rowId: string, columnId: string) => {
    setProfileRowId(null);
    setInspected({ rowId, columnId });
  }, []);

  const handleCloseInspector = useCallback(() => setInspected(null), []);

  const handleDeepProfile = useCallback((rowId: string) => {
    setInspected(null);
    setProfileRowId(rowId);
  }, []);

  const handleCancelRun = useCallback(
    async (runId: string) => {
      setCancelling(true);
      setActionError(null);
      const result = await cancelRun(sheetId, runId);
      setCancelling(false);
      if (result.kind !== "ok") {
        setActionError(
          result.kind === "api_error"
            ? result.message
            : "The cancel request could not be completed.",
        );
        return;
      }
      // Queued cells return to idle without events — the resnapshot lands it.
      useSheetStore.getState().resnapshot();
    },
    [sheetId],
  );

  const handlePlan = useCallback(
    async (prompt: string): Promise<SheetResult<PlanResult>> => {
      setPlanning(true);
      setPlanError(null);
      const result = await planColumns(sheetId, prompt);
      setPlanning(false);
      if (result.kind === "ok") {
        setPlan(result.data);
      }
      return result;
    },
    [sheetId],
  );

  const handleApply = useCallback(
    async (columns: ColumnSpecInput[], run: boolean) => {
      setApplying(true);
      setPlanError(null);
      const result = await createColumns(sheetId, columns, run);
      setApplying(false);
      if (result.kind === "ok") {
        setPlan(null);
        // New columns' cells are not event-driven — the resnapshot lands
        // them (and the queued states when run=true).
        useSheetStore.getState().resnapshot();
        return;
      }
      setPlanError(
        result.kind === "api_error"
          ? result.message
          : result.kind === "validation"
            ? "The backend rejected the column spec — check the highlighted fields."
            : result.kind === "network"
              ? "Could not reach the sheets service. Check that the backend is running."
              : "The columns response could not be read.",
      );
    },
    [sheetId],
  );

  // The only cell-level retry the API offers is an error-scoped run — the
  // control is labelled to say exactly that.
  const handleRetryErrors = useCallback(async () => {
    setActionError(null);
    const result = await createRun(sheetId, { scope: "error" });
    if (result.kind !== "ok") {
      setActionError(
        result.kind === "api_error"
          ? result.message
          : "The retry run could not be started.",
      );
    }
    // A started run publishes a run event; the store resnapshots on it.
  }, [sheetId]);

  const handleImported = useCallback(() => {
    useSheetStore.getState().resnapshot();
  }, []);

  // Mock honesty (plan §4.6): a banner while any cell's sources carry mock
  // provenance — the whole store is checked so the banner never flickers
  // on scroll; "visible" cells are a subset of loaded cells.
  const hasMock = useMemo(
    () =>
      Object.values(cells).some((cell) =>
        (cell.result?.sources ?? []).some((source) => source.mock),
      ),
    [cells],
  );

  return (
    <div className="flex h-screen flex-col bg-page">
      <SheetToolbar
        onCancelRun={(runId) => void handleCancelRun(runId)}
        onImportRows={() => setImportOpen(true)}
        cancelling={cancelling}
      />

      {hasMock ? (
        <div
          role="status"
          data-testid="sheet-mock-banner"
          className="border-b border-inferred-line bg-inferred-bg px-4 py-2 text-xs text-inferred-ink"
        >
          <p className="flex items-center gap-2 font-medium">
            <TriangleAlert aria-hidden="true" className="h-3.5 w-3.5" />
            Some visible cell values carry mock provenance — synthetic provider data, not
            real company facts.
          </p>
        </div>
      ) : null}

      {actionError !== null ? (
        <div className="border-b border-danger-line bg-danger-bg px-4 py-2 text-xs text-danger-ink" role="alert">
          {actionError}
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1">
          {connection === "unavailable" || storeError !== null ? (
            <div className="p-4">
              <ErrorAlert
                status={null}
                code="snapshot_unavailable"
                message={storeError ?? "The sheet could not be loaded."}
                requestId={null}
              />
            </div>
          ) : !hydrated ? (
            <p role="status" className="p-4 text-sm text-ink-muted">
              Loading sheet…
            </p>
          ) : (
            <SheetGrid
              apiRef={gridApi}
              onInspectCell={handleInspectCell}
              onCloseInspector={handleCloseInspector}
              onDeepProfile={handleDeepProfile}
              onRetryErrors={() => void handleRetryErrors()}
              selectedCellKey={inspected !== null ? cellKey(inspected.rowId, inspected.columnId) : null}
            />
          )}
        </div>

        {inspected !== null ? (
          <CellInspector
            sheetId={sheetId}
            rowId={inspected.rowId}
            columnId={inspected.columnId}
            onClose={handleCloseInspector}
            onRetryErrors={() => void handleRetryErrors()}
          />
        ) : null}

        {profileRowId !== null ? (
          <RowProfileSheet rowId={profileRowId} onClose={() => setProfileRowId(null)} />
        ) : null}
      </div>

      <PromptBar
        planning={planning}
        plan={plan}
        planError={planError}
        onPlan={handlePlan}
        onApply={(columns, run) => void handleApply(columns, run)}
        onDismissPlan={() => setPlan(null)}
        applying={applying}
        apiRef={promptApi}
      />

      {importOpen ? (
        <ImportRowsDialog
          sheetId={sheetId}
          onClose={() => setImportOpen(false)}
          onImported={handleImported}
        />
      ) : null}
    </div>
  );
}
