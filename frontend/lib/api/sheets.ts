/**
 * Sheets API client (plan §4.5 / P6). Same contract discipline as
 * `lib/api/client.ts`: every function returns a `SheetResult`
 * discriminated union and never throws — fetch rejections become
 * `network`, bodies that fail the Zod mirror become `invalid_response`,
 * and error fields come only from the backend's `ErrorResponse` body.
 */

import type { z } from "zod";

import { apiBaseUrl } from "@/lib/api/client";
import { ErrorResponseSchema } from "@/lib/schema/report";
import {
  CellDetailSchema,
  CreateColumnsResponseSchema,
  ImportRowsResponseSchema,
  PlanResultSchema,
  SheetColumnSchema,
  SheetListResponseSchema,
  SheetRunSchema,
  SheetSchema,
  SheetSnapshotSchema,
  type CellDetail,
  type ColumnSpecInput,
  type CreateColumnsResponse,
  type CreateRunInput,
  type ImportRowInput,
  type ImportRowsResponse,
  type PlanResult,
  type Sheet,
  type SheetColumn,
  type SheetListResponse,
  type SheetRun,
  type SheetSnapshot,
} from "@/lib/schema/sheet";

export type SheetResult<T> =
  | { kind: "ok"; data: T }
  | { kind: "invalid_response" }
  | { kind: "validation"; fields: Record<string, string> }
  | {
      kind: "api_error";
      status: number;
      code: string;
      message: string;
      requestId: string | null;
    }
  | { kind: "network" };

const enc = encodeURIComponent;

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}

/** Error tail shared by every endpoint — mirrors client.ts exactly. */
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
    message: `The sheets service returned an unexpected response (HTTP ${response.status}).`,
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

/** For 204 No Content endpoints (deletes) — there is no body to parse. */
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

// --- sheets ---------------------------------------------------------------

export function createSheet(name: string): Promise<SheetResult<Sheet>> {
  return request("/api/sheets", { method: "POST", body: { name } }, 201, SheetSchema);
}

export function listSheets(): Promise<SheetResult<SheetListResponse>> {
  return request("/api/sheets", {}, 200, SheetListResponseSchema);
}

export function getSnapshot(sheetId: string): Promise<SheetResult<SheetSnapshot>> {
  return request(`/api/sheets/${enc(sheetId)}/snapshot`, {}, 200, SheetSnapshotSchema);
}

export function deleteSheet(sheetId: string): Promise<SheetResult<null>> {
  return requestEmpty(`/api/sheets/${enc(sheetId)}`, { method: "DELETE" });
}

// --- rows -------------------------------------------------------------------

export function importRows(
  sheetId: string,
  rows: ImportRowInput[],
): Promise<SheetResult<ImportRowsResponse>> {
  return request(
    `/api/sheets/${enc(sheetId)}/rows`,
    { method: "POST", body: { rows } },
    201,
    ImportRowsResponseSchema,
  );
}

// --- planner ------------------------------------------------------------------

export function planColumns(sheetId: string, prompt: string): Promise<SheetResult<PlanResult>> {
  return request(
    `/api/sheets/${enc(sheetId)}/plan`,
    { method: "POST", body: { prompt } },
    200,
    PlanResultSchema,
  );
}

// --- columns ------------------------------------------------------------------

export function createColumns(
  sheetId: string,
  columns: ColumnSpecInput[],
  run: boolean,
): Promise<SheetResult<CreateColumnsResponse>> {
  return request(
    `/api/sheets/${enc(sheetId)}/columns`,
    { method: "POST", body: { columns, run } },
    201,
    CreateColumnsResponseSchema,
  );
}

export function updateColumn(
  sheetId: string,
  columnId: string,
  spec: ColumnSpecInput,
): Promise<SheetResult<SheetColumn>> {
  return request(
    `/api/sheets/${enc(sheetId)}/columns/${enc(columnId)}`,
    { method: "PATCH", body: spec },
    200,
    SheetColumnSchema,
  );
}

export function deleteColumn(
  sheetId: string,
  columnId: string,
): Promise<SheetResult<null>> {
  return requestEmpty(`/api/sheets/${enc(sheetId)}/columns/${enc(columnId)}`, {
    method: "DELETE",
  });
}

// --- runs ---------------------------------------------------------------------

export function createRun(
  sheetId: string,
  input: CreateRunInput = {},
): Promise<SheetResult<SheetRun>> {
  return request(
    `/api/sheets/${enc(sheetId)}/runs`,
    {
      method: "POST",
      body: {
        scope: input.scope ?? "empty",
        confirm_overwrite: input.confirm_overwrite ?? false,
        ...(input.cap !== undefined ? { cap: input.cap } : {}),
      },
    },
    202,
    SheetRunSchema,
  );
}

export function cancelRun(sheetId: string, runId: string): Promise<SheetResult<SheetRun>> {
  return request(
    `/api/sheets/${enc(sheetId)}/runs/${enc(runId)}/cancel`,
    { method: "POST" },
    200,
    SheetRunSchema,
  );
}

// --- cells --------------------------------------------------------------------

export function getCellDetail(
  sheetId: string,
  rowId: string,
  columnId: string,
): Promise<SheetResult<CellDetail>> {
  return request(
    `/api/sheets/${enc(sheetId)}/cells/${enc(rowId)}/${enc(columnId)}`,
    {},
    200,
    CellDetailSchema,
  );
}

// --- export / events ----------------------------------------------------------

/** URL for the CSV export — consumed as a link href, not fetched. */
export function exportCsvUrl(sheetId: string): string {
  return `${apiBaseUrl()}/api/sheets/${enc(sheetId)}/export.csv`;
}

/** URL for the SSE event stream — consumed by `lib/api/events.ts`. */
export function sheetEventsUrl(sheetId: string): string {
  return `${apiBaseUrl()}/api/sheets/${enc(sheetId)}/events`;
}
