"use client";

import { CircleAlert, TriangleAlert } from "lucide-react";
import { useMemo, useState } from "react";

import type {
  ColumnSpecInput,
  Depth,
  OutputType,
  PlanColumn,
  PlanResult,
  SourceScope,
} from "@/lib/schema/sheet";
import { formatUsd } from "@/lib/utils/cell-format";

/**
 * Plan card (P7 / plan §4.4): the planner's preview, editable before it is
 * applied — column name, type, scope and depth per planned column, the
 * labelled cost estimate, and "Add and run" / "Add only" / "Cancel".
 * The planner's output is never trusted: the apply payload is rebuilt from
 * these drafts and the backend re-validates everything.
 */

const OUTPUT_TYPES: OutputType[] = ["text", "number", "boolean", "enum", "url", "date"];
const SCOPES: SourceScope[] = ["company_site", "web", "news", "people"];
const DEPTHS: Depth[] = ["standard", "deep"];

type Draft = {
  name: string;
  prompt_template: string;
  output_type: OutputType;
  enum_options: string;
  source_scope: SourceScope;
  depth: Depth;
};

function toDraft(column: PlanColumn): Draft {
  return {
    name: column.name,
    prompt_template: column.prompt_template,
    output_type: column.output_type,
    enum_options: column.enum_options.join(", "),
    source_scope: column.source_scope,
    depth: column.depth,
  };
}

function toSpec(draft: Draft): ColumnSpecInput {
  return {
    name: draft.name.trim(),
    prompt_template: draft.prompt_template.trim(),
    output_type: draft.output_type,
    enum_options:
      draft.output_type === "enum"
        ? draft.enum_options
            .split(",")
            .map((option) => option.trim())
            .filter(Boolean)
        : [],
    source_scope: draft.source_scope,
    depth: draft.depth,
  };
}

const selectClass =
  "rounded border border-line bg-surface px-1.5 py-1 text-xs text-ink";

export function PlanCard({
  plan,
  existingNames,
  applying,
  onApply,
  onCancel,
}: {
  plan: PlanResult;
  existingNames: string[];
  applying?: boolean;
  onApply: (columns: ColumnSpecInput[], run: boolean) => void;
  onCancel: () => void;
}) {
  const [drafts, setDrafts] = useState<Draft[]>(() => plan.columns.map(toDraft));

  const problems = useMemo(() => {
    const seen = new Set<string>();
    const existing = new Set(existingNames.map((name) => name.toLowerCase()));
    const map = new Map<number, string>();
    drafts.forEach((draft, index) => {
      const name = draft.name.trim();
      if (!name) {
        map.set(index, "Name is required.");
        return;
      }
      const lowered = name.toLowerCase();
      if (existing.has(lowered)) {
        map.set(index, `A column named "${name}" already exists.`);
        return;
      }
      if (seen.has(lowered)) {
        map.set(index, "Duplicate column name in this plan.");
        return;
      }
      seen.add(lowered);
      if (draft.output_type === "enum" && !draft.enum_options.trim()) {
        map.set(index, "List the enum options, comma-separated.");
      }
    });
    return map;
  }, [drafts, existingNames]);

  if (plan.intent === "needs_clarification") {
    return (
      <div className="rounded-card border border-line bg-surface p-4 shadow-card" data-testid="plan-card">
        <p className="flex items-center gap-2 text-sm font-medium text-ink-strong">
          <CircleAlert aria-hidden="true" className="h-4 w-4 text-inferred-ink" />
          The planner needs one clarification
        </p>
        <p className="mt-2 text-sm text-ink">{plan.question}</p>
        <p className="mt-1 text-xs text-ink-muted">
          Answer in the prompt box and plan again — nothing was added.
        </p>
        <div className="mt-3 flex justify-end">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-card border border-line-strong bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface-muted"
          >
            Dismiss
          </button>
        </div>
      </div>
    );
  }

  if (plan.intent === "unsupported" || plan.intent === "failed") {
    return (
      <div className="rounded-card border border-line bg-surface p-4 shadow-card" data-testid="plan-card">
        <p className="flex items-center gap-2 text-sm font-medium text-ink-strong">
          <TriangleAlert aria-hidden="true" className="h-4 w-4 text-danger-ink" />
          {plan.intent === "unsupported" ? "Unsupported request" : "Planning failed"}
        </p>
        <p className="mt-2 text-sm text-ink">{plan.reason ?? "No reason was given."}</p>
        <div className="mt-3 flex justify-end">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-card border border-line-strong bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface-muted"
          >
            Dismiss
          </button>
        </div>
      </div>
    );
  }

  const update = (index: number, patch: Partial<Draft>) => {
    setDrafts((current) =>
      current.map((draft, i) => (i === index ? { ...draft, ...patch } : draft)),
    );
  };

  return (
    <div className="rounded-card border border-line bg-surface p-4 shadow-card" data-testid="plan-card">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-semibold text-ink-strong">
          Planned {drafts.length === 1 ? "column" : `${drafts.length} columns`}
        </p>
        {plan.mock ? (
          <span className="rounded-full border border-inferred-line bg-inferred-bg px-2 py-0.5 text-[0.65rem] font-semibold text-inferred-ink">
            Mock planner
          </span>
        ) : null}
      </div>

      <ul className="mt-3 space-y-3">
        {drafts.map((draft, index) => {
          const problem = problems.get(index);
          return (
            <li
              key={index}
              className="rounded-card border border-line bg-surface-muted/50 p-3"
              data-testid="plan-column"
            >
              <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto_auto_auto]">
                <label className="block">
                  <span className="sr-only">Column name</span>
                  <input
                    type="text"
                    value={draft.name}
                    onChange={(event) => update(index, { name: event.target.value })}
                    aria-label={`Column ${index + 1} name`}
                    aria-invalid={problem ? true : undefined}
                    className="w-full rounded border border-line bg-surface px-2 py-1 text-sm font-medium text-ink"
                  />
                </label>
                <label className="flex items-center gap-1 text-xs text-ink-muted">
                  Type
                  <select
                    value={draft.output_type}
                    onChange={(event) =>
                      update(index, { output_type: event.target.value as OutputType })
                    }
                    aria-label={`Column ${index + 1} type`}
                    className={selectClass}
                  >
                    {OUTPUT_TYPES.map((type) => (
                      <option key={type} value={type}>
                        {type}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex items-center gap-1 text-xs text-ink-muted">
                  Scope
                  <select
                    value={draft.source_scope}
                    onChange={(event) =>
                      update(index, { source_scope: event.target.value as SourceScope })
                    }
                    aria-label={`Column ${index + 1} scope`}
                    className={selectClass}
                  >
                    {SCOPES.map((scope) => (
                      <option key={scope} value={scope}>
                        {scope}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex items-center gap-1 text-xs text-ink-muted">
                  Depth
                  <select
                    value={draft.depth}
                    onChange={(event) =>
                      update(index, { depth: event.target.value as Depth })
                    }
                    aria-label={`Column ${index + 1} depth`}
                    className={selectClass}
                  >
                    {DEPTHS.map((depth) => (
                      <option key={depth} value={depth}>
                        {depth}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              {draft.output_type === "enum" ? (
                <label className="mt-2 block text-xs text-ink-muted">
                  Enum options (comma-separated)
                  <input
                    type="text"
                    value={draft.enum_options}
                    onChange={(event) =>
                      update(index, { enum_options: event.target.value })
                    }
                    className="mt-1 w-full rounded border border-line bg-surface px-2 py-1 text-xs text-ink"
                  />
                </label>
              ) : null}
              <label className="mt-2 block text-xs text-ink-muted">
                Prompt template
                <textarea
                  value={draft.prompt_template}
                  onChange={(event) =>
                    update(index, { prompt_template: event.target.value })
                  }
                  rows={2}
                  className="mt-1 w-full rounded border border-line bg-surface px-2 py-1 font-mono text-xs text-ink"
                />
              </label>
              {problem ? (
                <p className="mt-1 text-xs font-medium text-danger-ink">{problem}</p>
              ) : null}
            </li>
          );
        })}
      </ul>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-ink-muted">
          {typeof plan.estimated_cost_usd === "number" ? (
            <>
              Estimated cost{" "}
              <span className="tabular-nums text-ink">{formatUsd(plan.estimated_cost_usd)}</span>{" "}
              — estimate only; actual spend is reported per cell after the run.
            </>
          ) : (
            "No cost estimate was returned — spend is reported per cell after the run."
          )}
        </p>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={applying}
            className="rounded-card border border-line-strong bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface-muted disabled:opacity-60"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => onApply(drafts.map(toSpec), false)}
            disabled={applying || problems.size > 0}
            className="rounded-card border border-line-strong bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface-muted disabled:opacity-60"
          >
            Add only
          </button>
          <button
            type="button"
            onClick={() => onApply(drafts.map(toSpec), true)}
            disabled={applying || problems.size > 0}
            className="rounded-card bg-accent px-3 py-1.5 text-xs font-semibold text-white hover:bg-accent-strong disabled:opacity-60"
          >
            {applying ? "Adding…" : "Add and run"}
          </button>
        </div>
      </div>
    </div>
  );
}
