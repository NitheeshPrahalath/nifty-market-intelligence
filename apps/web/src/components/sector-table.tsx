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
  formatSignedPct,
  isMissing,
  signalTone,
  titleCase,
} from "@/lib/format";
import type { SectorView } from "@/lib/types";

function toneOf(value: number | null | undefined) {
  if (isMissing(value)) return "neutral" as const;
  const n = value as number;
  return n > 0 ? ("bullish" as const) : n < 0 ? ("bearish" as const) : ("neutral" as const);
}

export function SectorTable({ sectors }: { sectors: SectorView[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Sector</TableHead>
          <TableHead>State</TableHead>
          <TableHead className="text-right">Score</TableHead>
          <TableHead className="text-right">Members</TableHead>
          <TableHead className="text-right">1M avg</TableHead>
          <TableHead className="text-right">3M avg</TableHead>
          <TableHead className="text-right">RS 3M</TableHead>
          <TableHead className="text-right">Breadth &gt; SMA50</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {sectors.map((sector) => (
          <TableRow key={sector.sector_id}>
            <TableCell className="font-medium">{titleCase(sector.sector)}</TableCell>
            <TableCell>
              <ToneBadge
                label={titleCase(sector.state)}
                tone={signalTone(sector.state)}
              />
            </TableCell>
            <TableCell className="tabular text-right">
              {formatPct(sector.score, 1).replace("%", "")}
            </TableCell>
            <TableCell className="tabular text-right">
              {sector.member_count}
            </TableCell>
            <TableCell className="tabular text-right">
              {formatSignedPct(sector.avg_return_1m)}
            </TableCell>
            <TableCell className="tabular text-right">
              {formatSignedPct(sector.avg_return_3m)}
            </TableCell>
            <TableCell className="tabular text-right">
              {formatSignedPct(sector.avg_rs_3m)}
            </TableCell>
            <TableCell className="tabular text-right">
              {formatPct(sector.breadth_above_sma50_pct, 0)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
