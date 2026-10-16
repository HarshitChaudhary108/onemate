/**
 * Research API client (plan M4 task 1).
 *
 * `researchCompany` never throws: every outcome — including a fetch rejection
 * and a body that does not match the contract — is mapped onto the `Result`
 * discriminated union. Nothing is invented: when the response cannot be
 * narrowed with the Zod `Report` schema the caller gets `invalid_response`,
 * and error fields come only from the backend's `ErrorResponse` body.
 */

import { ErrorResponseSchema, ReportSchema, type Report } from "@/lib/schema/report";

export type ResearchInput = {
  company_name?: string;
  domain?: string;
  research_objective?: string;
};

export type Result =
  | { kind: "report"; report: Report }
  | { kind: "invalid_response" }
  | { kind: "validation"; fields: Record<string, string> }
  | { kind: "api_error"; status: number; code: string; message: string; requestId: string | null }
  | { kind: "network" };

const REQUEST_PATH = "/api/research";
const REQUEST_KEYS = ["company_name", "domain", "research_objective"] as const;

/** The API origin, from `NEXT_PUBLIC_API_BASE_URL` only, trailing slash stripped. */
export function apiBaseUrl(): string {
  return (process.env.NEXT_PUBLIC_API_BASE_URL ?? "").trim().replace(/\/+$/, "");
}

/** Only the three request keys; blank values are omitted entirely. */
function requestBody(input: ResearchInput): Record<string, string> {
  const body: Record<string, string> = {};
  for (const key of REQUEST_KEYS) {
    const value = input[key]?.trim();
    if (value) body[key] = value;
  }
  return body;
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}

export async function researchCompany(input: ResearchInput): Promise<Result> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}${REQUEST_PATH}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody(input)),
    });
  } catch {
    return { kind: "network" };
  }

  const payload = await readJson(response);

  if (response.status === 200) {
    const parsed = ReportSchema.safeParse(payload);
    return parsed.success
      ? { kind: "report", report: parsed.data }
      : { kind: "invalid_response" };
  }

  const errorBody = ErrorResponseSchema.safeParse(payload);

  if (response.status === 422 && errorBody.success) {
    const fields: Record<string, string> = {};
    for (const detail of errorBody.data.error.details ?? []) {
      fields[detail.field] = detail.msg;
    }
    return { kind: "validation", fields };
  }

  if (response.status >= 200 && response.status < 300) {
    // A 2xx that is not a 200 report and not an error body.
    return { kind: "invalid_response" };
  }

  if (errorBody.success) {
    const { code, message, request_id } = errorBody.data.error;
    return { kind: "api_error", status: response.status, code, message, requestId: request_id };
  }

  // No parseable error body: a generic, safe message. The code is a
  // client-side marker, not something the backend said.
  return {
    kind: "api_error",
    status: response.status,
    code: "unexpected_response",
    message: `The research service returned an unexpected response (HTTP ${response.status}).`,
    requestId: null,
  };
}
