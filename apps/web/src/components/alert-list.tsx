import Link from "next/link";

import { ToneBadge } from "@/components/tone-badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  formatDate,
  severityTone,
  titleCase,
} from "@/lib/format";
import type { AlertView } from "@/lib/types";

export function AlertList({ alerts }: { alerts: AlertView[] }) {
  return (
    <ul className="space-y-2">
      {alerts.map((alert) => (
        <li key={alert.id}>
          <Card>
            <CardContent className="flex flex-col gap-2 p-4 sm:flex-row sm:items-start sm:justify-between">
              <div className="space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <ToneBadge
                    label={titleCase(alert.severity)}
                    tone={severityTone(alert.severity)}
                  />
                  <span className="text-xs text-muted-foreground">
                    {titleCase(alert.notification_type)}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {formatDate(alert.as_of)}
                  </span>
                </div>
                <p className="text-sm font-medium">{alert.title}</p>
                <p className="text-sm text-muted-foreground">{alert.message}</p>
                {alert.reason ? (
                  <p className="text-xs text-muted-foreground">{alert.reason}</p>
                ) : null}
              </div>
              <div className="flex shrink-0 flex-col items-start gap-1 text-xs sm:items-end">
                {alert.symbol ? (
                  <Link
                    href={`/stocks/${encodeURIComponent(alert.symbol)}`}
                    className="font-medium hover:text-primary"
                  >
                    {alert.symbol}
                  </Link>
                ) : null}
                {alert.recommendation_id ? (
                  <Link
                    href={`/recommendations/${alert.recommendation_id}`}
                    className="text-muted-foreground hover:text-primary"
                  >
                    Recommendation #{alert.recommendation_id}
                  </Link>
                ) : null}
                {alert.strategy_code ? (
                  <span className="text-muted-foreground">
                    {titleCase(alert.strategy_code)}
                  </span>
                ) : null}
              </div>
            </CardContent>
          </Card>
        </li>
      ))}
    </ul>
  );
}
