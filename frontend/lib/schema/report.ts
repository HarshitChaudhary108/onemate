/**
 * Zod mirror of the backend report contract (`backend/app/schemas/report.py`).
 * Kept in lockstep via `backend/tests/fixtures/report.schema.json` +
 * `tests/report-schema.test.ts` (contract drift check).
 */

import { z } from "zod";

export const StatusSchema = z.enum([
  "verified",
  "provider-reported",
  "inferred",
  "unavailable",
]);
export type Status = z.infer<typeof StatusSchema>;

export const ProviderNameSchema = z.enum(["Exa", "MoltSets"]);
export type ProviderName = z.infer<typeof ProviderNameSchema>;

export const ProviderStateSchema = z.enum([
  "ok",
  "partial",
  "error",
  "not_configured",
  "mock",
  "disabled",
  "not_found",
  "quota_exhausted",
]);
export type ProviderState = z.infer<typeof ProviderStateSchema>;

export const DataModeSchema = z.enum(["live", "mock", "mixed"]);
export type DataMode = z.infer<typeof DataModeSchema>;

export const SourceSchema = z.strictObject({
  provider: ProviderNameSchema,
  mock: z.boolean(),
  retrieved_at: z.iso.datetime(),
  url: z.string().nullable().optional(),
  title: z.string().nullable().optional(),
  published_date: z.string().nullable().optional(),
  highlight: z.string().nullable().optional(),
  query: z.string().nullable().optional(),
});
export type Source = z.infer<typeof SourceSchema>;

/** Mirrors the backend invariant: "verified" requires >=1 source with a URL. */
function verifiedNeedsUrlSource(
  status: Status,
  sources: Source[] | undefined,
): boolean {
  return status !== "verified" || (sources ?? []).some((s) => s.url != null);
}

/**
 * The Field[T] wrapper: value + status + sources (+ note), used by every
 * user-facing factual field. Mirrors the backend validators: a null/empty
 * value must be "unavailable"; "verified" requires a URL-carrying source.
 */
export function fieldSchema<T extends z.ZodType>(valueSchema: T) {
  return z
    .strictObject({
      value: valueSchema.nullable().optional(),
      status: StatusSchema,
      sources: z.array(SourceSchema).optional(),
      note: z.string().nullable().optional(),
    })
    .check((ctx) => {
      const { value, status, sources } = ctx.value;
      const empty = value == null || (Array.isArray(value) && value.length === 0);
      if (empty && status !== "unavailable") {
        ctx.issues.push({
          code: "custom",
          message: 'an empty value must have status "unavailable"',
          input: value,
        });
      }
      if (!verifiedNeedsUrlSource(status, sources)) {
        ctx.issues.push({
          code: "custom",
          message: '"verified" requires at least one source with a URL',
          input: status,
        });
      }
    });
}

export const GrowthSignalSchema = z.strictObject({
  type: z.enum([
    "hiring",
    "funding",
    "expansion",
    "leadership_change",
    "product_launch",
    "other",
  ]),
  summary: z.string(),
  date: z.string().nullable().optional(),
  sources: z.array(SourceSchema).optional(),
});
export type GrowthSignal = z.infer<typeof GrowthSignalSchema>;

export const DecisionMakerSchema = z
  .strictObject({
    name: z.string(),
    title: z.string().nullable().optional(),
    seniority: z.string().nullable().optional(),
    profile_url: z.string().nullable().optional(),
    status: StatusSchema,
    sources: z.array(SourceSchema).optional(),
    mock: z.boolean().optional(),
  })
  .check((ctx) => {
    if (!verifiedNeedsUrlSource(ctx.value.status, ctx.value.sources)) {
      ctx.issues.push({
        code: "custom",
        message: '"verified" requires at least one source with a URL',
        input: ctx.value.status,
      });
    }
  });
export type DecisionMaker = z.infer<typeof DecisionMakerSchema>;

export const EMAIL_RISK_NOTE = "deliverability signal, not identity proof";

export const ContactInfoSchema = z
  .strictObject({
    person_name: z.string(),
    business_email: z.string().nullable().optional(),
    email_risk_grade: z.string().nullable().optional(),
    email_last_validated: z.string().nullable().optional(),
    email_risk_note: z.literal(EMAIL_RISK_NOTE).optional(),
    status: StatusSchema,
    sources: z.array(SourceSchema).optional(),
    mock: z.boolean().optional(),
  })
  .check((ctx) => {
    if (!verifiedNeedsUrlSource(ctx.value.status, ctx.value.sources)) {
      ctx.issues.push({
        code: "custom",
        message: '"verified" requires at least one source with a URL',
        input: ctx.value.status,
      });
    }
  });
export type ContactInfo = z.infer<typeof ContactInfoSchema>;

export const RationalePointSchema = z.strictObject({
  point: z.string(),
  sources: z.array(SourceSchema).optional(),
});
export type RationalePoint = z.infer<typeof RationalePointSchema>;

export const GtmHypothesisSchema = z.strictObject({
  summary: z.string(),
  rationale: z.array(RationalePointSchema),
  caveat: z.string(),
});
export type GtmHypothesis = z.infer<typeof GtmHypothesisSchema>;

export const StringFieldSchema = fieldSchema(z.string());
export type StringField = z.infer<typeof StringFieldSchema>;

export const CompanyIdentitySchema = z.strictObject({
  resolved: z.boolean(),
  company_name: StringFieldSchema,
  domain: StringFieldSchema,
  note: z.string().nullable().optional(),
});
export type CompanyIdentity = z.infer<typeof CompanyIdentitySchema>;

export const ProviderSummarySchema = z.strictObject({
  state: ProviderStateSchema,
  mock: z.boolean(),
  calls: z.number().int(),
  usage: z.record(z.string(), z.unknown()).nullable().optional(),
  note: z.string().nullable().optional(),
});
export type ProviderSummary = z.infer<typeof ProviderSummarySchema>;

export const ProvidersSchema = z.strictObject({
  exa: ProviderSummarySchema,
  moltsets: ProviderSummarySchema,
});
export type Providers = z.infer<typeof ProvidersSchema>;

export const ReportErrorSchema = z.strictObject({
  provider: z.enum(["Exa", "MoltSets", "Application"]),
  stage: z.enum([
    "identity",
    "overview",
    "growth",
    "leadership",
    "objective",
    "enrichment",
    "normalization",
    "api",
  ]),
  code: z.string(),
  message: z.string(),
  retryable: z.boolean(),
  request_id: z.string().nullable().optional(),
});
export type ReportError = z.infer<typeof ReportErrorSchema>;

/** The request echo embedded in a report (mirrors backend ResearchRequest). */
export const RequestEchoSchema = z.strictObject({
  company_name: z.string().nullable().optional(),
  domain: z.string().nullable().optional(),
  research_objective: z.string().nullable().optional(),
});
export type RequestEcho = z.infer<typeof RequestEchoSchema>;

export const ReportSchema = z.strictObject({
  schema_version: z.literal("1.0"),
  data_mode: DataModeSchema,
  request: RequestEchoSchema,
  company_identity: CompanyIdentitySchema,
  description: StringFieldSchema,
  industry: StringFieldSchema,
  target_market: StringFieldSchema,
  employee_size: StringFieldSchema,
  growth_signals: fieldSchema(z.array(GrowthSignalSchema)),
  decision_makers: fieldSchema(z.array(DecisionMakerSchema)),
  contact_information: fieldSchema(z.array(ContactInfoSchema)),
  potential_gtm_opportunity: fieldSchema(GtmHypothesisSchema),
  overall_status: StatusSchema,
  missing_fields: z.array(z.string()),
  errors: z.array(ReportErrorSchema),
  warnings: z.array(z.string()),
  providers: ProvidersSchema,
  request_id: z.string(),
  generated_at: z.iso.datetime(),
});
export type Report = z.infer<typeof ReportSchema>;

export const ErrorDetailSchema = z.strictObject({
  field: z.string(),
  msg: z.string(),
});
export type ErrorDetail = z.infer<typeof ErrorDetailSchema>;

export const ErrorResponseSchema = z.strictObject({
  error: z.strictObject({
    code: z.string(),
    message: z.string(),
    request_id: z.string(),
    details: z.array(ErrorDetailSchema).nullable().optional(),
  }),
});
export type ErrorResponse = z.infer<typeof ErrorResponseSchema>;
