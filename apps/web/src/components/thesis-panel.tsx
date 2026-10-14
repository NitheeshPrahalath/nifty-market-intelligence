import { ToneBadge } from "@/components/tone-badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  formatDate,
  formatPct,
  isMissing,
  signalTone,
  titleCase,
} from "@/lib/format";
import type { ThesisFactorView, ThesisView } from "@/lib/types";

function factorValue(factor: ThesisFactorView): string {
  if (!isMissing(factor.after_text)) return factor.after_text as string;
  if (!isMissing(factor.after)) return formatPct(factor.after, 1).replace("%", "");
  return "—";
}

function factorDelta(factor: ThesisFactorView): string | null {
  if (isMissing(factor.before) || isMissing(factor.after)) return null;
  const delta = (factor.after as number) - (factor.before as number);
  const sign = delta > 0 ? "+" : "";
  return `${sign}${delta.toFixed(1)}`;
}

export function ThesisPanel({ thesis }: { thesis: ThesisView[] }) {
  if (thesis.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Thesis changes</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          No scored thesis changes recorded for this recommendation yet.
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      {thesis.map((entry) => (
        <Card key={entry.as_of}>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <div>
              <CardTitle>Thesis · {formatDate(entry.as_of)}</CardTitle>
              <p className="text-xs text-muted-foreground">
                {entry.weakened_count} weakened · {entry.improved_count} improved
              </p>
            </div>
            <ToneBadge label={titleCase(entry.state)} tone={signalTone(entry.state)} />
          </CardHeader>
          <CardContent className="space-y-3">
            {entry.summary ? (
              <p className="text-sm text-muted-foreground">{entry.summary}</p>
            ) : null}
            <ul className="space-y-2">
              {entry.factors.map((factor) => {
                const delta = factorDelta(factor);
                return (
                  <li
                    key={factor.key}
                    className="flex items-center justify-between gap-3 border-b border-border/60 pb-2 last:border-0 last:pb-0"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm">{factor.label}</p>
                      <p className="text-xs text-muted-foreground">
                        {titleCase(factor.group)} · {titleCase(factor.direction)}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-3 text-sm">
                      <span className="tabular">{factorValue(factor)}</span>
                      {delta ? (
                        <ToneBadge
                          label={delta}
                          tone={delta.startsWith("-") ? "bearish" : "bullish"}
                        />
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
