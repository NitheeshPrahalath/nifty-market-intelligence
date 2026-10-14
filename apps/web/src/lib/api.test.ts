import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, buildQuery, fetchJson } from "./api";

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("buildQuery", () => {
  it("drops null, undefined and empty params", () => {
    expect(
      buildQuery({ a: 1, b: null, c: "", d: undefined, e: "x", f: false }),
    ).toBe("?a=1&e=x&f=false");
  });

  it("returns an empty string when nothing remains", () => {
    expect(buildQuery({ a: null, b: undefined })).toBe("");
  });
});

describe("fetchJson", () => {
  it("returns the parsed body on success", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ status: "ok" })));
    await expect(fetchJson("/health")).resolves.toEqual({ status: "ok" });
  });

  it("throws an ApiError carrying the server detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "not found" }, 404)),
    );
    const error = (await fetchJson("/api/v1/stocks/NOPE").catch(
      (e: unknown) => e,
    )) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(404);
    expect(error.message).toBe("not found");
  });

  it("falls back to a generic message when the body is not JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 502,
        json: async () => {
          throw new Error("bad gateway");
        },
      })),
    );
    const error = (await fetchJson("/health").catch((e: unknown) => e)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.message).toBe("request failed with status 502");
  });
});

describe("api client", () => {
  it("builds the screener URL with only the provided filters", async () => {
    const mockFetch = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({ items: [] }),
    );
    vi.stubGlobal("fetch", mockFetch);

    await api.screener({ index: "NIFTY_50", page: 2, horizon: undefined });

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch.mock.calls[0][0]).toBe(
      "/api/v1/screener?index=NIFTY_50&page=2",
    );
  });

  it("url-encodes symbols in the stock path", async () => {
    const mockFetch = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({}),
    );
    vi.stubGlobal("fetch", mockFetch);

    await api.stockDetail("M&M");

    expect(mockFetch.mock.calls[0][0]).toBe("/api/v1/stocks/M%26M");
  });

  it("requests an explanation with the optional kind filter", async () => {
    const mockFetch = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonResponse({ headline: "x" }),
    );
    vi.stubGlobal("fetch", mockFetch);

    await api.explanation(7, "change");

    expect(mockFetch.mock.calls[0][0]).toBe(
      "/api/v1/recommendations/7/explanation?kind=change",
    );
  });

  it("returns null when no explanation has been generated yet", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "not found" }, 404)),
    );

    await expect(api.explanation(7)).resolves.toBeNull();
  });
});
