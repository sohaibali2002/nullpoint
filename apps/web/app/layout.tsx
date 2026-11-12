import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";

import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono", display: "swap" });

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "https://nullpoint.research"),
  title: {
    default: "Nullpoint — nothing ships until it beats the null",
    template: "%s · Nullpoint",
  },
  description:
    "Multi-agent autonomous quant research. Ten AI agents write, backtest and falsify systematic strategies; only what survives anti-overfitting validation reaches the leaderboard. Research only.",
  applicationName: "Nullpoint",
  icons: { icon: "/brand/favicon.svg" },
  openGraph: {
    title: "Nullpoint — nothing ships until it beats the null",
    description:
      "Ten agents. One falsification gauntlet. Zero live-trading hooks. Systematic research that has to survive the null.",
    images: ["/brand/og.svg"],
    type: "website",
  },
};

export const viewport: Viewport = {
  themeColor: "#07080A",
  colorScheme: "dark",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${mono.variable}`}>
      <body className="min-h-screen font-sans antialiased">{children}</body>
    </html>
  );
}
