import type { Metadata, Viewport } from "next";
import { IBM_Plex_Mono, Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({
  variable: "--lw-font",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
});

const plexMono = IBM_Plex_Mono({
  variable: "--lw-mono",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Laneway — weather-aware walking routes for Melbourne",
  description:
    "Melbourne has a second pedestrian network of arcades, subways and building " +
    "pass-throughs that mapping apps ignore. Laneway routes you through it, " +
    "weighted by what the weather is actually doing.",
};

export const viewport: Viewport = {
  themeColor: "#070a10",
  colorScheme: "dark",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${inter.variable} ${plexMono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
