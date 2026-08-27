import type { LucideIcon } from "lucide-react";

/**
 * Build-time contribution from a trusted product edition. This is deliberately
 * separate from the Marketplace plugin contract: an edition is shipped with
 * the application, while plugins remain tenant-installed artefacts.
 */
export interface FrontendEditionExtension {
  readonly id: string;
  readonly navigation?: readonly EditionNavigationItem[];
  readonly capabilities?: readonly string[];
}

/** A sidebar entry that an edition adds to the shared Community shell. */
export interface EditionNavigationItem {
  readonly id: string;
  readonly to: string;
  readonly label: LocalizedLabel;
  readonly icon: LucideIcon;
  readonly needs?: string;
  readonly strict?: boolean;
}

export interface LocalizedLabel {
  readonly en: string;
  readonly de: string;
}
