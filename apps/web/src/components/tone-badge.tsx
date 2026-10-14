import { Badge } from "@/components/ui/badge";
import type { Tone } from "@/lib/format";
import { cn } from "@/lib/utils";

const TONE_VARIANT: Record<Tone, "bullish" | "bearish" | "warning" | "default"> =
  {
    bullish: "bullish",
    bearish: "bearish",
    warning: "warning",
    neutral: "default",
  };

export function ToneBadge({
  label,
  tone,
  className,
}: {
  label: string;
  tone: Tone;
  className?: string;
}) {
  return (
    <Badge variant={TONE_VARIANT[tone]} className={className}>
      {label}
    </Badge>
  );
}
