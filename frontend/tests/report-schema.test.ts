/**
 * Contract drift check: the Zod schemas in lib/schema/report.ts must mirror
 * the backend's committed `Report.model_json_schema()` snapshot and parse the
 * backend-generated sample report.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  CompanyIdentitySchema,
  ContactInfoSchema,
  DataModeSchema,
  DecisionMakerSchema,
  GrowthSignalSchema,
  GtmHypothesisSchema,
  ProviderNameSchema,
  ProviderStateSchema,
  ProviderSummarySchema,
  RationalePointSchema,
  ReportErrorSchema,
  ReportSchema,
  SourceSchema,
  StatusSchema,
} from "@/lib/schema/report";

type Json = Record<string, unknown>;

// import.meta.dirname avoids Vite's static `new URL(..., import.meta.url)`
// asset rewrite, which cannot reach files outside the frontend root.
const FIXTURE_DIR = join(
  import.meta.dirname,
  "..",
  "..",
  "backend",
  "tests",
  "fixtures",
);

function loadFixture(name: string): Json {
  return JSON.parse(
    readFileSync(join(FIXTURE_DIR, name), "utf-8"),
  ) as Json;
}

const schema = loadFixture("report.schema.json");
const defs = schema["$defs"] as Record<string, Json>;

function zodShapeKeys(s: unknown): string[] {
  const shape = (s as { shape: Record<string, unknown> }).shape;
  return Object.keys(shape).sort();
}

describe("enum parity with backend JSON schema", () => {
  it("Status", () => {
    const expected = (schema.properties as Json).overall_status as Json;
    expect(StatusSchema.options).toEqual(expected.enum);
  });

  it("ProviderState", () => {
    const expected = (defs.ProviderSummary.properties as Json).state as Json;
    expect(ProviderStateSchema.options).toEqual(expected.enum);
  });

  it("ProviderName (Source.provider)", () => {
    const expected = (defs.Source.properties as Json).provider as Json;
    expect(ProviderNameSchema.options).toEqual(expected.enum);
  });

  it("data_mode", () => {
    const expected = (schema.properties as Json).data_mode as Json;
    expect(DataModeSchema.options).toEqual(expected.enum);
  });

  it("GrowthSignal.type", () => {
    const expected = (defs.GrowthSignal.properties as Json).type as Json;
    expect(GrowthSignalSchema.shape.type.options).toEqual(expected.enum);
  });

  it("ReportError provider and stage", () => {
    const props = defs.ReportError.properties as Json;
    expect(ReportErrorSchema.shape.provider.options).toEqual(
      (props.provider as Json).enum,
    );
    expect(ReportErrorSchema.shape.stage.options).toEqual(
      (props.stage as Json).enum,
    );
  });
});

describe("property-name parity", () => {
  it("top-level Report properties match exactly", () => {
    expect(zodShapeKeys(ReportSchema)).toEqual(
      Object.keys(schema.properties as Json).sort(),
    );
  });

  it("nested model properties match their $defs", () => {
    const pairs: Array<[string, unknown]> = [
      ["Source", SourceSchema],
      ["GrowthSignal", GrowthSignalSchema],
      ["DecisionMaker", DecisionMakerSchema],
      ["ContactInfo", ContactInfoSchema],
      ["GtmHypothesis", GtmHypothesisSchema],
      ["RationalePoint", RationalePointSchema],
      ["CompanyIdentity", CompanyIdentitySchema],
      ["ProviderSummary", ProviderSummarySchema],
      ["ReportError", ReportErrorSchema],
    ];
    for (const [defName, zodSchema] of pairs) {
      expect(zodShapeKeys(zodSchema), `shape mismatch for ${defName}`).toEqual(
        Object.keys(defs[defName].properties as Json).sort(),
      );
    }
  });

  it("every Field[T] def uses the value/status/sources/note shape", () => {
    const fieldKeys = ["note", "sources", "status", "value"];
    for (const [name, def] of Object.entries(defs)) {
      if (name.startsWith("Field")) {
        expect(Object.keys(def.properties as Json).sort(), name).toEqual(fieldKeys);
      }
    }
  });
});

describe("sample report", () => {
  it("parses the backend-generated sample report", () => {
    const sample = loadFixture("sample_report.json");
    const result = ReportSchema.safeParse(sample);
    expect(result.success, JSON.stringify(result.error?.issues)).toBe(true);
  });

  it("rejects a report with an invalid status", () => {
    const sample = loadFixture("sample_report.json");
    const bogus = {
      ...sample,
      description: {
        ...(sample.description as Json),
        status: "bogus",
      },
    };
    expect(ReportSchema.safeParse(bogus).success).toBe(false);
  });

  it("rejects a verified field with no URL-carrying source", () => {
    const sample = loadFixture("sample_report.json");
    const bogus = {
      ...sample,
      description: {
        ...(sample.description as Json),
        sources: [
          { provider: "MoltSets", mock: false, retrieved_at: "2026-10-16T12:00:00Z" },
        ],
      },
    };
    expect(ReportSchema.safeParse(bogus).success).toBe(false);
  });
});
