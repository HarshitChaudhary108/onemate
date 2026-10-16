/**
 * Form-level validation for the research request. Mirrors the backend's
 * limits (`backend/app/schemas/research.py`); the backend remains
 * authoritative — this is only for immediate user feedback.
 */

import { z } from "zod";

export const COMPANY_NAME_MAX = 200;
export const DOMAIN_MAX = 253;
export const OBJECTIVE_MAX = 1000;

export const researchRequestSchema = z
  .object({
    company_name: z.string().trim().max(COMPANY_NAME_MAX).optional(),
    domain: z.string().trim().max(DOMAIN_MAX).optional(),
    research_objective: z.string().trim().max(OBJECTIVE_MAX).optional(),
  })
  .check((ctx) => {
    const { company_name, domain } = ctx.value;
    if (!company_name && !domain) {
      ctx.issues.push({
        code: "custom",
        message: "Enter a company name or a domain.",
        input: ctx.value,
      });
    }
  });

export type ResearchRequest = z.infer<typeof researchRequestSchema>;
