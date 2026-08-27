"use client";

import { useEffect, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Search } from "lucide-react";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
} from "@/components/ui/command";
import { cn } from "@/lib/utils";
import { useT } from "@/lib/i18n";
import {
  useAgents,
  useDepartments,
  useIntegrations,
  useKnowledgeBases,
  useActivity,
} from "@/lib/hooks";
import type { Agent, Department, Integration, KnowledgeBase, ActivityItem } from "@/lib/mock-data";

export function GlobalSearch() {
  const t = useT();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");

  // Fetch data - these are already cached app-wide by React Query. Global
  // search needs the whole tenant to search over, not a paginated list view.
  const { data: agentsPage } = useAgents({ pageSize: 200 });
  const { data: departmentsPage } = useDepartments({ pageSize: 200 });
  const { data: integrations = [] } = useIntegrations();
  const { data: knowledgeBasesPage } = useKnowledgeBases({ pageSize: 200 });
  const { data: activities = [] } = useActivity({ limit: 100 });
  const agents = agentsPage?.items ?? [];
  const departments = departmentsPage?.items ?? [];
  const knowledgeBases = knowledgeBasesPage?.items ?? [];

  // Filter logic: case-insensitive substring match
  const filterItems = <T extends { name: string; id: string }>(
    items: T[],
    query: string,
    limit = 5,
  ): T[] => {
    if (!query) return items.slice(0, limit);
    const q = query.toLowerCase();
    return items.filter((item) => item.name.toLowerCase().includes(q)).slice(0, limit);
  };

  const filteredAgents = filterItems(agents, search);
  const filteredDepartments = filterItems(departments, search);
  const filteredIntegrations = filterItems(integrations, search);
  const filteredBases = filterItems(knowledgeBases, search);

  // For activities/runs, filter by message or detail
  const filteredActivities = (() => {
    if (!search) return activities.slice(0, 5);
    const q = search.toLowerCase();
    return activities
      .filter(
        (item) =>
          item.message.toLowerCase().includes(q) ||
          item.detail?.toLowerCase().includes(q),
      )
      .slice(0, 5);
  })();

  // Set up global keyboard shortcut
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      // Cmd+K or Ctrl+K
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setOpen((prev) => !prev);
      }
    };

    document.addEventListener("keydown", down);
    return () => document.removeEventListener("keydown", down);
  }, []);

  const hasResults =
    filteredAgents.length > 0 ||
    filteredDepartments.length > 0 ||
    filteredIntegrations.length > 0 ||
    filteredBases.length > 0 ||
    filteredActivities.length > 0;

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className={cn(
          "group hidden items-center gap-2 rounded-md border border-border bg-panel px-3 py-2 text-sm text-muted-foreground transition hover:text-foreground sm:inline-flex",
        )}
        aria-label={t("Search", "Suchen")}
      >
        <Search className="h-4 w-4" />
        <span>{t("Search…", "Suchen…")}</span>
        <CommandShortcut className="ml-auto">⌘K</CommandShortcut>
      </button>

      <CommandDialog open={open} onOpenChange={setOpen}>
        <CommandInput
          placeholder={t("Search agents, departments, runs…", "Nach Agenten, Abteilungen, Läufen suchen…")}
          value={search}
          onValueChange={setSearch}
        />
        <CommandList>
          {!hasResults && <CommandEmpty>{t("No results", "Keine Ergebnisse")}</CommandEmpty>}

          {filteredAgents.length > 0 && (
            <CommandGroup title={t("Agents", "Agenten")}>
              {filteredAgents.map((agent) => (
                <CommandItem
                  key={agent.id}
                  onSelect={() => {
                    navigate({ to: `/agents/${agent.id}` });
                    setOpen(false);
                  }}
                >
                  <span>{agent.name}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          )}

          {filteredDepartments.length > 0 && (
            <CommandGroup title={t("Departments", "Abteilungen")}>
              {filteredDepartments.map((dept) => (
                <CommandItem
                  key={dept.id}
                  onSelect={() => {
                    navigate({ to: `/departments/${dept.id}` });
                    setOpen(false);
                  }}
                >
                  <span>{dept.name}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          )}

          {filteredIntegrations.length > 0 && (
            <CommandGroup title={t("Capas", "Capas")}>
              {filteredIntegrations.map((integration) => (
                <CommandItem
                  key={integration.id}
                  onSelect={() => {
                    navigate({ to: "/capas" });
                    setOpen(false);
                  }}
                >
                  <span>{integration.name}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          )}

          {filteredBases.length > 0 && (
            <CommandGroup title={t("Knowledge", "Wissen")}>
              {filteredBases.map((base) => (
                <CommandItem
                  key={base.id}
                  onSelect={() => {
                    navigate({ to: "/knowledge" });
                    setOpen(false);
                  }}
                >
                  <span>{base.name}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          )}

          {filteredActivities.length > 0 && (
            <CommandGroup title={t("Runs", "Läufe")}>
              {filteredActivities.map((activity, idx) => (
                <CommandItem
                  key={`activity-${idx}`}
                  onSelect={() => {
                    navigate({ to: "/activity" });
                    setOpen(false);
                  }}
                >
                  <span className="truncate">{activity.message}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          )}
        </CommandList>
      </CommandDialog>
    </>
  );
}
