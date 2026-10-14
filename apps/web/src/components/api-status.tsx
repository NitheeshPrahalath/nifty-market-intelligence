"use client";

import { useHealth } from "@/lib/queries";
import { cn } from "@/lib/utils";

export function ApiStatus() {
  const { data, isError, isLoading } = useHealth();

  const healthy = !isError && data?.status === "ok";
  const label = isLoading
    ? "Checking API…"
    : healthy
      ? `API ${data?.version ?? ""} · ${data?.database ?? ""}`.trim()
      : "API unavailable";

  return (
    <div className="flex items-center gap-2 text-xs text-muted-foreground">
      <span
        aria-hidden
        className={cn(
          "h-2 w-2 rounded-full",
          isLoading
            ? "bg-warning"
            : healthy
              ? "bg-bullish"
              : "bg-bearish",
        )}
      />
      <span className="hidden truncate sm:inline">{label}</span>
    </div>
  );
}
