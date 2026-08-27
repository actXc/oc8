import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  Outlet,
  Link,
  createRootRouteWithContext,
  useRouter,
  useRouterState,
  HeadContent,
  Scripts,
} from "@tanstack/react-router";
import { useEffect, useState, type ReactNode } from "react";

import appCss from "../styles.css?url";
import { reportLovableError } from "../lib/lovable-error-reporting";
import { AppShell } from "../components/app-shell";
import { Toaster } from "../components/ui/sonner";
import { LanguageProvider, useT } from "../lib/i18n";
import { LiveUpdatesProvider } from "../lib/live/provider";
import { hasDevSession, hasCommunitySession } from "../lib/api";
import { DevSignIn } from "../components/dev-sign-in";

const AUTH_CONFIG_URL =
  (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8099/api/v1";

import { EditionCompositionProvider } from "../edition/composition";
import { editionExtensions } from "@oc8/edition-entry";

function AuthGate({ children }: { children: ReactNode }) {
  const t = useT();
  // `hydrated` starts false on BOTH the server and the first client render, so the
  // first paint always renders children -> no hydration mismatch. Only after mount
  // (in the effect) do we flip it and, while auth mode is still resolving/redirecting,
  // show the gate. A failed `/auth/config` fetch fails open to dev mode below.
  //
  // `children` is route-aware (see `RootComponent`): it is the bare `Outlet` on the
  // public `/login` route and `AppShell` everywhere else. That decision is therefore
  // synchronous on every render -- server, first client paint and post-hydration --
  // so `/login` can never flash the application shell while auth state resolves.
  const [hydrated, setHydrated] = useState(false);
  const [authMode, setAuthMode] = useState<"dev" | "community" | null>(null);

  useEffect(() => {
    setHydrated(true);

    // Detect auth mode for Community routing
    if (typeof window !== "undefined") {
      fetch(`${AUTH_CONFIG_URL}/auth/config`)
        .then((r) => r.json() as Promise<{ mode: string }>)
        .then((cfg) => {
          setAuthMode(cfg.mode === "community" ? "community" : "dev");
        })
        .catch(() => setAuthMode("dev")); // fail-open to dev
    }
  }, []);

  // Reaching here means either dev/community mode is live and a session is
  // already stored, or nobody has picked a persona/logged in yet --
  // `hasDevSession()`/`hasCommunitySession()` decide that second case,
  // replacing what used to be a silent auto-mint into org_admin on the first
  // API call.
  if (hydrated && !hasDevSession() && !hasCommunitySession()) {
    // `authMode` resolves asynchronously (a `/auth/config` fetch) -- while
    // it's still null, we don't yet know whether this is community or dev
    // mode, so show a neutral loading state instead of guessing. Treating
    // null the same as "community" here previously flash-redirected every
    // session-less dev-mode load straight to /login before the fetch
    // resolved.
    if (authMode === null) {
      return (
        <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">
          {t("Signing in…", "Anmeldung…")}
        </div>
      );
    }
    // If community mode and no community session, route to the login page --
    // unless we are already on it, in which case there is nothing to gate and
    // we fall through to render `children` (the public login `Outlet`).
    if (authMode === "community") {
      const onLoginPage = typeof window !== "undefined" && window.location.pathname === "/login";
      if (!onLoginPage) {
        if (typeof window !== "undefined") {
          window.location.href = "/login";
        }
        return (
          <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">
            {t("Redirecting…", "Wird weitergeleitet…")}
          </div>
        );
      }
    } else {
      // Dev mode: show dev sign-in
      return <DevSignIn />;
    }
  }

  return <>{children}</>;
}

function NotFoundComponent() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="max-w-md text-center">
        <h1 className="text-7xl font-bold text-foreground">404</h1>
        <h2 className="mt-4 text-xl font-semibold text-foreground">Page not found</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          The page you're looking for doesn't exist or has been moved.
        </p>
        <div className="mt-6">
          <Link
            to="/"
            className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          >
            Go home
          </Link>
        </div>
      </div>
    </div>
  );
}

function ErrorComponent({ error, reset }: { error: Error; reset: () => void }) {
  console.error(error);
  const router = useRouter();
  useEffect(() => {
    reportLovableError(error, { boundary: "tanstack_root_error_component" });
  }, [error]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="max-w-md text-center">
        <h1 className="text-xl font-semibold tracking-tight text-foreground">
          This page didn't load
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Something went wrong on our end. You can try refreshing or head back home.
        </p>
        <div className="mt-6 flex flex-wrap justify-center gap-2">
          <button
            onClick={() => {
              router.invalidate();
              reset();
            }}
            className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          >
            Try again
          </button>
          <a
            href="/"
            className="inline-flex items-center justify-center rounded-md border border-input bg-background px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-accent"
          >
            Go home
          </a>
        </div>
      </div>
    </div>
  );
}

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1" },
      { title: "oc8 — AI Agent Control Panel" },
      {
        name: "description",
        content:
          "oc8 is the control panel for your AI team. Monitor, control, and orchestrate every AI agent in your company from one place.",
      },
      { name: "author", content: "oc8" },
      { property: "og:title", content: "oc8 — AI Agent Control Panel" },
      { property: "og:description", content: "The command center for your AI team." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
    links: [
      {
        rel: "stylesheet",
        href: appCss,
      },
      { rel: "icon", href: "/favicon.ico", sizes: "any" },
      { rel: "icon", href: "/octopus_oc8.svg", type: "image/svg+xml" },
      { rel: "icon", href: "/favicon-32x32.png", type: "image/png", sizes: "32x32" },
      { rel: "icon", href: "/favicon-16x16.png", type: "image/png", sizes: "16x16" },
      { rel: "apple-touch-icon", href: "/apple-touch-icon.png", sizes: "180x180" },
    ],
  }),
  shellComponent: RootShell,
  component: RootComponent,
  notFoundComponent: NotFoundComponent,
  errorComponent: ErrorComponent,
});

function RootShell({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        <HeadContent />
      </head>
      <body>
        {children}
        <Scripts />
      </body>
    </html>
  );
}

function RootComponent() {
  const { queryClient } = Route.useRouteContext();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  // `/welcome` renders the same always-dark `PublicAuthLayout` docking station as
  // `/login` (see `routes/welcome.tsx`) -- both need the Toaster forced dark
  // regardless of the stored preference, or a light-mode operator gets a light
  // toast on a dark surface there too.
  const publicRoute = pathname === "/login" || pathname === "/welcome";
  // Single source of truth for "which UI wrapper does this route get". Computed
  // synchronously from the pathname so `/login` is never wrapped in `AppShell`,
  // regardless of auth state, session staleness or hydration timing.
  const gatedContent = publicRoute ? <Outlet /> : <AppShell />;

  // `AppShell` owns the theme toggle and persists it to localStorage("bf-theme")
  // (plus the `dark` class on <html>). The Toaster below lives ABOVE `AppShell`,
  // so it reads that same persisted value instead of the state. Seeding "dark"
  // and only reading storage in an effect keeps the server render and the first
  // client render identical (`RootShell` means this app is SSR'd), matching how
  // `AppShell` itself defaults when there is no window.
  //
  // A one-time read isn't enough: `richColors` makes the Toaster's own
  // `data-sonner-theme` attribute load-bearing for the toast's actual colors
  // (its CSS specificity out-ranks the design-system token classes), and the
  // wrapper below scopes Tailwind's `dark` variant locally -- so an in-session
  // theme toggle in `AppShell` (which only updates ITS OWN state + <html>'s
  // class + localStorage, none of which this component re-renders on) would
  // otherwise leave the toast showing the stale theme until reload. Watching
  // <html>'s class directly keeps this reactive without adding shared state.
  const [storedTheme, setStoredTheme] = useState<"light" | "dark">("dark");
  useEffect(() => {
    const root = window.document.documentElement;
    const sync = () => setStoredTheme(root.classList.contains("dark") ? "dark" : "light");
    sync();
    const observer = new MutationObserver(sync);
    observer.observe(root, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  // The docking station (`/login`, `/welcome`) is always a dark surface by design.
  const toasterTheme = publicRoute ? "dark" : storedTheme;

  return (
    <QueryClientProvider client={queryClient}>
      <LiveUpdatesProvider>
        <EditionCompositionProvider extensions={editionExtensions}>
          <LanguageProvider>
            <AuthGate>{gatedContent}</AuthGate>
            {/*
              The ONE <Toaster/> in the app, deliberately a sibling of `gatedContent`
              rather than inside it. `sonner` seeds a freshly mounted <Toaster/> from
              empty state and never replays toasts raised before it mounted, so the
              two per-layout Toasters this replaces (one in `PublicAuthLayout`, one in
              `AppShell`) silently swallowed every toast fired immediately before a
              route change -- notably the login-success and 2FA grace-period toasts in
              `routes/login.tsx`, which fire and then navigate /login -> / in the same
              breath. `RootComponent` is the one wrapper that survives that swap.
              The `dark` wrapper is what gives the toast's design-system tokens
              (bg-background, text-foreground, border-border) their dark values: on
              `/login` the surrounding `dark` class is scoped to `PublicAuthLayout`'s
              own subtree, which no longer contains this Toaster.
            */}
            <div className={toasterTheme === "dark" ? "dark" : undefined}>
              <Toaster theme={toasterTheme} position="bottom-right" richColors closeButton />
            </div>
          </LanguageProvider>
        </EditionCompositionProvider>
      </LiveUpdatesProvider>
    </QueryClientProvider>
  );
}
