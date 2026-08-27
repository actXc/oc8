import { createFileRoute, redirect } from "@tanstack/react-router";

// Absorbed into /capas (Task 9: App Store + Integrations + Plugins merged
// into one "Capas" concept) -- kept as a redirect so any bookmark or
// external link still lands somewhere real.
export const Route = createFileRoute("/integrations")({
  beforeLoad: () => {
    throw redirect({ to: "/capas" });
  },
});
