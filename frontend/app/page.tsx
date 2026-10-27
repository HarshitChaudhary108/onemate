"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { ErrorAlert } from "@/components/error-alert";
import { createSheet, deleteSheet, listSheets, type SheetResult } from "@/lib/api/sheets";
import type { Sheet } from "@/lib/schema/sheet";

/**
 * Sheets entry (D9): list existing sheets, create a new one, open it.
 * The previous single-company workspace moved unchanged to /research.
 * All data comes from the sheets API at request time — nothing is
 * invented when the list cannot be loaded.
 */

type ListState =
  | { kind: "loading" }
  | { kind: "ready"; sheets: Sheet[] }
  | {
      kind: "error";
      status: number | null;
      code: string;
      message: string;
      requestId: string | null;
    };

const NETWORK_MESSAGE =
  "Could not reach the sheets service. Check that the backend is running and that NEXT_PUBLIC_API_BASE_URL points at it, then try again.";
const INVALID_RESPONSE_MESSAGE =
  "The sheets service returned a response this app could not read. Nothing was invented to fill the gap.";

function errorState(result: Exclude<SheetResult<unknown>, { kind: "ok" }>): ListState {
  switch (result.kind) {
    case "api_error":
      return {
        kind: "error",
        status: result.status,
        code: result.code,
        message: result.message,
        requestId: result.requestId,
      };
    case "validation":
      return {
        kind: "error",
        status: 422,
        code: "validation",
        message: "The sheet name was rejected by the backend.",
        requestId: null,
      };
    case "invalid_response":
      return {
        kind: "error",
        status: null,
        code: "invalid_response",
        message: INVALID_RESPONSE_MESSAGE,
        requestId: null,
      };
    case "network":
      return {
        kind: "error",
        status: null,
        code: "network",
        message: NETWORK_MESSAGE,
        requestId: null,
      };
  }
}

export default function SheetsHome() {
  const router = useRouter();
  const [state, setState] = useState<ListState>({ kind: "loading" });
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void listSheets().then((result) => {
      if (cancelled) return;
      setState(
        result.kind === "ok"
          ? { kind: "ready", sheets: result.data.sheets }
          : errorState(result),
      );
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleCreate = useCallback(
    async (event: FormEvent) => {
      event.preventDefault();
      const trimmed = name.trim();
      if (!trimmed || creating) return;
      setCreating(true);
      setCreateError(null);
      const result = await createSheet(trimmed);
      if (result.kind === "ok") {
        router.push(`/sheets/${result.data.id}`);
        return;
      }
      setCreating(false);
      setCreateError(
        result.kind === "api_error"
          ? result.message
          : result.kind === "network"
            ? NETWORK_MESSAGE
            : INVALID_RESPONSE_MESSAGE,
      );
    },
    [name, creating, router],
  );

  const handleDelete = useCallback(
    async (sheetId: string) => {
      if (deletingId !== null) return;
      setDeletingId(sheetId);
      setDeleteError(null);
      const result = await deleteSheet(sheetId);
      setDeletingId(null);
      if (result.kind === "ok") {
        setConfirmDeleteId(null);
        setState((prev) =>
          prev.kind === "ready"
            ? { kind: "ready", sheets: prev.sheets.filter((s) => s.id !== sheetId) }
            : prev,
        );
        return;
      }
      setDeleteError(
        result.kind === "api_error"
          ? result.message
          : result.kind === "network"
            ? NETWORK_MESSAGE
            : INVALID_RESPONSE_MESSAGE,
      );
    },
    [deletingId],
  );

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
      <header className="mb-6">
        <h1 className="text-3xl font-semibold tracking-tight text-ink-strong">Outmate</h1>
        <p className="mt-1 max-w-2xl text-sm text-ink-muted">
          Paste a company list, describe the columns you need, and watch cells fill in —
          every value carries its provider source, and anything the run could not support
          stays explicitly unavailable. For a single deep profile, use{" "}
          <Link href="/research" className="text-accent underline underline-offset-2">
            the research tool
          </Link>
          . Connect a CRM on{" "}
          <Link href="/integrations" className="text-accent underline underline-offset-2">
            the integrations page
          </Link>
          .
        </p>
      </header>

      <form onSubmit={handleCreate} className="flex items-center gap-3">
        <label htmlFor="new-sheet-name" className="sr-only">
          New sheet name
        </label>
        <input
          id="new-sheet-name"
          type="text"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="New sheet name"
          maxLength={200}
          className="w-72 rounded-card border border-line bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-muted"
        />
        <button
          type="submit"
          disabled={creating || !name.trim()}
          className="rounded-card bg-accent px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-50"
        >
          {creating ? "Creating…" : "New sheet"}
        </button>
      </form>
      {createError !== null ? (
        <p role="alert" className="mt-2 text-sm text-danger-ink">
          {createError}
        </p>
      ) : null}

      <section aria-label="Your sheets" className="mt-8">
        {state.kind === "loading" ? (
          <p role="status" className="text-sm text-ink-muted">
            Loading sheets…
          </p>
        ) : null}

        {state.kind === "error" ? (
          <ErrorAlert
            status={state.status}
            code={state.code}
            message={state.message}
            requestId={state.requestId}
          />
        ) : null}

        {deleteError !== null ? (
          <p role="alert" className="mb-2 text-sm text-danger-ink">
            {deleteError}
          </p>
        ) : null}

        {state.kind === "ready" && state.sheets.length === 0 ? (
          <p className="text-sm text-ink-muted">
            No sheets yet — create one above to get started.
          </p>
        ) : null}

        {state.kind === "ready" && state.sheets.length > 0 ? (
          <ul className="divide-y divide-line rounded-card border border-line bg-surface shadow-card">
            {state.sheets.map((sheet) => (
              <li key={sheet.id} className="flex items-stretch">
                <Link
                  href={`/sheets/${sheet.id}`}
                  className="flex min-w-0 flex-1 items-center justify-between gap-3 px-4 py-3 text-sm text-ink transition-colors hover:bg-surface-muted"
                >
                  <span className="truncate font-medium text-ink-strong">{sheet.name}</span>
                  <span className="shrink-0 font-mono text-xs text-ink-muted">
                    {new Date(sheet.updated_at).toLocaleString()}
                  </span>
                </Link>
                <div className="flex shrink-0 items-center gap-1.5 pr-2">
                  {confirmDeleteId === sheet.id ? (
                    <>
                      <button
                        type="button"
                        onClick={() => void handleDelete(sheet.id)}
                        disabled={deletingId !== null}
                        className="rounded-card border border-danger-line bg-danger-bg px-2 py-1 text-xs font-medium text-danger-ink disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {deletingId === sheet.id ? "Deleting…" : "Confirm delete"}
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirmDeleteId(null)}
                        className="rounded-card border border-line-strong bg-surface px-2 py-1 text-xs font-medium text-ink hover:bg-surface-muted"
                      >
                        Cancel
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      aria-label={`Delete ${sheet.name}`}
                      onClick={() => setConfirmDeleteId(sheet.id)}
                      className="rounded-card border border-line-strong bg-surface px-2 py-1 text-xs font-medium text-ink hover:bg-surface-muted"
                    >
                      Delete
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        ) : null}
      </section>
    </div>
  );
}
