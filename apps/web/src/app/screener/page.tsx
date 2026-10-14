"use client";

import { useState } from "react";

import { Pagination } from "@/components/pagination";
import { ScreenerTable, type SortState } from "@/components/screener-table";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { IndexSelector } from "@/components/index-selector";
import { Card, CardContent } from "@/components/ui/card";
import { Input, Label, Select } from "@/components/ui/input";
import { useScreener } from "@/lib/queries";
import { useUiStore } from "@/store/ui";

const HORIZONS = ["SHORT_TERM", "MEDIUM_TERM", "LONG_TERM"];
const LABELS = ["STRONG", "GOOD", "NEUTRAL", "WEAK", "POOR"];
const PAGE_SIZE = 25;

export default function ScreenerPage() {
  const indexCode = useUiStore((state) => state.indexCode);

  const [horizon, setHorizon] = useState("");
  const [label, setLabel] = useState("");
  const [sector, setSector] = useState("");
  const [query, setQuery] = useState("");
  const [minScore, setMinScore] = useState("");
  const [maxScore, setMaxScore] = useState("");
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState<SortState>({ field: "composite", order: "desc" });

  const { data, error, isLoading, isFetching } = useScreener({
    index: indexCode,
    horizon: horizon || undefined,
    label: label || undefined,
    sector: sector || undefined,
    q: query || undefined,
    min_score: minScore ? Number(minScore) : undefined,
    max_score: maxScore ? Number(maxScore) : undefined,
    sort: sort.field,
    order: sort.order,
    page,
    page_size: PAGE_SIZE,
  });

  const updateSort = (next: SortState) => {
    setSort(next);
    setPage(1);
  };

  const resetPage = <T,>(setter: (value: T) => void) => (value: T) => {
    setter(value);
    setPage(1);
  };

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold">Screener</h1>
        <p className="text-sm text-muted-foreground">
          Ranked, filterable universe across technical, fundamental and horizon
          scores.
        </p>
      </div>

      <Card>
        <CardContent className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3">
          <div className="space-y-1">
            <Label htmlFor="screener-index">Index</Label>
            <div id="screener-index">
              <IndexSelector />
            </div>
          </div>

          <div className="space-y-1">
            <Label htmlFor="screener-horizon">Horizon</Label>
            <Select
              id="screener-horizon"
              value={horizon}
              onChange={(event) => resetPage(setHorizon)(event.target.value)}
            >
              <option value="">Any</option>
              {HORIZONS.map((item) => (
                <option key={item} value={item}>
                  {item.replace("_", " ")}
                </option>
              ))}
            </Select>
          </div>

          <div className="space-y-1">
            <Label htmlFor="screener-label">Score label</Label>
            <Select
              id="screener-label"
              value={label}
              onChange={(event) => resetPage(setLabel)(event.target.value)}
            >
              <option value="">Any</option>
              {LABELS.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </Select>
          </div>

          <div className="space-y-1">
            <Label htmlFor="screener-sector">Sector</Label>
            <Input
              id="screener-sector"
              placeholder="e.g. Information Technology"
              value={sector}
              onChange={(event) => resetPage(setSector)(event.target.value)}
            />
          </div>

          <div className="space-y-1">
            <Label htmlFor="screener-query">Search</Label>
            <Input
              id="screener-query"
              placeholder="Symbol or name"
              value={query}
              onChange={(event) => resetPage(setQuery)(event.target.value)}
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label htmlFor="screener-min">Min score</Label>
              <Input
                id="screener-min"
                type="number"
                min={0}
                max={100}
                value={minScore}
                onChange={(event) => resetPage(setMinScore)(event.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="screener-max">Max score</Label>
              <Input
                id="screener-max"
                type="number"
                min={0}
                max={100}
                value={maxScore}
                onChange={(event) => resetPage(setMaxScore)(event.target.value)}
              />
            </div>
          </div>
        </CardContent>
      </Card>

      {isLoading ? (
        <LoadingState rows={10} />
      ) : error ? (
        <ErrorState error={error} />
      ) : !data || data.items.length === 0 ? (
        <EmptyState
          title="No matches"
          description="Loosen the filters or run the analysis pipeline."
        />
      ) : (
        <>
          <div className="flex items-center justify-between">
            <span className="text-xs text-muted-foreground">
              {isFetching ? "Updating…" : "Results"}
            </span>
          </div>
          <Card>
            <CardContent className="p-0">
              <ScreenerTable
                rows={data.items}
                sort={sort}
                onSortChange={updateSort}
              />
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
