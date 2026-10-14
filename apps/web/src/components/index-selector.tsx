"use client";

import { Select } from "@/components/ui/input";
import { INDEX_OPTIONS, useUiStore } from "@/store/ui";

export function IndexSelector({ className }: { className?: string }) {
  const indexCode = useUiStore((state) => state.indexCode);
  const setIndexCode = useUiStore((state) => state.setIndexCode);

  return (
    <Select
      aria-label="Index"
      value={indexCode}
      onChange={(event) => setIndexCode(event.target.value)}
      className={className}
    >
      {INDEX_OPTIONS.map((option) => (
        <option key={option.code} value={option.code}>
          {option.label}
        </option>
      ))}
    </Select>
  );
}
