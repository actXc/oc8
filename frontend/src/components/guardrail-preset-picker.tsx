import { useState } from "react";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** Mirrors `GuardrailPresetDTO` (`backend/src/oc8/schemas/dto.py`) exactly --
 * the API's camelCase field names, unchanged. A named permission set a
 * connection's plugin ships in its manifest (§ guardrail presets), offered
 * as a ceiling the operator can pick instead of deriving one from an
 * unfamiliar tool list.
 *
 * `only` is NOT decoration: it is the ONLY thing that makes a preset like
 * `autonomous_with_limit` safe. That preset grants write+send above a euro
 * threshold, which sounds unattended-deletion-safe only because a deletion
 * carries no amount and so can never meet the threshold -- UNLESS the
 * preset also reaches `delete_record`, in which case the threshold means
 * nothing for that action. Dropping `only` anywhere in this component (in
 * the type, in `policyOf`, in `matches`) silently turns "Autonomous with a
 * limit" back into "read+write+send above EUR 1000, every tool reachable" --
 * the unattended-deletion configuration, under a name promising a limit.
 * This has already happened twice in this feature (manifest, then DTO) and
 * was caught only in review both times. */
export interface GuardrailPreset {
  key: string;
  label: string;
  labelEn: string;
  summary: string;
  summaryEn: string;
  recommended: boolean;
  read: boolean;
  write: boolean;
  send: boolean;
  approvalActions: string[];
  approvalEur: number | null;
  /** The only tool names this preset puts within reach; empty means all of
   * them. Must survive selection into `GuardrailValue` -- see the module
   * doc above. */
  only: string[];
}

/** The policy actually applied to a connection -- what a preset selection
 * writes, and what the free controls edit directly. Carries `only` for the
 * same reason `GuardrailPreset` does: it is part of the policy, not
 * metadata about it. Free controls never widen `only` (there is no control
 * for it here -- the tool catalog behind those names is not this
 * component's concern), so choosing "Configure myself" after a preset keeps
 * whatever restriction that preset had. */
export interface GuardrailValue {
  read: boolean;
  write: boolean;
  send: boolean;
  approvalActions: string[];
  approvalEur: number | null;
  only: string[];
}

/** Mirrors `GuardrailAdjustableDTO` (`backend/src/oc8/schemas/dto.py`)
 * exactly -- one number on a `GuardrailLibraryEntry` the wizard lets an
 * operator change before applying it. In every real guardrail shipped today
 * (`plugins/odoo_mcp/guardrails/`) `field` is `"approval_eur"`; the type
 * stays general because the schema is, but the renderer below only knows how
 * to edit that one field and skips (never crashes on) any other name. */
export interface GuardrailAdjustable {
  field: string;
  label: string;
  labelEn: string;
  unit: string | null;
  min: number | null;
  max: number | null;
}

/** Mirrors `GuardrailDTO` (`backend/src/oc8/schemas/dto.py`) exactly -- one
 * named, documented ERP scenario from a plugin's own `guardrails/*.toml`
 * library (design §3-4), grouped in the wizard by `useCase`. Unlike `GuardrailPreset`
 * this is not a permission ceiling picked from a fixed five -- it names a
 * situation ("approve quotes above an amount") and says which of its numbers
 * an operator is expected to change (`adjustable`).
 *
 * `only` carries the exact same weight here as on `GuardrailPreset` -- see
 * that interface's doc comment above. Dropping it anywhere in this
 * component's library-selection path is the same silent widening bug under a
 * different name. */
export interface GuardrailLibraryEntry {
  key: string;
  label: string;
  labelEn: string;
  summary: string;
  summaryEn: string;
  useCase: string;
  read: boolean;
  write: boolean;
  send: boolean;
  approvalEur: number | null;
  approvalActions: string[];
  only: string[];
  adjustable: GuardrailAdjustable[];
}

function sameSet(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  return [...a].sort().join(",") === [...b].sort().join(",");
}

function matches(preset: GuardrailPreset, value: GuardrailValue, hasValueSpec: boolean): boolean {
  return (
    preset.read === value.read &&
    preset.write === value.write &&
    preset.send === value.send &&
    sameSet(preset.approvalActions, value.approvalActions) &&
    sameSet(preset.only, value.only) &&
    (!hasValueSpec || preset.approvalEur === value.approvalEur)
  );
}

function policyOf(preset: GuardrailPreset): GuardrailValue {
  return {
    read: preset.read,
    write: preset.write,
    send: preset.send,
    approvalActions: preset.approvalActions,
    approvalEur: preset.approvalEur,
    only: preset.only,
  };
}

/** Known `use_case` values from the reference library
 * (`plugins/odoo_mcp/guardrails/`, design §7), bilingual. `use_case` is a
 * plain string, not an enum (design §4) -- a plugin for a system oc8 has
 * never seen must be able to name its own domain -- so this is a lookup, not
 * an exhaustive type, and `formatUseCaseHeading` below falls back to a
 * humanized rendering of the raw string for anything not in this table. That fallback
 * is the one place this component may show text that isn't drawn from a
 * `t("en", "de")` pair, because there is no bilingual pair for a domain name
 * oc8 doesn't know about yet -- the same reasoning that already lets a raw
 * tool name (e.g. in `only`) render unmodified elsewhere in this file. */
const USE_CASE_LABELS: Record<string, [en: string, de: string]> = {
  sales: ["Sales", "Vertrieb"],
  helpdesk: ["Helpdesk", "Helpdesk"],
  purchasing: ["Purchasing", "Einkauf"],
  finance: ["Finance", "Finanzen"],
  inventory: ["Inventory", "Lager"],
  cross_cutting: ["Cross-cutting", "Bereichsübergreifend"],
};

// NOT named `useCaseHeading` -- that spelling makes eslint-plugin-react-hooks
// treat it as a hook (any `use*`-prefixed function is), which then rejects
// calling it from inside the `.map()` callback below.
function formatUseCaseHeading(useCase: string, t: (en: string, de: string) => string): string {
  const known = USE_CASE_LABELS[useCase];
  if (known) return t(known[0], known[1]);
  return useCase.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Groups library entries by `useCase` in first-seen order.
 *
 * Historically this preserved the plugin author's TOML authoring order.
 * Since the plugin-restructure's one-file-per-guardrail `guardrails/`
 * layout (design §2), the backend reads entries via a sorted directory glob,
 * so the array arrives alphabetical by guardrail key, not authoring order --
 * this function still groups deterministically, it just no longer reflects
 * an author's deliberate sequencing. Restoring authoring order would need an
 * explicit `order` field in the guardrail format; accepted as out of scope
 * for the restructure (controller ruling, Plugin Package Restructure plan,
 * Task 16). */
function groupByUseCase(
  entries: GuardrailLibraryEntry[],
): Array<[string, GuardrailLibraryEntry[]]> {
  const order: string[] = [];
  const byUseCase = new Map<string, GuardrailLibraryEntry[]>();
  for (const entry of entries) {
    if (!byUseCase.has(entry.useCase)) {
      byUseCase.set(entry.useCase, []);
      order.push(entry.useCase);
    }
    byUseCase.get(entry.useCase)!.push(entry);
  }
  return order.map((useCase) => [useCase, byUseCase.get(useCase)!]);
}

/** The `adjustable` entries this component actually knows how to render.
 * Every real entry in `plugins/odoo_mcp/guardrails/` only ever adjusts
 * `approval_eur` (confirmed by reading that file), and `GuardrailValue` has
 * a slot for exactly that field and no other. A future `adjustable.field`
 * naming anything else is filtered out here -- skipped, not crashed on --
 * rather than inventing UI for a case that doesn't occur in real data. */
function supportedAdjustable(entry: GuardrailLibraryEntry): GuardrailAdjustable[] {
  return entry.adjustable.filter((a) => a.field === "approval_eur");
}

function libraryPolicyOf(entry: GuardrailLibraryEntry): GuardrailValue {
  return {
    read: entry.read,
    write: entry.write,
    send: entry.send,
    approvalActions: entry.approvalActions,
    approvalEur: entry.approvalEur,
    only: entry.only,
  };
}

/** Same derivation strategy as `matches()` above -- the selected entry is
 * read back from `value`, not tracked as separate state -- except that when
 * the entry declares `approval_eur` adjustable, the shipped default is
 * allowed to differ from the live value: that is the "fine-tune before
 * applying" step design §6 describes, and requiring exact equality there
 * would make editing the revealed number field immediately read back as
 * "custom", defeating the point of showing it inline under the still-selected
 * entry. */
function libraryMatches(
  entry: GuardrailLibraryEntry,
  value: GuardrailValue,
  hasValueSpec: boolean,
): boolean {
  const eurIsAdjustable = entry.adjustable.some((a) => a.field === "approval_eur");
  return (
    entry.read === value.read &&
    entry.write === value.write &&
    entry.send === value.send &&
    sameSet(entry.approvalActions, value.approvalActions) &&
    sameSet(entry.only, value.only) &&
    (!hasValueSpec || eurIsAdjustable || entry.approvalEur === value.approvalEur)
  );
}

/** A small non-interactive chip using the SAME words the free controls use
 * (`Read`/`Write`/`Send`), so a preset's card shows what it grants without
 * requiring the operator to trust the preset's name. */
function GrantChip({ on, label }: { on: boolean; label: string }) {
  return (
    <span
      className={cn(
        "rounded-full border px-2 py-0.5 text-[11px]",
        on
          ? "border-primary/60 bg-primary/10 text-primary"
          : "border-border text-muted-foreground line-through",
      )}
    >
      {label}
    </span>
  );
}

/** Renders a plugin's named guardrail presets (recommended first) plus an
 * always-last "decide myself" option that reveals free controls. A plugin
 * with no presets renders only the free controls -- no empty chooser.
 *
 * The selected preset is DERIVED by comparing `value` (and, importantly,
 * `value.only`) to each preset's policy, not tracked as separate state:
 * editing any control after picking a preset makes it stop matching, so the
 * screen never claims a preset is in force when the values have drifted
 * from it. "Configure myself" additionally carries one bit of local UI
 * state (`manualCustom`) purely to stay revealed when clicked on a value
 * that happens to match a preset exactly -- clicking a preset button clears
 * it again. */
export function GuardrailPresetPicker({
  presets,
  guardrailLibrary,
  hasValueSpec,
  value,
  onChange,
}: {
  presets: GuardrailPreset[];
  /** A connection's plugin's own `guardrails/*.toml` library (design §3-6),
   * `null`/absent for a plugin that ships none -- the common case today.
   * When non-empty this REPLACES the generic `presets` list below rather
   * than supplementing it (design §6: "should NOT be shown alongside a
   * library; two lists of overlapping suggestions is worse than either"). */
  guardrailLibrary?: GuardrailLibraryEntry[] | null;
  hasValueSpec: boolean;
  value: GuardrailValue;
  onChange: (next: GuardrailValue) => void;
}) {
  const t = useT();
  const [manualCustom, setManualCustom] = useState(false);

  // Library branch, kept entirely separate from -- and returning before --
  // the generic-preset path below so that path's markup/logic stays exactly
  // what it was before this feature: a byte-for-byte backward-compat
  // guarantee for every connection whose plugin ships no library (design §6,
  // "a plugin with no library falls back to today's behaviour exactly").
  if (guardrailLibrary && guardrailLibrary.length > 0) {
    const groups = groupByUseCase(guardrailLibrary);
    const matchedEntry =
      guardrailLibrary.find((entry) => libraryMatches(entry, value, hasValueSpec)) ?? null;
    const isCustom = matchedEntry === null || manualCustom;
    return (
      <div className="space-y-4">
        <div className="flex flex-col gap-4">
          {groups.map(([useCase, entries]) => (
            <div key={useCase} className="flex flex-col gap-2">
              <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                {formatUseCaseHeading(useCase, t)}
              </div>
              {entries.map((entry) => {
                const selected = matchedEntry?.key === entry.key && !manualCustom;
                const adjustable = supportedAdjustable(entry);
                return (
                  <div key={entry.key} className="flex flex-col gap-2">
                    <button
                      type="button"
                      aria-pressed={selected}
                      onClick={() => {
                        setManualCustom(false);
                        onChange(libraryPolicyOf(entry));
                      }}
                      className={cn(
                        "rounded-md border p-3 text-left transition",
                        selected
                          ? "border-primary bg-primary/10"
                          : "border-border hover:bg-muted/40",
                      )}
                    >
                      <span className="text-sm font-medium text-foreground">
                        {t(entry.labelEn, entry.label)}
                      </span>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {t(entry.summaryEn, entry.summary)}
                      </p>
                      <div className="mt-2 flex flex-wrap items-center gap-1.5">
                        <GrantChip on={entry.read} label={t("Read", "Lesen")} />
                        <GrantChip on={entry.write} label={t("Write", "Schreiben")} />
                        <GrantChip on={entry.send} label={t("Send", "Senden")} />
                        {entry.approvalActions.length > 0 && (
                          <span className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">
                            {t("Approval for:", "Freigabe für:")}{" "}
                            {entry.approvalActions
                              .map((a) => {
                                // Unlike `GuardrailPreset.approvalActions` (generic-preset
                                // branch below), a library `Guardrail.approval_actions` is
                                // NOT restricted to rights -- it can name a real tool (e.g.
                                // "post_message"), which is the whole point of an entry like
                                // `helpdesk_reply_needs_approval` (gate one send-capable tool,
                                // leave the rest of `send` ungated). Only "write"/"send"
                                // literals get the bilingual right label; anything else is a
                                // raw tool name, rendered unmodified -- same convention as
                                // `entry.only.join(", ")` below.
                                if (a === "write") return t("writes", "Schreiben");
                                if (a === "send") return t("sends", "Senden");
                                return a;
                              })
                              .join(", ")}
                          </span>
                        )}
                        {hasValueSpec && entry.approvalEur != null && (
                          <span className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">
                            &ge; {entry.approvalEur} €
                          </span>
                        )}
                        {entry.only.length > 0 && (
                          <span className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">
                            {t("Only:", "Nur:")} {entry.only.join(", ")}
                          </span>
                        )}
                      </div>
                    </button>
                    {selected && adjustable.length > 0 && (
                      <div className="ml-3 space-y-2 rounded-md border border-border/70 bg-background/30 p-3">
                        {adjustable.map((adj) => (
                          <label
                            key={adj.field}
                            className="block text-xs uppercase tracking-wider text-muted-foreground"
                          >
                            {t(adj.labelEn, adj.label)}
                            {adj.unit ? ` (${adj.unit})` : ""}
                            <input
                              aria-label={t(adj.labelEn, adj.label)}
                              type="number"
                              min={adj.min ?? undefined}
                              max={adj.max ?? undefined}
                              value={value.approvalEur ?? ""}
                              onChange={(e) =>
                                onChange({
                                  ...value,
                                  approvalEur:
                                    e.target.value.trim() === "" ? null : Number(e.target.value),
                                })
                              }
                              className="mt-1 w-full rounded-md border border-border bg-background/40 px-3 py-2 text-sm normal-case text-foreground outline-none focus:border-primary/50"
                            />
                          </label>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          ))}
          <button
            type="button"
            aria-pressed={isCustom}
            onClick={() => setManualCustom(true)}
            className={cn(
              "rounded-md border p-3 text-left text-sm transition",
              isCustom ? "border-primary bg-primary/10" : "border-border hover:bg-muted/40",
            )}
          >
            {t("Configure myself", "Selbst festlegen")}
          </button>
        </div>
        {isCustom && (
          <div className="space-y-3 rounded-md border border-border p-3">
            <div className="flex flex-wrap gap-2">
              {(["write", "send"] as const).map((right) => (
                <button
                  key={right}
                  type="button"
                  onClick={() => onChange({ ...value, [right]: !value[right] })}
                  className={cn(
                    "rounded-full border px-3 py-1.5 text-sm transition",
                    value[right]
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-border text-muted-foreground",
                  )}
                >
                  {right === "write" ? t("Write", "Schreiben") : t("Send", "Senden")}
                </button>
              ))}
            </div>
            <label className="block text-xs uppercase tracking-wider text-muted-foreground">
              {t("Approval needed for:", "Freigabe nötig für:")}
              <div className="mt-1 flex gap-3">
                {(["write", "send"] as const).map((right) => (
                  <label
                    key={right}
                    className="flex items-center gap-1.5 text-sm normal-case text-foreground"
                  >
                    <input
                      type="checkbox"
                      checked={value.approvalActions.includes(right)}
                      onChange={(e) =>
                        onChange({
                          ...value,
                          approvalActions: e.target.checked
                            ? [...value.approvalActions, right]
                            : value.approvalActions.filter((a) => a !== right),
                        })
                      }
                    />
                    {right === "write" ? t("writes", "Schreiben") : t("sends", "Senden")}
                  </label>
                ))}
              </div>
            </label>
            {hasValueSpec && (
              <label className="block text-xs uppercase tracking-wider text-muted-foreground">
                {t("Approval needed from (€, optional)", "Freigabe nötig ab (€, optional)")}
                <input
                  aria-label="€"
                  type="number"
                  min={0}
                  step={100}
                  value={value.approvalEur ?? ""}
                  onChange={(e) =>
                    onChange({
                      ...value,
                      approvalEur: e.target.value.trim() === "" ? null : Number(e.target.value),
                    })
                  }
                  placeholder="—"
                  className="mt-1 w-full rounded-md border border-border bg-background/40 px-3 py-2 text-sm text-foreground outline-none focus:border-primary/50"
                />
              </label>
            )}
          </div>
        )}
      </div>
    );
  }

  const ordered = [...presets].sort((a, b) => Number(b.recommended) - Number(a.recommended));
  const matchedKey = ordered.find((p) => matches(p, value, hasValueSpec))?.key ?? null;
  const isCustom = matchedKey === null || manualCustom;
  const showFree = presets.length === 0 || isCustom;

  return (
    <div className="space-y-4">
      {presets.length > 0 && (
        <div className="flex flex-col gap-2">
          {ordered.map((preset) => {
            const selected = matchedKey === preset.key && !manualCustom;
            return (
              <button
                key={preset.key}
                type="button"
                aria-pressed={selected}
                onClick={() => {
                  setManualCustom(false);
                  onChange(policyOf(preset));
                }}
                className={cn(
                  "rounded-md border p-3 text-left transition",
                  selected ? "border-primary bg-primary/10" : "border-border hover:bg-muted/40",
                )}
              >
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-foreground">
                    {t(preset.labelEn, preset.label)}
                  </span>
                  {preset.recommended && (
                    <span className="rounded-full bg-primary/20 px-2 py-0.5 text-xs text-primary">
                      {t("Recommended", "Empfohlen")}
                    </span>
                  )}
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {t(preset.summaryEn, preset.summary)}
                </p>
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <GrantChip on={preset.read} label={t("Read", "Lesen")} />
                  <GrantChip on={preset.write} label={t("Write", "Schreiben")} />
                  <GrantChip on={preset.send} label={t("Send", "Senden")} />
                  {preset.approvalActions.length > 0 && (
                    <span className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">
                      {t("Approval for:", "Freigabe für:")}{" "}
                      {preset.approvalActions
                        .map((a) =>
                          a === "write" ? t("writes", "Schreiben") : t("sends", "Senden"),
                        )
                        .join(", ")}
                    </span>
                  )}
                  {hasValueSpec && preset.approvalEur != null && (
                    <span className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">
                      &ge; {preset.approvalEur} €
                    </span>
                  )}
                  {preset.only.length > 0 && (
                    <span className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">
                      {t("Only:", "Nur:")} {preset.only.join(", ")}
                    </span>
                  )}
                </div>
              </button>
            );
          })}
          <button
            type="button"
            aria-pressed={isCustom}
            onClick={() => setManualCustom(true)}
            className={cn(
              "rounded-md border p-3 text-left text-sm transition",
              isCustom ? "border-primary bg-primary/10" : "border-border hover:bg-muted/40",
            )}
          >
            {t("Configure myself", "Selbst festlegen")}
          </button>
        </div>
      )}
      {showFree && (
        <div className="space-y-3 rounded-md border border-border p-3">
          <div className="flex flex-wrap gap-2">
            {(["write", "send"] as const).map((right) => (
              <button
                key={right}
                type="button"
                onClick={() => onChange({ ...value, [right]: !value[right] })}
                className={cn(
                  "rounded-full border px-3 py-1.5 text-sm transition",
                  value[right]
                    ? "border-primary bg-primary/10 text-primary"
                    : "border-border text-muted-foreground",
                )}
              >
                {right === "write" ? t("Write", "Schreiben") : t("Send", "Senden")}
              </button>
            ))}
          </div>
          <label className="block text-xs uppercase tracking-wider text-muted-foreground">
            {t("Approval needed for:", "Freigabe nötig für:")}
            <div className="mt-1 flex gap-3">
              {(["write", "send"] as const).map((right) => (
                <label
                  key={right}
                  className="flex items-center gap-1.5 text-sm normal-case text-foreground"
                >
                  <input
                    type="checkbox"
                    checked={value.approvalActions.includes(right)}
                    onChange={(e) =>
                      onChange({
                        ...value,
                        approvalActions: e.target.checked
                          ? [...value.approvalActions, right]
                          : value.approvalActions.filter((a) => a !== right),
                      })
                    }
                  />
                  {right === "write" ? t("writes", "Schreiben") : t("sends", "Senden")}
                </label>
              ))}
            </div>
          </label>
          {hasValueSpec && (
            <label className="block text-xs uppercase tracking-wider text-muted-foreground">
              {t("Approval needed from (€, optional)", "Freigabe nötig ab (€, optional)")}
              <input
                aria-label="€"
                type="number"
                min={0}
                step={100}
                value={value.approvalEur ?? ""}
                onChange={(e) =>
                  onChange({
                    ...value,
                    approvalEur: e.target.value.trim() === "" ? null : Number(e.target.value),
                  })
                }
                placeholder="—"
                className="mt-1 w-full rounded-md border border-border bg-background/40 px-3 py-2 text-sm text-foreground outline-none focus:border-primary/50"
              />
            </label>
          )}
        </div>
      )}
    </div>
  );
}
