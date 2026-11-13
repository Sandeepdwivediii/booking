"""Loads the real domain engine (Tanish's app.domain.adapter.create_domain).

There is deliberately NO production fallback: without the real adapter or a valid catalog
the API reports not-ready (503) instead of serving fake packages.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from app.contracts import CatalogUnavailableError, DomainPort

log = logging.getLogger(__name__)

DomainLoader = Callable[[Path], "tuple[DomainPort | None, str | None]"]


def load_domain(catalog_dir: Path) -> tuple[DomainPort | None, str | None]:
    """Return (domain, None) or (None, reason)."""
    try:
        from app.domain.adapter import create_domain
    except ModuleNotFoundError as e:
        if e.name and e.name.startswith("app.domain"):
            return None, "domain engine not integrated yet (app.domain.adapter missing)"
        log.exception("domain adapter import failed")
        return None, "domain adapter failed to import"
    except Exception:
        log.exception("domain adapter import failed")
        return None, "domain adapter failed to import"
    try:
        domain = create_domain(str(catalog_dir))
    except CatalogUnavailableError as e:
        return None, f"catalog unavailable: {e.message}"
    except Exception:
        log.exception("catalog load failed")
        return None, "catalog failed to load"
    if not isinstance(domain, DomainPort):
        return None, "domain adapter does not implement DomainPort"
    return domain, None
