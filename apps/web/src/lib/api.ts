import type {
  AlertView,
  HealthResponse,
  MarketOverview,
  Page,
  PriceBar,
  RecommendationDetail,
  RecommendationRow,
  ScreenerRow,
  StockDetail,
} from "./types";

/**
 * Base URL for the API.
 *
 * Empty by default so the browser calls its own origin and Next's rewrite proxy
 * forwards to FastAPI — no CORS, no backend origin in the client bundle. Set
 * ``NEXT_PUBLIC_API_BASE_URL`` only when the API is hosted separately.
 */
export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

export class ApiError extends Error {
  readonly status: number;
  readonly path: string;

  constructor(status: number, path: string, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.path = path;
  }
}

export type QueryValue = string | number | boolean | null | undefined;

/** Drop null/undefined/empty params so the backend sees only real filters. */
export function buildQuery(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

async function parseError(response: Response, path: string): Promise<ApiError> {
  let detail = `request failed with status ${response.status}`;
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") detail = body.detail;
  } catch {
    // non-JSON error body (proxy/network error) — keep the generic message
  }
  return new ApiError(response.status, path, detail);
}

/** Perform a JSON GET against the API, throwing ``ApiError`` on non-2xx. */
export async function fetchJson<T>(
  path: string,
  options: { signal?: AbortSignal } = {},
): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { Accept: "application/json" },
    signal: options.signal,
    cache: "no-store",
  });
  if (!response.ok) throw await parseError(response, path);
  return (await response.json()) as T;
}

export const api = {
  health: (signal?: AbortSignal) =>
    fetchJson<HealthResponse>("/health", { signal }),

  marketOverview: (
    params: { index?: string; as_of?: string; movers?: number } = {},
    signal?: AbortSignal,
  ) =>
    fetchJson<MarketOverview>(
      `/api/v1/market/overview${buildQuery(params)}`,
      { signal },
    ),

  screener: (
    params: {
      index?: string;
      as_of?: string;
      horizon?: string;
      label?: string;
      sector?: string;
      q?: string;
      min_score?: number;
      max_score?: number;
      sort?: string;
      order?: "asc" | "desc";
      page?: number;
      page_size?: number;
    } = {},
    signal?: AbortSignal,
  ) => fetchJson<Page<ScreenerRow>>(`/api/v1/screener${buildQuery(params)}`, { signal }),

  stockDetail: (symbol: string, asOf?: string, signal?: AbortSignal) =>
    fetchJson<StockDetail>(
      `/api/v1/stocks/${encodeURIComponent(symbol)}${buildQuery({ as_of: asOf })}`,
      { signal },
    ),

  prices: (
    symbol: string,
    params: { start?: string; end?: string; page?: number; page_size?: number } = {},
    signal?: AbortSignal,
  ) =>
    fetchJson<Page<PriceBar>>(
      `/api/v1/stocks/${encodeURIComponent(symbol)}/prices${buildQuery(params)}`,
      { signal },
    ),

  recommendations: (
    params: {
      index?: string;
      state?: string;
      strategy?: string;
      horizon?: string;
      as_of?: string;
      page?: number;
      page_size?: number;
    } = {},
    signal?: AbortSignal,
  ) =>
    fetchJson<Page<RecommendationRow>>(
      `/api/v1/recommendations${buildQuery(params)}`,
      { signal },
    ),

  recommendation: (id: number, signal?: AbortSignal) =>
    fetchJson<RecommendationDetail>(`/api/v1/recommendations/${id}`, { signal }),

  alerts: (
    params: {
      type?: string;
      severity?: string;
      since?: string;
      index?: string;
      symbol?: string;
      page?: number;
      page_size?: number;
    } = {},
    signal?: AbortSignal,
  ) => fetchJson<Page<AlertView>>(`/api/v1/alerts${buildQuery(params)}`, { signal }),
};
