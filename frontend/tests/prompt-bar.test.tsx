/**
 * Prompt bar + plan card tests (P7): @-mention insertion of {{Column}},
 * suggestion chips, the planning state, and plan-card editing before apply.
 * The store is hydrated directly; onPlan/onApply are spies — no network.
 */

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PlanCard } from "@/components/sheet/plan-card";
import { mentionAt, PromptBar } from "@/components/sheet/prompt-bar";
import type { SheetResult } from "@/lib/api/sheets";
import type { PlanResult, SheetSnapshot } from "@/lib/schema/sheet";
import { useSheetStore } from "@/lib/store/sheet-store";

const TS = "2026-10-17T12:00:00Z";

const COLUMN_NAMES = ["Industry", "Funding", "Headcount"];

function hydrate() {
  const snapshot: SheetSnapshot = {
    sheet: { id: "s1", name: "targets", created_at: TS, updated_at: TS },
    rows: [],
    columns: COLUMN_NAMES.map((name, i) => ({
      id: `c${i}`,
      sheet_id: "s1",
      name,
      prompt_template: `${name} of {{Company}}`,
      output_type: "text",
      strategy: "research",
      source_scope: "web",
      depth: "standard",
      depends_on: [],
      version: 1,
      position: i,
      deleted: false,
      created_at: TS,
      updated_at: TS,
    })),
    cells: [],
    latest_run: null,
  };
  useSheetStore.getState().loadSnapshot(snapshot);
}

function renderBar(overrides: Partial<Parameters<typeof PromptBar>[0]> = {}) {
  const props: Parameters<typeof PromptBar>[0] = {
    planning: false,
    plan: null,
    planError: null,
    onPlan: vi.fn(async (): Promise<SheetResult<PlanResult>> => ({ kind: "invalid_response" })),
    onApply: vi.fn(),
    onDismissPlan: vi.fn(),
    ...overrides,
  };
  render(<PromptBar {...props} />);
  return props;
}

afterEach(() => {
  cleanup();
  useSheetStore.getState().disconnect();
});

describe("mentionAt", () => {
  it("detects an @-mention only after whitespace or at the start", () => {
    expect(mentionAt("@Ind", 4)).toEqual({ start: 0, query: "Ind" });
    expect(mentionAt("size of @Fund", 13)).toEqual({ start: 8, query: "Fund" });
    expect(mentionAt("email@test", 9)).toBeNull();
    expect(mentionAt("{{Industry}}", 12)).toBeNull();
    expect(mentionAt("@with space", 10)).toBeNull();
  });
});

describe("@ column autocomplete", () => {
  it("inserts {{Column}} at the mention position", async () => {
    const user = userEvent.setup();
    hydrate();
    renderBar();
    const input = screen.getByLabelText("Describe the column to add");
    await user.type(input, "add @Ind");
    const option = await screen.findByRole("option", { name: /Industry/ });
    await user.click(option);
    expect(input).toHaveValue("add {{Industry}}");
  });

  it("arrow keys + Enter pick the highlighted suggestion", async () => {
    const user = userEvent.setup();
    hydrate();
    renderBar();
    const input = screen.getByLabelText("Describe the column to add");
    await user.type(input, "size by @");
    await screen.findAllByRole("option");
    await user.keyboard("{ArrowDown}{Enter}");
    expect(input).toHaveValue("size by {{Funding}}");
  });
});

describe("prompt bar submission", () => {
  it("sends the prompt and shows the planning spinner — no stage labels", async () => {
    const user = userEvent.setup();
    hydrate();
    const onPlan = vi.fn(
      async (): Promise<SheetResult<PlanResult>> => ({ kind: "invalid_response" }),
    );
    const { rerender } = render(
      <PromptBar
        planning={false}
        plan={null}
        planError={null}
        onPlan={onPlan}
        onApply={vi.fn()}
        onDismissPlan={vi.fn()}
      />,
    );
    const input = screen.getByLabelText("Describe the column to add");
    await user.type(input, "industry for each company");
    await user.click(screen.getByRole("button", { name: "Plan" }));
    expect(onPlan).toHaveBeenCalledWith("industry for each company");
    expect(await screen.findByRole("alert")).toBeInTheDocument();

    // The planning spinner is the only progress signal — no stage labels.
    rerender(
      <PromptBar
        planning={true}
        plan={null}
        planError={null}
        onPlan={onPlan}
        onApply={vi.fn()}
        onDismissPlan={vi.fn()}
      />,
    );
    expect(screen.getByText("Planning…")).toBeInTheDocument();
  });

  it("a suggestion chip fills the textarea", async () => {
    const user = userEvent.setup();
    hydrate();
    renderBar();
    await user.click(screen.getByRole("button", { name: "Industry for each company" }));
    expect(screen.getByLabelText("Describe the column to add")).toHaveValue(
      "Industry for each company",
    );
  });
});

describe("plan card editing", () => {
  const plan: PlanResult = {
    intent: "add_columns",
    columns: [
      {
        name: "Sector",
        prompt_template: "Sector of {{Company}}",
        output_type: "text",
        enum_options: [],
        source_scope: "web",
        depth: "standard",
        strategy: "research",
        depends_on: [],
      },
    ],
    estimated_cost_usd: 0.021,
    mock: false,
  };

  it("edits name/type/scope/depth and applies the edited spec", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    render(<PlanCard plan={plan} existingNames={[]} onApply={onApply} onCancel={vi.fn()} />);

    await user.clear(screen.getByLabelText("Column 1 name"));
    await user.type(screen.getByLabelText("Column 1 name"), "Sector X");
    await user.selectOptions(screen.getByLabelText("Column 1 type"), "enum");
    await user.type(
      screen.getByLabelText(/enum options/i),
      "robotics, logistics, retail",
    );
    await user.selectOptions(screen.getByLabelText("Column 1 scope"), "news");
    await user.selectOptions(screen.getByLabelText("Column 1 depth"), "deep");

    await user.click(screen.getByRole("button", { name: "Add and run" }));
    expect(onApply).toHaveBeenCalledWith(
      [
        {
          name: "Sector X",
          prompt_template: "Sector of {{Company}}",
          output_type: "enum",
          enum_options: ["robotics", "logistics", "retail"],
          source_scope: "news",
          depth: "deep",
        },
      ],
      true,
    );
  });

  it("labels the estimate as an estimate and shows mock provenance", () => {
    render(
      <PlanCard
        plan={{ ...plan, mock: true }}
        existingNames={[]}
        onApply={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByText(/estimate only/i)).toBeInTheDocument();
    expect(screen.getByText("$0.02")).toBeInTheDocument();
    expect(screen.getByText("Mock planner")).toBeInTheDocument();
  });

  it("blocks apply on a name collision with an existing column", () => {
    render(
      <PlanCard plan={plan} existingNames={["Sector"]} onApply={vi.fn()} onCancel={vi.fn()} />,
    );
    expect(screen.getByText(/already exists/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add and run" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Add only" })).toBeDisabled();
  });

  it("a clarification intent shows the question — no silent column add", () => {
    render(
      <PlanCard
        plan={{ ...plan, intent: "needs_clarification", columns: [], question: "Which year?" }}
        existingNames={[]}
        onApply={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByText("Which year?")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add and run" })).not.toBeInTheDocument();
  });
});
