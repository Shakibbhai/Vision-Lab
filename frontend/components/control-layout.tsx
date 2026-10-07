"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutDashboard, ScanFace, Settings2 } from "lucide-react";

import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { href: "/dashboard", label: "Analytics Dashboard", icon: LayoutDashboard },
  { href: "/dashboard/faces", label: "Face Manager", icon: ScanFace },
  { href: "/configurations", label: "Configurations", icon: Settings2 },
];

function activeNav(pathname: string, href: string): boolean {
  if (href === "/dashboard") {
    return pathname === href;
  }

  return pathname === href || pathname.startsWith(`${href}/`);
}



export function ControlLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20">
        <div className="mx-auto w-full max-w-[1500px] p-3 pb-0 md:p-4 md:pb-0">
          <div className="dashboard-panel rounded-2xl p-3">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
              <div>
                <h1 className="font-[var(--font-headline)] text-xl leading-none">VisionLab</h1>
                <p className="mt-1 text-xs text-muted-foreground">Structured multi-page operations console.</p>
              </div>

              <nav className="flex flex-wrap gap-2">
                {NAV_ITEMS.map((item) => {
                  const active = activeNav(pathname, item.href);
                  const Icon = item.icon;
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      className={cn(
                        "inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-xs font-semibold transition",
                        active
                          ? "border-primary bg-primary text-primary-foreground"
                          : "border-border bg-white/85 text-foreground hover:bg-accent",
                      )}
                    >
                      <Icon className="h-3.5 w-3.5" />
                      <span className="truncate">{item.label}</span>
                    </Link>
                  );
                })}
              </nav>
            </div>
          </div>
        </div>
      </header>

      <div className="mx-auto w-full max-w-[1500px] p-3 md:p-4">
        <main className="grid content-start gap-3">

          {children}
        </main>
      </div>
    </div>
  );
}
