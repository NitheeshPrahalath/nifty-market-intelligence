/**
 * Display formatting.
 *
 * The backend uses ``None`` to mean "no data" — this module is deliberate about
 * rendering that as an em-dash rather than ``0`` so a missing input is never
 * mistaken for a real value.
 */

export const EMPTY = "—";

export function isMissing(value: unknown): boolean {
  return value === null || value === undefined || value === "";
}

/** Percentage from an already-percent value (14.2 -> "14.2%"). */
export function formatPct(value: number | null | undefined, digits = 2): string {
  if (isMissing(value)) return EMPTY;
  return `${(value as number).toFixed(digits)}%`;
}

/** Signed percentage with an explicit sign (14.2 -> "+14.20%"). */
export function formatSignedPct(
  value: number | null | undefined,
  digits = 2,
): string {
  if (isMissing(value)) return EMPTY;
  const n = value as number;
  return `${n > 0 ? "+" : ""}${n.toFixed(digits)}%`;
}

export function formatNumber(
  value: number | null | undefined,
  digits = 2,
): string {
  if (isMissing(value)) return EMPTY;
  return (value as number).toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function formatInt(value: number | null | undefined): string {
  if (isMissing(value)) return EMPTY;
  return Math.round(value as number).toLocaleString("en-IN");
}

/** Hundredths of a rupee as an Indian price (1234.5 -> "₹1,234.50"). */
export function formatPrice(value: number | null | undefined, digits = 2): string {
  if (isMissing(value)) return EMPTY;
  return `₹${formatNumber(value, digits)}`;
}

export function formatCompact(value: number | null | undefined): string {
  if (isMissing(value)) return EMPTY;
  const n = value as number;
  const abs = Math.abs(n);
  if (abs >= 1e7) return `${(n / 1e7).toFixed(2)} Cr`;
  if (abs >= 1e5) return `${(n / 1e5).toFixed(2)} L`;
  if (abs >= 1e3) return `${(n / 1e3).toFixed(1)} K`;
  return n.toFixed(0);
}

/** ISO date → "14 Oct 2026". Leaves anything unparseable untouched. */
export function formatDate(value: string | null | undefined): string {
  if (isMissing(value)) return EMPTY;
  const parsed = new Date(value as string);
  if (Number.isNaN(parsed.getTime())) return value as string;
  return parsed.toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

export function formatDateTime(value: string | null | undefined): string {
  if (isMissing(value)) return EMPTY;
  const parsed = new Date(value as string);
  if (Number.isNaN(parsed.getTime())) return value as string;
  return parsed.toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function titleCase(value: string | null | undefined): string {
  if (isMissing(value)) return EMPTY;
  return (value as string)
    .toLowerCase()
    .split(/[_\s]+/)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

export type Tone = "bullish" | "bearish" | "neutral" | "warning";

/** Map a signed metric to a directional tone (0 counts as neutral). */
export function signedTone(value: number | null | undefined): Tone {
  if (isMissing(value)) return "neutral";
  const n = value as number;
  if (n > 0) return "bullish";
  if (n < 0) return "bearish";
  return "neutral";
}

const POSITIVE_SIGNALS = new Set([
  "ENTRY",
  "BUY",
  "HOLD",
  "ACCUMULATE",
  "STRONG_BUY",
  "IMPROVING",
  "LEADER",
  "UP",
]);
const NEGATIVE_SIGNALS = new Set([
  "EXIT",
  "SELL",
  "REDUCE",
  "AVOID",
  "INVALIDATED",
  "WEAKENING",
  "LAGGARD",
  "DOWN",
]);
const WARNING_SIGNALS = new Set(["WATCH", "CAUTION"]);

export function signalTone(value: string | null | undefined): Tone {
  if (isMissing(value)) return "neutral";
  const key = (value as string).toUpperCase();
  if (POSITIVE_SIGNALS.has(key)) return "bullish";
  if (NEGATIVE_SIGNALS.has(key)) return "bearish";
  if (WARNING_SIGNALS.has(key)) return "warning";
  return "neutral";
}

export function scoreLabelTone(value: string | null | undefined): Tone {
  const key = (value ?? "").toUpperCase();
  if (key === "STRONG" || key === "GOOD") return "bullish";
  if (key === "WEAK" || key === "POOR") return "bearish";
  return "neutral";
}

export function severityTone(value: string | null | undefined): Tone {
  const key = (value ?? "").toUpperCase();
  if (key === "INFO") return "bullish";
  if (key === "WARNING") return "warning";
  if (key === "ERROR") return "bearish";
  return "neutral";
}

const TONE_TREND: Record<Tone, string> = {
  bullish: "up",
  bearish: "down",
  neutral: "flat",
  warning: "flat",
};

export function toneTrend(tone: Tone): string {
  return TONE_TREND[tone];
}
