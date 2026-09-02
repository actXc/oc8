// Task 21 (Design System Consistency plan): the Members list page now goes
// through <ListToolbar> (Task 14) instead of a plain "N users" summary --
// server-side search/pagination state lives in a local `ListQueryState` and
// is threaded straight into `useAssignees(enabled, params)` (Task 14/15).
// Member has neither `SoftDeleteMixin` nor an archive concept (Global
// Constraints excludes it explicitly), so unlike Departments/Agents (Tasks
// 18/19) there is NO `groupBy` and NO `showArchivedToggle` -- search and
// pagination only. Mirrors Task 19's `departments-list-toolbar.test.tsx`
// minus the archive-toggle test.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const useAssigneesMock = vi.fn();
const createMemberMutateMock = vi.fn();
const { toastSuccess } = vi.hoisted(() => ({ toastSuccess: vi.fn() }));

// Real member rows go through <Link to="/members/$memberId">, which throws
// without a <RouterProvider>; stub it as a plain anchor, same as
// knowledge-source-cluster.test.tsx's own mock for the same reason.
vi.mock("@tanstack/react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-router")>();
  return {
    ...actual,
    Link: ({ children, className }: { children?: ReactNode; className?: string }) => (
      <a className={className}>{children}</a>
    ),
  };
});

vi.mock("@/lib/roles-hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/roles-hooks")>();
  return {
    ...actual,
    useAssignees: (enabled: boolean, params: unknown) => useAssigneesMock(enabled, params),
    useCreateMember: () => ({ mutate: createMemberMutateMock, isPending: false }),
  };
});

vi.mock("sonner", async (importOriginal) => {
  const actual = await importOriginal<typeof import("sonner")>();
  return { ...actual, toast: { ...actual.toast, success: toastSuccess } };
});

vi.mock("@/lib/hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/hooks")>();
  return {
    ...actual,
    useAuthConfig: () => ({ data: { mode: "community" } }),
  };
});

vi.mock("@/lib/governance-hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/governance-hooks")>();
  return {
    ...actual,
    useMay: () => () => true,
  };
});

import { MembersPage } from "@/routes/members.index";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MembersPage />
    </QueryClientProvider>,
  );
}

describe("Members list page", () => {
  beforeEach(() => {
    useAssigneesMock.mockReset();
    useAssigneesMock.mockReturnValue({
      data: { items: [], totalCount: 0 },
      isLoading: false,
      error: null,
    });
    createMemberMutateMock.mockReset();
    toastSuccess.mockReset();
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
  });

  it("re-fetches with the typed search term", () => {
    renderPage();

    fireEvent.change(screen.getByPlaceholderText(/search members/i), {
      target: { value: "jane" },
    });

    const lastCall = useAssigneesMock.mock.calls.at(-1);
    expect(lastCall?.[1]).toMatchObject({ search: "jane" });
  });

  it("does not render a grouping dropdown or an archived toggle -- Member has no archive concept", () => {
    renderPage();

    expect(screen.queryByText(/group by/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/show archived/i)).not.toBeInTheDocument();
  });

  it("paginates from the exact totalCount, not the capped page length", () => {
    // Deliberately `items: []` here -- rendering actual member rows uses
    // <Link to="/members/$memberId"> which needs a <RouterProvider> this
    // lightweight render tree doesn't set up (same scoping call Task 19 made
    // for department cards). This only exercises that `totalCount` reaches
    // <ListToolbar> and drives its pagination control.
    useAssigneesMock.mockReturnValue({
      data: { items: [], totalCount: 57 },
      isLoading: false,
      error: null,
    });

    renderPage();

    expect(screen.getByText("1 / 3")).toBeInTheDocument();
  });

  // Fix-round regression test (whole-branch review finding): this page used
  // to warn "more than 1000 people, this list is incomplete, use oc8 member
  // import --csv" whenever `totalCount > ASSIGNEE_LIMIT`. That was true when
  // `/members` returned a bare, hard-capped array with no pagination -- but
  // Task 21 made this page paginate through <ListToolbar> (page/offset, not
  // a one-shot capped fetch), so the pager now reaches every member
  // regardless of `totalCount`. The banner's premise -- "the list is
  // incomplete" -- no longer holds, so it (and the `truncated` concept
  // behind it) is gone rather than fixed to read differently.
  it("does not show the old truncation banner even when totalCount exceeds ASSIGNEE_LIMIT", () => {
    useAssigneesMock.mockReturnValue({
      data: { items: [], totalCount: 1500 },
      isLoading: false,
      error: null,
    });

    renderPage();

    expect(screen.queryByText(/list is incomplete/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/member import --csv/i)).not.toBeInTheDocument();
  });

  // Regression for "können wir den display name für die rollen schöner
  // machen?": this list used to render the raw backend role name
  // ("org_admin", "dept_manager") verbatim instead of the friendly,
  // bilingual label app-shell.tsx's roleLabel() already provides elsewhere
  // (the header's own assigned-role display).
  it("shows the friendly role label, not the raw backend role name", () => {
    useAssigneesMock.mockReturnValue({
      data: {
        items: [
          {
            id: "m-1",
            subject: "admin@example.com",
            displayName: "Admin Person",
            roleId: "role-1",
            roleName: "org_admin",
          },
        ],
        totalCount: 1,
      },
      isLoading: false,
      error: null,
    });

    renderPage();

    expect(screen.getByText("Admin")).toBeInTheDocument();
    expect(screen.queryByText("org_admin")).not.toBeInTheDocument();
  });

  it("still shows the sign-in fallback text for a member with no assigned role", () => {
    useAssigneesMock.mockReturnValue({
      data: {
        items: [
          {
            id: "m-2",
            subject: "unassigned@example.com",
            displayName: "Unassigned Person",
            roleId: null,
            roleName: "",
          },
        ],
        totalCount: 1,
      },
      isLoading: false,
      error: null,
    });

    renderPage();

    expect(screen.getByText(/from their sign-in/i)).toBeInTheDocument();
  });
});

// The invite-link follow-up (design: an admin-created user with no password
// gets a mailed, or copyable, link to set one instead of being left with no
// way in). These drive `CreateMemberDialog`'s `onSuccess` handler directly
// through the captured `mutate(vars, { onSuccess })` callback, the same way
// `departments-delete.test.tsx` drives a mutation's callbacks without a real
// network layer.
describe("Create-user invite link", () => {
  function openCreateDialogAndSubmit(subject: string) {
    fireEvent.click(screen.getByRole("button", { name: /new user/i }));
    fireEvent.change(screen.getByPlaceholderText("jane.doe@example.com"), {
      target: { value: subject },
    });
    fireEvent.click(screen.getByRole("button", { name: /^create user$/i }));
  }

  type CreatedMember = {
    id: string;
    subject: string;
    displayName: string;
    inviteLink?: string | null;
    inviteSent?: boolean;
  };

  function lastOnSuccess(): (created: CreatedMember) => void {
    const call = createMemberMutateMock.mock.calls.at(-1) as [
      unknown,
      { onSuccess: (created: CreatedMember) => void },
    ];
    return call[1].onSuccess;
  }

  it("shows a copyable link dialog when an invite was minted but not emailed", () => {
    renderPage();
    openCreateDialogAndSubmit("newperson@example.com");

    expect(createMemberMutateMock).toHaveBeenCalledTimes(1);
    act(() => {
      lastOnSuccess()({
        id: "m-3",
        subject: "newperson@example.com",
        displayName: "",
        inviteLink: "https://oc8.example.test/reset-password?token=abc123",
        inviteSent: false,
      });
    });

    expect(screen.getByText(/share this invite link/i)).toBeInTheDocument();
    expect(
      screen.getByDisplayValue("https://oc8.example.test/reset-password?token=abc123"),
    ).toBeInTheDocument();
  });

  it("shows an emailed-invite toast instead of the link dialog when the invite was sent", () => {
    renderPage();
    openCreateDialogAndSubmit("emailed@example.com");

    act(() => {
      lastOnSuccess()({
        id: "m-4",
        subject: "emailed@example.com",
        displayName: "",
        inviteLink: "https://oc8.example.test/reset-password?token=xyz",
        inviteSent: true,
      });
    });

    expect(toastSuccess).toHaveBeenCalledWith(expect.stringMatching(/invite was emailed/i));
    expect(screen.queryByText(/share this invite link/i)).not.toBeInTheDocument();
  });

  it("does not open the invite dialog for a member created with a password", () => {
    renderPage();
    openCreateDialogAndSubmit("haspassword@example.com");

    act(() => {
      lastOnSuccess()({
        id: "m-5",
        subject: "haspassword@example.com",
        displayName: "",
        inviteLink: null,
        inviteSent: false,
      });
    });

    expect(toastSuccess).toHaveBeenCalledWith("User created");
    expect(screen.queryByText(/share this invite link/i)).not.toBeInTheDocument();
  });
});
