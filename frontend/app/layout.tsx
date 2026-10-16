import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Outmate — evidence-backed company research",
  description:
    "Submit a company name or website and inspect an evidence-backed research report: overview, growth signals, decision-makers, and an inferred opportunity hypothesis.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full">
      <body className="flex min-h-full flex-col">{children}</body>
    </html>
  );
}
