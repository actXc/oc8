import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useRef, type ReactNode } from "react";

import { applyEvent, liveQueryKeys } from "@/lib/live/apply-event";
import { useLiveConnection } from "@/lib/live/connection";
import { toastForEvent } from "@/lib/live/toast-for-event";
import type { RealtimeEvent } from "@/lib/live/types";

export function LiveUpdatesProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const seen = useRef<Set<string>>(new Set());

  const onEvent = useCallback(
    (e: RealtimeEvent) => {
      if (seen.current.has(e.id)) return;
      seen.current.add(e.id);
      if (seen.current.size > 500) {
        // bound the dedup set
        seen.current = new Set(Array.from(seen.current).slice(-250));
      }
      applyEvent(qc, e);
      toastForEvent(e);
    },
    [qc],
  );

  const onOpen = useCallback(() => {
    // reconnect-resync: refetch the live queries so any pub/sub gap is closed
    for (const key of liveQueryKeys) {
      qc.invalidateQueries({ queryKey: key });
    }
  }, [qc]);

  useLiveConnection(onEvent, onOpen);
  return <>{children}</>;
}
