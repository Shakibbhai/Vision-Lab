import type { Metadata } from "next";

import { ControlLayout } from "@/components/control-layout";

import "./globals.css";

export const metadata: Metadata = {
  title: "PersonVisionAi",
  description: "Person detection and re-identification console",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="font-[var(--font-body)]">
        <ControlLayout>{children}</ControlLayout>
      </body>
    </html>
  );
}
