/**
 * Integrations API client (integration plan §7). Same contract
 * discipline as `lib/api/sheets.ts`: every function returns the shared
 * `SheetResult` discriminated union and never throws — fetch rejections
 * become `network`, bodies that fail the Zod mirror become
 * `invalid_response`, and error fields come only from the backend's
 * `ErrorResponse` body.
 *
 * `connectHubSpotUrl` is the one exception to the fetch rule: it only
 * builds the OAuth entry URL for `window.location.href` — the connect
 * endpoint 302s to HubSpot, so it is navigated to, never fetched.
 */

import { z } from "zod";

import { apiBaseUrl } from "@/lib/api/client";
import type { SheetResult } from "@/lib/api/sheets";
import { ErrorResponseSchema } from "@/lib/schema/report";
import {
  HubSpotStatusSchema,
  ImportResponseSchema,
  LinksResponseSchema,
  PushResponseSchema,
  RefreshResponseSchema,
  RemoteObjectPreviewSchema,
  type HubSpotImportInput,
  type HubSpotStatus,
  type ImportResponse,
  type LinksResponse,
  type PushApprovalInput,
  type PushResponse,
  type RefreshResponse,
  type RemoteObjectPreview,
} from "@/lib/schema/integration";

const enc = encodeURIComponent;

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}

/** Error tail shared by every endpoint — mirrors sheets.ts exactly. */
function errorResult<T>(response: Response, payload: unknown): SheetResult<T> {
  const errorBody = ErrorResponseSchema.safeParse(payload);

  if (response.status === 422 && errorBody.success) {
    const fields: Record<string, string> = {};
    for (const detail of errorBody.data.error.details ?? []) {
      fields[detail.field] = detail.msg;
    }
    return { kind: "validation", fields };
  }

  if (response.status >= 200 && response.status < 300) {
    // A 2xx that is not the expected status and not an error body.
    return { kind: "invalid_response" };
  }

  if (errorBody.success) {
    const { code, message, request_id } = errorBody.data.error;
    return {
      kind: "api_error",
      status: response.status,
      code,
      message,
      requestId: request_id,
    };
  }

  // No parseable error body: a generic, safe message with a client-side
  // marker code — not something the backend said.
  return {
    kind: "api_error",
    status: response.status,
    code: "unexpected_response",
    message: `The integrations service returned an unexpected response (HTTP ${response.status}).`,
    requestId: null,
  };
}

async function request<T>(
  path: string,
  init: { method?: string; body?: unknown },
  okStatus: number,
  schema: z.ZodType<T>,
): Promise<SheetResult<T>> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}${path}`, {
      method: init.method ?? "GET",
      headers: init.body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
    });
  } catch {
    return { kind: "network" };
  }

  const payload = await readJson(response);
  if (response.status === okStatus) {
    const parsed = schema.safeParse(payload);
    return parsed.success ? { kind: "ok", data: parsed.data } : { kind: "invalid_response" };
  }
  return errorResult(response, payload);
}

/** For 204 No Content endpoints (disconnect) — there is no body to parse. */
async function requestEmpty(
  path: string,
  init: { method?: string; body?: unknown },
): Promise<SheetResult<null>> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}${path}`, { method: init.method ?? "GET" });
  } catch {
    return { kind: "network" };
  }
  if (response.status === 204) return { kind: "ok", data: null };
  return errorResult(response, await readJson(response));
}

// --- HubSpot integration ------------------------------------------------------

/** GET /status — always answers 200; `connected: false` is data, not an error. */
export function hubspotStatus(): Promise<SheetResult<HubSpotStatus>> {
  return request("/api/integrations/hubspot/status", {}, 200, HubSpotStatusSchema);
}

/** Remote objects for the import picker. `objectType` is the route's
 *  plural query value: "companies" | "contacts". */
export function listHubSpotObjects(
  objectType: "companies" | "contacts",
): Promise<SheetResult<RemoteObjectPreview[]>> {
  return request(
    `/api/integrations/hubspot/objects?object_type=${objectType}`,
    {},
    200,
    z.array(RemoteObjectPreviewSchema),
  );
}

/** Remote→row links for one sheet — used by the push approval dialog. */
export function listHubSpotLinks(sheetId: string): Promise<SheetResult<LinksResponse>> {
  return request(
    `/api/integrations/hubspot/links?sheet_id=${enc(sheetId)}`,
    {},
    200,
    LinksResponseSchema,
  );
}

/** HubSpot → Outmate: selected remote objects become rows (companies)
 *  and snapshot links (contacts). */
export function importFromHubSpot(
  sheetId: string,
  input: HubSpotImportInput,
): Promise<SheetResult<ImportResponse>> {
  return request(
    "/api/integrations/hubspot/import",
    {
      method: "POST",
      body: {
        sheet_id: sheetId,
        ...(input.company_ids !== undefined ? { company_ids: input.company_ids } : {}),
        ...(input.contact_ids !== undefined ? { contact_ids: input.contact_ids } : {}),
      },
    },
    200,
    ImportResponseSchema,
  );
}

/** Outmate → HubSpot: approved `outmate_*` writes. The client sends WHICH
 *  cells and WHICH property — never the values. */
export function pushToHubSpot(
  sheetId: string,
  approvals: PushApprovalInput[],
): Promise<SheetResult<PushResponse>> {
  return request(
    "/api/integrations/hubspot/push",
    { method: "POST", body: { sheet_id: sheetId, approvals } },
    200,
    PushResponseSchema,
  );
}

/** Pull remote changes: returns the diff report; cells are never touched. */
export function refreshHubSpot(sheetId: string): Promise<SheetResult<RefreshResponse>> {
  return request(
    "/api/integrations/hubspot/refresh",
    { method: "POST", body: { sheet_id: sheetId } },
    200,
    RefreshResponseSchema,
  );
}

/** DELETE /connection — 204; links survive a reconnect. */
export function disconnectHubSpot(): Promise<SheetResult<null>> {
  return requestEmpty("/api/integrations/hubspot/connection", { method: "DELETE" });
}

/** OAuth entry URL — assigned to `window.location.href`, NOT fetched:
 *  the endpoint mints `state` and 302s to the HubSpot consent screen. */
export function connectHubSpotUrl(): string {
  return `${apiBaseUrl()}/api/integrations/hubspot/connect`;
}
