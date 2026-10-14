import Link from "next/link";

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
  formatSignedPct,
  signalTone,
  titleCase,
} from "@/lib/format";
import type { Mover } from "@/lib/types";

export function MoversTable({
  title,
  movers,
  metric = "change_1m",
}: {
  title: string;
  movers: Mover[];
  metric?: "change_1m" | "change_3m" | "composite_score";
}) {
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold">{title}</h3>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Symbol</TableHead>
            <TableHead className="text-right">Close</TableHead>
            <TableHead className="text-right">
              {metric === "change_1m" ? "1M" : metric === "change_3m" ? "3M" : "Score"}
            </TableHead>
            <TableHead>Signal</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {movers.map((mover) => {
            const raw = mover[metric];
            const display =
              metric === "composite_score"
                ? formatPct(raw, 1).replace("%", "")
                : formatSignedPct(raw);
            return (
              <TableRow key={mover.symbol}>
                <TableCell>
                  <Link
                    href={`/stocks/${encodeURIComponent(mover.symbol)}`}
                    className="font-medium hover:text-primary"
                  >
                    {mover.symbol}
                  </Link>
                  <p className="truncate text-xs text-muted-foreground">
                    {mover.name}
                  </p>
                </TableCell>
                <TableCell className="tabular text-right">
                  {formatPrice(mover.close)}
                </TableCell>
                <TableCell className="tabular text-right">{display}</TableCell>
                <TableCell>
                  {mover.signal ? (
                    <ToneBadge
                      label={titleCase(mover.signal)}
                      tone={signalTone(mover.signal)}
                    />
                  ) : (
                    <span className="text-xs text-muted-foreground">—</span>
                  )}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
