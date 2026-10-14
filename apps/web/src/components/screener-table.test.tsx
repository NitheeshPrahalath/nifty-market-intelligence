import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ScreenerTable, type SortState } from "./screener-table";
import type { ScreenerRow } from "@/lib/types";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...props
  }: {
    href: string;
    children: React.ReactNode;
  }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

function makeRow(overrides: Partial<ScreenerRow> = {}): ScreenerRow {
  return {
    instrument_id: 1,
    symbol: "RELIANCE",
    name: "Reliance Industries",
    exchange: "NSE",
    sector: "ENERGY",
    industry: "Oil & Gas",
    as_of: "2026-10-14",
    close: 2450.5,
    composite_score: 78.4,
    score_label: "STRONG",
    preferred_horizon: "LONG_TERM",
    scores: {
      trend: 80,
      momentum: 70,
      relative_strength: 65,
      quality: 75,
      growth: 60,
      valuation: 50,
      risk: 55,
      liquidity: 90,
      horizon: 72,
      sector: 66,
    },
    signals: [],
    ...overrides,
  };
}

describe("ScreenerTable", () => {
  it("renders the symbol, sector and composite score", () => {
    render(
      <ScreenerTable
        rows={[makeRow()]}
        sort={{ field: "composite", order: "desc" }}
        onSortChange={() => {}}
      />,
    );

    expect(screen.getByText("RELIANCE")).toBeInTheDocument();
    expect(screen.getByText("Reliance Industries")).toBeInTheDocument();
    expect(screen.getByText("Energy")).toBeInTheDocument();
    expect(screen.getByText("78.4")).toBeInTheDocument();
    expect(screen.getByText("Strong")).toBeInTheDocument();
  });

  it("emits a descending sort when a new column header is clicked", async () => {
    const onSortChange = vi.fn<(sort: SortState) => void>();
    render(
      <ScreenerTable
        rows={[makeRow()]}
        sort={{ field: "composite", order: "desc" }}
        onSortChange={onSortChange}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /momentum/i }));

    expect(onSortChange).toHaveBeenCalledWith({
      field: "momentum",
      order: "desc",
    });
  });

  it("toggles the order when the active column is clicked again", async () => {
    const onSortChange = vi.fn<(sort: SortState) => void>();
    render(
      <ScreenerTable
        rows={[makeRow()]}
        sort={{ field: "composite", order: "desc" }}
        onSortChange={onSortChange}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /composite/i }));

    expect(onSortChange).toHaveBeenCalledWith({
      field: "composite",
      order: "asc",
    });
  });

  it("links each row to its stock detail page", () => {
    render(
      <ScreenerTable
        rows={[makeRow()]}
        sort={{ field: "composite", order: "desc" }}
        onSortChange={() => {}}
      />,
    );

    expect(screen.getByRole("link", { name: "RELIANCE" })).toHaveAttribute(
      "href",
      "/stocks/RELIANCE",
    );
  });
});
