import { Search } from "lucide-react";
import { useT } from "@/lib/i18n";
import {
  Pagination,
  PaginationContent,
  PaginationItem,
  PaginationNext,
  PaginationPrevious,
} from "@/components/ui/pagination";

export interface ListToolbarConfig {
  searchPlaceholder: string;
  filters?: Array<{ key: string; label: string; options: Array<{ value: string; label: string }> }>;
  groupBy?: Array<{ value: string; label: string }>;
  showArchivedToggle?: boolean;
  archivedToggleLabel?: string;
  restoreActionEnabled?: boolean;
}

export interface ListQueryState {
  search: string;
  filters: Record<string, string>;
  groupBy: string | null;
  includeArchived: boolean;
  page: number;
  pageSize: number;
}

export function ListToolbar({
  config,
  state,
  onStateChange,
  totalCount,
}: {
  config: ListToolbarConfig;
  state: ListQueryState;
  onStateChange: (state: ListQueryState) => void;
  totalCount: number;
}) {
  // Every label a CALLER passes in (search placeholder, filter labels, archive
  // toggle) already arrives translated. The two the toolbar writes itself were
  // hardcoded English, so a German operator saw "No grouping" wedged between
  // translated controls -- exactly the kind of seam this plan exists to close.
  const t = useT();
  const totalPages = Math.max(1, Math.ceil(totalCount / state.pageSize));
  return (
    <div className="flex flex-wrap items-center gap-3 border-b border-border pb-3">
      <div className="relative flex-1 min-w-[200px]">
        <Search className="pointer-events-none absolute left-2 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <input
          type="text"
          placeholder={config.searchPlaceholder}
          value={state.search}
          onChange={(e) => onStateChange({ ...state, search: e.target.value, page: 1 })}
          className="w-full rounded-md border border-border bg-panel py-1.5 pl-8 pr-2 text-sm"
        />
      </div>
      {(config.filters ?? []).map((f) => (
        <select
          key={f.key}
          value={state.filters[f.key] ?? ""}
          onChange={(e) =>
            onStateChange({
              ...state,
              filters: { ...state.filters, [f.key]: e.target.value },
              page: 1,
            })
          }
          className="rounded-md border border-border bg-panel px-2 py-1.5 text-sm"
        >
          <option value="">{f.label}</option>
          {f.options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      ))}
      {config.groupBy && config.groupBy.length > 0 ? (
        <select
          value={state.groupBy ?? ""}
          onChange={(e) => onStateChange({ ...state, groupBy: e.target.value || null, page: 1 })}
          className="rounded-md border border-border bg-panel px-2 py-1.5 text-sm"
        >
          <option value="">{t("No grouping", "Keine Gruppierung")}</option>
          {config.groupBy.map((g) => (
            <option key={g.value} value={g.value}>
              {t(`Group by ${g.label}`, `Gruppieren nach ${g.label}`)}
            </option>
          ))}
        </select>
      ) : null}
      {config.showArchivedToggle ? (
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          <input
            type="checkbox"
            checked={state.includeArchived}
            onChange={(e) =>
              onStateChange({ ...state, includeArchived: e.target.checked, page: 1 })
            }
          />
          {config.archivedToggleLabel ?? "Show archived"}
        </label>
      ) : null}
      {totalPages > 1 ? (
        <Pagination className="ml-auto w-auto">
          <PaginationContent>
            <PaginationItem>
              <PaginationPrevious
                href="#"
                onClick={(e) => {
                  e.preventDefault();
                  if (state.page > 1) onStateChange({ ...state, page: state.page - 1 });
                }}
              />
            </PaginationItem>
            <PaginationItem>
              <span className="px-2 text-sm text-muted-foreground">
                {state.page} / {totalPages}
              </span>
            </PaginationItem>
            <PaginationItem>
              <PaginationNext
                href="#"
                onClick={(e) => {
                  e.preventDefault();
                  if (state.page < totalPages) onStateChange({ ...state, page: state.page + 1 });
                }}
              />
            </PaginationItem>
          </PaginationContent>
        </Pagination>
      ) : null}
    </div>
  );
}

export function groupItems<T>(
  items: T[],
  groupBy: string | null,
  groupKeyOf: (item: T) => string,
): Array<{ group: string | null; items: T[] }> {
  if (groupBy === null) {
    return [{ group: null, items }];
  }
  const order: string[] = [];
  const buckets = new Map<string, T[]>();
  for (const item of items) {
    const key = groupKeyOf(item);
    if (!buckets.has(key)) {
      buckets.set(key, []);
      order.push(key);
    }
    buckets.get(key)!.push(item);
  }
  return order.map((group) => ({ group, items: buckets.get(group)! }));
}
