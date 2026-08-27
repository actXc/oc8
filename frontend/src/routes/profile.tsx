import { createFileRoute } from "@tanstack/react-router";
import { Check, Copy, Link2, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Panel } from "@/components/app-shell";
import { BackupCodesReveal } from "@/components/totp-backup-codes";
import { TotpEnroll } from "@/components/totp-enroll";
import { useT } from "@/lib/i18n";
import { api, getToken } from "@/lib/api";
import {
  useAvailableChannels,
  useChannelBindings,
  useRequestChannelLink,
  useRevokeChannelBinding,
} from "@/lib/hooks";
import { useCan } from "@/lib/governance-hooks";
import { cn } from "@/lib/utils";
import {
  getPushSubscriptionStatus,
  isPushSupported,
  subscribeToPush,
  unsubscribeFromPush,
} from "@/lib/push-notifications";

const CHANNEL_LABELS: Record<string, string> = {
  telegram: "Telegram",
  whatsapp: "WhatsApp",
};

function channelLabel(id: string): string {
  return CHANNEL_LABELS[id] ?? id.charAt(0).toUpperCase() + id.slice(1);
}

export const Route = createFileRoute("/profile")({
  component: ProfilePage,
});

export function ProfilePage() {
  const t = useT();
  const [subscription, setSubscription] = useState<PushSubscription | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [totpEnrolled, setTotpEnrolled] = useState<boolean | null>(null);
  const [totpLoading, setTotpLoading] = useState(true);
  const [enrolling, setEnrolling] = useState(false);
  const [enrollToken, setEnrollToken] = useState<string | null>(null);
  const [regeneratedCodes, setRegeneratedCodes] = useState<string[] | null>(null);
  const [totpBusy, setTotpBusy] = useState(false);

  useEffect(() => {
    getPushSubscriptionStatus()
      .then(setSubscription)
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    api
      .get<{ enrolled: boolean }>("/auth/totp/status")
      .then((r) => setTotpEnrolled(r.enrolled))
      .finally(() => setTotpLoading(false));
  }, []);

  async function handleRegenerate() {
    setTotpBusy(true);
    try {
      const r = await api.post<{ backupCodes: string[] }>("/auth/totp/regenerate-backup-codes");
      setRegeneratedCodes(r.backupCodes);
    } catch {
      toast.error(
        t("Could not regenerate backup codes.", "Backup-Codes konnten nicht neu erzeugt werden."),
      );
    } finally {
      setTotpBusy(false);
    }
  }

  async function handleStartEnrollment() {
    // `<TotpEnroll>`'s `token` prop needs the member's REAL bearer token,
    // and `getToken()` is async -- so it must resolve BEFORE `enrolling` is
    // set true, not be passed as a placeholder that resolves later.
    setTotpBusy(true);
    try {
      const token = await getToken();
      setEnrollToken(token);
      setEnrolling(true);
    } catch {
      toast.error(t("Could not start enrollment.", "Einrichtung konnte nicht gestartet werden."));
    } finally {
      setTotpBusy(false);
    }
  }

  async function handleToggle() {
    setError(null);
    setBusy(true);
    try {
      if (subscription) {
        await unsubscribeFromPush(subscription);
        setSubscription(null);
      } else {
        await subscribeToPush();
        const status = await getPushSubscriptionStatus();
        setSubscription(status);
        toast.success(t("Notifications enabled", "Benachrichtigungen aktiviert"));
      }
    } catch (err) {
      if (err instanceof Error && err.message === "permission-denied") {
        setError(
          t(
            "Notifications were blocked in the browser.",
            "Benachrichtigungen wurden im Browser blockiert.",
          ),
        );
      } else if (err instanceof Error && err.message === "push-not-configured") {
        // Distinct from a browser refusal on purpose: nothing the operator can
        // do in their browser fixes a server without VAPID keys.
        setError(
          t(
            "Push notifications aren't configured on this server yet.",
            "Push-Benachrichtigungen sind auf diesem Server noch nicht eingerichtet.",
          ),
        );
      } else {
        setError(t("Something went wrong.", "Etwas ist schiefgelaufen."));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Panel className="p-4">
        <h2 className="mb-3 text-sm font-semibold text-foreground">
          {t("Notifications", "Benachrichtigungen")}
        </h2>
        {!isPushSupported() ? (
          <p className="text-sm text-muted-foreground">
            {t(
              "This browser does not support push notifications.",
              "Dieser Browser unterstützt keine Push-Benachrichtigungen.",
            )}
          </p>
        ) : (
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-sm font-medium text-foreground">
                {t(
                  "Push notifications for new approvals",
                  "Push-Benachrichtigungen für neue Freigaben",
                )}
              </div>
              <div className="text-xs text-muted-foreground">
                {t(
                  "Get notified even when this tab isn't open.",
                  "Werde benachrichtigt, auch wenn dieser Tab nicht offen ist.",
                )}
              </div>
              {error && <div className="mt-1 text-xs text-destructive">{error}</div>}
            </div>
            <button
              type="button"
              disabled={loading || busy}
              onClick={handleToggle}
              aria-pressed={!!subscription}
              className="h-8 shrink-0 rounded-md border border-border bg-panel px-3 text-xs font-medium text-foreground transition hover:bg-accent disabled:opacity-50"
            >
              {subscription ? t("Turn off", "Ausschalten") : t("Turn on", "Einschalten")}
            </button>
          </div>
        )}
      </Panel>
      <Panel className="p-4">
        <h2 className="mb-3 text-sm font-semibold text-foreground">
          {t("Two-factor authentication", "Zwei-Faktor-Authentifizierung")}
        </h2>
        {enrolling && enrollToken !== null ? (
          <TotpEnroll
            token={enrollToken}
            onDone={() => {
              setEnrolling(false);
              setEnrollToken(null);
              setTotpEnrolled(true);
              toast.success(
                t("Two-factor authentication enabled", "Zwei-Faktor-Authentifizierung aktiviert"),
              );
            }}
          />
        ) : regeneratedCodes ? (
          <BackupCodesReveal
            codes={regeneratedCodes}
            onAcknowledge={() => setRegeneratedCodes(null)}
          />
        ) : (
          <div className="flex items-center justify-between gap-3">
            <div className="text-sm font-medium text-foreground">
              {totpEnrolled ? t("Enabled", "Aktiviert") : t("Not enabled", "Nicht aktiviert")}
            </div>
            <button
              type="button"
              disabled={totpLoading || totpBusy}
              onClick={async () => {
                if (totpEnrolled) {
                  await handleRegenerate();
                } else {
                  await handleStartEnrollment();
                }
              }}
              className="h-8 shrink-0 rounded-md border border-border bg-panel px-3 text-xs font-medium text-foreground transition hover:bg-accent disabled:opacity-50"
            >
              {totpEnrolled
                ? t("Regenerate backup codes", "Backup-Codes neu erzeugen")
                : t("Turn on", "Einschalten")}
            </button>
          </div>
        )}
      </Panel>
      <ApprovalChannelsPanel />
    </div>
  );
}

/** Personal channel linking (§5.6) -- distinct from a capa's own admin setup
 * (bot token, done once via CapaSetupDialog): this is the step a PERSON takes
 * to bind their own chat so approvals reach them there. `channels/bindings`
 * is tenant-wide (an operator has to audit who can decide from a phone), so
 * this table is honestly labelled as such rather than claimed as "mine". */
function ApprovalChannelsPanel() {
  const t = useT();
  const can = useCan();
  const canRequest = can("channel:manage");
  const canView = can("channel:view");
  const { data: channels = [] } = useAvailableChannels();
  const { data: bindings = [] } = useChannelBindings();
  const requestLink = useRequestChannelLink();
  const revokeBinding = useRevokeChannelBinding();
  const [issued, setIssued] = useState<{ channel: string; code: string; expiresAt: string } | null>(
    null,
  );
  const [copied, setCopied] = useState(false);

  if (!canRequest && !canView) return null;

  function request(channel: string) {
    setIssued(null);
    requestLink.mutate(channel, {
      onSuccess: (r) => {
        setIssued(r);
        setCopied(false);
      },
      onError: (err) =>
        toast.error(t("Couldn't create a link code", "Link-Code konnte nicht erstellt werden"), {
          description: err instanceof Error ? err.message : String(err),
        }),
    });
  }

  async function copyCode() {
    if (!issued) return;
    await navigator.clipboard.writeText(issued.code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <Panel className="p-4 md:col-span-2">
      <h2 className="mb-3 text-sm font-semibold text-foreground">
        {t("Approval channels", "Freigabe-Kanäle")}
      </h2>
      <p className="mb-3 text-xs text-muted-foreground">
        {t(
          "Link your own Telegram or WhatsApp so approvals reach you there. This is separate from a capa's admin setup (bot token) — this step binds YOUR chat.",
          "Verknüpfe dein eigenes Telegram oder WhatsApp, damit Freigaben dort ankommen. Das ist getrennt vom Admin-Setup einer Capa (Bot-Token) — dieser Schritt verknüpft DEIN Chat.",
        )}
      </p>

      {canRequest && (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          {channels.length === 0 && (
            <p className="text-xs text-muted-foreground">
              {t(
                "No approval-channel capa is installed and enabled yet.",
                "Noch keine Freigabe-Kanal-Capa installiert und aktiviert.",
              )}
            </p>
          )}
          {channels.map((c) => (
            <button
              key={c.id}
              type="button"
              disabled={requestLink.isPending}
              onClick={() => request(c.id)}
              className="inline-flex items-center gap-1.5 rounded-md border border-border bg-panel px-3 py-1.5 text-xs font-medium text-foreground transition hover:bg-accent disabled:opacity-50"
            >
              <Link2 className="h-3.5 w-3.5" />
              {t(`Link ${channelLabel(c.id)}`, `${channelLabel(c.id)} verknüpfen`)}
            </button>
          ))}
        </div>
      )}

      {issued && (
        <div className="mb-4 rounded-md border border-primary/40 bg-primary/5 p-3 text-xs">
          <p className="mb-2 text-foreground/90">
            {t(
              `Send this code to the ${channelLabel(issued.channel)} bot (e.g. /start <code>) before it expires.`,
              `Sende diesen Code an den ${channelLabel(issued.channel)}-Bot (z. B. /start <code>), bevor er abläuft.`,
            )}
          </p>
          <div className="flex items-center gap-2">
            <code className="flex-1 rounded border border-border bg-background/60 px-2 py-1 font-mono text-sm">
              {issued.code}
            </code>
            <button
              type="button"
              onClick={copyCode}
              className="inline-flex items-center gap-1 rounded-md border border-border bg-panel px-2 py-1 text-[11px] text-muted-foreground transition hover:text-foreground"
            >
              {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
              {copied ? t("Copied", "Kopiert") : t("Copy", "Kopieren")}
            </button>
          </div>
          <p className="mt-1.5 text-[11px] text-muted-foreground">
            {t("Expires", "Läuft ab")}: {new Date(issued.expiresAt).toLocaleString()}
          </p>
        </div>
      )}

      {canView && (
        <div>
          <div className="mb-1.5 text-[10px] uppercase tracking-widest text-muted-foreground">
            {t("Linked in this tenant", "Verknüpft in diesem Mandanten")}
          </div>
          {bindings.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              {t("No channel is linked yet.", "Noch kein Kanal verknüpft.")}
            </p>
          ) : (
            <ul className="space-y-1.5">
              {bindings.map((b) => (
                <li
                  key={b.id}
                  className={cn(
                    "flex items-center justify-between gap-2 rounded-md border border-border bg-background/30 px-2.5 py-1.5 text-xs",
                  )}
                >
                  <span className="font-medium text-foreground">{channelLabel(b.channel)}</span>
                  {canRequest && (
                    <button
                      type="button"
                      onClick={() =>
                        revokeBinding.mutate(b.id, {
                          onSuccess: () =>
                            toast.success(t("Binding revoked", "Verknüpfung entfernt")),
                          onError: () =>
                            toast.error(t("Couldn't revoke", "Konnte nicht entfernt werden")),
                        })
                      }
                      disabled={revokeBinding.isPending}
                      aria-label={t("Revoke", "Entfernen")}
                      className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-muted-foreground transition hover:text-destructive disabled:opacity-50"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Panel>
  );
}
