import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { SESSION_COOKIE, authConfig, readSession } from "@/lib/auth";

export const dynamic = "force-dynamic";

export async function GET() {
  const username = await readSession(cookies().get(SESSION_COOKIE)?.value, authConfig().secret);
  if (!username) return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
  return NextResponse.json({ username });
}
