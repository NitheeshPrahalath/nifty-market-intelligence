import Link from "next/link";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";

import { ToneBadge } from "@/components/tone-badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  formatPct,
  formatPrice,
  scoreLabelTone,
  signalTone,
  titleCase,
} from "@/lib/format";
import type { ScreenerRow } from "@/lib/types";
import { cn } from "@/lib/utils";

export interface SortState {
  field: string;
  order: "asc" | "desc";
}

interface Column {
  key: string;
  label: string;
  sortable?: boolean;
  align?: "left" | "right";
}

const COLUMNS: Column[] = [
  { key: "symbol", label: "Symbol", sortable: true },
  { key: "sector", label: "Sector", sortable: true },
  { key: "close", label: "Close", sortable: true, align: "right" },
  { key: "composite", label: "Composite", sortable: true, align: "right" },
  { key: "label", label: "Label" },
  { key: "trend", label: "Trend", sortable: true, align: "right" },
  { key: "momentum", label: "Momentum", sortable: true, align: "right" },
  { key: "relative_strength", label: "RS", sortable: true, align: "right" },
  { key: "valuation", label: "Valuation", sortable: true, align: "right" },
  { key: "risk", label: "Risk", sortable: true, align: "right" },
  { key: "horizon", label: "Horizon" },
];

export function ScreenerTable({
  rows,
  sort,
  onSortChange,
}: {
  rows: ScreenerRow[];
  sort: SortState;
  onSortChange: (sort: SortState) => void;
}) {
  const toggle = (field: string) => {
    if (sort.field === field) {
      onSortChange({ field, order: sort.order === "desc" ? "asc" : "desc" });
    } else {
      onSortChange({ field, order: "desc" });
    }
  };

  return (
    <Table>
      <TableHeader>
        <TableRow>
          {COLUMNS.map((column) => (
            <TableHead
              key={column.key}
              className={cn(column.align === "right" && "text-right")}
            >
              {column.sortable ? (
                <button
                  type="button"
                  className="inline-flex items-center gap-1 hover:text-foreground"
                  onClick={() => toggle(column.key)}
                >
                  {column.label}
                  <SortIcon active={sort.field === column.key} order={sort.order} />
                </button>
              ) : (
                column.label
              )}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => {
          const signal = row.signals[0];
          return (
            <TableRow key={row.instrument_id}>
              <TableCell className="font-medium">
                <Link
                  href={`/stocks/${encodeURIComponent(row.symbol)}`}
                  className="hover:text-primary"
                >
                  {row.symbol}
                </Link>
                <p className="max-w-[12rem] truncate text-xs text-muted-foreground">
                  {row.name}
                </p>
              </TableCell>
              <TableCell className="text-muted-foreground">
                {titleCase(row.sector)}
              </TableCell>
              <TableCell className="tabular text-right">
                {formatPrice(row.close)}
              </TableCell>
              <TableCell className="tabular text-right font-medium">
                {formatPct(row.composite_score, 1).replace("%", "")}
              </TableCell>
              <TableCell>
                <ToneBadge
                  label={titleCase(row.score_label)}
                  tone={scoreLabelTone(row.score_label)}
                />
              </TableCell>
              <TableCell className="tabular text-right">
                {formatPct(row.scores.trend, 1).replace("%", "")}
              </TableCell>
              <TableCell className="tabular text-right">
                {formatPct(row.scores.momentum, 1).replace("%", "")}
              </TableCell>
              <TableCell className="tabular text-right">
                {formatPct(row.scores.relative_strength, 1).replace("%", "")}
              </TableCell>
              <TableCell className="tabular text-right">
                {formatPct(row.scores.valuation, 1).replace("%", "")}
              </TableCell>
              <TableCell className="tabular text-right">
                {formatPct(row.scores.risk, 1).replace("%", "")}
              </TableCell>
              <TableCell>
                {signal ? (
                  <ToneBadge
                    label={titleCase(signal.signal_type)}
                    tone={signalTone(signal.signal_type)}
                  />
                ) : (
                  <span className="text-xs text-muted-foreground">
                    {titleCase(row.preferred_horizon)}
                  </span>
                )}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

function SortIcon({ active, order }: { active: boolean; order: "asc" | "desc" }) {
  if (!active) return <ArrowUpDown className="h-3 w-3 opacity-40" />;
  return order === "asc" ? (
    <ArrowUp className="h-3 w-3" />
  ) : (
    <ArrowDown className="h-3 w-3" />
  );
}
