export interface RealtimeEvent {
  id: string;
  type: string;
  tenantid: string;
  time: string;
  source: string;
  data: Record<string, unknown>;
}
