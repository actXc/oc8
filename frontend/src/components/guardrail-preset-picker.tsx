import { useState } from "react";
import { resolveTranslation, useLang, useT } from "@/lib/i18n";
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
  labelTranslations: Record<string, string>;
  summary: string;
  summaryTranslations: Record<string, string>;
  recommended: boolean;
  read: boolean;
  modify: boolean;
  approvalActions: string[];
  approvalEur: number | null;
  /** The only tool names this preset puts within reach; empty means all of
   * them. Must survive selection into `GuardrailValue` -- see the module
   * doc above. */
  only: string[];
}

/** The policy actually applied to a connection -- what a preset selection
 * writes, and what the always-visible free controls below it edit directly.
 * Carries `only` for the same reason `GuardrailPreset` does: it is part of
 * the policy, not metadata about it. The free controls never widen `only`
 * (there is no control for it here -- the tool catalog behind those names is
 * not this component's concern), so continuing to edit after a preset click
 * keeps whatever restriction that preset had. */
export interface GuardrailValue {
  read: boolean;
  modify: boolean;
  approvalActions: string[];
  approvalEur: number | null;
  only: string[];
}

/** Mirrors `GuardrailAdjustableDTO` (`backend/src/oc8/schemas/dto.py`)
 * exactly -- carried on `GuardrailLibraryEntry` for wire-format fidelity with
 * the backend, though this component no longer renders a special per-entry
 * control for it: the shared `FreeGuardrailControls` €-field (always visible
 * below the entry list) is the one place `approval_eur` gets edited now,
 * regardless of which entry, if any, is selected. */
export interface GuardrailAdjustable {
  field: string;
  label: string;
  labelTranslations: Record<string, string>;
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
  labelTranslations: Record<string, string>;
  summary: string;
  summaryTranslations: Record<string, string>;
  useCase: string;
  read: boolean;
  modify: boolean;
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
    preset.modify === value.modify &&
    sameSet(preset.approvalActions, value.approvalActions) &&
    sameSet(preset.only, value.only) &&
    (!hasValueSpec || preset.approvalEur === value.approvalEur)
  );
}

function policyOf(preset: GuardrailPreset): GuardrailValue {
  return {
    read: preset.read,
    modify: preset.modify,
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

function libraryPolicyOf(entry: GuardrailLibraryEntry): GuardrailValue {
  return {
    read: entry.read,
    modify: entry.modify,
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
    entry.modify === value.modify &&
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

/** Free-text add/remove for `approvalActions` entries beyond the `modify`
 * checkbox rendered alongside it. That checkbox covers the one literal
 * right `authorize_tool_call` understands directly; this input lets an
 * operator additionally gate one specific tool call by name (e.g.
 * `delete_record`) that a blanket `modify` gate wouldn't otherwise single
 * out for approval. Renders only the non-`modify` entries as removable
 * chips -- the checkbox above already owns that one. */
function FreeTextApprovalActions({
  value,
  onChange,
  t,
}: {
  value: GuardrailValue;
  onChange: (next: GuardrailValue) => void;
  t: (en: string, de: string) => string;
}) {
  const [draft, setDraft] = useState("");
  const custom = value.approvalActions.filter((a) => a !== "modify");

  function addDraft() {
    const name = draft.trim();
    setDraft("");
    if (!name || value.approvalActions.includes(name)) return;
    onChange({ ...value, approvalActions: [...value.approvalActions, name] });
  }

  return (
    <label className="block text-xs uppercase tracking-wider text-muted-foreground">
      {t(
        "Also require approval for a specific tool (optional)",
        "Zusätzlich Freigabe für ein bestimmtes Tool (optional)",
      )}
      <div className="mt-1 flex flex-wrap items-center gap-1.5">
        {custom.map((name) => (
          <span
            key={name}
            className="inline-flex items-center gap-1 rounded-full border border-border bg-background/40 px-2 py-0.5 text-[11px] normal-case text-foreground"
          >
            {name}
            <button
              type="button"
              aria-label={t(`Remove ${name}`, `${name} entfernen`)}
              onClick={() =>
                onChange({
                  ...value,
                  approvalActions: value.approvalActions.filter((a) => a !== name),
                })
              }
              className="text-muted-foreground hover:text-foreground"
            >
              ×
            </button>
          </span>
        ))}
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              addDraft();
            }
          }}
          onBlur={addDraft}
          placeholder={t("e.g. delete_record", "z. B. delete_record")}
          className="min-w-[140px] flex-1 rounded-md border border-border bg-background/40 px-2 py-1 text-xs normal-case text-foreground outline-none focus:border-primary/50"
        />
      </div>
    </label>
  );
}

/** The free controls (Modify, approval-actions, €-threshold) -- always
 * rendered below the preset/library list, never gated behind a "does the
 * current value happen to match a preset" check. A preset or library entry
 * is a quick-fill shortcut for this same editor, not a separate locked
 * state: once applied, the result is a plain, independent policy record
 * with no further tie to the capa that suggested it, so it must stay
 * editable the same way a from-scratch policy is. */
function FreeGuardrailControls({
  value,
  onChange,
  hasValueSpec,
  t,
}: {
  value: GuardrailValue;
  onChange: (next: GuardrailValue) => void;
  hasValueSpec: boolean;
  t: (en: string, de: string) => string;
}) {
  return (
    <div className="space-y-3 rounded-md border border-border p-3">
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => onChange({ ...value, modify: !value.modify })}
          className={cn(
            "rounded-full border px-3 py-1.5 text-sm transition",
            value.modify
              ? "border-primary bg-primary/10 text-primary"
              : "border-border text-muted-foreground",
          )}
        >
          {t("Modify", "Verändern")}
        </button>
      </div>
      <label className="block text-xs uppercase tracking-wider text-muted-foreground">
        {t("Approval needed for:", "Freigabe nötig für:")}
        <div className="mt-1 flex gap-3">
          <label className="flex items-center gap-1.5 text-sm normal-case text-foreground">
            <input
              type="checkbox"
              checked={value.approvalActions.includes("modify")}
              onChange={(e) =>
                onChange({
                  ...value,
                  approvalActions: e.target.checked
                    ? [...value.approvalActions, "modify"]
                    : value.approvalActions.filter((a) => a !== "modify"),
                })
              }
            />
            {t("modifies", "verändert")}
          </label>
        </div>
      </label>
      <FreeTextApprovalActions value={value} onChange={onChange} t={t} />
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
  );
}

/** Renders a plugin's named guardrail presets (recommended first), each a
 * quick-fill shortcut into the always-visible free controls below -- not a
 * locked, mutually-exclusive state. Clicking one seeds `value` with that
 * preset's exact policy; the free controls right underneath then let the
 * operator keep editing from there, since the applied policy is now a plain,
 * independent record with no further dependency on the capa that suggested
 * it. A plugin with no presets renders only the free controls -- no empty
 * chooser.
 *
 * The "selected" highlight on a preset card is DERIVED by comparing `value`
 * (and, importantly, `value.only`) to each preset's policy, not tracked as
 * separate state -- purely visual feedback for "this preset currently
 * describes your active values," not a gate on editing. */
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
  const { lang } = useLang();

  // Library branch, kept entirely separate from -- and returning before --
  // the generic-preset path below so that path's markup/logic stays exactly
  // what it was before this feature, aside from the shared unification below
  // (design §6, "a plugin with no library falls back to today's behaviour
  // exactly").
  if (guardrailLibrary && guardrailLibrary.length > 0) {
    const groups = groupByUseCase(guardrailLibrary);
    const matchedEntry =
      guardrailLibrary.find((entry) => libraryMatches(entry, value, hasValueSpec)) ?? null;
    return (
      <div className="space-y-4">
        <div className="flex flex-col gap-4">
          {groups.map(([useCase, entries]) => (
            <div key={useCase} className="flex flex-col gap-2">
              <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                {formatUseCaseHeading(useCase, t)}
              </div>
              {entries.map((entry) => {
                const selected = matchedEntry?.key === entry.key;
                return (
                  <button
                    key={entry.key}
                    type="button"
                    aria-pressed={selected}
                    onClick={() => onChange(libraryPolicyOf(entry))}
                    className={cn(
                      "rounded-md border p-3 text-left transition",
                      selected ? "border-primary bg-primary/10" : "border-border hover:bg-muted/40",
                    )}
                  >
                    <span className="text-sm font-medium text-foreground">
                      {resolveTranslation(entry.label, entry.labelTranslations, lang)}
                    </span>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {resolveTranslation(entry.summary, entry.summaryTranslations, lang)}
                    </p>
                    <div className="mt-2 flex flex-wrap items-center gap-1.5">
                      <GrantChip on={entry.read} label={t("Read", "Lesen")} />
                      <GrantChip on={entry.modify} label={t("Modify", "Verändern")} />
                      {entry.approvalActions.length > 0 && (
                        <span className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">
                          {t("Approval for:", "Freigabe für:")}{" "}
                          {entry.approvalActions
                            .map((a) => {
                              // Unlike `GuardrailPreset.approvalActions` (generic-preset
                              // branch below), a library `Guardrail.approval_actions` is
                              // NOT restricted to rights -- it can name a real tool (e.g.
                              // "post_message"), which is the whole point of an entry like
                              // `helpdesk_reply_needs_approval` (gate one modify-capable tool,
                              // leave the rest of `modify` ungated). Only the "modify"
                              // literal gets the bilingual right label; anything else is a
                              // raw tool name, rendered unmodified -- same convention as
                              // `entry.only.join(", ")` below.
                              if (a === "modify") return t("modifies", "verändert");
                              return a; // a real tool name, rendered unmodified
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
                );
              })}
            </div>
          ))}
        </div>
        <FreeGuardrailControls
          value={value}
          onChange={onChange}
          hasValueSpec={hasValueSpec}
          t={t}
        />
      </div>
    );
  }

  const ordered = [...presets].sort((a, b) => Number(b.recommended) - Number(a.recommended));
  const matchedKey = ordered.find((p) => matches(p, value, hasValueSpec))?.key ?? null;

  return (
    <div className="space-y-4">
      {presets.length > 0 && (
        <div className="flex flex-col gap-2">
          {ordered.map((preset) => {
            const selected = matchedKey === preset.key;
            return (
              <button
                key={preset.key}
                type="button"
                aria-pressed={selected}
                onClick={() => onChange(policyOf(preset))}
                className={cn(
                  "rounded-md border p-3 text-left transition",
                  selected ? "border-primary bg-primary/10" : "border-border hover:bg-muted/40",
                )}
              >
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-foreground">
                    {resolveTranslation(preset.label, preset.labelTranslations, lang)}
                  </span>
                  {preset.recommended && (
                    <span className="rounded-full bg-primary/20 px-2 py-0.5 text-xs text-primary">
                      {t("Recommended", "Empfohlen")}
                    </span>
                  )}
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {resolveTranslation(preset.summary, preset.summaryTranslations, lang)}
                </p>
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <GrantChip on={preset.read} label={t("Read", "Lesen")} />
                  <GrantChip on={preset.modify} label={t("Modify", "Verändern")} />
                  {preset.approvalActions.length > 0 && (
                    <span className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">
                      {t("Approval for:", "Freigabe für:")}{" "}
                      {preset.approvalActions
                        .map((a) => {
                          if (a === "modify") return t("modifies", "verändert");
                          return a; // a real tool name, rendered unmodified
                        })
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
        </div>
      )}
      <FreeGuardrailControls value={value} onChange={onChange} hasValueSpec={hasValueSpec} t={t} />
    </div>
  );
}
