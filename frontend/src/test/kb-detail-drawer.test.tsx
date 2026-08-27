import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// Task 12: KbDetailDrawer used to be a hand-rolled `fixed inset-0 ... flex
// justify-end` div; it now goes through the shared <DetailSheet> (Task 10),
// which renders Radix Sheet content with role="dialog". Chrome-only
// migration -- no edit-capability amendment here.

// Content-view plan (Task 4): the Pipeline section's header now always
// renders a "Browse content ->" <Link>, unconditionally (unlike the
// Governance section's linkedDepartments/linkedAgents Links, which this
// test's empty-array fixture data already kept from rendering). `<Link>`
// from @tanstack/react-router throws without a <RouterProvider> in the tree,
// which this lightweight render deliberately doesn't set up -- stub it as a
// plain anchor, same as `agents-list-toolbar.test.tsx`.
vi.mock("@tanstack/react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-router")>();
  return {
    ...actual,
    Link: ({
      children,
      className,
    }: {
      children?: ReactNode;
      className?: string;
      to?: string;
      params?: unknown;
    }) => <a className={className}>{children}</a>,
  };
});

vi.mock("@/lib/hooks", () => ({
  // `Page[T]` envelope (Design System Consistency plan) -- `useKnowledgeBases`
  // et al. now resolve `.data` to `{ items, totalCount }`, not a bare array.
  useKnowledgeBases: () => ({
    data: {
      items: [
        {
          id: "kb-1",
          name: "Sales KB",
          description: "Pricing, playbooks, objection handling.",
          sourceIds: [],
          docs: 10,
          chunks: 100,
          embeddingModel: "google/gemini-embedding-2",
          sensitivity: "internal",
          updated: "2h ago",
          status: "current",
          linkedDepartments: [],
          linkedAgents: [],
          roles: ["org_admin"],
        },
      ],
      totalCount: 1,
    },
  }),
  useDepartments: () => ({ data: { items: [], totalCount: 0 } }),
  useAgents: () => ({ data: { items: [], totalCount: 0 } }),
  // Source-cluster UI (KB edit + Sources section): a lookup with nothing to
  // resolve, and inert mutations this chrome-only test never triggers.
  useDataSources: () => ({ data: { items: [], totalCount: 0 } }),
  useUpdateKnowledgeBase: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteKnowledgeBase: () => ({ mutate: vi.fn(), isPending: false }),
  useUnlinkSourceFromBase: () => ({ mutate: vi.fn(), isPending: false }),
}));

import { KbDetailDrawer } from "@/routes/knowledge";

function renderDrawer() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <KbDetailDrawer kbId="kb-1" onClose={vi.fn()} onSync={vi.fn()} />
    </QueryClientProvider>,
  );
}

describe("KbDetailDrawer", () => {
  it("renders inside a DetailSheet (right-side sheet chrome)", () => {
    renderDrawer();

    // A Radix Sheet's content renders with role="dialog" via SheetContent;
    // asserting that role is what proves this now goes through DetailSheet
    // rather than the old hand-rolled `fixed inset-0 ... flex justify-end` div.
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
