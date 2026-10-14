import { describe, expect, it } from "vitest";

import {
  EMPTY,
  formatCompact,
  formatDate,
  formatNumber,
  formatPct,
  formatPrice,
  formatSignedPct,
  isMissing,
  scoreLabelTone,
  severityTone,
  signalTone,
  signedTone,
  titleCase,
} from "./format";

describe("isMissing", () => {
  it("treats null, undefined and empty string as missing", () => {
    expect(isMissing(null)).toBe(true);
    expect(isMissing(undefined)).toBe(true);
    expect(isMissing("")).toBe(true);
  });

  it("treats zero and false as present values", () => {
    expect(isMissing(0)).toBe(false);
    expect(isMissing(false)).toBe(false);
  });
});

describe("percentage formatting", () => {
  it("renders missing values as an em-dash, never zero", () => {
    expect(formatPct(null)).toBe(EMPTY);
    expect(formatSignedPct(undefined)).toBe(EMPTY);
  });

  it("formats to the requested precision", () => {
    expect(formatPct(14.234, 1)).toBe("14.2%");
    expect(formatPct(14.234, 2)).toBe("14.23%");
  });

  it("adds a sign only for positive values", () => {
    expect(formatSignedPct(5)).toBe("+5.00%");
    expect(formatSignedPct(-3.1)).toBe("-3.10%");
    expect(formatSignedPct(0)).toBe("0.00%");
  });
});

describe("number formatting", () => {
  it("groups thousands", () => {
    expect(formatNumber(1234567.891, 2)).toBe("12,34,567.89");
  });

  it("formats Indian prices", () => {
    expect(formatPrice(1234.5)).toBe("₹1,234.50");
    expect(formatPrice(null)).toBe(EMPTY);
  });

  it("compacts Indian magnitudes", () => {
    expect(formatCompact(2_500_000)).toBe("25.00 L");
    expect(formatCompact(15_000_000)).toBe("1.50 Cr");
    expect(formatCompact(1500)).toBe("1.5 K");
  });
});

describe("date and text formatting", () => {
  it("formats ISO dates", () => {
    expect(formatDate("2026-10-14")).toBe("14 Oct 2026");
  });

  it("passes through unparseable values", () => {
    expect(formatDate("not-a-date")).toBe("not-a-date");
  });

  it("title-cases snake and spaced values", () => {
    expect(titleCase("NIFTY_50")).toBe("Nifty 50");
    expect(titleCase("relative_strength")).toBe("Relative Strength");
    expect(titleCase(null)).toBe(EMPTY);
  });
});

describe("tone helpers", () => {
  it("maps signed numbers to directional tone", () => {
    expect(signedTone(3)).toBe("bullish");
    expect(signedTone(-1)).toBe("bearish");
    expect(signedTone(0)).toBe("neutral");
    expect(signedTone(null)).toBe("neutral");
  });

  it("maps recommendation signals to tone", () => {
    expect(signalTone("ENTRY")).toBe("bullish");
    expect(signalTone("EXIT")).toBe("bearish");
    expect(signalTone("WATCH")).toBe("warning");
    expect(signalTone("SOMETHING_ELSE")).toBe("neutral");
  });

  it("maps score labels and severities to tone", () => {
    expect(scoreLabelTone("STRONG")).toBe("bullish");
    expect(scoreLabelTone("POOR")).toBe("bearish");
    expect(scoreLabelTone("NEUTRAL")).toBe("neutral");
    expect(severityTone("ERROR")).toBe("bearish");
    expect(severityTone("WARNING")).toBe("warning");
    expect(severityTone("INFO")).toBe("bullish");
  });
});
