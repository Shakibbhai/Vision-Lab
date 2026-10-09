// Session cookie signed with HMAC-SHA256 (Web Crypto, so it runs in middleware and route handlers alike).
// Credentials come from AUTH_USERNAME / AUTH_PASSWORD; AUTH_SECRET signs the cookie.

export const SESSION_COOKIE = "pv_session";
export const SESSION_MAX_AGE = 60 * 60 * 24 * 7; // 7 days

const encoder = new TextEncoder();

export function authConfig() {
  const username = process.env.AUTH_USERNAME || "admin";
  const password = process.env.AUTH_PASSWORD || "admin123";
  // Without an explicit secret, derive one from the password so changing the password logs everyone out
  const secret = process.env.AUTH_SECRET || `pv-session:${username}:${password}`;
  return { username, password, secret };
}

function toBase64Url(bytes: ArrayBuffer): string {
  let binary = "";
  new Uint8Array(bytes).forEach((b) => {
    binary += String.fromCharCode(b);
  });
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function hmac(secret: string, payload: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, [
    "sign",
  ]);
  return toBase64Url(await crypto.subtle.sign("HMAC", key, encoder.encode(payload)));
}

function safeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export async function createSession(username: string, secret: string): Promise<string> {
  const expires = Math.floor(Date.now() / 1000) + SESSION_MAX_AGE;
  const payload = `${encodeURIComponent(username)}.${expires}`;
  return `${payload}.${await hmac(secret, payload)}`;
}

/** Returns the username for a valid, unexpired session token, otherwise null. */
export async function readSession(token: string | undefined, secret: string): Promise<string | null> {
  if (!token) return null;
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  const [user, expires, signature] = parts;
  if (!safeEqual(signature, await hmac(secret, `${user}.${expires}`))) return null;
  if (Number(expires) < Math.floor(Date.now() / 1000)) return null;
  return decodeURIComponent(user);
}

export function credentialsMatch(username: string, password: string): boolean {
  const config = authConfig();
  return safeEqual(username, config.username) && safeEqual(password, config.password);
}
