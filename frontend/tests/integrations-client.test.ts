/**
 * Integrations client result mapping (I4) — same contract discipline as
 * `tests/sheet-client.test.ts`: `fetch` stubbed via `vi.stubGlobal`, no
 * network. Bodies are hand-built from the backend contract.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  connectHubSpotUrl,
  disconnectHubSpot,
  hubspotStatus,
  importFromHubSpot,
  listHubSpotLinks,
  listHubSpotObjects,
  pushToHubSpot,
  refreshHubSpot,
} from "@/lib/api/integrations";

const BASE = "http://api.test:8000";
const TS = "2026-10-26T12:00:00Z";

function fakeResponse(status: number, body: unknown): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: async () => body,
  } as unknown as Response;
}

const STATUS = {
  connected: true,
  portal_id: "12345678",
  scopes: ["crm.objects.companies.read", "crm.objects.companies.write"],
  connected_at: TS,
  last_sync_at: TS,
  companies_imported: 2,
  contacts_imported: 1,
  mock: false,
};

const ERROR_BODY = {
  error: {
    code: "hubspot_not_connected",
    message: "No HubSpot connection is active.",
    request_id: "req-7",
  },
};

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", BASE);
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("integrations client result mapping", () => {
  it("200 status -> {kind: ok} with the parsed HubSpotStatus", async () => {
    const mock = vi.fn(async () => fakeResponse(200, STATUS));
    vi.stubGlobal("fetch", mock);

    const result = await hubspotStatus();
    expect(result).toEqual({ kind: "ok", data: STATUS });
    const [url, init] = mock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${BASE}/api/integrations/hubspot/status`);
    expect(init.method ?? "GET").toBe("GET");
  });

  it("objects endpoint sends the plural object_type and parses the bare array", async () => {
    const objects = [
      {
        remote_id: "1001",
        object_type: "company",
        properties: { name: "Example Robotics Ltd", domain: "example-robotics.test" },
      },
    ];
    const mock = vi.fn(async () => fakeResponse(200, objects));
    vi.stubGlobal("fetch", mock);

    const result = await listHubSpotObjects("companies");
    expect(result).toEqual({ kind: "ok", data: objects });
    const [url] = mock.mock.calls[0] as unknown as [string];
    expect(url).toBe(
      `${BASE}/api/integrations/hubspot/objects?object_type=companies`,
    );
  });

  it("links endpoint encodes sheet_id as a query param", async () => {
    const links = {
      links: [
        { row_id: "r1", remote_id: "1001", object_type: "company" },
        { row_id: null, remote_id: "2001", object_type: "contact" },
      ],
    };
    const mock = vi.fn(async () => fakeResponse(200, links));
    vi.stubGlobal("fetch", mock);

    const result = await listHubSpotLinks("s 1");
    expect(result).toEqual({ kind: "ok", data: links });
    const [url] = mock.mock.calls[0] as unknown as [string];
    expect(url).toBe(`${BASE}/api/integrations/hubspot/links?sheet_id=s%201`);
  });

  it("import posts sheet_id + selected id lists and parses the counts", async () => {
    const body = { created: 2, skipped: 1, linked_companies: 2, linked_contacts: 1 };
    const mock = vi.fn(async () => fakeResponse(200, body));
    vi.stubGlobal("fetch", mock);

    const result = await importFromHubSpot("s1", {
      company_ids: ["1001"],
      contact_ids: [],
    });
    expect(result).toEqual({ kind: "ok", data: body });
    const [url, init] = mock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${BASE}/api/integrations/hubspot/import`);
    expect(JSON.parse(String(init.body))).toEqual({
      sheet_id: "s1",
      company_ids: ["1001"],
      contact_ids: [],
    });
  });

  it("push sends approvals only — never cell values", async () => {
    const body = {
      results: [
        {
          row_id: "r1",
          column_id: "c1",
          property: "outmate_tech_stack",
          status: "pushed",
          detail: null,
        },
      ],
    };
    const mock = vi.fn(async () => fakeResponse(200, body));
    vi.stubGlobal("fetch", mock);

    const result = await pushToHubSpot("s1", [
      { row_id: "r1", column_id: "c1", property: "outmate_tech_stack" },
    ]);
    expect(result).toEqual({ kind: "ok", data: body });
    const [url, init] = mock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${BASE}/api/integrations/hubspot/push`);
    expect(JSON.parse(String(init.body))).toEqual({
      sheet_id: "s1",
      approvals: [{ row_id: "r1", column_id: "c1", property: "outmate_tech_stack" }],
    });
  });

  it("refresh posts sheet_id and parses the change list", async () => {
    const body = {
      changes: [
        {
          row_id: "r1",
          remote_id: "1001",
          property: "industry",
          remote_value: "Manufacturing",
          previous: "Robotics",
        },
      ],
    };
    const mock = vi.fn(async () => fakeResponse(200, body));
    vi.stubGlobal("fetch", mock);

    const result = await refreshHubSpot("s1");
    expect(result).toEqual({ kind: "ok", data: body });
    const [url, init] = mock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${BASE}/api/integrations/hubspot/refresh`);
    expect(JSON.parse(String(init.body))).toEqual({ sheet_id: "s1" });
  });

  it("204 disconnect -> {kind: ok, data: null}", async () => {
    const mock = vi.fn(async () => fakeResponse(204, null));
    vi.stubGlobal("fetch", mock);

    const result = await disconnectHubSpot();
    expect(result).toEqual({ kind: "ok", data: null });
    const [url, init] = mock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${BASE}/api/integrations/hubspot/connection`);
    expect(init.method).toBe("DELETE");
  });

  it("wrong shape -> {kind: invalid_response}", async () => {
    vi.stubGlobal("fetch", async () => fakeResponse(200, { connected: "yes" }));
    expect(await hubspotStatus()).toEqual({ kind: "invalid_response" });
  });

  it("ErrorResponse body -> {kind: api_error} with backend code and request id", async () => {
    vi.stubGlobal("fetch", async () => fakeResponse(409, ERROR_BODY));
    const result = await hubspotStatus();
    expect(result).toEqual({
      kind: "api_error",
      status: 409,
      code: "hubspot_not_connected",
      message: "No HubSpot connection is active.",
      requestId: "req-7",
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
            details: [{ field: "sheet_id", msg: "Field required" }],
          },
        }),
    );
    const result = await importFromHubSpot("", {});
    expect(result).toEqual({ kind: "validation", fields: { sheet_id: "Field required" } });
  });

  it("non-ErrorResponse 503 -> api_error with the client marker code", async () => {
    vi.stubGlobal("fetch", async () => fakeResponse(503, { oops: true }));
    const result = await disconnectHubSpot();
    expect(result.kind).toBe("api_error");
    if (result.kind !== "api_error") return;
    expect(result.status).toBe(503);
    expect(result.code).toBe("unexpected_response");
  });

  it("a fetch rejection -> {kind: network}", async () => {
    vi.stubGlobal("fetch", async () => {
      throw new TypeError("Failed to fetch");
    });
    expect(await hubspotStatus()).toEqual({ kind: "network" });
  });

  it("connectHubSpotUrl points at the backend OAuth path (navigated to, never fetched)", () => {
    expect(connectHubSpotUrl()).toBe(`${BASE}/api/integrations/hubspot/connect`);
  });
});
