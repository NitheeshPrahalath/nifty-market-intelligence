import { isMissing, titleCase } from "@/lib/format";
import type { ScoreBreakdown } from "@/lib/types";
import { cn } from "@/lib/utils";

const DIMENSIONS: (keyof ScoreBreakdown)[] = [
  "trend",
  "momentum",
  "relative_strength",
  "quality",
  "growth",
  "valuation",
  "risk",
  "liquidity",
  "horizon",
  "sector",
];

export function ScoreBars({
  scores,
  className,
}: {
  scores: ScoreBreakdown;
  className?: string;
}) {
  return (
    <div className={cn("space-y-2", className)}>
      {DIMENSIONS.map((key) => {
        const value = scores[key];
        const pct = isMissing(value) ? 0 : Math.max(0, Math.min(100, value as number));
        return (
          <div key={key} className="space-y-1">
            <div className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">
                {titleCase(key)}
              </span>
              <span className="tabular">
                {isMissing(value) ? "—" : (value as number).toFixed(1)}
              </span>
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary"
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
