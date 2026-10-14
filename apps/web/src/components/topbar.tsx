"use client";

import { Menu } from "lucide-react";

import { ApiStatus } from "@/components/api-status";
import { IndexSelector } from "@/components/index-selector";
import { useUiStore } from "@/store/ui";

export function Topbar() {
  const setSidebarOpen = useUiStore((state) => state.setSidebarOpen);

  return (
    <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-border bg-background/95 px-4 backdrop-blur">
      <button
        type="button"
        aria-label="Open navigation"
        className="md:hidden"
        onClick={() => setSidebarOpen(true)}
      >
        <Menu className="h-5 w-5" />
      </button>
      <div className="flex-1" />
      <ApiStatus />
      <IndexSelector className="w-44" />
    </header>
  );
}
