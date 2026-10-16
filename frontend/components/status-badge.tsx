import { CircleSlash, Landmark, Lightbulb, ShieldCheck, TriangleAlert } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import type { Source, Status } from "@/lib/schema/report";
import { hasInspectableSource } from "@/lib/utils/format";

/**
 * Status badge: text label + icon, never colour alone.
 *
 * Session decision (plan M4 task 5): when a field claims `verified` but no
 * attached source carries a URL, the badge reads "Source missing" with
 * unavailable styling — the word "Verified" is never shown.
 */

type Tone = "verified" | "reported" | "inferred" | "unavailable";

const TONE_CLASSES: Record<Tone, string> = {
  verified: "border-verified-line bg-verified-bg text-verified-ink",
  reported: "border-reported-line bg-reported-bg text-reported-ink",
  inferred: "border-inferred-line bg-inferred-bg text-inferred-ink",
  unavailable: "border-unavailable-line bg-unavailable-bg text-unavailable-ink",
};

const STATUS_BADGES: Record<Status, { label: string; tone: Tone; icon: LucideIcon }> = {
  verified: { label: "Verified", tone: "verified", icon: ShieldCheck },
  "provider-reported": { label: "Provider-reported", tone: "reported", icon: Landmark },
  inferred: { label: "Inferred", tone: "inferred", icon: Lightbulb },
  unavailable: { label: "Unavailable", tone: "unavailable", icon: CircleSlash },
};

function Badge({
  label,
  tone,
  icon: Icon,
  title,
  className,
}: {
  label: string;
  tone: Tone;
  icon: LucideIcon;
  title?: string;
  className?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${TONE_CLASSES[tone]} ${className ?? ""}`}
    >
      <Icon aria-hidden="true" className="h-3.5 w-3.5" />
      {label}
    </span>
  );
}

export function StatusBadge({
  status,
  sources,
  className,
}: {
  status: Status;
  sources?: Source[];
  className?: string;
}) {
  if (status === "verified" && !hasInspectableSource(sources)) {
    return (
      <Badge
        label="Source missing"
        tone="unavailable"
        icon={TriangleAlert}
        title="This value is marked verified, but no attached source has an inspectable URL."
        className={className}
      />
    );
  }
  const badge = STATUS_BADGES[status];
  return (
    <Badge
      label={badge.label}
      tone={badge.tone}
      icon={badge.icon}
      className={className}
    />
  );
}
