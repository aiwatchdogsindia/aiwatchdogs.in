"""Public dataset connectors for local files (CSV, JSON/OCDS, PDF) and polite Tier-1 HTTP URLs."""

from aiwatchdogs.connectors.loader import (
    USER_AGENT,
    DigestMismatchError,
    load_dataset,
)

__all__ = ["USER_AGENT", "DigestMismatchError", "load_dataset"]
