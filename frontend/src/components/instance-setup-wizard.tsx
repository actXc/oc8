import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  Cloud,
  KeyRound,
  Loader2,
  Server,
  Settings2,
} from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  useCreateModel,
  useCreateSecret,
  useModelProviders,
  useOrganizationSettings,
  useUpdateOrganizationSettings,
} from "@/lib/hooks";

type Step = 1 | 2 | 3 | 4 | 5;

// These are curated provider model identifiers, not free-text values. The
// provider itself is still resolved from the tenant-scoped backend registry.
const PROVIDER_SETUP = {
  anthropic: {
    label: "Anthropic",
    credentialLabel: "Anthropic API key",
    models: ["claude-sonnet-4-20250514", "claude-opus-4-20250514", "claude-3-5-haiku-20241022"],
  },
  openai: {
    label: "OpenAI",
    credentialLabel: "OpenAI API key",
    models: ["gpt-4.1", "gpt-4.1-mini", "gpt-4o", "o3-mini"],
  },
  openai_compatible: {
    label: "Mistral / OpenAI-compatible",
    credentialLabel: "Provider API key",
    models: ["mistral-large-latest", "mistral-small-latest", "codestral-latest"],
  },
  ollama: {
    label: "Ollama (local)",
    credentialLabel: null,
    models: ["llama3.3:70b", "llama3.1:8b", "qwen2.5:14b", "mistral:7b"],
  },
} as const;

type ProviderId = keyof typeof PROVIDER_SETUP;

/** First-run configuration for a tenant already provisioned by the schema-owner CLI (§4). */
export function InstanceSetupWizard({ onClose }: { onClose: () => void }) {
  const { data: organization } = useOrganizationSettings();
  const { data: providers = [], isLoading: providersLoading } = useModelProviders();
  const updateOrganization = useUpdateOrganizationSettings();
  const createModel = useCreateModel();
  const createSecret = useCreateSecret();
  const [step, setStep] = useState<Step>(1);
  const [name, setName] = useState("");
  const [region, setRegion] = useState("eu");
  const [provider, setProvider] = useState<ProviderId>("openai");
  const [model, setModel] = useState<string>(PROVIDER_SETUP.openai.models[1]);
  const [apiKey, setApiKey] = useState("");

  useEffect(() => {
    if (!organization) return;
    setName(organization.name);
    setRegion(organization.region || "eu");
  }, [organization]);

  const supportedProviders = providers
    .map((item) => item.canonical)
    .filter((id) => id in PROVIDER_SETUP) as ProviderId[];
  const options = supportedProviders.length
    ? supportedProviders
    : (["openai", "anthropic", "openai_compatible", "ollama"] as ProviderId[]);
  const setup = PROVIDER_SETUP[provider];
  const saving = updateOrganization.isPending || createModel.isPending || createSecret.isPending;
  const valid =
    (step === 1 && Boolean(name.trim()) && Boolean(region.trim())) ||
    (step === 2 && Boolean(provider)) ||
    (step === 3 && (!setup.credentialLabel || Boolean(apiKey))) ||
    (step === 4 && Boolean(model)) ||
    step === 5;

  function chooseProvider(id: ProviderId) {
    setProvider(id);
    setModel(PROVIDER_SETUP[id].models[0]);
    setApiKey("");
  }

  async function next() {
    try {
      if (step === 1)
        await updateOrganization.mutateAsync({ name: name.trim(), region: region.trim() });
      if (step === 3 && setup.credentialLabel) {
        await createSecret.mutateAsync({ name: `model/${provider}/api_key`, value: apiKey });
        setApiKey("");
      }
      if (step === 4) {
        await createModel.mutateAsync({
          provider,
          model,
          locality: provider === "ollama" ? "local" : "cloud",
          displayName: `${setup.label} · ${model}`,
        });
      }
      setStep((current) => Math.min(5, current + 1) as Step);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Setup could not be saved.");
    }
  }

  const steps = ["Workspace", "Provider", "Credential", "Model", "Ready"];
  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <section
        className="w-full max-w-xl overflow-hidden rounded-xl border border-border bg-panel shadow-2xl"
        onClick={(event) => event.stopPropagation()}
        aria-modal="true"
        role="dialog"
        aria-label="Instance setup"
      >
        <header className="flex items-start gap-3 border-b border-border px-5 py-4">
          <div className="grid h-9 w-9 place-items-center rounded-md bg-primary/15 text-primary">
            <Settings2 className="h-4 w-4" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="font-serif text-lg">Set up your workspace</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              A guided, tenant-safe first configuration.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            Close
          </button>
        </header>
        <div className="flex h-1 bg-background/50">
          <div className="bg-primary transition-all" style={{ width: `${(step / 5) * 100}%` }} />
        </div>
        <div className="flex justify-between px-5 pt-3 text-[10px] uppercase tracking-widest text-muted-foreground">
          {steps.map((label, index) => (
            <span key={label} className={index + 1 <= step ? "text-primary" : undefined}>
              {label}
            </span>
          ))}
        </div>
        <div className="min-h-64 space-y-4 px-5 py-5 text-sm">
          {step === 1 && (
            <>
              <p className="text-muted-foreground">
                Name the workspace and select its operating region.
              </p>
              <label className="block text-xs text-muted-foreground">
                Workspace name
                <input
                  autoFocus
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="mt-1 w-full rounded-md border border-border bg-background/40 px-3 py-2 text-sm text-foreground"
                />
              </label>
              <label className="block text-xs text-muted-foreground">
                Region
                <input
                  value={region}
                  onChange={(e) => setRegion(e.target.value)}
                  placeholder="eu"
                  className="mt-1 w-full rounded-md border border-border bg-background/40 px-3 py-2 text-sm text-foreground"
                />
              </label>
            </>
          )}
          {step === 2 && (
            <>
              <p className="text-muted-foreground">
                Choose the LLM provider. The available choices come from this tenant's provider
                registry.
              </p>
              {providersLoading ? (
                <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
              ) : (
                <div className="grid gap-2 sm:grid-cols-2">
                  {options.map((id) => (
                    <button
                      key={id}
                      type="button"
                      onClick={() => chooseProvider(id)}
                      className={`rounded-lg border p-3 text-left transition ${provider === id ? "border-primary bg-primary/10" : "border-border bg-background/30 hover:border-primary/50"}`}
                    >
                      <span className="flex items-center gap-2 font-medium">
                        <Cloud className="h-4 w-4 text-primary" />
                        {PROVIDER_SETUP[id].label}
                      </span>
                      <span className="mt-1 block text-[11px] text-muted-foreground">
                        {id === "ollama"
                          ? "Local runtime — no API key"
                          : "Your own encrypted API credential"}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </>
          )}
          {step === 3 &&
            (setup.credentialLabel ? (
              <>
                <div className="flex items-start gap-3 rounded-md border border-primary/30 bg-primary/5 p-3 text-xs text-muted-foreground">
                  <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                  <span>
                    Your key is encrypted per tenant and never returned. It is stored as{" "}
                    <code>model/{provider}/api_key</code>.
                  </span>
                </div>
                <label className="block text-xs text-muted-foreground">
                  {setup.credentialLabel}
                  <input
                    autoFocus
                    type="password"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    autoComplete="new-password"
                    className="mt-1 w-full rounded-md border border-border bg-background/40 px-3 py-2 font-mono text-sm text-foreground"
                  />
                </label>
              </>
            ) : (
              <div className="rounded-md border border-border bg-background/30 p-4 text-sm text-muted-foreground">
                Ollama runs locally and needs no API credential. Ensure the selected model exists on
                the configured Ollama host.
              </div>
            ))}
          {step === 4 && (
            <>
              <p className="text-muted-foreground">
                Select a model for {setup.label}. These are valid curated identifiers, not free-text
                input.
              </p>
              <div className="grid gap-2">
                {setup.models.map((id) => (
                  <button
                    key={id}
                    type="button"
                    onClick={() => setModel(id)}
                    className={`flex items-center justify-between rounded-md border px-3 py-2 text-left font-mono text-sm transition ${model === id ? "border-primary bg-primary/10 text-primary" : "border-border bg-background/30 hover:border-primary/50"}`}
                  >
                    <span>{id}</span>
                    {model === id && <CheckCircle2 className="h-4 w-4" />}
                  </button>
                ))}
              </div>
            </>
          )}
          {step === 5 && (
            <div className="space-y-4">
              <div className="flex items-start gap-3 rounded-md border border-[color:var(--status-running)]/40 bg-[color:var(--status-running)]/5 p-4">
                <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-[color:var(--status-running)]" />
                <div>
                  <div className="font-medium">Core workspace configuration is complete.</div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    The default department came from secure tenant provisioning. MCP connections are
                    optional and configured only where needed.
                  </p>
                </div>
              </div>
              <a
                href="/capas"
                className="flex items-center gap-2 rounded-md border border-border bg-background/30 p-3 text-xs text-muted-foreground hover:border-primary/50 hover:text-foreground"
              >
                <Server className="h-4 w-4 text-primary" />
                Configure an optional MCP connection →
              </a>
            </div>
          )}
        </div>
        <footer className="flex justify-between border-t border-border bg-background/30 px-5 py-3">
          <button
            type="button"
            onClick={() => setStep((current) => Math.max(1, current - 1) as Step)}
            disabled={step === 1 || saving}
            className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-xs text-muted-foreground disabled:opacity-40"
          >
            <ArrowLeft className="h-3 w-3" /> Back
          </button>
          {step === 5 ? (
            <button
              type="button"
              onClick={onClose}
              className="inline-flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground"
            >
              <CheckCircle2 className="h-3 w-3" /> Finish
            </button>
          ) : (
            <button
              type="button"
              onClick={next}
              disabled={!valid || saving}
              className="inline-flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-40"
            >
              {saving ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <ArrowRight className="h-3 w-3" />
              )}{" "}
              Continue
            </button>
          )}
        </footer>
      </section>
    </div>
  );
}
