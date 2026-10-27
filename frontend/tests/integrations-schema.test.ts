/**
 * Integrations contract drift check (I4). Parses backend-shaped payloads
 * with the Zod mirrors in `lib/schema/integration.ts` — the same pattern
 * as `tests/sheet-schema.test.ts`. Payloads are hand-built from
 * `backend/app/schemas/integration.py` — synthetic and fictional.
 */

import { describe, expect, it } from "vitest";

import {
  HubSpotStatusSchema,
  ImportResponseSchema,
  LinkInfoSchema,
  LinksResponseSchema,
  PushItemResultSchema,
  PushResponseSchema,
  RefreshChangeSchema,
  RefreshResponseSchema,
  RemoteObjectPreviewSchema,
} from "@/lib/schema/integration";

const TS = "2026-10-26T12:00:00Z";

describe("HubSpotStatusSchema", () => {
  it("parses a connected status", () => {
    const parsed = HubSpotStatusSchema.safeParse({
      connected: true,
      portal_id: "12345678",
      scopes: [
        "crm.objects.companies.read",
        "crm.objects.companies.write",
        "crm.objects.contacts.read",
        "crm.objects.contacts.write",
      ],
      connected_at: TS,
      last_sync_at: TS,
      companies_imported: 3,
      contacts_imported: 2,
      mock: false,
    });
    expect(parsed.success).toBe(true);
    if (parsed.success) {
      expect(parsed.data.portal_id).toBe("12345678");
      expect(parsed.data.companies_imported).toBe(3);
    }
  });

  it("parses a disconnected status (all nulls, never an error)", () => {
    const parsed = HubSpotStatusSchema.safeParse({
      connected: false,
      portal_id: null,
      scopes: [],
      connected_at: null,
      last_sync_at: null,
      companies_imported: 0,
      contacts_imported: 0,
      mock: false,
    });
    expect(parsed.success).toBe(true);
    if (parsed.success) expect(parsed.data.connected).toBe(false);
  });

  it("rejects a payload with an extra key (extra=forbid)", () => {
    expect(
      HubSpotStatusSchema.safeParse({
        connected: true,
        portal_id: null,
        scopes: [],
        connected_at: null,
        last_sync_at: null,
        companies_imported: 0,
        contacts_imported: 0,
        mock: false,
        access_token: "never-serialized",
      }).success,
    ).toBe(false);
  });
});

describe("RemoteObjectPreviewSchema", () => {
  it("parses company and contact previews (bare-array item shape)", () => {
    const company = RemoteObjectPreviewSchema.safeParse({
      remote_id: "1001",
      object_type: "company",
      properties: {
        name: "Example Robotics Ltd",
        domain: "example-robotics.test",
        industry: "Robotics",
      },
    });
    expect(company.success).toBe(true);

    const contact = RemoteObjectPreviewSchema.safeParse({
      remote_id: "2001",
      object_type: "contact",
      properties: {
        firstname: "Ada",
        lastname: "Example",
        email: "ada@example-robotics.test",
        jobtitle: "CTO",
        associatedcompanyid: "1001",
      },
    });
    expect(contact.success).toBe(true);
    if (contact.success) {
      expect(contact.data.properties.email).toBe("ada@example-robotics.test");
    }
  });

  it("rejects an unknown object_type", () => {
    expect(
      RemoteObjectPreviewSchema.safeParse({
        remote_id: "1",
        object_type: "deal",
        properties: {},
      }).success,
    ).toBe(false);
  });
});

describe("ImportResponseSchema", () => {
  it("roundtrips the honest-accounting counts", () => {
    const parsed = ImportResponseSchema.safeParse({
      created: 2,
      skipped: 1,
      linked_companies: 2,
      linked_contacts: 1,
    });
    expect(parsed.success).toBe(true);
    if (parsed.success) {
      expect(parsed.data).toEqual({
        created: 2,
        skipped: 1,
        linked_companies: 2,
        linked_contacts: 1,
      });
    }
  });
});

describe("Push schemas", () => {
  it("parses a per-item result list with every status", () => {
    const parsed = PushResponseSchema.safeParse({
      results: [
        {
          row_id: "r1",
          column_id: "c1",
          property: "outmate_tech_stack",
          status: "pushed",
          detail: null,
        },
        {
          row_id: "r2",
          column_id: "c2",
          property: "outmate_description",
          status: "skipped",
          detail: "not_linked",
        },
        {
          row_id: "r3",
          column_id: "c3",
          property: "name",
          status: "error",
          detail: "property_not_allowed",
        },
      ],
    });
    expect(parsed.success).toBe(true);
    if (parsed.success) {
      expect(parsed.data.results).toHaveLength(3);
      expect(parsed.data.results.map((r) => r.status)).toEqual([
        "pushed",
        "skipped",
        "error",
      ]);
    }
  });

  it("rejects a bogus item status", () => {
    expect(
      PushItemResultSchema.safeParse({
        row_id: "r1",
        column_id: "c1",
        property: "outmate_tech_stack",
        status: "maybe",
        detail: null,
      }).success,
    ).toBe(false);
  });
});

describe("Refresh schemas", () => {
  it("parses changes including a null row_id (contact link)", () => {
    const parsed = RefreshResponseSchema.safeParse({
      changes: [
        {
          row_id: "r1",
          remote_id: "1001",
          property: "industry",
          remote_value: "Manufacturing",
          previous: "Robotics",
        },
        {
          row_id: null,
          remote_id: "2001",
          property: "email",
          remote_value: null,
          previous: "ada@example-robotics.test",
        },
      ],
    });
    expect(parsed.success).toBe(true);
    if (parsed.success) {
      expect(parsed.data.changes).toHaveLength(2);
      expect(parsed.data.changes[1].row_id).toBeNull();
      expect(parsed.data.changes[1].remote_value).toBeNull();
    }
  });

  it("requires row_id to be present (nullable, not optional)", () => {
    expect(
      RefreshChangeSchema.safeParse({
        remote_id: "1001",
        property: "industry",
        remote_value: "x",
        previous: null,
      }).success,
    ).toBe(false);
  });
});

describe("Links schemas", () => {
  it("parses company + contact links (contact row_id is null)", () => {
    const parsed = LinksResponseSchema.safeParse({
      links: [
        { row_id: "r1", remote_id: "1001", object_type: "company" },
        { row_id: null, remote_id: "2001", object_type: "contact" },
      ],
    });
    expect(parsed.success).toBe(true);
    if (parsed.success) {
      expect(parsed.data.links[0].row_id).toBe("r1");
      expect(parsed.data.links[1].row_id).toBeNull();
    }
  });

  it("rejects a link missing row_id", () => {
    expect(
      LinkInfoSchema.safeParse({ remote_id: "1001", object_type: "company" }).success,
    ).toBe(false);
  });
});
