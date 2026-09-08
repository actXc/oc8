import { ReportsSection } from "@/components/reports-section";

export function ReportsWidget(_props: {
  config: Record<string, unknown>;
  onConfigChange: (c: Record<string, unknown>) => void;
}) {
  return (
    <div data-testid="reports-widget-scroll" className="h-full overflow-y-auto p-2">
      <ReportsSection />
    </div>
  );
}
