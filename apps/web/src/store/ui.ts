"use client";

import { create } from "zustand";

/**
 * Cross-page UI state.
 *
 * Only ephemeral presentation state lives here — server data is owned by
 * TanStack Query. The selected index is shared so switching it on the market
 * page is reflected when the user moves on to the screener or alerts.
 */
export interface UiState {
  sidebarOpen: boolean;
  indexCode: string;
  toggleSidebar: () => void;
  setSidebarOpen: (open: boolean) => void;
  setIndexCode: (code: string) => void;
}

export const INDEX_OPTIONS = [
  { code: "NIFTY_50", label: "Nifty 50" },
  { code: "NIFTY_MIDCAP_150", label: "Nifty Midcap 150" },
  { code: "NIFTY_SMALLCAP_250", label: "Nifty Smallcap 250" },
] as const;

export const useUiStore = create<UiState>((set) => ({
  sidebarOpen: false,
  indexCode: "NIFTY_50",
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
  setSidebarOpen: (open) => set({ sidebarOpen: open }),
  setIndexCode: (code) => set({ indexCode: code }),
}));
