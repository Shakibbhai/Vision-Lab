import { NextResponse, type NextRequest } from "next/server";

import { SESSION_COOKIE, authConfig, readSession } from "@/lib/auth";

// Every page and the backend proxy require a session; the login page and auth API do not.
export async function middleware(req: NextRequest) {
  const user = await readSession(req.cookies.get(SESSION_COOKIE)?.value, authConfig().secret);
  if (user) return NextResponse.next();

  if (req.nextUrl.pathname.startsWith("/api/")) {
    return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
  }
  const login = new URL("/login", publicOrigin(req));
  const next = `${req.nextUrl.pathname}${req.nextUrl.search}`;
  if (next !== "/") login.searchParams.set("next", next);
  return NextResponse.redirect(login);
}

// Behind the proxy req.url carries the internal host (localhost:13000); the proxy passes the public one along
function publicOrigin(req: NextRequest): string {
  const host = req.headers.get("x-forwarded-host")?.split(",")[0].trim();
  const proto = req.headers.get("x-forwarded-proto")?.split(",")[0].trim();
  if (!host || !/^[A-Za-z0-9.\-\[\]:]+$/.test(host)) return req.url;
  return `${proto === "https" ? "https" : "http"}://${host}`;
}

export const config = {
  matcher: ["/((?!login|api/auth|_next/static|_next/image|favicon.ico).*)"],
};
