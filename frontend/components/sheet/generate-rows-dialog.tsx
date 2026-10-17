"use client";

import { X } from "lucide-react";
import { useState } from "react";

import { generateRows, type SheetResult } from "@/lib/api/sheets";
import type { GenerateRowsResponse } from "@/lib/schema/sheet";
import { formatUsd } from "@/lib/utils/cell-format";

/**
 * List-building dialog (P9a): the user describes companies to find and ONE
 * Exa `category="company"` search runs server-side; hits become pending
 * rows identical to pasted ones (domain only — a name is never guessed
 * from a hit title). Generated rows then verify their identity in a
 * background pass — no cell run.
 *
 * The estimate is LABELLED an estimate (D11) — the documented `/search`
 * upper bound — and the summary shows only provider-reported cost.
 */

// Mirrors ESTIMATED_SEARCH_COST_USD in backend/app/engine/budget.py — the
// documented /search upper bound for ONE company search.
const ESTIMATED_SEARCH_COST_USD = 0.015;

const MAX_RESULTS_OPTIONS = [1, 2, 3, 4, 5];

export function GenerateRowsDialog({
  sheetId,
  onClose,
  onGenerated,
}: {
  sheetId: string;
  onClose: () => void;
  onGenerated: () => void;
}) {
  const [query, setQuery] = useState("");
  const [maxResults, setMaxResults] = useState(5);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<GenerateRowsResponse | null>(null);

  async function handleGenerate() {
    if (!query.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    const result: SheetResult<GenerateRowsResponse> = await generateRows(sheetId, {
      query: query.trim(),
      max_results: maxResults,
    });
    setSubmitting(false);
    if (result.kind !== "ok") {
      setError(
        result.kind === "api_error"
          ? result.message
          : result.kind === "network"
            ? "Could not reach the sheets service. Check that the backend is running."
            : result.kind === "validation"
              ? "The backend rejected the request — check the query."
              : "The generate response could not be read.",
      );
      return;
    }
    setSummary(result.data);
    // New rows carry no cell events — the resnapshot lands them.
    onGenerated();
  }

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-ink-strong/30 p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="generate-rows-title"
        data-testid="generate-rows-dialog"
        className="w-full max-w-lg rounded-card border border-line bg-surface p-5 shadow-card"
      >
        <div className="flex items-start justify-between gap-2">
          <div>
            <h2 id="generate-rows-title" className="text-base font-semibold text-ink-strong">
              Find companies
            </h2>
            <p className="mt-1 text-xs text-ink-muted">
              One Exa company search turns your query into pending rows (at most 5 per
              call — run it again for more). New rows verify their identity
              automatically in the background — no cell run needed.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close find companies dialog"
            className="rounded p-1 text-ink-muted hover:bg-surface-muted hover:text-ink"
          >
            <X aria-hidden="true" className="h-4 w-4" />
          </button>
        </div>

        <form
          onSubmit={(event) => {
            event.preventDefault();
            void handleGenerate();
          }}
        >
          <label
            htmlFor="generate-rows-query"
            className="mt-3 block text-xs font-medium text-ink"
          >
            Companies to find
          </label>
          <input
            id="generate-rows-query"
            type="text"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            autoFocus
            placeholder="robotics suppliers in Germany"
            className="mt-1 w-full rounded-card border border-line-strong bg-surface px-3 py-2 text-xs text-ink placeholder:text-ink-muted"
          />

          <label
            htmlFor="generate-rows-max"
            className="mt-3 block text-xs font-medium text-ink"
          >
            Max results
          </label>
          <select
            id="generate-rows-max"
            value={maxResults}
            onChange={(event) => setMaxResults(Number(event.target.value))}
            className="mt-1 w-full rounded-card border border-line-strong bg-surface px-3 py-2 text-xs text-ink"
          >
            {MAX_RESULTS_OPTIONS.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>

          <p className="mt-3 text-xs text-ink-muted">
            Estimated cost{" "}
            <span className="tabular-nums text-ink">
              {formatUsd(ESTIMATED_SEARCH_COST_USD)}
            </span>{" "}
            — estimate only; the provider-reported cost is shown after the search.
          </p>

          {error !== null ? (
            <p role="alert" className="mt-2 text-xs font-medium text-danger-ink">
              {error}
            </p>
          ) : null}
          {summary !== null ? (
            <div role="status" className="mt-2 text-xs font-medium text-verified-ink">
              <p>
                Added {summary.created} {summary.created === 1 ? "row" : "rows"}
                {summary.skipped > 0
                  ? ` · ${summary.skipped} skipped (duplicates, unusable hits, or a full sheet)`
                  : ""}
                {summary.cost_usd !== null
                  ? ` · provider-reported cost ${formatUsd(summary.cost_usd)}`
                  : ""}
                .
              </p>
              {summary.mock ? (
                <p className="mt-1 text-inferred-ink">
                  Mock provider — synthetic results, not real companies.
                </p>
              ) : null}
            </div>
          ) : null}

          <div className="mt-4 flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-card border border-line-strong bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface-muted"
            >
              {summary !== null ? "Done" : "Cancel"}
            </button>
            <button
              type="submit"
              disabled={submitting || !query.trim() || summary !== null}
              className="rounded-card bg-accent px-3 py-1.5 text-xs font-semibold text-white hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-60"
            >
              {submitting ? "Searching…" : "Find companies"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
