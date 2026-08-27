import { QueryClient } from "@tanstack/react-query";
import { createRouter, type AnyRoute } from "@tanstack/react-router";
import { routeTree } from "./routeTree.gen";
import { editionRoutes } from "@oc8/edition-entry";

// Compose exactly once at module load. TanStack's addChildren wires parent and
// child route state; repeating it for every SSR router could append the same
// edition route more than once to the shared generated tree.
const composedRouteTree =
  editionRoutes.length === 0
    ? routeTree
    : routeTree.addChildren([
        ...(Object.values(routeTree.children ?? {}) as AnyRoute[]),
        ...editionRoutes,
      ]);

export const getRouter = () => {
  const queryClient = new QueryClient();

  // The generated Community tree is unchanged. A trusted edition can append
  // code-based children only through the build-time entry alias; Community's
  // entry is empty and never resolves Enterprise source.
  const router = createRouter({
    routeTree: composedRouteTree,
    context: { queryClient },
    scrollRestoration: true,
    defaultPreloadStaleTime: 0,
  });

  return router;
};
