"""Strict request schema for the 7-field build request (M1).

Static checks here (types, sanity caps); catalog-dependent checks (airports, countries,
nights range, data window) live in app/services/request_validation.py using domain.options().
"""

from __future__ import annotations

import re
from datetime import date
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.contracts import (
    CHILD_MAX_AGE, CHILD_MIN_AGE, MAX_ADULTS, MAX_BUDGET_INR, MAX_CHILDREN, MIN_ADULTS,
)

ChildAge = Annotated[int, Field(ge=CHILD_MIN_AGE, le=CHILD_MAX_AGE)]
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class BuildRequestIn(BaseModel):
    # strict: no "6" -> 6, no 1.5 -> 1, no true -> 1; forbid: client price/end_date/country are rejected
    model_config = ConfigDict(strict=True, extra="forbid")

    departure_city: str = Field(min_length=1, max_length=8)
    destination_country: str = Field(min_length=1, max_length=64)
    start_date: str = Field(min_length=10, max_length=10)
    nights: int = Field(ge=0, le=366)
    adults: int = Field(ge=MIN_ADULTS, le=MAX_ADULTS)
    children_ages: list[ChildAge] = Field(max_length=MAX_CHILDREN)
    budget_inr: int = Field(ge=0, le=MAX_BUDGET_INR)

    @field_validator("start_date")
    @classmethod
    def _iso_date(cls, v: str) -> str:
        msg = "start_date must be a real date in YYYY-MM-DD format"
        if not _DATE_RE.fullmatch(v):
            raise ValueError(msg)
        try:
            date.fromisoformat(v)
        except ValueError:
            raise ValueError(msg) from None
        return v
