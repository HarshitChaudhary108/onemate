/**
 * Client result mapping (plan M4 tests). `fetch` is stubbed with
 * `vi.stubGlobal` — no real network. Fixtures come from the backend-generated
 * `backend/tests/fixtures/reports/` snapshots.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiBaseUrl, researchCompany } from "@/lib/api/client";

const FIXTURE_DIR = join(
  import.meta.dirname,
  "..",
  "..",
  "backend",
  "tests",
  "fixtures",
  "reports",
);

function loadFixture(name: string): unknown {
  return JSON.parse(readFileSync(join(FIXTURE_DIR, name), "utf-8"));
}

function fakeResponse({
  status = 200,
  body,
  jsonThrows = false,
}: {
  status?: number;
  body?: unknown;
  jsonThrows?: boolean;
}): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: async () => {
      if (jsonThrows) throw new SyntaxError("Unexpected token '<'");
      return body;
    },
  } as unknown as Response;
}

type FetchMock = ReturnType<typeof vi.fn>;

function stubFetch(response: Response | (() => Promise<Response>)): FetchMock {
  const mock =
    typeof response === "function"
      ? vi.fn(response)
      : vi.fn(async () => response);
  vi.stubGlobal("fetch", mock);
  return mock as FetchMock;
}

function callArgs(mock: FetchMock): { url: string; init: RequestInit } {
  const [url, init] = mock.mock.calls[0] as [string, RequestInit];
  return { url, init };
}

function bodyOf(mock: FetchMock): Record<string, unknown> {
  const { init } = callArgs(mock);
  return JSON.parse(String(init.body)) as Record<string, unknown>;
}

const BASE = "http://api.test:8000";

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", BASE);
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("apiBaseUrl", () => {
  it("reads NEXT_PUBLIC_API_BASE_URL and strips a trailing slash", () => {
    expect(apiBaseUrl()).toBe(BASE);
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://api.test:8000/");
    expect(apiBaseUrl()).toBe(BASE);
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://api.test:8000///");
    expect(apiBaseUrl()).toBe(BASE);
  });
});

describe("researchCompany result mapping", () => {
  it("200 with a valid report -> {kind: report}", async () => {
    const fixture = loadFixture("live_success.json");
    const mock = stubFetch(fakeResponse({ body: fixture }));

    const result = await researchCompany({ domain: "example-robotics.test" });

    expect(result.kind).toBe("report");
    if (result.kind !== "report") return;
    expect(result.report.company_identity.domain.value).toBe("example-robotics.test");
    expect(result.report.request_id).toBe("req-fixture-0002");
    expect(callArgs(mock).url).toBe(`${BASE}/api/research`);
  });

  it("200 with the wrong shape -> {kind: invalid_response}", async () => {
    stubFetch(fakeResponse({ body: { not: "a report" } }));
    const result = await researchCompany({ company_name: "Example Robotics Ltd" });
    expect(result).toEqual({ kind: "invalid_response" });
  });

  it("200 with a non-JSON body -> {kind: invalid_response}", async () => {
    stubFetch(fakeResponse({ jsonThrows: true }));
    const result = await researchCompany({ company_name: "Example Robotics Ltd" });
    expect(result).toEqual({ kind: "invalid_response" });
  });

  it("422 -> {kind: validation} with the field map", async () => {
    const fixture = loadFixture("validation_error.json");
    stubFetch(fakeResponse({ status: 422, body: fixture }));

    const result = await researchCompany({ domain: "http://" });

    expect(result.kind).toBe("validation");
    if (result.kind !== "validation") return;
    expect(result.fields).toEqual({
      company_name: "must be at most 200 characters",
      domain: "invalid domain: missing host",
      research_objective: "must be at most 1000 characters",
    });
  });

  it("503 -> {kind: api_error} with code, message and request id", async () => {
    const fixture = loadFixture("api_error.json");
    stubFetch(fakeResponse({ status: 503, body: fixture }));

    const result = await researchCompany({ domain: "example-robotics.test" });

    expect(result).toEqual({
      kind: "api_error",
      status: 503,
      code: "exa_not_configured",
      message: "Exa search is not configured: set EXA_API_KEY on the backend.",
      requestId: "req-fixture-0007",
    });
  });

  it("non-JSON 500 -> {kind: api_error} with a generic message and no request id", async () => {
    stubFetch(fakeResponse({ status: 500, jsonThrows: true }));

    const result = await researchCompany({ domain: "example-robotics.test" });

    expect(result.kind).toBe("api_error");
    if (result.kind !== "api_error") return;
    expect(result.status).toBe(500);
    expect(result.code).toBe("unexpected_response");
    expect(result.message).toContain("HTTP 500");
    expect(result.requestId).toBeNull();
  });

  it("a fetch rejection -> {kind: network}", async () => {
    stubFetch(async () => {
      throw new TypeError("Failed to fetch");
    });
    const result = await researchCompany({ domain: "example-robotics.test" });
    expect(result).toEqual({ kind: "network" });
  });
});

describe("request construction", () => {
  it("POSTs JSON to the env base URL + /api/research", async () => {
    const mock = stubFetch(fakeResponse({ body: loadFixture("live_success.json") }));
    await researchCompany({ company_name: "Example Robotics Ltd" });

    const { url, init } = callArgs(mock);
    expect(url).toBe(`${BASE}/api/research`);
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("strips a trailing slash from the configured base URL", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", `${BASE}/`);
    const mock = stubFetch(fakeResponse({ body: loadFixture("live_success.json") }));
    await researchCompany({ company_name: "Example Robotics Ltd" });
    expect(callArgs(mock).url).toBe(`${BASE}/api/research`);
  });

  it("sends only the three request keys, trimming values and omitting blanks", async () => {
    const mock = stubFetch(fakeResponse({ body: loadFixture("live_success.json") }));
    await researchCompany({
      company_name: "  Example Robotics Ltd  ",
      domain: "",
      research_objective: "   ",
    });

    const body = bodyOf(mock);
    expect(Object.keys(body).sort()).toEqual(["company_name"]);
    expect(body.company_name).toBe("Example Robotics Ltd");
  });

  it("keeps all three keys when they have values", async () => {
    const mock = stubFetch(fakeResponse({ body: loadFixture("live_success.json") }));
    await researchCompany({
      company_name: "Example Robotics Ltd",
      domain: "example-robotics.test",
      research_objective: "Evaluate fit for a B2B GTM service",
    });

    const body = bodyOf(mock);
    expect(Object.keys(body).sort()).toEqual([
      "company_name",
      "domain",
      "research_objective",
    ]);
  });
});
