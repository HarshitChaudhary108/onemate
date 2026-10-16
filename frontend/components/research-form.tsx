"use client";

import { Search } from "lucide-react";
import { useId, useState } from "react";
import { z } from "zod";

import type { ResearchInput, Result } from "@/lib/api/client";
import { COMPANY_NAME_MAX, DOMAIN_MAX, OBJECTIVE_MAX } from "@/lib/schema/request";

/**
 * Research form (plan M4 task 2).
 *
 * One combined "Company name or website" input plus an optional objective.
 * A trimmed input with no whitespace that starts with http(s):// or looks like
 * a bare host ("stripe.com", "www.stripe.com/pricing") is sent as `domain`;
 * everything else is sent as `company_name`. Zod validation produces
 * field-level messages linked with `aria-describedby`; 422 field errors from
 * the backend are mapped onto the same controls (company_name/domain both map
 * to the combined input). Values are never cleared on error.
 */

const DOMAIN_PATTERN = /^[^\s/]+\.[a-z]{2,}([/?#].*)?$/i;
const EXAMPLE_DOMAIN = "stripe.com";

export function classifyInput(raw: string): "domain" | "company_name" {
  const value = raw.trim();
  if (value.startsWith("http://") || value.startsWith("https://")) return "domain";
  return DOMAIN_PATTERN.test(value) ? "domain" : "company_name";
}

const companyField = z.string().check((ctx) => {
  const trimmed = ctx.value.trim();
  if (!trimmed) {
    ctx.issues.push({
      code: "custom",
      message: "Enter a company name or a website.",
      input: ctx.value,
    });
    return;
  }
  const limit = classifyInput(trimmed) === "domain" ? DOMAIN_MAX : COMPANY_NAME_MAX;
  if (trimmed.length > limit) {
    ctx.issues.push({
      code: "custom",
      message: `Keep this under ${limit} characters.`,
      input: ctx.value,
    });
  }
});

const objectiveField = z.string().check((ctx) => {
  if (ctx.value.trim().length > OBJECTIVE_MAX) {
    ctx.issues.push({
      code: "custom",
      message: `Keep the objective under ${OBJECTIVE_MAX} characters.`,
      input: ctx.value,
    });
  }
});

const formSchema = z.object({ company: companyField, objective: objectiveField });

type FieldErrors = { company?: string; objective?: string };

function issuesToFieldErrors(error: z.ZodError): FieldErrors {
  const errors: FieldErrors = {};
  for (const issue of error.issues) {
    const key = issue.path[0];
    if (key === "company" && !errors.company) errors.company = issue.message;
    if (key === "objective" && !errors.objective) errors.objective = issue.message;
  }
  return errors;
}

/** Map a 422 field map onto the two controls; company_name/domain share one. */
function serverErrorsToFieldErrors(fields: Record<string, string>): FieldErrors {
  return {
    company: fields.company_name ?? fields.domain,
    objective: fields.research_objective,
  };
}

export function ResearchForm({
  pending,
  onSubmit,
}: {
  pending: boolean;
  onSubmit: (input: ResearchInput) => Promise<Result>;
}) {
  const inputId = useId();
  const objectiveId = useId();
  const companyHintId = `${inputId}-hint`;
  const companyErrorId = `${inputId}-error`;
  const objectiveHintId = `${objectiveId}-hint`;
  const objectiveErrorId = `${objectiveId}-error`;

  const [company, setCompany] = useState("");
  const [objective, setObjective] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [serverErrors, setServerErrors] = useState<FieldErrors>({});

  const companyError = fieldErrors.company ?? serverErrors.company;
  const objectiveError = fieldErrors.objective ?? serverErrors.objective;

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setServerErrors({});

    const parsed = formSchema.safeParse({ company, objective });
    if (!parsed.success) {
      setFieldErrors(issuesToFieldErrors(parsed.error));
      return; // no request is made for invalid input
    }
    setFieldErrors({});

    const trimmed = company.trim();
    const input: ResearchInput =
      classifyInput(trimmed) === "domain"
        ? { domain: trimmed }
        : { company_name: trimmed };
    const trimmedObjective = objective.trim();
    if (trimmedObjective) input.research_objective = trimmedObjective;

    const result = await onSubmit(input);
    if (result.kind === "validation") {
      setServerErrors(serverErrorsToFieldErrors(result.fields));
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      noValidate
      aria-busy={pending}
      data-testid="research-form"
      className="rounded-card border border-line bg-surface p-5 shadow-card sm:p-6"
    >
      <div className="grid gap-4 lg:grid-cols-2">
        <div>
          <label htmlFor={inputId} className="block text-sm font-medium text-ink-strong">
            Company name or website
          </label>
          <input
            id={inputId}
            name="company"
            type="text"
            value={company}
            onChange={(event) => setCompany(event.target.value)}
            placeholder="Example Robotics Ltd or example-robotics.test"
            aria-invalid={companyError ? true : undefined}
            aria-describedby={
              companyError ? `${companyHintId} ${companyErrorId}` : companyHintId
            }
            className={`mt-1 w-full rounded-card border bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-muted ${
              companyError ? "border-danger-line" : "border-line-strong"
            }`}
          />
          <p id={companyHintId} className="mt-1 text-xs text-ink-muted">
            A domain or a company name — the app detects which one it is.
          </p>
          {companyError ? (
            <p id={companyErrorId} className="mt-1 text-xs font-medium text-danger-ink">
              {companyError}
            </p>
          ) : null}
        </div>

        <div>
          <label htmlFor={objectiveId} className="block text-sm font-medium text-ink-strong">
            Research objective (optional)
          </label>
          <textarea
            id={objectiveId}
            name="objective"
            value={objective}
            onChange={(event) => setObjective(event.target.value)}
            rows={3}
            placeholder="e.g. Evaluate whether this company could be a good prospect for a B2B GTM service"
            aria-invalid={objectiveError ? true : undefined}
            aria-describedby={
              objectiveError ? `${objectiveHintId} ${objectiveErrorId}` : objectiveHintId
            }
            className={`mt-1 w-full rounded-card border bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-muted ${
              objectiveError ? "border-danger-line" : "border-line-strong"
            }`}
          />
          <p id={objectiveHintId} className="mt-1 text-xs text-ink-muted">
            Used only to shape the inferred opportunity hypothesis.
          </p>
          {objectiveError ? (
            <p id={objectiveErrorId} className="mt-1 text-xs font-medium text-danger-ink">
              {objectiveError}
            </p>
          ) : null}
        </div>
      </div>

      <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-ink-muted">
          Try an example:{" "}
          <button
            type="button"
            aria-label={`Use example ${EXAMPLE_DOMAIN}`}
            onClick={() => {
              setCompany(EXAMPLE_DOMAIN);
              setFieldErrors({});
              setServerErrors({});
            }}
            className="rounded-full border border-accent-line bg-accent-soft px-2.5 py-1 font-medium text-accent-strong hover:bg-accent-soft/70"
          >
            {EXAMPLE_DOMAIN}
          </button>
        </p>
        <button
          type="submit"
          disabled={pending}
          className="inline-flex items-center gap-2 rounded-card bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-60"
        >
          <Search aria-hidden="true" className="h-4 w-4" />
          {pending ? "Researching…" : "Research company"}
        </button>
      </div>
    </form>
  );
}
