import type { Metadata } from "next";
import { Cormorant_Garamond, Inter } from "next/font/google";
import "../styles/globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});

const cormorant = Cormorant_Garamond({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-display",
  display: "swap",
});

export const metadata: Metadata = {
  title: "ALAIN",
  description: "Document intelligence workspace.",
  icons: {
    icon: [{ url: "/alain-logo.svg", type: "image/svg+xml" }],
    shortcut: "/alain-logo.svg",
    apple: "/alain-logo.svg",
  },
};

/** Applies the product's permanent dark theme before the first paint. */
const themeInit = `(function(){try{document.documentElement.classList.add('dark')}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <link rel="icon" href="/alain-logo.svg" type="image/svg+xml" />
        <link rel="shortcut icon" href="/alain-logo.svg" />
        <script dangerouslySetInnerHTML={{ __html: themeInit }} />
      </head>
      <body className={`${inter.variable} ${cormorant.variable} font-sans antialiased`}>{children}</body>
    </html>
  );
}
