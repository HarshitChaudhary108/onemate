/**
 * SSE wrapper for the sheet event stream (plan §4.5 / P6).
 *
 * The backend (`GET /api/sheets/{id}/events`, sse-starlette) batches hub
 * events into one SSE frame about every 50 ms; each frame's `data` is a
 * JSON array of event objects (`{"type": "cell" | "row" | "run", ...}`).
 * There is no replay: the client protocol is subscribe FIRST, then fetch
 * the snapshot, then merge per cell by revision (last write wins).
 *
 * This module is the transport half: it parses frames leniently (a
 * malformed event is dropped, never thrown) and reports connection state
 * through handlers. The ordering / merge policy lives in the sheet store;
 * `onOpen` fires on every (re)connection so the store re-snapshots after
 * a drop — native EventSource retry supplies the reconnect itself.
 */

import { z } from "zod";

import { sheetEventsUrl } from "@/lib/api/sheets";
import { CellStatusSchema, IdentityStatusSchema } from "@/lib/schema/sheet";

// --- wire shapes (backend/app/engine/runner.py publish sites) ----------------

/** Queued/running/done/error/blocked transitions. Note: carries NO result —
 *  a terminal event means "fetch the cell detail", the value is not inline. */
export const CellEventSchema = z.strictObject({
  type: z.literal("cell"),
  row_id: z.string(),
  column_id: z.string(),
  status: CellStatusSchema,
  revision: z.number().int(),
  stale: z.boolean(),
});
export type CellEvent = z.infer<typeof CellEventSchema>;

/** Identity-resolution progress for one row. */
export const RowEventSchema = z.strictObject({
  type: z.literal("row"),
  row_id: z.string(),
  identity_status: IdentityStatusSchema,
  resolved_name: z.string().nullable(),
  resolved_domain: z.string().nullable(),
});
export type RowEvent = z.infer<typeof RowEventSchema>;

/** Run lifecycle. `reason` carries the stop reason (e.g. a provider error
 *  code or "cost_cap") when the run stopped early. */
export const RunEventSchema = z.strictObject({
  type: z.literal("run"),
  run_id: z.string(),
  status: z.enum(["started", "stopped", "finished"]),
  reason: z.string().nullable(),
});
export type RunEvent = z.infer<typeof RunEventSchema>;

export const SheetEventSchema = z.discriminatedUnion("type", [
  CellEventSchema,
  RowEventSchema,
  RunEventSchema,
]);
export type SheetEvent = z.infer<typeof SheetEventSchema>;

/** Parse one SSE frame: `data` is a JSON array of event objects.
 *  Returns only the events that match the wire schema — a malformed frame
 *  or element is dropped silently, never thrown. */
export function parseEventFrame(data: unknown): SheetEvent[] {
  if (typeof data !== "string") return [];
  let raw: unknown;
  try {
    raw = JSON.parse(data);
  } catch {
    return [];
  }
  if (!Array.isArray(raw)) return [];
  const events: SheetEvent[] = [];
  for (const item of raw) {
    const parsed = SheetEventSchema.safeParse(item);
    if (parsed.success) events.push(parsed.data);
  }
  return events;
}

// --- subscription -------------------------------------------------------------

/** The slice of the DOM EventSource the wrapper needs — injectable for
 *  tests (jsdom has no EventSource). */
export interface EventSourceLike {
  addEventListener(
    type: "open" | "message" | "error",
    listener: (event: Event) => void,
  ): void;
  close(): void;
}

export type EventSourceFactory = (url: string) => EventSourceLike;

function defaultFactory(url: string): EventSourceLike {
  return new EventSource(url);
}

export type SheetEventsHandlers = {
  /** Fires on the initial open AND on every reconnect — the caller should
   *  (re)fetch the snapshot each time, since events may have been lost
   *  while the connection was down and there is no replay. */
  onOpen(): void;
  /** One batch of validated events from a single SSE frame. */
  onEvents(events: SheetEvent[]): void;
  /** The connection dropped (or failed to open); EventSource keeps
   *  retrying on its own — the next `onOpen` means we are back. */
  onDrop(): void;
};

export type SheetSubscription = { close(): void };

/** Subscribe to a sheet's event stream. Subscribe FIRST, then fetch the
 *  snapshot on `onOpen`, then merge — events arriving before the snapshot
 *  resolves must be buffered by the caller (the store does this). */
export function subscribeSheetEvents(
  sheetId: string,
  handlers: SheetEventsHandlers,
  createSource: EventSourceFactory = defaultFactory,
): SheetSubscription {
  const source = createSource(sheetEventsUrl(sheetId));
  source.addEventListener("open", () => handlers.onOpen());
  source.addEventListener("message", (event) => {
    const events = parseEventFrame((event as MessageEvent).data);
    if (events.length > 0) handlers.onEvents(events);
  });
  source.addEventListener("error", () => handlers.onDrop());
  return { close: () => source.close() };
}
