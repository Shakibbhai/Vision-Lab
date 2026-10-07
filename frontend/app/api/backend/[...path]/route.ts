import type { NextRequest } from "next/server";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";
export const dynamic = "force-dynamic";
export const revalidate = 0;

type RouteContext = {
  params: {
    path: string[];
  };
};

function buildTargetUrl(req: NextRequest, pathParts: string[]): string {
  const normalizedBase = API_BASE.replace(/\/$/, "");
  const path = pathParts.join("/");
  const hasTrailingSlash = req.nextUrl.pathname.endsWith("/");
  const targetPath = hasTrailingSlash && path ? `${path}/` : path;
  const query = req.nextUrl.search ? req.nextUrl.search : "";
  return `${normalizedBase}/${targetPath}${query}`;
}

async function forward(req: NextRequest, pathParts: string[]): Promise<Response> {
  const method = req.method.toUpperCase();
  const headers = new Headers(req.headers);
  headers.delete("host");
  headers.delete("content-length");

  const init: RequestInit = {
    method,
    headers,
    redirect: "manual",
    cache: "no-store",
  };

  if (method !== "GET" && method !== "HEAD") {
    init.body = await req.arrayBuffer();
  }

  try {
    const upstream = await fetch(buildTargetUrl(req, pathParts), init);
    const responseHeaders = new Headers(upstream.headers);
    const location = responseHeaders.get("location");
    if (location) {
      try {
        const apiBaseUrl = new URL(API_BASE);
        const locationUrl = new URL(location, apiBaseUrl);
        if (location.startsWith("/") || locationUrl.host === apiBaseUrl.host) {
          responseHeaders.set(
            "location",
            `/api/backend${locationUrl.pathname}${locationUrl.search}${locationUrl.hash}`,
          );
        }
      } catch {
        if (location.startsWith("/")) {
          responseHeaders.set("location", `/api/backend${location}`);
        }
      }
    }
    responseHeaders.set("x-proxied-by", "next-backend-proxy");
    responseHeaders.set("cache-control", "no-store, no-cache, must-revalidate");
    responseHeaders.set("pragma", "no-cache");
    responseHeaders.set("expires", "0");
    return new Response(upstream.body, {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "Unknown proxy error";
    return Response.json({ detail }, { status: 502 });
  }
}

export async function GET(req: NextRequest, ctx: RouteContext): Promise<Response> {
  return forward(req, ctx.params.path);
}

export async function HEAD(req: NextRequest, ctx: RouteContext): Promise<Response> {
  return forward(req, ctx.params.path);
}

export async function POST(req: NextRequest, ctx: RouteContext): Promise<Response> {
  return forward(req, ctx.params.path);
}

export async function PUT(req: NextRequest, ctx: RouteContext): Promise<Response> {
  return forward(req, ctx.params.path);
}

export async function PATCH(req: NextRequest, ctx: RouteContext): Promise<Response> {
  return forward(req, ctx.params.path);
}

export async function DELETE(req: NextRequest, ctx: RouteContext): Promise<Response> {
  return forward(req, ctx.params.path);
}
