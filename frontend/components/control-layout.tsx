"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  BarChart3,
  LayoutDashboard,
  LogOut,
  Menu,
  MonitorPlay,
  ScanFace,
  ShieldCheck,
  SlidersHorizontal,
  UserCircle2,
  X,
} from "lucide-react";

import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { href: "/overview", label: "Dashboard", icon: LayoutDashboard, title: "Dashboard", subtitle: "Detection and re-identification at a glance." },
  { href: "/dashboard", label: "Live Monitoring", icon: MonitorPlay, title: "Live Monitoring", subtitle: "Live camera feeds with detection and Re-ID overlays." },
  { href: "/analytics", label: "Analytics", icon: BarChart3, title: "Analytics", subtitle: "Who was seen, where, and for how long." },
  { href: "/configurations", label: "Sources & Zones", icon: SlidersHorizontal, title: "Sources & Zones", subtitle: "Add cameras or videos, draw zones and choose analytics." },
  { href: "/dashboard/faces", label: "Face Manager", icon: ScanFace, title: "Face Manager", subtitle: "Enroll people for face recognition." },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/dashboard") return pathname === "/dashboard";
  return pathname === href || pathname.startsWith(`${href}/`);
}

function useBackendStatus(enabled: boolean, pathname: string) {
  const [online, setOnline] = useState<boolean | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let active = true;
    const check = async () => {
      try {
        const response = await fetch("/api/backend/health", { cache: "no-store" });
        if (active) setOnline(response.ok);
      } catch {
        if (active) setOnline(false);
      }
    };
    void check();
    const timer = setInterval(check, 30000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [enabled, pathname]);
  return online;
}

function useCurrentUser() {
  const [username, setUsername] = useState<string | null>(null);
  useEffect(() => {
    fetch("/api/auth/me", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((body) => setUsername(body?.username ?? null))
      .catch(() => setUsername(null));
  }, []);
  return username;
}

function Sidebar({ pathname, onNavigate }: { pathname: string; onNavigate?: () => void }) {
  const router = useRouter();
  const username = useCurrentUser();
  const [leaving, setLeaving] = useState(false);

  async function logout() {
    setLeaving(true);
    await fetch("/api/auth/logout", { method: "POST" }).catch(() => undefined);
    router.replace("/login");
    router.refresh();
  }

  return (
    <div className="flex h-full flex-col bg-slate-900 text-slate-300">
      <div className="flex items-center gap-3 border-b border-white/10 px-5 py-5">
        <span className="grid h-10 w-10 place-items-center rounded-xl bg-primary text-white">
          <ShieldCheck className="h-5 w-5" />
        </span>
        <div className="min-w-0">
          <p className="truncate text-base font-semibold text-white">PersonVisionAi</p>
          <p className="truncate text-xs text-slate-400">Person Detection &amp; Re-ID</p>
        </div>
      </div>

      <nav className="flex-1 space-y-1 px-3 py-4" aria-label="Main">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          const active = isActive(pathname, item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              onClick={onNavigate}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition",
                active ? "bg-primary text-white shadow-sm" : "hover:bg-white/5 hover:text-white",
              )}
            >
              <Icon className="h-[18px] w-[18px] shrink-0" />
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-white/10 p-3">
        <div className="flex items-center gap-3 rounded-lg px-2 py-2">
          <UserCircle2 className="h-8 w-8 shrink-0 text-slate-400" />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-white">{username ?? "…"}</p>
            <p className="text-xs text-slate-500">Operator</p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => void logout()}
          disabled={leaving}
          className="mt-1 flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition hover:bg-red-500/10 hover:text-red-300 disabled:opacity-60"
        >
          <LogOut className="h-[18px] w-[18px]" />
          {leaving ? "Signing out..." : "Logout"}
        </button>
      </div>
    </div>
  );
}

export function ControlLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const onLoginPage = pathname === "/login" || pathname.startsWith("/login/");
  const online = useBackendStatus(!onLoginPage, pathname);
  const [menuOpen, setMenuOpen] = useState(false);

  if (onLoginPage) {
    return <>{children}</>;
  }

  const current = NAV_ITEMS.find((item) => isActive(pathname, item.href));

  return (
    <div className="min-h-screen bg-slate-50">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 lg:block">
        <Sidebar pathname={pathname} />
      </aside>

      {menuOpen ? (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button type="button" aria-label="Close menu" className="absolute inset-0 bg-slate-900/50" onClick={() => setMenuOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-64 shadow-xl">
            <Sidebar pathname={pathname} onNavigate={() => setMenuOpen(false)} />
          </aside>
        </div>
      ) : null}

      <div className="lg:pl-64">
        <header
          className="sticky z-20 border-b border-slate-200 bg-white/90 backdrop-blur"
          style={{ top: "env(safe-area-inset-top, 0px)" }}
        >
          <div className="flex items-center gap-3 px-4 py-3 md:px-6">
            <button
              type="button"
              aria-label="Open menu"
              onClick={() => setMenuOpen(true)}
              className="grid h-9 w-9 place-items-center rounded-lg border border-slate-200 text-slate-600 lg:hidden"
            >
              {menuOpen ? <X className="h-4 w-4" /> : <Menu className="h-4 w-4" />}
            </button>
            <div className="min-w-0 flex-1">
              <h1 className="truncate text-lg font-semibold text-slate-900 md:text-xl">{current?.title ?? "PersonVisionAi"}</h1>
              {current ? <p className="hidden truncate text-sm text-slate-500 sm:block">{current.subtitle}</p> : null}
            </div>
            <span
              className={cn(
                "inline-flex shrink-0 items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold",
                online === false ? "bg-red-50 text-red-700" : "bg-emerald-50 text-emerald-700",
              )}
            >
              <span className={cn("h-2 w-2 rounded-full", online === false ? "bg-red-500" : "bg-emerald-500")} />
              {online === false ? "Backend Offline" : "System Online"}
            </span>
          </div>
        </header>

        <main className="mx-auto grid w-full max-w-[1500px] content-start gap-4 p-4 md:p-6">{children}</main>
      </div>
    </div>
  );
}
