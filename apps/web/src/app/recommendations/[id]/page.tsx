"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { ExplanationPanel } from "@/components/explanation-panel";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { StatCard } from "@/components/stat-card";
import { ThesisPanel } from "@/components/thesis-panel";
import { ToneBadge } from "@/components/tone-badge";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  formatDate,
  formatPrice,
  isMissing,
  signalTone,
  titleCase,
} from "@/lib/format";
import { useRecommendation } from "@/lib/queries";

function zone(low: number | null, high: number | null): string {
  if (isMissing(low) && isMissing(high)) return "—";
  if (isMissing(low)) return formatPrice(high);
  if (isMissing(high)) return formatPrice(low);
  return `${formatPrice(low)} – ${formatPrice(high)}`;
}

export default function RecommendationDetailPage() {
  const params = useParams<{ id: string }>();
  const id = Number(params.id);
  const { data, error, isLoading } = useRecommendation(id);

  if (isLoading) return <LoadingState rows={8} />;
  if (error) return <ErrorState error={error} />;
  if (!data) return <EmptyState title="Recommendation not found" />;

  const tone = signalTone(data.state);
  const latestThesis = data.thesis.at(-1);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-lg font-semibold">
              <Link
                href={`/stocks/${encodeURIComponent(data.symbol)}`}
                className="hover:text-primary"
              >
                {data.symbol}
              </Link>
            </h1>
            <ToneBadge label={titleCase(data.state)} tone={tone} />
            <Badge variant="outline">{titleCase(data.horizon)}</Badge>
            <Badge variant="default">v{data.latest_version}</Badge>
          </div>
          <p className="text-sm text-muted-foreground">
            {data.name} · {titleCase(data.strategy_code)} · risk{" "}
            {titleCase(data.risk_level)}
          </p>
        </div>
        <p className="text-xs text-muted-foreground">
          Active since {formatDate(data.active_since ?? data.first_as_of)} · updated{" "}
          {formatDate(data.last_as_of)}
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <StatCard label="Current price" value={formatPrice(data.current_price)} />
        <StatCard
          label="Confidence"
          value={isMissing(data.confidence) ? "—" : `${data.confidence}%`}
        />
        <StatCard label="Entry zone" value={zone(data.entry_low, data.entry_high)} />
        <StatCard
          label="Target zone"
          value={zone(data.target_low, data.target_high)}
        />
        <StatCard
          label="Invalidation"
          value={formatPrice(data.invalidation_price)}
        />
        <StatCard label="Exit reason" value={titleCase(data.exit_reason)} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Thesis</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm leading-relaxed">
            {latestThesis?.summary ??
              "No scored thesis summary is available for this recommendation yet."}
          </p>
          {data.created_reason ? (
            <p className="text-xs text-muted-foreground">
              Created because: {data.created_reason}
            </p>
          ) : null}
        </CardContent>
      </Card>

      <ExplanationPanel id={id} />

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Version history</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {data.versions.length === 0 ? (
              <EmptyState title="No versions" />
            ) : (
              <ol className="space-y-3">
                {data.versions.map((version) => (
                  <li
                    key={version.version}
                    className="border-l-2 border-border pl-3"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium">
                        v{version.version} · {titleCase(version.state)}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {formatDate(version.as_of)}
                      </span>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {titleCase(version.signal_type)} · confidence{" "}
                      {isMissing(version.confidence) ? "—" : `${version.confidence}%`}
                    </p>
                    {version.change_summary ? (
                      <p className="mt-1 text-xs text-muted-foreground">
                        {version.change_summary}
                      </p>
                    ) : null}
                    {version.reasons.length > 0 ? (
                      <ul className="mt-1 list-inside list-disc text-xs text-muted-foreground">
                        {version.reasons.map((reason, index) => (
                          <li key={index}>{reason}</li>
                        ))}
                      </ul>
                    ) : null}
                  </li>
                ))}
              </ol>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Events</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {data.events.length === 0 ? (
              <EmptyState title="No events" />
            ) : (
              <ol className="space-y-3">
                {data.events.map((event, index) => (
                  <li key={index} className="border-l-2 border-border pl-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium">{event.title}</span>
                      <Badge variant="default">
                        {titleCase(event.event_type)}
                      </Badge>
                      <span className="text-xs text-muted-foreground">
                        {formatDate(event.as_of)}
                      </span>
                    </div>
                    <p className="text-xs text-muted-foreground">{event.message}</p>
                    {event.previous_state || event.new_state ? (
                      <p className="mt-1 text-xs text-muted-foreground">
                        {titleCase(event.previous_state)} →{" "}
                        {titleCase(event.new_state)}
                        {event.price !== null
                          ? ` · ${formatPrice(event.price)}`
                          : ""}
                      </p>
                    ) : null}
                  </li>
                ))}
              </ol>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="space-y-3">
        <h2 className="text-sm font-semibold">Thesis evolution</h2>
        <ThesisPanel thesis={data.thesis} />
      </div>
    </div>
  );
}
