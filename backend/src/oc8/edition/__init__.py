"""Public composition contracts for privileged product editions.

Community owns these contracts.  Edition packages depend on them; this package
must never import an edition implementation.
"""

from oc8.edition.contracts import EditionExtension

__all__ = ["EditionExtension"]
