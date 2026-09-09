// Same shape and spirit as RUN_COMPONENT_REGISTRY (run-record-card.tsx): a
// short, compiled lookup table, not a plugin system. A WidgetInstance whose
// `type` is absent from this table (a future/removed type) is handled by
// DashboardGrid's own fallback -- this registry itself only ever holds the
// five current types.
import { Activity, CheckCircle2, MessageSquare, PieChart, Wallet } from "lucide-react";
import type { ReactElement } from "react";
import { ActivityWidget } from "@/components/dashboard/widgets/activity-widget";
import { ApprovalsWidget } from "@/components/dashboard/widgets/approvals-widget";
import { BudgetWidget } from "@/components/dashboard/widgets/budget-widget";
import { ChatWidget } from "@/components/dashboard/widgets/chat-widget";
import { ReportsWidget } from "@/components/dashboard/widgets/reports-widget";
import type { WidgetType } from "@/lib/hooks";

type WidgetComponentProps = {
  config: Record<string, unknown>;
  onConfigChange: (config: Record<string, unknown>) => void;
};

export const WIDGET_REGISTRY: Record<
  WidgetType,
  {
    label: (de: boolean) => string;
    component: (props: WidgetComponentProps) => ReactElement;
    defaultSize: { w: number; h: number };
    /** Small glyph shown in the layout sketch and widget picker -- purely
     * decorative, keep separate from `component` so the preview can render
     * without mounting a widget's (potentially data-fetching) full body. */
    icon: typeof MessageSquare;
  }
> = {
  chat: {
    label: (de) => (de ? "Chat" : "Chat"),
    component: ChatWidget,
    defaultSize: { w: 6, h: 6 },
    icon: MessageSquare,
  },
  approvals: {
    label: (de) => (de ? "Freigaben" : "Approvals"),
    component: ApprovalsWidget,
    defaultSize: { w: 4, h: 5 },
    icon: CheckCircle2,
  },
  reports: {
    label: (de) => (de ? "Reports" : "Reports"),
    component: ReportsWidget,
    defaultSize: { w: 4, h: 4 },
    icon: PieChart,
  },
  budget: {
    label: (de) => (de ? "Budget" : "Budget"),
    component: BudgetWidget,
    defaultSize: { w: 3, h: 3 },
    icon: Wallet,
  },
  activity: {
    label: (de) => (de ? "Aktivität" : "Activity"),
    component: ActivityWidget,
    defaultSize: { w: 3, h: 4 },
    icon: Activity,
  },
};
