"use client";

import { useMemo, useState } from "react";
import { useParams } from "next/navigation";

import { PriceChart } from "@/components/price-chart";
import { ScoreBars } from "@/components/score-bars";
import { SignalCard } from "@/components/signal-card";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { StatCard } from "@/components/stat-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  formatCompact,
  formatInt,
  formatNumber,
  formatPct,
  formatPrice,
  formatSignedPct,
  isMissing,
  signedTone,
  signalTone,
  titleCase,
} from "@/lib/format";
import { usePrices, useStockDetail } from "@/lib/queries";
import type { SignalView } from "@/lib/types";

const RANGES = [
  { label: "3M", days: 90 },
  { label: "6M", days: 182 },
  { label: "1Y", days: 365 },
  { label: "3Y", days: 1095 },
] as const;

function isoDaysAgo(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return date.toISOString().slice(0, 10);
}

export default function StockDetailPage() {
  const params = useParams<{ symbol: string }>();
  const symbol = decodeURIComponent(params.symbol ?? "");
  const [rangeDays, setRangeDays] = useState<number>(365);

  const detailQuery = useStockDetail(symbol);
  const pricesQuery = usePrices(symbol, {
    start: isoDaysAgo(rangeDays),
    page_size: 1000,
  });

  const bars = useMemo(
    () =>
      [...(pricesQuery.data?.items ?? [])].sort((a, b) =>
        a.trade_date.localeCompare(b.trade_date),
      ),
    [pricesQuery.data],
  );

  if (detailQuery.isLoading) return <LoadingState rows={8} />;
  if (detailQuery.error) return <ErrorState error={detailQuery.error} />;
  if (!detailQuery.data)
    return <EmptyState title="Stock not found" description={symbol} />;

  const detail = detailQuery.data;
  const tech = detail.technical;
  const momentum = detail.momentum;
  const valuation = detail.valuation;
  const horizon = detail.horizon;
  const signals: SignalView[] = detail.scoring?.signals ?? [];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-lg font-semibold">{detail.symbol}</h1>
            <Badge variant="outline">{detail.exchange}</Badge>
            {!detail.is_active ? <Badge variant="bearish">Inactive</Badge> : null}
          </div>
          <p className="text-sm text-muted-foreground">
            {detail.company.name}
            {detail.company.sector ? ` · ${titleCase(detail.company.sector)}` : ""}
            {detail.company.industry
              ? ` · ${titleCase(detail.company.industry)}`
              : ""}
          </p>
        </div>
        <div className="flex flex-wrap gap-1">
          {detail.indices.map((index) => (
            <Badge key={index.code} variant="default">
              {index.name}
            </Badge>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <StatCard label="Close" value={formatPrice(detail.latest_price?.close)} />
        <StatCard
          label="1M return"
          value={formatSignedPct(momentum?.return_1m)}
          tone={signedTone(momentum?.return_1m)}
        />
        <StatCard
          label="3M return"
          value={formatSignedPct(momentum?.return_3m)}
          tone={signedTone(momentum?.return_3m)}
        />
        <StatCard label="RSI (14)" value={formatPct(tech?.rsi14, 1).replace("%", "")} />
        <StatCard
          label="Trend"
          value={titleCase(tech?.trend_state)}
          tone={signalTone(tech?.trend_state)}
        />
        <StatCard
          label="From 52W high"
          value={formatPct(tech?.dist_from_high_52w_pct, 1)}
          tone={signedTone(tech?.dist_from_high_52w_pct)}
        />
      </div>

      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <CardTitle>Price history</CardTitle>
          <div className="flex gap-1">
            {RANGES.map((range) => (
              <Button
                key={range.label}
                size="sm"
                variant={rangeDays === range.days ? "default" : "outline"}
                onClick={() => setRangeDays(range.days)}
              >
                {range.label}
              </Button>
            ))}
          </div>
        </CardHeader>
        <CardContent>
          {pricesQuery.isLoading ? (
            <LoadingState rows={6} />
          ) : pricesQuery.error ? (
            <ErrorState error={pricesQuery.error} />
          ) : bars.length === 0 ? (
            <EmptyState title="No price history" />
          ) : (
            <PriceChart bars={bars} />
          )}
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <MetricCard
          title="Technical"
          rows={[
            ["SMA 20", formatPrice(tech?.sma20)],
            ["SMA 50", formatPrice(tech?.sma50)],
            ["SMA 200", formatPrice(tech?.sma200)],
            ["RSI (14)", formatNumber(tech?.rsi14)],
            ["MACD", formatNumber(tech?.macd)],
            ["MACD hist.", formatNumber(tech?.macd_hist)],
            ["ADX (14)", formatNumber(tech?.adx14)],
            ["ATR (14)", formatNumber(tech?.atr14)],
            ["52W high", formatPrice(tech?.high_52w)],
            ["52W low", formatPrice(tech?.low_52w)],
            ["Volume ratio", formatNumber(tech?.volume_ratio)],
            ["Breakout 52W", tech?.breakout_52w ? "Yes" : "No"],
          ]}
        />
        <MetricCard
          title="Momentum & relative strength"
          rows={[
            ["1M", formatSignedPct(momentum?.return_1m)],
            ["3M", formatSignedPct(momentum?.return_3m)],
            ["6M", formatSignedPct(momentum?.return_6m)],
            ["12M", formatSignedPct(momentum?.return_12m)],
            ["RS 1M", formatNumber(momentum?.rs_1m)],
            ["RS 3M", formatNumber(momentum?.rs_3m)],
            ["RS 6M", formatNumber(momentum?.rs_6m)],
            ["RS trend", titleCase(momentum?.rs_trend)],
            ["Benchmark", momentum?.benchmark ?? "—"],
          ]}
        />
        <MetricCard
          title="Valuation"
          rows={[
            ["P/E", formatNumber(valuation?.pe)],
            ["P/B", formatNumber(valuation?.pb)],
            ["EV/EBITDA", formatNumber(valuation?.ev_ebitda)],
            ["Dividend yield", formatPct(valuation?.dividend_yield)],
            ["FCF yield", formatPct(valuation?.fcf_yield)],
            ["P/E pct (3Y)", formatPct(valuation?.pe_percentile_3y, 1)],
            ["P/B pct (3Y)", formatPct(valuation?.pb_percentile_3y, 1)],
            ["Label", titleCase(valuation?.valuation_label)],
          ]}
        />
        <MetricCard
          title="Horizon"
          rows={[
            ["Short term", formatNumber(horizon?.short_term_score)],
            ["Medium term", formatNumber(horizon?.medium_term_score)],
            ["Long term", formatNumber(horizon?.long_term_score)],
            ["Preferred", titleCase(horizon?.preferred_horizon)],
            [
              "Confidence",
              isMissing(horizon?.horizon_confidence)
                ? "—"
                : `${horizon?.horizon_confidence}%`,
            ],
          ]}
        />
      </div>

      {detail.scoring ? (
        <div className="grid gap-6 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Score breakdown</CardTitle>
            </CardHeader>
            <CardContent>
              <ScoreBars scores={detail.scoring.scores} />
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Snapshot</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-3">
              <StatCard
                label="Composite"
                value={formatPct(detail.scoring.composite_score, 1).replace("%", "")}
              />
              <StatCard label="Label" value={titleCase(detail.scoring.score_label)} />
              <StatCard
                label="Preferred horizon"
                value={titleCase(detail.scoring.preferred_horizon)}
              />
              <StatCard
                label="Avg. volume"
                value={formatCompact(detail.latest_price?.volume)}
              />
              <StatCard
                label="Indices"
                value={formatInt(detail.indices.length)}
              />
            </CardContent>
          </Card>
        </div>
      ) : null}

      <div className="space-y-3">
        <h2 className="text-sm font-semibold">Signals & recommendations</h2>
        {signals.length === 0 ? (
          <EmptyState
            title="No signal"
            description="This instrument has no recommendation on the latest day."
          />
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            {signals.map((signal) => (
              <SignalCard
                key={`${signal.strategy_code}-${signal.signal_type}`}
                signal={signal}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function MetricCard({
  title,
  rows,
}: {
  title: string;
  rows: [string, string][];
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
          {rows.map(([label, value]) => (
            <div key={label} className="flex items-center justify-between gap-2">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="tabular">{value}</dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}
