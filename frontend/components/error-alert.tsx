"use client";

import { CircleAlert } from "lucide-react";

/**
 * Accessible error state for api_error / network / invalid_response: a
 * `role="alert"` block with the safe message, the HTTP status when there was
 * one, the stable code, and the request ID when the backend supplied it.
 */
export function ErrorAlert({
  status,
  code,
  message,
  requestId,
}: {
  status: number | null;
  code: string;
  message: string;
  requestId: string | null;
}) {
  return (
    <div
      role="alert"
      data-testid="error-alert"
      className="rounded-card border border-danger-line bg-danger-bg p-4 text-sm text-danger-ink"
    >
      <p className="flex items-center gap-2 font-semibold">
        <CircleAlert aria-hidden="true" className="h-4 w-4" />
        Research request failed
      </p>
      <p className="mt-1">{message}</p>
      <p className="mt-2 font-mono text-xs">
        {status !== null ? `HTTP ${status} · ` : null}
        code: {code}
        {requestId ? ` · request ${requestId}` : null}
      </p>
    </div>
  );
}
