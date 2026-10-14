import { Sparkles } from "lucide-react";

import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDateTime, titleCase } from "@/lib/format";
import { useExplanation } from "@/lib/queries";
import type { ExplanationBulletView } from "@/lib/types";

/** Statement plus the fact keys it was written from, shown as small chips. */
function Bullet({ bullet }: { bullet: ExplanationBulletView }) {
  return (
    <li className="space-y-1">
      <p className="text-sm leading-relaxed">{bullet.text}</p>
      {bullet.citations.length > 0 ? (
        <div className="flex flex-wrap gap-1">
          {bullet.citations.map((key) => (
            <span
              key={key}
              className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground"
            >
              {key}
            </span>
          ))}
        </div>
      ) : null}
    </li>
  );
}

export function ExplanationPanel({
  id,
  kind,
}: {
  id: number;
  kind?: string;
}) {
  const { data, error, isLoading } = useExplanation(id, kind);

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-primary" />
          AI explanation
        </CardTitle>
        {data ? (
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline">{titleCase(data.kind)}</Badge>
            <Badge variant={data.valid ? "default" : "outline"}>
              {data.valid ? "cited" : "unverified"}
            </Badge>
            <span className="text-xs text-muted-foreground">
              {data.provider}/{data.model}
            </span>
          </div>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading ? (
          <LoadingState rows={4} />
        ) : error ? (
          <ErrorState error={error} />
        ) : !data ? (
          <EmptyState
            title="No explanation yet"
            description="Generate one with `nmi explain --index NIFTY_50`."
          />
        ) : (
          <>
            <p className="text-sm font-medium leading-relaxed">{data.headline}</p>
            <p className="text-sm text-muted-foreground">{data.summary}</p>

            {data.fallback_reason ? (
              <p className="rounded border border-warning/40 bg-warning/10 p-2 text-xs text-muted-foreground">
                Fallback: {data.fallback_reason}
              </p>
            ) : null}

            {data.bullets.length > 0 ? (
              <ul className="space-y-3">
                {data.bullets.map((bullet, index) => (
                  <Bullet key={index} bullet={bullet} />
                ))}
              </ul>
            ) : null}

            {data.risks.length > 0 ? (
              <div className="space-y-2">
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Risks
                </p>
                <ul className="space-y-3">
                  {data.risks.map((risk, index) => (
                    <Bullet key={index} bullet={risk} />
                  ))}
                </ul>
              </div>
            ) : null}

            {data.citations.length > 0 ? (
              <details className="text-xs text-muted-foreground">
                <summary className="cursor-pointer select-none">
                  {data.citations.length} cited fact
                  {data.citations.length === 1 ? "" : "s"}
                </summary>
                <dl className="mt-2 grid gap-1 sm:grid-cols-2">
                  {data.citations.map((citation) => (
                    <div key={citation.key} className="flex justify-between gap-2">
                      <dt className="truncate" title={citation.source}>
                        {citation.label}
                      </dt>
                      <dd className="tabular">{citation.value}</dd>
                    </div>
                  ))}
                </dl>
              </details>
            ) : null}

            <p className="text-[11px] text-muted-foreground">
              Generated {formatDateTime(data.generated_at)} · prompt{" "}
              {data.prompt_version} · facts {data.source_hash.slice(0, 8)}
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}
