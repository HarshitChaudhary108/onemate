"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";

import { ErrorAlert } from "@/components/error-alert";
import {
  connectHubSpotUrl,
  disconnectHubSpot,
  hubspotStatus,
  importFromHubSpot,
  listHubSpotObjects,
  refreshHubSpot,
} from "@/lib/api/integrations";
import { listSheets, type SheetResult } from "@/lib/api/sheets";
import type {
  HubSpotStatus,
  ImportResponse,
  RefreshChange,
  RemoteObjectPreview,
} from "@/lib/schema/integration";
import type { Sheet } from "@/lib/schema/sheet";

/**
 * Integrations page (I4 / plan §7): the HubSpot card — connection status,
 * portal id, scopes, import counts, and last sync, ALL from GET /status;
 * nothing is hardcoded. Buttons: Connect (an anchor to the OAuth entry
 * URL — navigated to, never fetched), Disconnect (confirm → DELETE →
 * refetch), Import (sheet picker + remote-object preview with checkboxes),
 * and Refresh (sheet picker → change list).
 *
 * `?hubspot=connected` and `?hubspot_error=<code>` land here from the
 * backend's OAuth callback redirect; `useSearchParams` suspends during
 * prerender, so the view sits behind a Suspense boundary (Next.js docs:
 * "Missing Suspense boundary with useSearchParams").
 */

type StatusState =
  | { kind: "loading" }
  | { kind: "ready"; status: HubSpotStatus }
  | {
      kind: "error";
      status: number | null;
      code: string;
      message: string;
      requestId: string | null;
    };

type ObjectsState =
  | { kind: "loading" }
  | { kind: "ready"; companies: RemoteObjectPreview[]; contacts: RemoteObjectPreview[] }
  | { kind: "error"; message: string };

const NETWORK_MESSAGE =
  "Could not reach the integrations service. Check that the backend is running and that NEXT_PUBLIC_API_BASE_URL points at it, then try again.";
const INVALID_RESPONSE_MESSAGE =
  "The integrations service returned a response this app could not read. Nothing was invented to fill the gap.";

/** Safe one-line messages for the OAuth callback's `?hubspot_error=` code —
 *  a bounded machine token from our own backend, echoed verbatim as the
 *  fallback only (never free text). */
const HUBSPOT_ERROR_MESSAGES: Record<string, string> = {
  oauth_state_mismatch: "The HubSpot sign-in session expired — try connecting again.",
  oauth_exchange_failed: "HubSpot could not complete the connection — try again.",
  hubspot_not_configured:
    "HubSpot is not configured on the backend — set the client id, client secret, and token key.",
  hubspot_not_connected: "No HubSpot connection is active.",
  hubspot_api_error: "HubSpot returned an error while connecting — try again.",
};

function resultMessage(result: Exclude<SheetResult<unknown>, { kind: "ok" }>): string {
  switch (result.kind) {
    case "api_error":
      return result.message;
    case "network":
      return NETWORK_MESSAGE;
    case "validation":
      return "The backend rejected the request.";
    case "invalid_response":
      return INVALID_RESPONSE_MESSAGE;
  }
}

function statusErrorState(
  result: Exclude<SheetResult<unknown>, { kind: "ok" }>,
): StatusState {
  return result.kind === "api_error"
    ? {
        kind: "error",
        status: result.status,
        code: result.code,
        message: result.message,
        requestId: result.requestId,
      }
    : {
        kind: "error",
        status: result.kind === "validation" ? 422 : null,
        code: result.kind === "validation" ? "validation" : result.kind,
        message: resultMessage(result),
        requestId: null,
      };
}

/** Display label for a remote object — only properties the remote returned. */
function remoteLabel(object: RemoteObjectPreview): string {
  const props = object.properties;
  if (object.object_type === "company") {
    return props.name ?? props.domain ?? object.remote_id;
  }
  const name = [props.firstname, props.lastname].filter(Boolean).join(" ").trim();
  return name || (props.email ?? object.remote_id);
}

function remoteDetail(object: RemoteObjectPreview): string | null {
  const props = object.properties;
  if (object.object_type === "company") {
    return props.domain ?? props.website ?? null;
  }
  const name = [props.firstname, props.lastname].filter(Boolean).join(" ").trim();
  return props.jobtitle ?? (name ? (props.email ?? null) : null);
}

function IntegrationsView() {
  const searchParams = useSearchParams();
  const connectedParam = searchParams.get("hubspot");
  const errorParam = searchParams.get("hubspot_error");

  const [state, setState] = useState<StatusState>({ kind: "loading" });
  const [actionError, setActionError] = useState<string | null>(null);

  // Sheets for the import/refresh pickers — fetched lazily on first open.
  const [sheets, setSheets] = useState<Sheet[] | null>(null);
  const [sheetsError, setSheetsError] = useState<string | null>(null);

  // Import flow.
  const [importOpen, setImportOpen] = useState(false);
  const [objects, setObjects] = useState<ObjectsState | null>(null);
  const [importSheetId, setImportSheetId] = useState("");
  const [selectedCompanies, setSelectedCompanies] = useState<ReadonlySet<string>>(new Set());
  const [selectedContacts, setSelectedContacts] = useState<ReadonlySet<string>>(new Set());
  const [importBusy, setImportBusy] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [importResult, setImportResult] = useState<ImportResponse | null>(null);

  // Refresh flow.
  const [refreshOpen, setRefreshOpen] = useState(false);
  const [refreshSheetId, setRefreshSheetId] = useState("");
  const [refreshBusy, setRefreshBusy] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [refreshChanges, setRefreshChanges] = useState<RefreshChange[] | null>(null);

  // Disconnect confirm → DELETE → refetch.
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);

  const load = useCallback(async () => {
    const result = await hubspotStatus();
    setState(
      result.kind === "ok"
        ? { kind: "ready", status: result.data }
        : statusErrorState(result),
    );
  }, []);

  useEffect(() => {
    let cancelled = false;
    void hubspotStatus().then((result) => {
      if (cancelled) return;
      setState(
        result.kind === "ok"
          ? { kind: "ready", status: result.data }
          : statusErrorState(result),
      );
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const ensureSheets = useCallback(async () => {
    if (sheets !== null) return;
    const result = await listSheets();
    if (result.kind === "ok") {
      setSheets(result.data.sheets);
      setSheetsError(null);
    } else {
      setSheetsError(resultMessage(result));
    }
  }, [sheets]);

  const openImport = useCallback(() => {
    setImportOpen(true);
    setImportResult(null);
    setImportError(null);
    setSelectedCompanies(new Set());
    setSelectedContacts(new Set());
    void ensureSheets();
    setObjects({ kind: "loading" });
    void Promise.all([
      listHubSpotObjects("companies"),
      listHubSpotObjects("contacts"),
    ]).then(([companies, contacts]) => {
      if (companies.kind === "ok" && contacts.kind === "ok") {
        setObjects({ kind: "ready", companies: companies.data, contacts: contacts.data });
        return;
      }
      const failed = companies.kind !== "ok" ? companies : contacts;
      setObjects(
        failed.kind === "ok"
          ? { kind: "error", message: INVALID_RESPONSE_MESSAGE }
          : { kind: "error", message: resultMessage(failed) },
      );
    });
  }, [ensureSheets]);

  const toggleId = useCallback(
    (set: ReadonlySet<string>, id: string, apply: (next: ReadonlySet<string>) => void) => {
      const next = new Set(set);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      apply(next);
    },
    [],
  );

  const submitImport = useCallback(async () => {
    if (!importSheetId || importBusy) return;
    setImportBusy(true);
    setImportError(null);
    setImportResult(null);
    const result = await importFromHubSpot(importSheetId, {
      company_ids: [...selectedCompanies],
      contact_ids: [...selectedContacts],
    });
    setImportBusy(false);
    if (result.kind === "ok") {
      setImportResult(result.data);
      void load(); // import counts + last_sync_at moved — refetch.
    } else {
      setImportError(resultMessage(result));
    }
  }, [importSheetId, importBusy, selectedCompanies, selectedContacts, load]);

  const openRefresh = useCallback(() => {
    setRefreshOpen(true);
    setRefreshChanges(null);
    setRefreshError(null);
    void ensureSheets();
  }, [ensureSheets]);

  const submitRefresh = useCallback(async () => {
    if (!refreshSheetId || refreshBusy) return;
    setRefreshBusy(true);
    setRefreshError(null);
    setRefreshChanges(null);
    const result = await refreshHubSpot(refreshSheetId);
    setRefreshBusy(false);
    if (result.kind === "ok") {
      setRefreshChanges(result.data.changes);
      void load(); // last_sync_at moved — refetch.
    } else {
      setRefreshError(resultMessage(result));
    }
  }, [refreshSheetId, refreshBusy, load]);

  const submitDisconnect = useCallback(async () => {
    if (disconnecting) return;
    setDisconnecting(true);
    setActionError(null);
    const result = await disconnectHubSpot();
    setDisconnecting(false);
    setConfirmDisconnect(false);
    if (result.kind === "ok") {
      setImportOpen(false);
      setRefreshOpen(false);
      void load();
    } else {
      setActionError(resultMessage(result));
    }
  }, [disconnecting, load]);

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
      <header className="mb-6">
        <Link
          href="/"
          className="text-xs font-medium text-accent underline-offset-2 hover:underline"
        >
          ← Sheets
        </Link>
        <h1 className="mt-1 text-3xl font-semibold tracking-tight text-ink-strong">
          Integrations
        </h1>
        <p className="mt-1 max-w-2xl text-sm text-ink-muted">
          Connect a CRM to bring records in for research and push approved findings
          back — HubSpot keeps every standard property; Outmate writes only its own
          <span className="font-mono"> outmate_*</span> namespace.
        </p>
      </header>

      {connectedParam === "connected" ? (
        <p
          role="status"
          data-testid="hubspot-outcome"
          className="mb-4 rounded-card border border-verified-line bg-verified-bg px-4 py-2 text-sm text-verified-ink"
        >
          HubSpot connected.
        </p>
      ) : null}
      {errorParam !== null ? (
        <p
          role="alert"
          data-testid="hubspot-outcome"
          className="mb-4 rounded-card border border-danger-line bg-danger-bg px-4 py-2 text-sm text-danger-ink"
        >
          {HUBSPOT_ERROR_MESSAGES[errorParam] ?? `HubSpot connection failed (${errorParam}).`}
        </p>
      ) : null}

      {state.kind === "loading" ? (
        <p role="status" className="text-sm text-ink-muted">
          Loading integration status…
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

      {state.kind === "ready" && !state.status.connected ? (
        <section
          aria-label="HubSpot"
          className="max-w-xl rounded-card border border-line bg-surface p-5 shadow-card"
        >
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-base font-semibold text-ink-strong">HubSpot</h2>
            <span className="rounded-card border border-unavailable-line bg-unavailable-bg px-2 py-0.5 text-xs font-medium text-unavailable-ink">
              Not connected
            </span>
          </div>
          <p className="mt-2 text-sm text-ink-muted">
            Connect a HubSpot portal to import companies and contacts for research,
            then push approved findings back onto the same records.
          </p>
          {state.status.mock ? (
            <p className="mt-2 text-xs font-medium text-inferred-ink">
              Mock mode — synthetic HubSpot data, not a real portal.
            </p>
          ) : null}
          <a
            href={connectHubSpotUrl()}
            className="mt-4 inline-flex items-center gap-1.5 rounded-card bg-accent px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-strong"
          >
            Connect HubSpot
          </a>
        </section>
      ) : null}

      {state.kind === "ready" && state.status.connected ? (
        <section
          aria-label="HubSpot"
          className="rounded-card border border-line bg-surface p-5 shadow-card"
        >
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <h2 className="text-base font-semibold text-ink-strong">HubSpot</h2>
            <span className="rounded-card border border-verified-line bg-verified-bg px-2 py-0.5 text-xs font-medium text-verified-ink">
              Connected
            </span>
            {state.status.mock ? (
              <span className="rounded-card border border-inferred-line bg-inferred-bg px-2 py-0.5 text-xs font-medium text-inferred-ink">
                Mock mode — synthetic data
              </span>
            ) : null}
            {state.status.portal_id != null ? (
              <span className="font-mono text-xs text-ink-muted">
                portal {state.status.portal_id}
              </span>
            ) : null}
          </div>

          <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
            <div>
              <dt className="text-xs text-ink-muted">Companies imported</dt>
              <dd className="tabular-nums text-ink">{state.status.companies_imported}</dd>
            </div>
            <div>
              <dt className="text-xs text-ink-muted">Contacts imported</dt>
              <dd className="tabular-nums text-ink">{state.status.contacts_imported}</dd>
            </div>
            <div>
              <dt className="text-xs text-ink-muted">Connected at</dt>
              <dd className="text-ink">
                {state.status.connected_at != null
                  ? new Date(state.status.connected_at).toLocaleString()
                  : "—"}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-ink-muted">Last sync</dt>
              <dd className="text-ink">
                {state.status.last_sync_at != null
                  ? new Date(state.status.last_sync_at).toLocaleString()
                  : "Never"}
              </dd>
            </div>
          </dl>

          {state.status.scopes.length > 0 ? (
            <p className="mt-3 text-xs text-ink-muted">
              Scopes: <span className="font-mono">{state.status.scopes.join(" ")}</span>
            </p>
          ) : null}

          {actionError !== null ? (
            <p role="alert" className="mt-3 text-sm font-medium text-danger-ink">
              {actionError}
            </p>
          ) : null}

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={openImport}
              className="rounded-card border border-line-strong bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface-muted"
            >
              Import from HubSpot
            </button>
            <button
              type="button"
              onClick={openRefresh}
              className="rounded-card border border-line-strong bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface-muted"
            >
              Refresh from HubSpot
            </button>
            {confirmDisconnect ? (
              <>
                <button
                  type="button"
                  onClick={() => void submitDisconnect()}
                  disabled={disconnecting}
                  className="rounded-card border border-danger-line bg-danger-bg px-3 py-1.5 text-xs font-medium text-danger-ink disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {disconnecting ? "Disconnecting…" : "Confirm disconnect"}
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmDisconnect(false)}
                  className="rounded-card border border-line-strong bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface-muted"
                >
                  Cancel
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={() => setConfirmDisconnect(true)}
                className="rounded-card border border-line-strong bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface-muted"
              >
                Disconnect
              </button>
            )}
          </div>

          {importOpen ? (
            <div
              data-testid="import-panel"
              className="mt-4 rounded-card border border-line bg-surface-muted p-4"
            >
              <h3 className="text-sm font-semibold text-ink-strong">Import from HubSpot</h3>
              <p className="mt-1 text-xs text-ink-muted">
                Companies become sheet rows; contacts are linked as snapshots on the
                same sheet. Checked items only — unchecked remote records are not
                touched.
              </p>

              <label
                htmlFor="import-sheet"
                className="mt-3 block text-xs font-medium text-ink"
              >
                Target sheet
              </label>
              <select
                id="import-sheet"
                value={importSheetId}
                onChange={(event) => setImportSheetId(event.target.value)}
                className="mt-1 w-full max-w-sm rounded-card border border-line-strong bg-surface px-3 py-2 text-xs text-ink"
              >
                <option value="">Select a sheet…</option>
                {(sheets ?? []).map((sheet) => (
                  <option key={sheet.id} value={sheet.id}>
                    {sheet.name}
                  </option>
                ))}
              </select>
              {sheetsError !== null ? (
                <p role="alert" className="mt-1 text-xs text-danger-ink">
                  {sheetsError}
                </p>
              ) : null}
              {sheets !== null && sheets.length === 0 ? (
                <p className="mt-1 text-xs text-ink-muted">
                  No sheets yet — create one from the sheets page first.
                </p>
              ) : null}

              {objects?.kind === "loading" ? (
                <p role="status" className="mt-3 text-xs text-ink-muted">
                  Loading HubSpot objects…
                </p>
              ) : null}
              {objects?.kind === "error" ? (
                <p role="alert" className="mt-3 text-xs text-danger-ink">
                  {objects.message}
                </p>
              ) : null}
              {objects?.kind === "ready" ? (
                <div className="mt-3 grid gap-4 sm:grid-cols-2">
                  <fieldset>
                    <legend className="text-xs font-medium text-ink">
                      Companies ({objects.companies.length})
                    </legend>
                    {objects.companies.length === 0 ? (
                      <p className="mt-1 text-xs text-ink-muted">None returned.</p>
                    ) : (
                      <ul className="mt-1 max-h-48 space-y-1 overflow-y-auto">
                        {objects.companies.map((object) => (
                          <li key={object.remote_id}>
                            <label className="flex items-center gap-2 text-xs text-ink">
                              <input
                                type="checkbox"
                                checked={selectedCompanies.has(object.remote_id)}
                                onChange={() =>
                                  toggleId(
                                    selectedCompanies,
                                    object.remote_id,
                                    setSelectedCompanies,
                                  )
                                }
                              />
                              <span className="min-w-0 truncate">
                                {remoteLabel(object)}
                                {remoteDetail(object) !== null ? (
                                  <span className="text-ink-muted">
                                    {" "}
                                    · {remoteDetail(object)}
                                  </span>
                                ) : null}
                              </span>
                            </label>
                          </li>
                        ))}
                      </ul>
                    )}
                  </fieldset>
                  <fieldset>
                    <legend className="text-xs font-medium text-ink">
                      Contacts ({objects.contacts.length})
                    </legend>
                    {objects.contacts.length === 0 ? (
                      <p className="mt-1 text-xs text-ink-muted">None returned.</p>
                    ) : (
                      <ul className="mt-1 max-h-48 space-y-1 overflow-y-auto">
                        {objects.contacts.map((object) => (
                          <li key={object.remote_id}>
                            <label className="flex items-center gap-2 text-xs text-ink">
                              <input
                                type="checkbox"
                                checked={selectedContacts.has(object.remote_id)}
                                onChange={() =>
                                  toggleId(
                                    selectedContacts,
                                    object.remote_id,
                                    setSelectedContacts,
                                  )
                                }
                              />
                              <span className="min-w-0 truncate">
                                {remoteLabel(object)}
                                {remoteDetail(object) !== null ? (
                                  <span className="text-ink-muted">
                                    {" "}
                                    · {remoteDetail(object)}
                                  </span>
                                ) : null}
                              </span>
                            </label>
                          </li>
                        ))}
                      </ul>
                    )}
                  </fieldset>
                </div>
              ) : null}

              {importError !== null ? (
                <p role="alert" className="mt-3 text-xs font-medium text-danger-ink">
                  {importError}
                </p>
              ) : null}
              {importResult !== null ? (
                <p role="status" className="mt-3 text-xs font-medium text-verified-ink">
                  Imported {importResult.created}{" "}
                  {importResult.created === 1 ? "row" : "rows"}
                  {importResult.skipped > 0
                    ? ` · ${importResult.skipped} skipped (already linked or duplicate domains)`
                    : ""}{" "}
                  · {importResult.linked_companies}{" "}
                  {importResult.linked_companies === 1 ? "company" : "companies"} linked ·{" "}
                  {importResult.linked_contacts}{" "}
                  {importResult.linked_contacts === 1 ? "contact" : "contacts"} linked.
                </p>
              ) : null}

              <div className="mt-3 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setImportOpen(false)}
                  className="rounded-card border border-line-strong bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface-muted"
                >
                  {importResult !== null ? "Done" : "Cancel"}
                </button>
                <button
                  type="button"
                  onClick={() => void submitImport()}
                  disabled={
                    importBusy ||
                    !importSheetId ||
                    (selectedCompanies.size === 0 && selectedContacts.size === 0) ||
                    importResult !== null
                  }
                  className="rounded-card bg-accent px-3 py-1.5 text-xs font-semibold text-white hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {importBusy ? "Importing…" : "Import selected"}
                </button>
              </div>
            </div>
          ) : null}

          {refreshOpen ? (
            <div
              data-testid="refresh-panel"
              className="mt-4 rounded-card border border-line bg-surface-muted p-4"
            >
              <h3 className="text-sm font-semibold text-ink-strong">
                Refresh from HubSpot
              </h3>
              <p className="mt-1 text-xs text-ink-muted">
                Diffs linked remote records against the stored snapshot — the
                report below lists what changed remotely. Sheet cells are never
                modified.
              </p>

              <label
                htmlFor="refresh-sheet"
                className="mt-3 block text-xs font-medium text-ink"
              >
                Sheet to refresh
              </label>
              <select
                id="refresh-sheet"
                value={refreshSheetId}
                onChange={(event) => setRefreshSheetId(event.target.value)}
                className="mt-1 w-full max-w-sm rounded-card border border-line-strong bg-surface px-3 py-2 text-xs text-ink"
              >
                <option value="">Select a sheet…</option>
                {(sheets ?? []).map((sheet) => (
                  <option key={sheet.id} value={sheet.id}>
                    {sheet.name}
                  </option>
                ))}
              </select>
              {sheetsError !== null ? (
                <p role="alert" className="mt-1 text-xs text-danger-ink">
                  {sheetsError}
                </p>
              ) : null}

              {refreshError !== null ? (
                <p role="alert" className="mt-3 text-xs font-medium text-danger-ink">
                  {refreshError}
                </p>
              ) : null}
              {refreshChanges !== null ? (
                <div role="status" className="mt-3">
                  {refreshChanges.length === 0 ? (
                    <p className="text-xs font-medium text-verified-ink">
                      No remote changes — every linked record matches its snapshot.
                    </p>
                  ) : (
                    <ul className="space-y-1 text-xs text-ink">
                      {refreshChanges.map((change, index) => (
                        <li
                          key={`${change.remote_id}:${change.property}:${index}`}
                          className="rounded-card border border-line bg-surface px-3 py-2"
                        >
                          <span className="font-mono">{change.property}</span>{" "}
                          <span className="text-ink-muted">
                            on remote {change.remote_id}
                            {change.row_id !== null ? ` · row ${change.row_id}` : ""}
                          </span>
                          <br />
                          <span className="text-ink-muted">
                            was {change.previous ?? "∅"} → now{" "}
                            {change.remote_value ?? "∅"}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ) : null}

              <div className="mt-3 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setRefreshOpen(false)}
                  className="rounded-card border border-line-strong bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface-muted"
                >
                  {refreshChanges !== null ? "Done" : "Cancel"}
                </button>
                <button
                  type="button"
                  onClick={() => void submitRefresh()}
                  disabled={refreshBusy || !refreshSheetId}
                  className="rounded-card bg-accent px-3 py-1.5 text-xs font-semibold text-white hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {refreshBusy ? "Refreshing…" : "Refresh"}
                </button>
              </div>
            </div>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

export default function IntegrationsPage() {
  return (
    <Suspense
      fallback={
        <p role="status" className="px-4 py-8 text-sm text-ink-muted sm:px-6 lg:px-8">
          Loading integration status…
        </p>
      }
    >
      <IntegrationsView />
    </Suspense>
  );
}
