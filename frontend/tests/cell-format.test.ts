/**
 * cell-format helpers (P7): value formatting by output type, USD formatting
 * for provider-reported costs, and the paste-import line parser.
 */

import { describe, expect, it } from "vitest";

import {
  cellStateLabel,
  formatCellValue,
  formatUsd,
  parseImportLine,
} from "@/lib/utils/cell-format";

describe("formatCellValue", () => {
  it("formats per output type and hides null", () => {
    expect(formatCellValue(null, "text")).toBeNull();
    expect(formatCellValue("robotics", "text")).toBe("robotics");
    expect(formatCellValue(1234567.5, "number")).toBe("1,234,567.5");
    expect(formatCellValue(true, "boolean")).toBe("Yes");
    expect(formatCellValue(false, "boolean")).toBe("No");
    expect(formatCellValue("https://x.test", "url")).toBe("https://x.test");
    expect(formatCellValue("2026-01-05", "date")).toBe("2026-01-05");
  });
});

describe("formatUsd — provider-reported costs are never rounded away", () => {
  it("keeps sub-cent precision", () => {
    expect(formatUsd(0.007)).toBe("$0.0070");
    expect(formatUsd(0)).toBe("$0.00");
    expect(formatUsd(1.2)).toBe("$1.20");
  });
});

describe("cellStateLabel", () => {
  it("gives every state words", () => {
    expect(cellStateLabel("queued")).toBe("Queued");
    expect(cellStateLabel("blocked")).toBe("Blocked");
  });
});

describe("parseImportLine", () => {
  it("parses tab, comma and single-token lines", () => {
    expect(parseImportLine("Acme Corp\tacme.com")).toEqual({ name: "Acme Corp", domain: "acme.com" });
    expect(parseImportLine("Globex,globex.io")).toEqual({ name: "Globex", domain: "globex.io" });
    expect(parseImportLine("startup.dev")).toEqual({ domain: "startup.dev" });
    expect(parseImportLine("Some Company Inc")).toEqual({ name: "Some Company Inc" });
    expect(parseImportLine("https://stripe.com/pricing")).toEqual({ domain: "stripe.com" });
    expect(parseImportLine("  ")).toBeNull();
    expect(parseImportLine(",")).toBeNull();
  });
});
