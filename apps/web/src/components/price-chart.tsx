"use client";

import { useEffect, useRef } from "react";
import type { IChartApi, Time } from "lightweight-charts";

import type { PriceBar } from "@/lib/types";

/**
 * Price chart for the stock detail page.
 *
 * lightweight-charts touches the DOM and cannot run during SSR, so the library
 * is imported inside the effect and the chart is created against a plain div.
 * The whole chart is rebuilt whenever the data changes — simpler than diffing
 * series and cheap at this data volume.
 *
 * Bars with incomplete OHLC are dropped (candlesticks need all four prices); if
 * too few remain we fall back to a close-only area line.
 */
export function PriceChart({
  bars,
  height = 360,
}: {
  bars: PriceBar[];
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let disposed = false;
    let observer: ResizeObserver | null = null;

    void import("lightweight-charts").then(
      ({ createChart, ColorType, CandlestickSeries, AreaSeries }) => {
        if (disposed) return;

        const chart = createChart(container, {
          height,
          layout: {
            background: { type: ColorType.Solid, color: "transparent" },
            textColor: "hsl(215 20% 65%)",
            fontFamily: "inherit",
          },
          grid: {
            vertLines: { color: "hsl(217 33% 16%)" },
            horzLines: { color: "hsl(217 33% 16%)" },
          },
          rightPriceScale: { borderColor: "hsl(217 33% 20%)" },
          timeScale: { borderColor: "hsl(217 33% 20%)" },
          crosshair: { mode: 0 },
        });
        chartRef.current = chart;
        chart.applyOptions({ width: container.clientWidth });

        const complete = bars
          .filter(
            (bar) =>
              bar.open !== null &&
              bar.high !== null &&
              bar.low !== null &&
              typeof bar.close === "number",
          )
          .map((bar) => ({
            time: bar.trade_date as Time,
            open: bar.open as number,
            high: bar.high as number,
            low: bar.low as number,
            close: bar.close,
          }));

        if (complete.length >= 2) {
          const series = chart.addSeries(CandlestickSeries, {
            upColor: "hsl(142 71% 48%)",
            downColor: "hsl(0 72% 58%)",
            borderVisible: false,
            wickUpColor: "hsl(142 71% 48%)",
            wickDownColor: "hsl(0 72% 58%)",
          });
          series.setData(complete);
        } else {
          const series = chart.addSeries(AreaSeries, {
            lineColor: "hsl(160 84% 39%)",
            topColor: "hsl(160 84% 39% / 0.35)",
            bottomColor: "hsl(160 84% 39% / 0.02)",
          });
          series.setData(
            bars
              .filter((bar) => typeof bar.close === "number")
              .map((bar) => ({ time: bar.trade_date as Time, value: bar.close })),
          );
        }
        chart.timeScale().fitContent();

        const observerInstance = new ResizeObserver(() => {
          chart.applyOptions({ width: container.clientWidth });
        });
        observerInstance.observe(container);
        observer = observerInstance;
      },
    );

    return () => {
      disposed = true;
      observer?.disconnect();
      chartRef.current?.remove();
      chartRef.current = null;
    };
  }, [bars, height]);

  return (
    <div
      ref={containerRef}
      data-testid="price-chart"
      className="w-full"
      style={{ height }}
    />
  );
}
