// Provider-reported cost reconciliation (Cost Center design, 2026-08-19 spec
// Part C). Opt-in: rows only exist for a tenant once an admin key has been
// connected (see routes/models.tsx) and a refresh has run at least once.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

export interface ReconciliationRow {
  provider: string;
  reportDate: string;
  oc8CalculatedCostMicros: number;
  providerReportedCostMicros: number | null;
  fetchedAt: string;
}

const keys = { reconciliation: ["model-cost-reconciliation"] as const };

export const useReconciliation = () =>
  useQuery({
    queryKey: keys.reconciliation,
    queryFn: () => api.get<ReconciliationRow[]>("/model-cost-reconciliation"),
  });

export function useRefreshReconciliation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<ReconciliationRow[]>("/model-cost-reconciliation/refresh", {}),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.reconciliation }),
  });
}
