import Link from "next/link";
import { Suspense } from "react";

/**
 * Sheet workspace route — thin wrapper (P6). The grid lands in P7; for
 * now this renders a placeholder that proves the route resolves.
 *
 * U5 (cacheComponents): without `generateStaticParams`, `params` is
 * runtime data — unknown while prerendering — so any access must sit
 * inside a `<Suspense>` boundary or the build fails. The documented
 * pattern is a synchronous page that passes the `params` promise into
 * the boundary, exactly as below
 * (node_modules/next/dist/docs/01-app/03-api-reference/03-file-conventions/dynamic-routes.md,
 * "With Cache Components").
 */
export default function SheetPage({ params }: PageProps<"/sheets/[sheetId]">) {
  return (
    <Suspense
      fallback={
        <p role="status" className="px-4 py-8 text-sm text-ink-muted sm:px-6 lg:px-8">
          Loading sheet…
        </p>
      }
    >
      {params.then(({ sheetId }) => <SheetWorkspace sheetId={sheetId} />)}
    </Suspense>
  );
}

/** P6 placeholder — replaced by components/sheet/sheet-workspace in P7. */
function SheetWorkspace({ sheetId }: { sheetId: string }) {
  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
      <p className="text-sm text-ink-muted">
        <Link href="/" className="text-accent underline underline-offset-2">
          ← Sheets
        </Link>
      </p>
      <h1 className="mt-4 text-2xl font-semibold tracking-tight text-ink-strong">
        Sheet workspace
      </h1>
      <p className="mt-2 font-mono text-xs text-ink-muted">{sheetId}</p>
      <p className="mt-4 max-w-2xl text-sm text-ink-muted">
        The grid workspace is not built yet — this route only proves the sheet id
        resolves. The interactive grid lands in the next phase.
      </p>
    </div>
  );
}
