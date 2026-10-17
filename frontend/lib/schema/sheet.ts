/**
 * Zod mirror of the backend sheets contract:
 * `backend/app/sheets/models.py` (persistence rows serialized as-is),
 * `backend/app/schemas/sheet.py` (request/response envelopes), and
 * `PlanResult` from `backend/app/planner/spec.py`.
 *
 * `Field`, `Source`, `Status` and `CompanyIdentity` are imported from
 * `lib/schema/report.ts` — the report contract is on the unchanged list,
 * so the cell `result` is the exact same wrapper the report uses.
 */

import { z } from "zod";

import { CompanyIdentitySchema, fieldSchema } from "@/lib/schema/report";

// --- enum-like columns (backend Literal types, models.py) -------------------

export const IdentityStatusSchema = z.enum([
  "pending",
  "resolved",
  "unresolved",
  "ambiguous",
  "error",
]);
export type IdentityStatus = z.infer<typeof IdentityStatusSchema>;

export const OutputTypeSchema = z.enum([
  "text",
  "number",
  "boolean",
  "enum",
  "url",
  "date",
]);
export type OutputType = z.infer<typeof OutputTypeSchema>;

export const StrategySchema = z.literal("research");
export type Strategy = z.infer<typeof StrategySchema>;

export const SourceScopeSchema = z.enum(["company_site", "web", "news", "people"]);
export type SourceScope = z.infer<typeof SourceScopeSchema>;

export const DepthSchema = z.enum(["standard", "deep"]);
export type Depth = z.infer<typeof DepthSchema>;

export const CellStatusSchema = z.enum([
  "idle",
  "queued",
  "running",
  "done",
  "error",
  "blocked",
]);
export type CellStatus = z.infer<typeof CellStatusSchema>;

export const RunScopeSchema = z.enum(["empty", "stale", "error", "all"]);
export type RunScope = z.infer<typeof RunScopeSchema>;

/** CellValue = str | int | float | bool — each cell stores Field[CellValue]. */
export const CellValueSchema = z.union([z.string(), z.number(), z.boolean()]);
export type CellValue = z.infer<typeof CellValueSchema>;

/** The cell `result`: a report `Field` over the cell-value union. The
 *  fieldSchema checks carry over — empty values must be "unavailable" and
 *  "verified" needs a URL-bearing source. */
export const CellResultSchema = fieldSchema(CellValueSchema);
export type CellResult = z.infer<typeof CellResultSchema>;

// --- persistence rows (models.py, extra="forbid" → strictObject) ------------

export const SheetSchema = z.strictObject({
  id: z.string(),
  name: z.string(),
  created_at: z.iso.datetime(),
  updated_at: z.iso.datetime(),
});
export type Sheet = z.infer<typeof SheetSchema>;

export const SheetRowSchema = z.strictObject({
  id: z.string(),
  sheet_id: z.string(),
  input_name: z.string().nullable().optional(),
  input_domain: z.string().nullable().optional(),
  position: z.number().int(),
  identity_status: IdentityStatusSchema,
  resolved_name: z.string().nullable().optional(),
  resolved_domain: z.string().nullable().optional(),
  identity: CompanyIdentitySchema.nullable().optional(),
  deleted: z.boolean(),
  created_at: z.iso.datetime(),
  updated_at: z.iso.datetime(),
});
export type SheetRow = z.infer<typeof SheetRowSchema>;

export const SheetColumnSchema = z.strictObject({
  id: z.string(),
  sheet_id: z.string(),
  name: z.string(),
  prompt_template: z.string(),
  output_type: OutputTypeSchema,
  enum_options: z.array(z.string()).nullable().optional(),
  strategy: StrategySchema,
  source_scope: SourceScopeSchema,
  depth: DepthSchema,
  depends_on: z.array(z.string()),
  version: z.number().int(),
  position: z.number().int(),
  deleted: z.boolean(),
  created_at: z.iso.datetime(),
  updated_at: z.iso.datetime(),
});
export type SheetColumn = z.infer<typeof SheetColumnSchema>;

export const SheetCellSchema = z.strictObject({
  row_id: z.string(),
  column_id: z.string(),
  status: CellStatusSchema,
  result: CellResultSchema.nullable().optional(),
  column_version: z.number().int(),
  stale: z.boolean(),
  revision: z.number().int(),
  cost: z.number().nullable().optional(),
  rendered_prompt: z.string().nullable().optional(),
  error: z.string().nullable().optional(),
  run_id: z.string().nullable().optional(),
  created_at: z.iso.datetime(),
  updated_at: z.iso.datetime(),
});
export type SheetCell = z.infer<typeof SheetCellSchema>;

export const CellRevisionSchema = z.strictObject({
  id: z.number().int(),
  row_id: z.string(),
  column_id: z.string(),
  revision: z.number().int(),
  result: CellResultSchema.nullable().optional(),
  error: z.string().nullable().optional(),
  created_at: z.iso.datetime(),
});
export type CellRevision = z.infer<typeof CellRevisionSchema>;

export const SheetRunSchema = z.strictObject({
  id: z.string(),
  sheet_id: z.string(),
  cap: z.number().nullable().optional(),
  spent: z.number(),
  cancelled: z.boolean(),
  reason: z.string().nullable().optional(),
  created_at: z.iso.datetime(),
});
export type SheetRun = z.infer<typeof SheetRunSchema>;

// --- response envelopes (schemas/sheet.py) ----------------------------------

export const SheetSnapshotSchema = z.strictObject({
  sheet: SheetSchema,
  rows: z.array(SheetRowSchema),
  columns: z.array(SheetColumnSchema),
  cells: z.array(SheetCellSchema),
  latest_run: SheetRunSchema.nullable().optional(),
});
export type SheetSnapshot = z.infer<typeof SheetSnapshotSchema>;

export const SheetListResponseSchema = z.strictObject({
  sheets: z.array(SheetSchema),
});
export type SheetListResponse = z.infer<typeof SheetListResponseSchema>;

export const ImportRowsResponseSchema = z.strictObject({
  rows: z.array(SheetRowSchema),
  created: z.number().int(),
  skipped: z.number().int(),
});
export type ImportRowsResponse = z.infer<typeof ImportRowsResponseSchema>;

export const CreateColumnsResponseSchema = z.strictObject({
  columns: z.array(SheetColumnSchema),
  run: SheetRunSchema.nullable().optional(),
});
export type CreateColumnsResponse = z.infer<typeof CreateColumnsResponseSchema>;

export const CellDetailSchema = z.strictObject({
  cell: SheetCellSchema,
  revisions: z.array(CellRevisionSchema),
});
export type CellDetail = z.infer<typeof CellDetailSchema>;

// --- planner preview (planner/spec.py PlanResult) ----------------------------

export const PlanColumnSchema = z.strictObject({
  name: z.string(),
  prompt_template: z.string(),
  output_type: OutputTypeSchema,
  enum_options: z.array(z.string()),
  source_scope: SourceScopeSchema,
  depth: DepthSchema,
  strategy: StrategySchema,
  depends_on: z.array(z.string()),
});
export type PlanColumn = z.infer<typeof PlanColumnSchema>;

export const PlanResultSchema = z.strictObject({
  intent: z.enum(["add_columns", "needs_clarification", "unsupported", "failed"]),
  columns: z.array(PlanColumnSchema),
  question: z.string().nullable().optional(),
  reason: z.string().nullable().optional(),
  estimated_cost_usd: z.number().nullable().optional(),
  mock: z.boolean(),
});
export type PlanResult = z.infer<typeof PlanResultSchema>;

// --- request bodies (client → backend; validated server-side) ----------------

export type ImportRowInput = { name?: string; domain?: string };

export type ColumnSpecInput = {
  name: string;
  prompt_template: string;
  output_type: OutputType;
  enum_options?: string[];
  source_scope: SourceScope;
  depth?: Depth;
};

export type CreateRunInput = {
  scope?: RunScope;
  confirm_overwrite?: boolean;
  cap?: number;
};
