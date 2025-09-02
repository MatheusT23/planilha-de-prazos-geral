from __future__ import annotations

"""Helpers for per-scope last_checked timestamps.

This module centralizes reading and writing the `last_checked` table so both
`scrap_email.py` and `scrap_pje.py` share the same logic. Records are keyed by
`scope`, allowing each scraper to track its own progress independently.
"""

from datetime import datetime
from typing import Optional

from db import SessionLocal, LastChecked  # type: ignore


def get_last_checked(scope: str) -> Optional[datetime]:
    """Return the last checked timestamp for the given scope.

    Parameters
    ----------
    scope: str
        Identifier for the scraper (e.g. ``"scrap_email"`` or
        ``"pje_comunica"``).
    """
    with SessionLocal() as db:
        rec = db.query(LastChecked).filter_by(scope=scope).first()
        return rec.checked_at if rec else None


def set_last_checked(scope: str, dt: datetime) -> None:
    """Update the last checked timestamp for the given scope."""
    with SessionLocal() as db:
        rec = db.query(LastChecked).filter_by(scope=scope).first()
        if rec:
            rec.checked_at = dt
        else:
            db.add(LastChecked(scope=scope, checked_at=dt))
        db.commit()
