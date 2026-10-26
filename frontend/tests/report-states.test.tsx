/**
 * Report states (plan M4 tests), rendered from the backend-generated fixture
 * snapshots in `backend/tests/fixtures/reports/` (read via `fs`). No network.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ErrorAlert } from "@/components/error-alert";
import { JsonViewer } from "@/components/json-viewer";
import { OverviewSection } from "@/components/overview-section";
import { ReportView } from "@/components/report-view";
import { StatusBadge } from "@/components/status-badge";
import { ReportSchema, type Report, type Source } from "@/lib/schema/report";

const FIXTURE_DIR = join(
  import.meta.dirname,
  "..",
  "..",
  "backend",
  "tests",
  "fixtures",
  "reports",
);

function loadFixture(name: string): Record<string, unknown> {
  return JSON.parse(readFileSync(join(FIXTURE_DIR, name), "utf-8")) as Record<string, unknown>;
}

/** Parse a fixture with the Zod contract — an invalid fixture fails loudly. */
function report(name: string): Report {
  return ReportSchema.parse(loadFixture(name));
}

afterEach(() => {
  cleanup();
  Reflect.deleteProperty(navigator, "clipboard");
});

describe("live_success", () => {
  it("renders the report without a mock banner", () => {
    render(<ReportView report={report("live_success.json")} />);

    expect(screen.queryByTestId("mock-banner")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Example Robotics Ltd" })).toBeInTheDocument();
    expect(screen.getByText(/7 of 7 factual fields have a value/)).toBeInTheDocument();
    expect(screen.getByText("Live provider data")).toBeInTheDocument();
    const growth = screen.getByTestId("growth-signals");
    expect(
      within(growth).getByText("Example Robotics announced a fictional Series A round to fund expansion."),
    ).toBeInTheDocument();
  });
});

describe("mock", () => {
  it("shows the persistent banner and Sample tags", () => {
    render(<ReportView report={report("mock.json")} />);

    const banner = screen.getByTestId("mock-banner");
    expect(banner).toHaveTextContent("Mock / sample data");
    expect(banner).toHaveAttribute("role", "status");
    expect(screen.getAllByText("Sample").length).toBeGreaterThan(0);
  });
});

describe("partial", () => {
  it("shows the provider error and keeps the Exa data", () => {
    render(<ReportView report={report("partial.json")} />);

    const error = screen.getByTestId("report-error");
    expect(error).toHaveTextContent("MoltSets returned a server error; try again later.");
    expect(error).toHaveTextContent("moltsets_server_error");
    expect(error).toHaveTextContent("req-fixture-0003");
    // A plain recovery hint derived from the code.
    expect(error).toHaveTextContent("Try again shortly");
    // The working provider's data is still rendered.
    expect(
      screen.getByText("Example Robotics Ltd builds warehouse robots for logistics operators."),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("error-alert")).not.toBeInTheDocument();
  });
});

describe("unresolved identity", () => {
  it("explains what was searched and never claims the company lacks signals", () => {
    render(<ReportView report={report("unresolved.json")} />);

    const notice = screen.getByTestId("identity-notice");
    expect(notice).toHaveTextContent("What was searched");
    expect(notice).toHaveTextContent("Example Robotics");
    expect(notice).toHaveTextContent(/more than one company matched/i);
    expect(notice).toHaveTextContent("skipped");
    expect(notice).toHaveTextContent("not evidence that the company has no growth signals");
    expect(screen.queryByText(/No growth signals exist/i)).not.toBeInTheDocument();
    expect(screen.queryByTestId("growth-signals")).not.toBeInTheDocument();
  });
});

describe("no evidence", () => {
  it("explains the empty search without phrasing it as proof of absence", () => {
    render(<ReportView report={report("no_evidence.json")} />);

    const notice = screen.getByTestId("identity-notice");
    expect(notice).toHaveTextContent("no-evidence.test");
    expect(notice).toHaveTextContent("no public pages on this domain");
    expect(notice).toHaveTextContent("not evidence that the company has no growth signals");
    expect(screen.queryByText(/No growth signals exist/i)).not.toBeInTheDocument();
  });
});

describe("api_error state", () => {
  it("renders a role=alert block with the safe message and the request ID", () => {
    const fixture = loadFixture("api_error.json") as {
      error: { code: string; message: string; request_id: string };
    };
    render(
      <ErrorAlert
        status={503}
        code={fixture.error.code}
        message={fixture.error.message}
        requestId={fixture.error.request_id}
      />,
    );

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(fixture.error.message);
    expect(alert).toHaveTextContent(fixture.error.request_id);
    expect(alert).toHaveTextContent("HTTP 503");
  });
});

describe("claim-level source attachment", () => {
  it("keeps each growth item's sources inside that item only", () => {
    const base = report("live_success.json");
    const itemA = (base.growth_signals.value ?? [])[0];
    const itemB = {
      ...itemA,
      type: "expansion" as const,
      summary: "A second fixture signal for the cross-item check.",
      date: "2026-05-30",
      sources: [
        {
          ...(itemA.sources ?? [])[0],
          url: "https://news.example-robotics.test/expansion-2026",
          title: "Second fixture source",
        },
      ],
    };
    const twoItems: Report = {
      ...base,
      growth_signals: { ...base.growth_signals, value: [itemA, itemB] },
    };

    render(<ReportView report={twoItems} />);

    const items = screen.getAllByTestId("growth-item");
    expect(items).toHaveLength(2);
    const hrefsIn = (element: HTMLElement) =>
      within(element)
        .getAllByRole("link")
        .map((link) => link.getAttribute("href"));
    expect(hrefsIn(items[0])).toEqual(["https://news.example-robotics.test/funding-2026"]);
    expect(hrefsIn(items[1])).toEqual(["https://news.example-robotics.test/expansion-2026"]);
  });

  it("keeps each decision-maker's sources inside that person's row", () => {
    render(<ReportView report={report("live_success.json")} />);

    const rows = screen.getAllByTestId("decision-maker-row");
    expect(rows).toHaveLength(3);
    expect(within(rows[0]).getByText("profiles.example-robotics.test")).toBeInTheDocument();
    expect(within(rows[1]).queryByText("profiles.example-robotics.test")).not.toBeInTheDocument();
    expect(within(rows[1]).getByText("no public link")).toBeInTheDocument();
    expect(within(rows[2]).queryByText("profiles.example-robotics.test")).not.toBeInTheDocument();
  });
});

describe("verified without an inspectable source", () => {
  const sourceless: Source = {
    provider: "MoltSets",
    mock: false,
    retrieved_at: "2026-10-16T12:00:00Z",
  };

  it("renders 'Source missing' instead of 'Verified'", () => {
    render(<StatusBadge status="verified" sources={[sourceless]} />);
    expect(screen.getByText("Source missing")).toBeInTheDocument();
    expect(screen.queryByText("Verified")).not.toBeInTheDocument();
  });

  it("never shows 'Verified' for such a field in a section", () => {
    const base = report("live_success.json");
    const broken: Report = {
      ...base,
      description: { ...base.description, status: "verified", sources: [sourceless] },
    };

    render(<OverviewSection report={broken} />);

    const descriptionRow = screen.getByTestId("overview-description");
    expect(within(descriptionRow).getByText("Source missing")).toBeInTheDocument();
    expect(within(descriptionRow).queryByText("Verified")).not.toBeInTheDocument();
  });
});

describe("raw JSON viewer", () => {
  it("is collapsed by default", () => {
    render(<ReportView report={report("live_success.json")} />);
    const details = screen.getByTestId("json-viewer");
    expect(details.tagName).toBe("DETAILS");
    expect((details as HTMLDetailsElement).open).toBe(false);
    expect(within(details).getByText("Raw JSON")).toBeInTheDocument();
  });

  it("copies the report JSON to the clipboard and announces it", async () => {
    const user = userEvent.setup();
    const parsed = report("live_success.json");
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });

    render(<JsonViewer report={parsed} />);
    await user.click(screen.getByRole("button", { name: "Copy JSON" }));

    expect(writeText).toHaveBeenCalledTimes(1);
    expect(JSON.parse(String(writeText.mock.calls[0][0]))).toEqual(parsed);
    expect(screen.getByText("Copied")).toBeInTheDocument();
  });

  it("reports a clipboard failure gracefully", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });

    render(<JsonViewer report={report("live_success.json")} />);
    await user.click(screen.getByRole("button", { name: "Copy JSON" }));

    expect(screen.getByText(/Copy failed/)).toBeInTheDocument();
  });
});
