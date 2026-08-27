import type { ReactNode } from "react";

import { OctopusCompanion, type OctopusMood } from "./octopus-companion";

export function PublicAuthLayout({
  children,
  mood = "idle",
}: {
  children: ReactNode;
  mood?: OctopusMood;
}) {
  return (
    // The Docking Station is always a dark surface, but `AppShell` -- which owns
    // the `.dark` class on <html> -- never mounts on a public route. Scoping
    // `dark` here gives every design-system token (bg-panel, text-muted-foreground,
    // border-border, bg-primary, ...) its dark value inside this subtree, so the
    // page's own `color: white` can never land on a light-theme surface.
    <div className="dark">
      <main className="oc8-docking-station">
        <div className="oc8-docking-station__ambient" aria-hidden="true">
          <span className="oc8-docking-station__bubble oc8-docking-station__bubble--one" />
          <span className="oc8-docking-station__bubble oc8-docking-station__bubble--two" />
          <span className="oc8-docking-station__bubble oc8-docking-station__bubble--three" />
        </div>
        {/*
          Sr-only landmark title -- not a visible logo. The one visible oc8 brand
          mark now lives inside the login/setup card itself (see LoginPage), so
          this route no longer shows it a second time above the card. Kept as text
          so the docking station route stays identifiable in markup even before
          hydration (see login.test.tsx's static-markup assertions).
        */}
        <h1 className="oc8-docking-station__title">oc8 docking station</h1>
        <div className="oc8-docking-station__frame">
          <div className="oc8-docking-station__mascot" aria-hidden="true">
            <OctopusCompanion mood={mood} />
          </div>
          <section className="oc8-docking-station__content" aria-label="Authentication">
            {children}
          </section>
        </div>
      </main>
      {/*
        No <Toaster/> here on purpose. This layout used to mount its own, on the
        reasoning that `AppShell` owns the only other one and never renders on a
        public route -- but that split is exactly what broke the toasts raised
        from /login right before navigating away: this Toaster unmounted with the
        route and `AppShell`'s replacement mounted empty, since `sonner` never
        replays toasts dispatched before a Toaster existed. The single app-wide
        Toaster now lives in `routes/__root.tsx`, above the route swap.
      */}
    </div>
  );
}
