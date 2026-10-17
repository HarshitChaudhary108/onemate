/**
 * Sheets client result mapping (P6) — same contract discipline as
 * `tests/client.test.ts`: `fetch` stubbed via `vi.stubGlobal`, no network.
 * Bodies are hand-built from the backend contract — no fixture files.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createSheet, getCellDetail, listSheets } from "@/lib/api/sheets";

const BASE = "http://api.test:8000";
const TS = "2026-10-17T12:00:00Z";

function fakeResponse(status: number, body: unknown): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: async () => body,
  } as unknown as Response;
}

const SHEET = { id: "s1", name: "targets", created_at: TS, updated_at: TS };

const ERROR_BODY = {
  error: {
    code: "row_limit",
    message: "a sheet holds at most 200 rows",
    request_id: "req-1",
  },
};

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", BASE);
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("sheets client result mapping", () => {
  it("201 create -> {kind: ok} with the parsed sheet", async () => {
    const mock = vi.fn(async () => fakeResponse(201, SHEET));
    vi.stubGlobal("fetch", mock);

    const result = await createSheet("targets");
    expect(result).toEqual({ kind: "ok", data: SHEET });
    const [url, init] = mock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${BASE}/api/sheets`);
    expect(JSON.parse(String(init.body))).toEqual({ name: "targets" });
  });

  it("200 list -> {kind: ok}; wrong shape -> invalid_response", async () => {
    vi.stubGlobal("fetch", async () => fakeResponse(200, { sheets: [SHEET] }));
    const ok = await listSheets();
    expect(ok.kind).toBe("ok");

    vi.stubGlobal("fetch", async () => fakeResponse(200, { sheets: "nope" }));
    expect(await listSheets()).toEqual({ kind: "invalid_response" });
  });

  it("404 error body -> {kind: api_error} with backend code and request id", async () => {
    vi.stubGlobal(
      "fetch",
      async () =>
        fakeResponse(404, {
          error: { code: "not_found", message: "cell not found", request_id: "req-9" },
        }),
    );
    const result = await getCellDetail("s1", "r1", "c1");
    expect(result).toEqual({
      kind: "api_error",
      status: 404,
      code: "not_found",
      message: "cell not found",
      requestId: "req-9",
    });
  });

  it("422 -> {kind: validation} with the field map", async () => {
    vi.stubGlobal(
      "fetch",
      async () =>
        fakeResponse(422, {
          error: {
            code: "validation_error",
            message: "request validation failed",
            request_id: "req-2",
            details: [{ field: "name", msg: "Field required" }],
          },
        }),
    );
    const result = await createSheet("");
    expect(result).toEqual({ kind: "validation", fields: { name: "Field required" } });
  });

  it("non-ErrorResponse 409 -> api_error with the client marker code", async () => {
    vi.stubGlobal("fetch", async () => fakeResponse(409, { unexpected: true }));
    const result = await createSheet("x");
    expect(result.kind).toBe("api_error");
    if (result.kind !== "api_error") return;
    expect(result.status).toBe(409);
    expect(result.code).toBe("unexpected_response");
  });

  it("a fetch rejection -> {kind: network}", async () => {
    vi.stubGlobal("fetch", async () => {
      throw new TypeError("Failed to fetch");
    });
    expect(await createSheet("x")).toEqual({ kind: "network" });
  });

  it("uses the ErrorResponse code verbatim for domain errors", async () => {
    vi.stubGlobal("fetch", async () => fakeResponse(409, ERROR_BODY));
    const result = await createSheet("x");
    expect(result.kind).toBe("api_error");
    if (result.kind !== "api_error") return;
    expect(result.code).toBe("row_limit");
  });
});
