import { useT } from "@/lib/i18n";
import { useSkipOnboarding } from "@/lib/hooks";
import { toast } from "sonner";

export function SkipLink() {
  const t = useT();
  const skip = useSkipOnboarding();
  const doSkip = async () => {
    try {
      await skip.mutateAsync();
      window.location.href = "/";
    } catch (err) {
      toast.error(t("Could not skip setup", "Einrichtung konnte nicht übersprungen werden"), {
        description: err instanceof Error ? err.message : String(err),
      });
    }
  };
  return (
    <div className="text-center">
      <button
        onClick={doSkip}
        disabled={skip.isPending}
        className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
      >
        {t("Skip for now", "Später einrichten")}
      </button>
    </div>
  );
}
