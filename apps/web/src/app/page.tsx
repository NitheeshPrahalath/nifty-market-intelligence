"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MoversTable } from "@/components/movers-table";
import { RegimeCard } from "@/components/regime-card";
import { SectorTable } from "@/components/sector-table";
import { StatCard } from "@/components/stat-card";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { ToneBadge } from "@/components/tone-badge";
import { formatInt, signalTone, titleCase } from "@/lib/format";
import { useMarketOverview } from "@/lib/queries";
import { useUiStore } from "@/store/ui";

export default function MarketOverviewPage() {
  const indexCode = useUiStore((state) => state.indexCode);
  const { data, error, isLoading } = useMarketOverview(indexCode);

  if (isLoading) return <LoadingState rows={8} />;
  if (error) return <ErrorState error={error} />;
  if (!data) return <EmptyState title="No market data" />;

  const signalEntries = Object.entries(data.signal_counts).sort(
    (a, b) => b[1] - a[1],
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold">{data.index.name}</h1>
          <p className="text-sm text-muted-foreground">
            {formatInt(data.member_count)} members · as of {data.as_of}
          </p>
        </div>
      </div>

      {data.regime ? (
        <RegimeCard regime={data.regime} />
      ) : (
        <EmptyState
          title="No regime snapshot"
          description="Run the analysis pipeline to populate the market regime."
        />
      )}

      <Card>
        <CardHeader>
          <CardTitle>Signal activity</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          {signalEntries.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No active signals on this date.
            </p>
          ) : (
            signalEntries.map(([state, count]) => (
              <ToneBadge
                key={state}
                label={`${titleCase(state)} · ${count}`}
                tone={signalTone(state)}
              />
            ))
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Sector performance</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {data.sectors.length === 0 ? (
            <EmptyState title="No sector data" className="m-4" />
          ) : (
            <SectorTable sectors={data.sectors} />
          )}
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardContent className="p-4">
            <MoversTable
              title="Leaders"
              movers={data.leaders}
              metric="change_1m"
            />
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <MoversTable
              title="Laggards"
              movers={data.laggards}
              metric="change_1m"
            />
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="Indices tracked" value={formatInt(data.member_count)} />
        <StatCard label="Sectors" value={formatInt(data.sectors.length)} />
        <StatCard label="Leaders" value={formatInt(data.leaders.length)} />
        <StatCard label="Laggards" value={formatInt(data.laggards.length)} />
      </div>
    </div>
  );
}
