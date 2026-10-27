/**
 * Zod mirror of the backend integrations contract:
 * `backend/app/schemas/integration.py` (request/response envelopes) and
 * `RemoteObjectType` from `backend/app/integrations/hubspot/models.py`.
 *
 * `HubSpotStatus` carries metadata only — OAuth tokens never appear in
 * these contracts. Push approvals say WHICH cell and WHICH remote
 * property, never WHAT value: the backend re-reads cells server-side
 * (integration plan §3.4). Field nullability mirrors pydantic exactly:
 * `str | None` (required key) → `.nullable()`; `str | None = None`
 * (defaulted) → `.nullable().optional()` — the same convention as
 * `lib/schema/sheet.ts`.
 */

import { z } from "zod";

/** `RemoteObjectType` — Literal["company", "contact"]. */
export const RemoteObjectTypeSchema = z.enum(["company", "contact"]);
export type RemoteObjectType = z.infer<typeof RemoteObjectTypeSchema>;

/** `HubSpotStatus` — GET /status; `connected: false` is a state, never an
 *  error. Counts are real aggregates over `integration_links`. */
export const HubSpotStatusSchema = z.strictObject({
  connected: z.boolean(),
  portal_id: z.string().nullable().optional(),
  scopes: z.array(z.string()),
  connected_at: z.iso.datetime().nullable().optional(),
  last_sync_at: z.iso.datetime().nullable().optional(),
  companies_imported: z.number().int(),
  contacts_imported: z.number().int(),
  mock: z.boolean(),
});
export type HubSpotStatus = z.infer<typeof HubSpotStatusSchema>;

/** `RemoteObjectPreview` — one remote object for the import picker;
 *  the endpoint returns a bare array of these. */
export const RemoteObjectPreviewSchema = z.strictObject({
  remote_id: z.string(),
  object_type: RemoteObjectTypeSchema,
  properties: z.record(z.string(), z.string().nullable()),
});
export type RemoteObjectPreview = z.infer<typeof RemoteObjectPreviewSchema>;

/** `ImportResponse` — honest accounting: `created` new rows, `skipped`
 *  fetched companies that produced no row, `linked_*` links written. */
export const ImportResponseSchema = z.strictObject({
  created: z.number().int(),
  skipped: z.number().int(),
  linked_companies: z.number().int(),
  linked_contacts: z.number().int(),
});
export type ImportResponse = z.infer<typeof ImportResponseSchema>;

/** `PushItemResult` — per-approval outcome; `detail` carries the
 *  precondition/backend code for skipped and error items. */
export const PushItemResultSchema = z.strictObject({
  row_id: z.string(),
  column_id: z.string(),
  property: z.string(),
  status: z.enum(["pushed", "skipped", "error"]),
  detail: z.string().nullable().optional(),
});
export type PushItemResult = z.infer<typeof PushItemResultSchema>;

export const PushResponseSchema = z.strictObject({
  results: z.array(PushItemResultSchema),
});
export type PushResponse = z.infer<typeof PushResponseSchema>;

/** `RefreshChange` — one remote property that differs from the stored
 *  snapshot; `row_id` is null for snapshot-only links (contacts). */
export const RefreshChangeSchema = z.strictObject({
  row_id: z.string().nullable(),
  remote_id: z.string(),
  property: z.string(),
  remote_value: z.string().nullable(),
  previous: z.string().nullable(),
});
export type RefreshChange = z.infer<typeof RefreshChangeSchema>;

export const RefreshResponseSchema = z.strictObject({
  changes: z.array(RefreshChangeSchema),
});
export type RefreshResponse = z.infer<typeof RefreshResponseSchema>;

/** `LinkInfo` — one remote→row binding; `row_id` is null for contacts. */
export const LinkInfoSchema = z.strictObject({
  row_id: z.string().nullable(),
  remote_id: z.string(),
  object_type: RemoteObjectTypeSchema,
});
export type LinkInfo = z.infer<typeof LinkInfoSchema>;

export const LinksResponseSchema = z.strictObject({
  links: z.array(LinkInfoSchema),
});
export type LinksResponse = z.infer<typeof LinksResponseSchema>;

// --- request bodies (client → backend; validated server-side) ----------------

/** POST /import body fields. Absent id lists (`undefined`) mean "import
 *  everything fetched"; an empty list imports none of that object type —
 *  the distinction is the backend's `None` vs `[]` semantics. */
export type HubSpotImportInput = {
  company_ids?: string[];
  contact_ids?: string[];
};

/** One approved write — WHICH cell and WHICH remote property, never the
 *  value (mirrors `PushApproval`). */
export type PushApprovalInput = {
  row_id: string;
  column_id: string;
  property: string;
};
