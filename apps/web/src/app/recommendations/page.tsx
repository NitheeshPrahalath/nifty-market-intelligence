"use client";

import { useState } from "react";

import { Pagination } from "@/components/pagination";
import { RecommendationTable } from "@/components/recommendation-table";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Card, CardContent } from "@/components/ui/card";
import { Label, Select } from "@/components/ui/input";
import { useRecommendations } from "@/lib/queries";
import { useUiStore } from "@/store/ui";

const STATES = [
  "WATCH",
  "POTENTIAL_ENTRY",
  "ENTRY",
  "HOLD",
  "THESIS_WEAKENING",
  "EXIT_REVIEW",
  "EXIT",
  "CLOSED",
];

const HORIZONS = ["SHORT_TERM", "MEDIUM_TERM", "LONG_TERM"];
const PAGE_SIZE = 25;

function label(value: string): string {
  return value.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
}

export default function RecommendationsPage() {
  const indexCode = useUiStore((state) => state.indexCode);
  const [state, setState] = useState("");
  const [horizon, setHorizon] = useState("");
  const [page, setPage] = useState(1);

  const { data, error, isLoading, isFetching } = useRecommendations({
    index: indexCode,
    state: state || undefined,
    horizon: horizon || undefined,
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
        <h1 className="text-lg font-semibold">Recommendations</h1>
        <p className="text-sm text-muted-foreground">
          Active theses, entry opportunities and weakening positions — each with
          its state history.
        </p>
      </div>

      <Card>
        <CardContent className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="rec-state">State</Label>
            <Select
              id="rec-state"
              value={state}
              onChange={(event) => update(setState)(event.target.value)}
            >
              <option value="">All states</option>
              {STATES.map((item) => (
                <option key={item} value={item}>
                  {label(item)}
                </option>
              ))}
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="rec-horizon">Horizon</Label>
            <Select
              id="rec-horizon"
              value={horizon}
              onChange={(event) => update(setHorizon)(event.target.value)}
            >
              <option value="">Any horizon</option>
              {HORIZONS.map((item) => (
                <option key={item} value={item}>
                  {label(item)}
                </option>
              ))}
            </Select>
          </div>
        </CardContent>
      </Card>

      {isLoading ? (
        <LoadingState rows={10} />
      ) : error ? (
        <ErrorState error={error} />
      ) : !data || data.items.length === 0 ? (
        <EmptyState
          title="No recommendations"
          description="No recommendations match the selected filters."
        />
      ) : (
        <>
          <div className="text-xs text-muted-foreground">
            {isFetching ? "Updating…" : "Results"}
          </div>
          <Card>
            <CardContent className="p-0">
              <RecommendationTable rows={data.items} />
            </CardContent>
          </Card>
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
