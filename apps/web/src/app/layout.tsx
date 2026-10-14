import type { Metadata } from "next";

import { Providers } from "@/components/providers";
import { Sidebar } from "@/components/sidebar";
import { Topbar } from "@/components/topbar";

import "./globals.css";

export const metadata: Metadata = {
  title: "Nifty Market Intelligence",
  description:
    "Market overview, screening and explainable recommendations for the Indian equity market.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <Providers>
          <div className="md:grid md:grid-cols-[15rem_1fr]">
            <Sidebar />
            <div className="flex min-h-screen flex-col">
              <Topbar />
              <main className="flex-1 p-4 md:p-6">{children}</main>
            </div>
          </div>
        </Providers>
      </body>
    </html>
  );
}
