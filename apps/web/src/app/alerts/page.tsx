"use client";

import { useState } from "react";

import { AlertList } from "@/components/alert-list";
import { Pagination } from "@/components/pagination";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Card, CardContent } from "@/components/ui/card";
import { Input, Label, Select } from "@/components/ui/input";
import { useAlerts } from "@/lib/queries";
import { useUiStore } from "@/store/ui";

const TYPES = [
  "NEW_RECOMMENDATION",
  "ENTRY_ZONE_REACHED",
  "RECOMMENDATION_UPGRADED",
  "RECOMMENDATION_DOWNGRADED",
  "THESIS_WEAKENING",
  "THESIS_IMPROVED",
  "FUNDAMENTAL_CHANGE",
  "TECHNICAL_CHANGE",
  "TARGET_REVIEW_ZONE_REACHED",
  "RISK_CONDITION_TRIGGERED",
  "EXIT_CONDITION_TRIGGERED",
  "RECOMMENDATION_CLOSED",
  "MARKET_REGIME_CHANGE",
];

const SEVERITIES = ["INFO", "WARNING", "ERROR"];
const PAGE_SIZE = 20;

function humanise(value: string): string {
  return value
    .replace(/_/g, " ")
    .toLowerCase()
    .replace(/^\w/, (c) => c.toUpperCase());
}

export default function AlertsPage() {
  const indexCode = useUiStore((state) => state.indexCode);
  const [type, setType] = useState("");
  const [severity, setSeverity] = useState("");
  const [symbol, setSymbol] = useState("");
  const [since, setSince] = useState("");
  const [page, setPage] = useState(1);

  const { data, error, isLoading, isFetching } = useAlerts({
    index: indexCode,
    type: type || undefined,
    severity: severity || undefined,
    symbol: symbol || undefined,
    since: since || undefined,
    page,
    page_size: PAGE_SIZE,
  });

  const update = <T,>(setter: (value: T) => void) => (value: T) => {
    setter(value);
    setPage(1);
  };

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold">Alert center</h1>
        <p className="text-sm text-muted-foreground">
          Every recommendation change and risk event, each carrying its reason.
        </p>
      </div>

      <Card>
        <CardContent className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="space-y-1">
            <Label htmlFor="alert-type">Type</Label>
            <Select
              id="alert-type"
              value={type}
              onChange={(event) => update(setType)(event.target.value)}
            >
              <option value="">All types</option>
              {TYPES.map((item) => (
                <option key={item} value={item}>
                  {humanise(item)}
                </option>
              ))}
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="alert-severity">Severity</Label>
            <Select
              id="alert-severity"
              value={severity}
              onChange={(event) => update(setSeverity)(event.target.value)}
            >
              <option value="">All severities</option>
              {SEVERITIES.map((item) => (
                <option key={item} value={item}>
                  {humanise(item)}
                </option>
              ))}
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="alert-symbol">Symbol</Label>
            <Input
              id="alert-symbol"
              placeholder="e.g. RELIANCE"
              value={symbol}
              onChange={(event) => update(setSymbol)(event.target.value.toUpperCase())}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="alert-since">Since</Label>
            <Input
              id="alert-since"
              type="date"
              value={since}
              onChange={(event) => update(setSince)(event.target.value)}
            />
          </div>
        </CardContent>
      </Card>

      {isLoading ? (
        <LoadingState rows={8} />
      ) : error ? (
        <ErrorState error={error} />
      ) : !data || data.items.length === 0 ? (
        <EmptyState
          title="No alerts"
          description="No notifications match the selected filters."
        />
      ) : (
        <>
          <div className="text-xs text-muted-foreground">
            {isFetching ? "Updating…" : `${data.total} alerts`}
          </div>
          <AlertList alerts={data.items} />
          <Pagination
            page={data.page}
            pages={data.pages}
            total={data.total}
            pageSize={data.page_size}
            onPageChange={setPage}
          />
        </>
      )}
    </div>
  );
}
