// frontend/public/sw.js
// Plain, unbundled JS -- Vite serves everything under public/ at the root
// as-is, so this file needs no build step and is fetchable at /sw.js. Its
// ONLY job is push notifications; it does no offline caching and is not
// part of a full PWA setup.

// A browser may rotate a push subscription on its own -- key rotation, storage
// pressure -- with nothing on the server having asked for it. Without this
// handler the stored endpoint goes stale, the next send gets a 410, the row is
// pruned, and nothing ever re-registers: the toggle still reads "on" while push
// is permanently dead.
//
// This worker cannot re-register the new subscription itself: this app
// authenticates with a bearer token kept in `localStorage`, which a service
// worker has no access to (no DOM, no `window`), and this origin's API has no
// cookie session for a plain `fetch(..., {credentials: "include"})` to ride on
// either. So instead of failing silently against an endpoint it can never
// authenticate to, this relays the event to any open tab, which already has
// the token and can redo the full authenticated subscribe via
// `subscribeToPush()` (see src/lib/push-notifications.ts, wired up in
// app-shell.tsx). If no tab is open when the browser rotates the key, nothing
// re-registers until the operator next opens one -- an accepted limitation,
// not a masked failure.
self.addEventListener("pushsubscriptionchange", (event) => {
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientsList) => {
      for (const client of clientsList) {
        client.postMessage({ type: "oc8-push-subscription-changed" });
      }
    }),
  );
});

self.addEventListener("push", (event) => {
  if (!event.data) return;
  const payload = event.data.json();
  event.waitUntil(
    (async () => {
      const clientsList = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      const hasFocusedClient = clientsList.some((client) => client.focused);
      if (hasFocusedClient) {
        // That operator is already looking at a tab and will see the
        // in-app toast (frontend/src/lib/live/toast-for-event.ts) -- a
        // native notification too would double-fire the same event.
        return;
      }
      await self.registration.showNotification(payload.title, {
        body: payload.body,
        data: { url: payload.url },
      });
    })(),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data && event.notification.data.url;
  if (!url) return;
  event.waitUntil(
    (async () => {
      const clientsList = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      const target = clientsList.find((client) => {
        try {
          return new URL(client.url).origin === self.location.origin;
        } catch {
          return false;
        }
      });
      if (target) {
        await target.navigate(url);
        await target.focus();
      } else {
        await self.clients.openWindow(url);
      }
    })(),
  );
});
