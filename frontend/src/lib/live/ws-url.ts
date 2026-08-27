import { API_URL } from "@/lib/api";

export function liveWsUrl(token: string): string {
  const base = API_URL.replace(/^http/, "ws"); // http->ws, https->wss
  return `${base}/events/ws?token=${encodeURIComponent(token)}`;
}
