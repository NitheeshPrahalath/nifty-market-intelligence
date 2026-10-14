import { ToneBadge } from "@/components/tone-badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatPrice, isMissing, signalTone, titleCase } from "@/lib/format";
import type { SignalView } from "@/lib/types";

export function SignalCard({ signal }: { signal: SignalView }) {
  const tone = signalTone(signal.signal_type);
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between space-y-0">
        <div>
          <CardTitle>{titleCase(signal.strategy_code)}</CardTitle>
          <p className="text-xs text-muted-foreground">
            Confidence:{" "}
            {isMissing(signal.confidence) ? "—" : `${signal.confidence}%`}
            {signal.risk_level ? ` · Risk ${titleCase(signal.risk_level)}` : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <ToneBadge label={titleCase(signal.signal_type)} tone={tone} />
          <ToneBadge label={titleCase(signal.state)} tone={signalTone(signal.state)} />
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <dl className="grid grid-cols-2 gap-3 text-sm">
          <Field label="Price" value={formatPrice(signal.price)} />
          <Field
            label="Entry zone"
            value={range(signal.entry_low, signal.entry_high)}
          />
          <Field
            label="Target zone"
            value={range(signal.target_low, signal.target_high)}
          />
          <Field label="Invalidation" value={formatPrice(signal.invalidation_price)} />
        </dl>
        {signal.thesis ? (
          <p className="rounded-md bg-muted/50 p-3 text-xs leading-relaxed text-muted-foreground">
            {signal.thesis}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}

function range(low: number | null, high: number | null): string {
  if (isMissing(low) && isMissing(high)) return "—";
  if (isMissing(low)) return formatPrice(high);
  if (isMissing(high)) return formatPrice(low);
  return `${formatPrice(low)} – ${formatPrice(high)}`;
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-muted-foreground">
        {label}
      </dt>
      <dd className="tabular">{value}</dd>
    </div>
  );
}
