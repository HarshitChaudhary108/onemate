"use client";

import { LoaderCircle, Sparkles } from "lucide-react";
import {
  useCallback,
  useId,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type Ref,
} from "react";

import { PlanCard } from "@/components/sheet/plan-card";
import type { SheetResult } from "@/lib/api/sheets";
import type { ColumnSpecInput, PlanResult } from "@/lib/schema/sheet";
import { useSheetStore } from "@/lib/store/sheet-store";

/**
 * Docked prompt bar (P7 / plan §4.6): a textarea with `@` column
 * autocomplete that inserts `{{Column}}`, suggestion chips, a planning
 * spinner with NO fake stages, and the plan card once the planner answers.
 */

const SUGGESTIONS = [
  "Industry for each company",
  "Latest funding or headcount news",
  "One-line description of what {{Company}} sells",
];

type Mention = {
  /** Index of the "@" character in the textarea value. */
  start: number;
  /** Text typed after "@". */
  query: string;
};

/** The active "@mention" at the caret, if any: "@" at the start of the
 *  value or right after whitespace, followed by non-space text without
 *  a closing "}}". */
export function mentionAt(value: string, caret: number): Mention | null {
  const before = value.slice(0, caret);
  const at = before.lastIndexOf("@");
  if (at < 0) return null;
  if (at > 0 && !/\s/.test(before[at - 1])) return null;
  const query = before.slice(at + 1);
  if (/\s|\{|\}/.test(query)) return null;
  return { start: at, query };
}

export type PromptBarApi = {
  /** Focus the prompt textarea — the Ctrl/⌘+K target. */
  focus(): void;
};

export function PromptBar({
  planning,
  plan,
  planError,
  onPlan,
  onApply,
  onDismissPlan,
  applying,
  apiRef,
}: {
  planning: boolean;
  plan: PlanResult | null;
  planError: string | null;
  onPlan: (prompt: string) => Promise<SheetResult<PlanResult>>;
  onApply: (columns: ColumnSpecInput[], run: boolean) => void;
  onDismissPlan: () => void;
  applying?: boolean;
  apiRef?: Ref<PromptBarApi>;
}) {
  const columnOrder = useSheetStore((state) => state.columnOrder);
  const columnsById = useSheetStore((state) => state.columnsById);
  const columnNames = useMemo(
    () => columnOrder.map((id) => columnsById[id]?.name).filter((n): n is string => !!n),
    [columnOrder, columnsById],
  );
  const existingNames = useMemo(() => columnNames, [columnNames]);

  const [value, setValue] = useState("");
  const [mention, setMention] = useState<Mention | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const listboxId = useId();

  useImperativeHandle(
    apiRef,
    () => ({
      focus() {
        textareaRef.current?.focus();
      },
    }),
    [],
  );

  const options = useMemo(() => {
    if (mention === null) return [];
    const query = mention.query.toLowerCase();
    return columnNames.filter((name) => name.toLowerCase().includes(query)).slice(0, 8);
  }, [mention, columnNames]);

  const refreshMention = useCallback((nextValue: string, caret: number) => {
    setMention(mentionAt(nextValue, caret));
    setActiveIndex(0);
  }, []);

  const insertMention = useCallback(
    (name: string) => {
      if (mention === null) return;
      const caret = textareaRef.current?.selectionStart ?? value.length;
      const insertion = `{{${name}}}`;
      const next = value.slice(0, mention.start) + insertion + value.slice(caret);
      setValue(next);
      setMention(null);
      const caretAt = mention.start + insertion.length;
      requestAnimationFrame(() => {
        textareaRef.current?.focus();
        textareaRef.current?.setSelectionRange(caretAt, caretAt);
      });
    },
    [mention, value],
  );

  const submit = useCallback(async () => {
    const prompt = value.trim();
    if (!prompt || planning) return;
    setSubmitError(null);
    const result = await onPlan(prompt);
    if (result.kind === "ok") {
      setValue("");
      setMention(null);
      return;
    }
    setSubmitError(
      result.kind === "api_error"
        ? result.message
        : result.kind === "network"
          ? "Could not reach the sheets service. Check that the backend is running."
          : "The planner returned a response this app could not read.",
    );
  }, [value, planning, onPlan]);

  const handleKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
      if (mention !== null && options.length > 0) {
        if (event.key === "ArrowDown") {
          event.preventDefault();
          setActiveIndex((index) => (index + 1) % options.length);
          return;
        }
        if (event.key === "ArrowUp") {
          event.preventDefault();
          setActiveIndex((index) => (index - 1 + options.length) % options.length);
          return;
        }
        if (event.key === "Enter" || event.key === "Tab") {
          event.preventDefault();
          insertMention(options[activeIndex] ?? options[0]);
          return;
        }
        if (event.key === "Escape") {
          event.preventDefault();
          setMention(null);
          return;
        }
      }
      if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        void submit();
      }
    },
    [mention, options, activeIndex, insertMention, submit],
  );

  const mentionOpen = mention !== null && options.length > 0;

  return (
    <div className="border-t border-line bg-surface px-4 py-3" data-testid="prompt-bar">
      {plan !== null ? (
        <div className="mb-3">
          <PlanCard
            plan={plan}
            existingNames={existingNames}
            applying={applying}
            onApply={onApply}
            onCancel={onDismissPlan}
          />
        </div>
      ) : null}

      {planError !== null || submitError !== null ? (
        <p role="alert" className="mb-2 text-xs text-danger-ink">
          {planError ?? submitError}
        </p>
      ) : null}

      <div className="flex items-end gap-3">
        <div className="relative flex-1">
          <label htmlFor="sheet-prompt-input" className="sr-only">
            Describe the column to add
          </label>
          <textarea
            id="sheet-prompt-input"
            ref={textareaRef}
            value={value}
            onChange={(event) => {
              setValue(event.target.value);
              refreshMention(event.target.value, event.target.selectionStart);
            }}
            onSelect={(event) =>
              refreshMention(
                event.currentTarget.value,
                event.currentTarget.selectionStart,
              )
            }
            onKeyDown={handleKeyDown}
            rows={2}
            placeholder="Describe a column — e.g. “each company's latest funding round”. Type @ to reference a column."
            aria-controls={mentionOpen ? listboxId : undefined}
            aria-autocomplete="list"
            className="w-full resize-none rounded-card border border-line-strong bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-muted"
          />
          {mentionOpen ? (
            <ul
              id={listboxId}
              role="listbox"
              aria-label="Column references"
              className="absolute bottom-full left-0 mb-1 max-h-48 w-64 overflow-auto rounded-card border border-line bg-surface py-1 shadow-card"
            >
              {options.map((name, index) => (
                <li
                  key={name}
                  role="option"
                  aria-selected={index === activeIndex}
                  onMouseDown={(event) => {
                    event.preventDefault(); // keep textarea focus
                    insertMention(name);
                  }}
                  className={`cursor-pointer px-3 py-1.5 text-sm ${
                    index === activeIndex ? "bg-accent-soft text-ink-strong" : "text-ink"
                  }`}
                >
                  <span className="font-mono text-xs text-ink-muted">{"{{"}</span>
                  {name}
                  <span className="font-mono text-xs text-ink-muted">{"}}"}</span>
                </li>
              ))}
            </ul>
          ) : null}
        </div>

        <button
          type="button"
          onClick={() => void submit()}
          disabled={planning || !value.trim()}
          className="inline-flex shrink-0 items-center gap-2 rounded-card bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-60"
        >
          {planning ? (
            <>
              <LoaderCircle aria-hidden="true" className="h-4 w-4 animate-spin" />
              Planning…
            </>
          ) : (
            "Plan"
          )}
        </button>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-1 text-xs text-ink-muted">
          <Sparkles aria-hidden="true" className="h-3 w-3" />
          Try:
        </span>
        {SUGGESTIONS.map((suggestion) => (
          <button
            key={suggestion}
            type="button"
            onClick={() => setValue(suggestion)}
            className="rounded-full border border-accent-line bg-accent-soft px-2.5 py-1 text-xs font-medium text-accent-strong hover:bg-accent-soft/70"
          >
            {suggestion}
          </button>
        ))}
        <span className="ml-auto text-[0.65rem] text-ink-muted">
          Ctrl/⌘+Enter to plan · Ctrl/⌘+K focuses this box
        </span>
      </div>
    </div>
  );
}
