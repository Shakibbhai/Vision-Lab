import { NextResponse } from "next/server";

import { SESSION_COOKIE, SESSION_MAX_AGE, authConfig, createSession, credentialsMatch } from "@/lib/auth";

export async function POST(req: Request) {
  let body: { username?: string; password?: string } = {};
  try {
    body = await req.json();
  } catch {
    // Treated as empty credentials below
  }
  const username = (body.username ?? "").trim();
  const password = body.password ?? "";
  if (!credentialsMatch(username, password)) {
    return NextResponse.json({ detail: "Invalid username or password" }, { status: 401 });
  }

  const response = NextResponse.json({ username });
  response.cookies.set(SESSION_COOKIE, await createSession(username, authConfig().secret), {
    httpOnly: true,
    sameSite: "lax",
    secure: new URL(req.url).protocol === "https:",
    path: "/",
    maxAge: SESSION_MAX_AGE,
  });
  return response;
}
