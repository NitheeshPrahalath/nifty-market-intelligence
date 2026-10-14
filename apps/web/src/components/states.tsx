import { AlertTriangle, Inbox } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

export function LoadingState({
  rows = 6,
  className,
}: {
  rows?: number;
  className?: string;
}) {
  return (
    <div className={cn("space-y-2", className)} aria-busy="true">
      {Array.from({ length: rows }).map((_, index) => (
        <Skeleton key={index} className="h-9 w-full" />
      ))}
    </div>
  );
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 404) return "No data found for this request.";
    if (error.status === 401 || error.status === 403) {
      return "The API rejected this request (authentication required).";
    }
    if (error.status === 429) return "Rate limit exceeded — please slow down.";
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return "Unexpected error";
}

export function ErrorState({
  error,
  className,
}: {
  error: unknown;
  className?: string;
}) {
  return (
    <div
      role="alert"
      className={cn(
        "flex items-center gap-3 rounded-lg border border-bearish/40 bg-bearish/10 p-4 text-sm",
        className,
      )}
    >
      <AlertTriangle className="h-5 w-5 shrink-0 text-bearish" />
      <div>
        <p className="font-medium text-foreground">Could not load data</p>
        <p className="text-muted-foreground">{errorMessage(error)}</p>
      </div>
    </div>
  );
}

export function EmptyState({
  title = "Nothing to show",
  description,
  className,
}: {
  title?: string;
  description?: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border p-10 text-center",
        className,
      )}
    >
      <Inbox className="h-6 w-6 text-muted-foreground" />
      <p className="text-sm font-medium">{title}</p>
      {description ? (
        <p className="text-xs text-muted-foreground">{description}</p>
      ) : null}
    </div>
  );
}
