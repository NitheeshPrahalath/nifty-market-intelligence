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
  formatDate,
  formatPrice,
  isMissing,
  signalTone,
  titleCase,
} from "@/lib/format";
import type { RecommendationRow } from "@/lib/types";

function zone(low: number | null, high: number | null): string {
  if (isMissing(low) && isMissing(high)) return "—";
  if (isMissing(low)) return formatPrice(high);
  if (isMissing(high)) return formatPrice(low);
  return `${formatPrice(low)} – ${formatPrice(high)}`;
}

export function RecommendationTable({ rows }: { rows: RecommendationRow[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Symbol</TableHead>
          <TableHead>State</TableHead>
          <TableHead>Strategy</TableHead>
          <TableHead>Horizon</TableHead>
          <TableHead className="text-right">Confidence</TableHead>
          <TableHead className="text-right">Price</TableHead>
          <TableHead className="text-right">Entry zone</TableHead>
          <TableHead className="text-right">Target zone</TableHead>
          <TableHead>Since</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={row.id}>
            <TableCell className="font-medium">
              <Link href={`/recommendations/${row.id}`} className="hover:text-primary">
                {row.symbol}
              </Link>
              <p className="max-w-[12rem] truncate text-xs text-muted-foreground">
                {row.name}
              </p>
            </TableCell>
            <TableCell>
              <ToneBadge label={titleCase(row.state)} tone={signalTone(row.state)} />
            </TableCell>
            <TableCell className="text-muted-foreground">
              {titleCase(row.strategy_code)}
            </TableCell>
            <TableCell className="text-muted-foreground">
              {titleCase(row.horizon)}
            </TableCell>
            <TableCell className="tabular text-right">
              {isMissing(row.confidence) ? "—" : `${row.confidence}%`}
            </TableCell>
            <TableCell className="tabular text-right">
              {formatPrice(row.current_price)}
            </TableCell>
            <TableCell className="tabular text-right">
              {zone(row.entry_low, row.entry_high)}
            </TableCell>
            <TableCell className="tabular text-right">
              {zone(row.target_low, row.target_high)}
            </TableCell>
            <TableCell className="text-muted-foreground">
              {formatDate(row.active_since ?? row.first_as_of)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
