import { NextResponse, type NextRequest } from "next/server";

import { SESSION_COOKIE, authConfig, readSession } from "@/lib/auth";

// Every page and the backend proxy require a session; the login page and auth API do not.
export async function middleware(req: NextRequest) {
  const user = await readSession(req.cookies.get(SESSION_COOKIE)?.value, authConfig().secret);
  if (user) return NextResponse.next();

  if (req.nextUrl.pathname.startsWith("/api/")) {
    return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
  }
  // Relative Location: behind the proxy req.url carries the internal host (localhost:13000)
  const next = `${req.nextUrl.pathname}${req.nextUrl.search}`;
  const location = next === "/" ? "/login" : `/login?next=${encodeURIComponent(next)}`;
  return new NextResponse(null, { status: 307, headers: { Location: location } });
}

export const config = {
  matcher: ["/((?!login|api/auth|_next/static|_next/image|favicon.ico).*)"],
};
