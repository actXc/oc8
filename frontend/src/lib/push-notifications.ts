import { api } from "@/lib/api";

/** The backend's 409 detail for an endpoint another member already owns
 * (`oc8.api.v1.notifications.subscribe`). `api.request()` throws a plain
 * `Error` carrying only that detail string -- no status code -- so matching
 * the message is the only way to tell this apart, the same string-sentinel
 * approach this module already uses for "permission-denied". */
const ENDPOINT_TAKEN_DETAIL = "this push endpoint is already registered to another member";

export function isPushSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

// The browser Push API requires the VAPID public key as a Uint8Array, but
// the backend serves it base64url-encoded (the standard wire format for a
// VAPID key) -- this is the standard conversion, unchanged across every
// Web Push tutorial because the underlying API leaves no other way in.
function urlBase64ToUint8Array(base64String: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; i++) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

export async function getPushSubscriptionStatus(): Promise<PushSubscription | null> {
  if (!isPushSupported()) return null;
  const registration = await navigator.serviceWorker.getRegistration("/sw.js");
  if (!registration) return null;
  return registration.pushManager.getSubscription();
}

async function postSubscription(subscription: PushSubscription): Promise<void> {
  const json = subscription.toJSON();
  await api.post("/notifications/push/subscriptions", {
    endpoint: json.endpoint,
    keys: { p256dh: json.keys?.p256dh, auth: json.keys?.auth },
  });
}

function isEndpointTaken(err: unknown): boolean {
  return err instanceof Error && err.message === ENDPOINT_TAKEN_DETAIL;
}

export async function subscribeToPush(): Promise<void> {
  if (!isPushSupported()) {
    throw new Error("push-not-supported");
  }
  // The VAPID key comes FIRST, before any prompt. An instance with no keys
  // configured serves "", and `urlBase64ToUint8Array("")` produces garbage that
  // makes `pushManager.subscribe()` throw -- after the OS permission dialog has
  // already been answered. A notification permission is granted once and then
  // lives in browser settings, so burning it on a server that cannot send
  // anything is not a recoverable mistake for the user.
  const { publicKey } = await api.get<{ publicKey: string }>(
    "/notifications/push/vapid-public-key",
  );
  if (!publicKey) {
    throw new Error("push-not-configured");
  }
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    throw new Error("permission-denied");
  }
  const registration = await navigator.serviceWorker.register("/sw.js");
  await navigator.serviceWorker.ready;
  const applicationServerKey = urlBase64ToUint8Array(publicKey);
  let subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey,
  });
  try {
    await postSubscription(subscription);
  } catch (err) {
    if (!isEndpointTaken(err)) throw err;
    // A shared browser: the operator who used it before us still owns a row for
    // this endpoint, and `pushManager` hands out the SAME subscription no matter
    // who is signed into the web app -- so without this the 409 is a permanent
    // dead end for the new operator. Dropping the subscription at the BROWSER
    // level is what produces a genuinely new endpoint that nobody owns yet.
    //
    // Exactly one retry: a second 409 means something other than a stale row,
    // and a loop would turn that into an invisible spin.
    await subscription.unsubscribe();
    subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey,
    });
    await postSubscription(subscription);
  }
}

export async function unsubscribeFromPush(subscription: PushSubscription): Promise<void> {
  const endpoint = subscription.endpoint;
  await subscription.unsubscribe();
  await api.delete(`/notifications/push/subscriptions?endpoint=${encodeURIComponent(endpoint)}`);
}

/** Redo the authenticated subscribe whenever the service worker reports the
 * browser rotated the subscription on its own (see `public/sw.js` -- it has
 * no access to this tab's bearer token, so it relays the event here instead
 * of failing an unauthenticated request silently). Call once per app
 * lifetime; returns a cleanup function. */
export function listenForPushSubscriptionChanges(): () => void {
  if (!isPushSupported()) return () => {};
  function handleMessage(event: MessageEvent) {
    if ((event.data as { type?: string } | undefined)?.type === "oc8-push-subscription-changed") {
      void subscribeToPush().catch(() => {
        // Best-effort: the operator's next visit to the profile toggle
        // recovers from here exactly as it always could.
      });
    }
  }
  navigator.serviceWorker.addEventListener("message", handleMessage);
  return () => navigator.serviceWorker.removeEventListener("message", handleMessage);
}
