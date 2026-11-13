"""Strict request schema for the 7-field build request (M1).

Static checks here (types, sanity caps); catalog-dependent checks (airports, countries,
nights range, data window) live in app/services/request_validation.py using domain.options().
"""

from __future__ import annotations

import re
from datetime import date
from typing import Annotated, Literal, Union

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


# ---------------------------------------------------------------- change requests (PATCH body)

_Id = Annotated[str, Field(min_length=1, max_length=64)]
_Date = Annotated[str, Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]


class _ChangeBase(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class ReplaceHotelIn(_ChangeBase):
    op: Literal["replace_hotel"]
    item_id: _Id
    hotel_id: _Id
    room_id: _Id


class ReplaceFlightIn(_ChangeBase):
    op: Literal["replace_flight"]
    item_id: _Id
    flight_id: _Id


class AddAttractionIn(_ChangeBase):
    op: Literal["add_attraction"]
    attraction_id: _Id
    date: _Date
    start_time: Annotated[str, Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")] | None = None


class RemoveAttractionIn(_ChangeBase):
    op: Literal["remove_attraction"]
    item_id: _Id


class ChangeCityPlanIn(_ChangeBase):  # G3 extension body (Tanish's 0d7c931 proposal; contract doc update pending)
    op: Literal["change_city_plan"]
    cities: Annotated[list[_Id], Field(min_length=1, max_length=10)]
    nights: Annotated[list[Annotated[int, Field(ge=1, le=60)]], Field(min_length=1, max_length=10)]


ChangeIn = Annotated[
    Union[ReplaceHotelIn, ReplaceFlightIn, AddAttractionIn, RemoveAttractionIn, ChangeCityPlanIn],
    Field(discriminator="op"),
]
