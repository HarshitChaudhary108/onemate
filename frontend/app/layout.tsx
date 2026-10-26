import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Outmate — evidence-backed company sheets",
  description:
    "Paste a company list, describe the columns you need, and inspect evidence-backed cells: every value carries its provider source, and anything the run could not support stays explicitly unavailable.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full">
      <body className="flex min-h-full flex-col">{children}</body>
    </html>
  );
}
