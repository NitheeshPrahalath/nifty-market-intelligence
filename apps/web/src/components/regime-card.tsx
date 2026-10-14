import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatCard } from "@/components/stat-card";
import { ToneBadge } from "@/components/tone-badge";
import {
  formatDate,
  formatPct,
  formatSignedPct,
  signalTone,
  type Tone,
} from "@/lib/format";
import type { RegimeView } from "@/lib/types";

const REGIME_TONE: Record<string, Tone> = {
  BULL: "bullish",
  BEAR: "bearish",
  NEUTRAL: "neutral",
  RISK_ON: "bullish",
  RISK_OFF: "bearish",
};

function regimeTone(label: string | null | undefined): Tone {
  const key = (label ?? "").toUpperCase().replace(/[^A-Z]/g, "");
  if (key in REGIME_TONE) return REGIME_TONE[key];
  if (key.includes("BULL")) return "bullish";
  if (key.includes("BEAR") || key.includes("RISKOFF")) return "bearish";
  return signalTone(label);
}

export function RegimeCard({ regime }: { regime: RegimeView }) {
  const tone = regimeTone(regime.label);

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <div>
          <CardTitle>Market regime</CardTitle>
          <p className="text-xs text-muted-foreground">
            As of {formatDate(regime.as_of)} · {regime.member_count} members
          </p>
        </div>
        <ToneBadge label={regime.label} tone={tone} />
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <StatCard
            label="Regime score"
            value={formatPct(regime.score, 1).replace("%", "")}
            tone={tone}
          />
          <StatCard
            label="Breadth > SMA50"
            value={formatPct(regime.breadth_above_sma50_pct, 1)}
          />
          <StatCard
            label="Breadth > SMA200"
            value={formatPct(regime.breadth_above_sma200_pct, 1)}
          />
          <StatCard
            label="Index 1M"
            value={formatSignedPct(regime.index_return_1m)}
            tone={toneOf(regime.index_return_1m)}
          />
          <StatCard
            label="Index 3M"
            value={formatSignedPct(regime.index_return_3m)}
            tone={toneOf(regime.index_return_3m)}
          />
          <StatCard
            label="Hist. vol (20d)"
            value={formatPct(regime.index_hist_vol_20, 1)}
          />
          <StatCard
            label="Drawdown"
            value={formatPct(regime.index_drawdown_pct, 1)}
            tone={toneOf(regime.index_drawdown_pct)}
          />
          <StatCard
            label="Sector participation"
            value={formatPct(regime.sector_participation_pct, 0)}
          />
          <StatCard
            label="Breadth > SMA20"
            value={formatPct(regime.breadth_above_sma20_pct, 1)}
          />
        </div>
      </CardContent>
    </Card>
  );
}

function toneOf(value: number | null | undefined): Tone {
  if (value === null || value === undefined) return "neutral";
  if (value > 0) return "bullish";
  if (value < 0) return "bearish";
  return "neutral";
}
