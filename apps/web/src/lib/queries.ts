"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "./api";

/** Query keys are centralised so components and prefetching stay in sync. */
export const queryKeys = {
  health: ["health"] as const,
  marketOverview: (index: string, asOf?: string) =>
    ["market", "overview", index, asOf ?? "latest"] as const,
  screener: (params: object) => ["screener", params] as const,
  stock: (symbol: string, asOf?: string) =>
    ["stock", symbol, asOf ?? "latest"] as const,
  prices: (symbol: string, params: object) =>
    ["stock", symbol, "prices", params] as const,
  recommendations: (params: object) => ["recommendations", params] as const,
  recommendation: (id: number) => ["recommendation", id] as const,
  explanation: (id: number, kind?: string) =>
    ["recommendation", id, "explanation", kind ?? "latest"] as const,
  alerts: (params: object) => ["alerts", params] as const,
};

// Most list endpoints are as-of resolved and change only on the next pipeline
// run, so a 60s stale window avoids refetch storms while navigating.
const DEFAULT_STALE_MS = 60_000;

export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: ({ signal }) => api.health(signal),
    staleTime: 30_000,
    retry: 1,
  });
}

export function useMarketOverview(index = "NIFTY_50", asOf?: string) {
  return useQuery({
    queryKey: queryKeys.marketOverview(index, asOf),
    queryFn: ({ signal }) => api.marketOverview({ index, as_of: asOf }, signal),
    staleTime: DEFAULT_STALE_MS,
  });
}

export function useScreener(params: NonNullable<Parameters<typeof api.screener>[0]>) {
  return useQuery({
    queryKey: queryKeys.screener(params),
    queryFn: ({ signal }) => api.screener(params, signal),
    staleTime: DEFAULT_STALE_MS,
    placeholderData: keepPreviousData,
  });
}

export function useStockDetail(symbol: string, asOf?: string) {
  return useQuery({
    queryKey: queryKeys.stock(symbol, asOf),
    queryFn: ({ signal }) => api.stockDetail(symbol, asOf, signal),
    staleTime: DEFAULT_STALE_MS,
    enabled: symbol.length > 0,
  });
}

export function usePrices(
  symbol: string,
  params: Parameters<typeof api.prices>[1] = {},
) {
  return useQuery({
    queryKey: queryKeys.prices(symbol, params),
    queryFn: ({ signal }) => api.prices(symbol, params, signal),
    staleTime: DEFAULT_STALE_MS,
    enabled: symbol.length > 0,
  });
}

export function useRecommendations(
  params: NonNullable<Parameters<typeof api.recommendations>[0]>,
) {
  return useQuery({
    queryKey: queryKeys.recommendations(params),
    queryFn: ({ signal }) => api.recommendations(params, signal),
    staleTime: DEFAULT_STALE_MS,
    placeholderData: keepPreviousData,
  });
}

export function useRecommendation(id: number) {
  return useQuery({
    queryKey: queryKeys.recommendation(id),
    queryFn: ({ signal }) => api.recommendation(id, signal),
    staleTime: DEFAULT_STALE_MS,
    enabled: Number.isFinite(id),
  });
}

export function useExplanation(id: number, kind?: string) {
  return useQuery({
    queryKey: queryKeys.explanation(id, kind),
    queryFn: ({ signal }) => api.explanation(id, kind, signal),
    staleTime: DEFAULT_STALE_MS,
    enabled: Number.isFinite(id),
  });
}

export function useAlerts(params: NonNullable<Parameters<typeof api.alerts>[0]>) {
  return useQuery({
    queryKey: queryKeys.alerts(params),
    queryFn: ({ signal }) => api.alerts(params, signal),
    staleTime: DEFAULT_STALE_MS,
    placeholderData: keepPreviousData,
  });
}
