import { useEffect, useRef } from "react";

import { getToken } from "@/lib/api";
import { liveWsUrl } from "@/lib/live/ws-url";
import type { RealtimeEvent } from "@/lib/live/types";

const MAX_BACKOFF = 15_000;
/** The close code the backend uses for every refusal on this socket. */
const POLICY_VIOLATION = 1008;

export function useLiveConnection(onEvent: (e: RealtimeEvent) => void, onOpen: () => void): void {
  const onEventRef = useRef(onEvent);
  const onOpenRef = useRef(onOpen);
  onEventRef.current = onEvent;
  onOpenRef.current = onOpen;

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let backoff = 1000;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      if (closed) return;
      // Get the token from the SAME canonical flow the API client uses
      // (in-memory promise + optional localStorage cache), so the socket
      // authenticates even when localStorage is unavailable or not yet
      // populated -- not by racing an independent localStorage read.
      void getToken()
        .then((token) => {
          if (closed) return;
          ws = new WebSocket(liveWsUrl(token));
          ws.onopen = () => {
            backoff = 1000;
            onOpenRef.current();
          };
          ws.onmessage = (ev) => {
            try {
              onEventRef.current(JSON.parse(ev.data as string) as RealtimeEvent);
            } catch {
              /* ignore malformed frame */
            }
          };
          ws.onclose = (ev) => {
            if (closed) return;
            // 1008 is the server saying "not you": the socket carries the
            // activity feed and is gated on `run:view`, which an employee whose
            // authority is a seat does not hold. Retrying that once a second for
            // the length of his working day is a reconnect loop, so a refusal
            // goes straight to the ceiling. Not "never again": the same code is
            // sent for a token this connection could no longer verify, and a
            // session that silently stops receiving events until the tab is
            // reloaded is the worse of the two failures.
            if (ev.code === POLICY_VIOLATION) backoff = MAX_BACKOFF;
            timer = setTimeout(connect, backoff);
            backoff = Math.min(backoff * 2, MAX_BACKOFF);
          };
          ws.onerror = () => ws?.close();
        })
        .catch(() => {
          if (closed) return;
          timer = setTimeout(connect, backoff);
          backoff = Math.min(backoff * 2, MAX_BACKOFF);
        });
    };

    connect();
    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      ws?.close();
    };
  }, []);
}
