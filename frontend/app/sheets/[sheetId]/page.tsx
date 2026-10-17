import { Suspense } from "react";

import { SheetWorkspace } from "@/components/sheet/sheet-workspace";

/**
 * Sheet workspace route — thin wrapper (P6/P7).
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
