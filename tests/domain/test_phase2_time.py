"""Phase 2 time rules: local/UTC, 24:00, overnight windows, scheduled departures (TR-09/10/13, FL-11)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.domain.catalog import load_catalog
from app.domain.rules import transfer_arrival, transfer_departure_ok, transfer_in_service, transfer_serves_area
from app.domain.timeutil import (
    UTC_OFFSET_MINUTES, flight_is_chronological, flight_utc, format_local, from_utc, in_service,
    is_scheduled_departure, next_departure, parse_hhmm, parse_local, service_window, to_utc,
)
from tests.domain_fixtures.catalog_builder import OFFICIAL_CATALOG_DIR

T = lambda s: datetime.fromisoformat(s)  # noqa: E731


@pytest.fixture(scope="module")
def official():
    return load_catalog(OFFICIAL_CATALOG_DIR)


# ------------------------------------------------------------------ clock parsing

def test_parse_hhmm_bounds():
    assert parse_hhmm("00:00") == 0 and parse_hhmm("23:59") == 1439 and parse_hhmm("24:00") == 1440
    for bad in ("24:01", "25:00", "7:00", "07:60", "", None, "07:00:00"):
        with pytest.raises(ValueError):
            parse_hhmm(bad)


def test_parse_local_rejects_offsets_and_garbage():
    assert parse_local("2027-04-10T10:30") == parse_local("2027-04-10T10:30:00") == T("2027-04-10T10:30")
    for bad in ("2027-04-10T10:30+09:00", "2027-04-10", "garbage", None):
        with pytest.raises(ValueError):
            parse_local(bad)
    assert format_local(T("2027-04-10T07:05:00")) == "2027-04-10T07:05"


# ------------------------------------------------------------------ TR-09 / TR-10

def test_TR09_overnight_window_pickup_after_midnight():
    assert service_window(date(2027, 4, 10), "06:00", "00:30") == (T("2027-04-10T06:00"), T("2027-04-11T00:30"))
    assert in_service(T("2027-04-11T00:15"), "06:00", "00:30")  # belongs to 10 Apr service day
    assert in_service(T("2027-04-11T00:30"), "06:00", "00:30")  # inclusive end
    assert not in_service(T("2027-04-11T00:31"), "06:00", "00:30")
    assert not in_service(T("2027-04-11T05:59"), "06:00", "00:30")
    assert in_service(T("2027-04-11T06:00"), "06:00", "00:30")
    assert not in_service(T("2027-04-11T03:00"), "06:00", "00:30")  # gap between service days


def test_TR09_official_kix_shuttle(official):
    shuttle = official.transfers["TR-KIX-NMB-SHUTTLE"]
    assert (shuttle["service_from"], shuttle["service_until"]) == ("06:00", "00:30")
    assert transfer_departure_ok(shuttle, T("2027-04-11T00:15"))
    assert not transfer_departure_ok(shuttle, T("2027-04-11T01:00"))


def test_TR10_service_until_2400_is_end_of_day_midnight():
    assert service_window(date(2027, 4, 10), "05:00", "24:00") == (T("2027-04-10T05:00"), T("2027-04-11T00:00"))
    assert in_service(T("2027-04-10T23:59"), "05:00", "24:00")
    assert in_service(T("2027-04-11T00:00"), "05:00", "24:00")  # midnight is the inclusive end
    assert not in_service(T("2027-04-11T00:01"), "05:00", "24:00")
    assert not in_service(T("2027-04-11T04:59"), "05:00", "24:00")
    assert all(in_service(T(f"2027-04-10T{h:02d}:17"), "00:00", "24:00") for h in range(24))


def test_TR10_official_24h_transfers_do_not_crash(official):
    for t in official.transfers.values():
        if "24:00" in (t["service_from"], t["service_until"]):
            assert transfer_in_service(t, T("2027-04-10T23:30"))


# ------------------------------------------------------------------ TR-13

def test_TR13_frequency_defines_discrete_departures():
    assert not is_scheduled_departure(T("2027-04-10T08:37"), "08:00", "20:00", 60)
    assert is_scheduled_departure(T("2027-04-10T08:00"), "08:00", "20:00", 60)
    assert is_scheduled_departure(T("2027-04-10T20:00"), "08:00", "20:00", 60)
    assert not is_scheduled_departure(T("2027-04-10T21:00"), "08:00", "20:00", 60)
    assert is_scheduled_departure(T("2027-04-10T08:37"), "08:00", "20:00", None)  # unscheduled: any time


def test_TR13_next_departure():
    assert next_departure(T("2027-04-10T08:37"), "08:00", "20:00", 60) == T("2027-04-10T09:00")
    assert next_departure(T("2027-04-10T09:00"), "08:00", "20:00", 60) == T("2027-04-10T09:00")
    assert next_departure(T("2027-04-10T06:00"), "08:00", "20:00", 60) == T("2027-04-10T08:00")
    assert next_departure(T("2027-04-10T20:30"), "08:00", "20:00", 60) == T("2027-04-11T08:00")
    assert next_departure(T("2027-04-10T08:37"), "08:00", "20:00", None) == T("2027-04-10T08:37")
    assert next_departure(T("2027-04-11T00:10"), "06:00", "00:30", None) == T("2027-04-11T00:10")
    # Overnight scheduled window 22:00-02:00 every 45m: 22:00, 22:45, 23:30, 00:15, 01:00, 01:45.
    assert next_departure(T("2027-04-11T00:01"), "22:00", "02:00", 45) == T("2027-04-11T00:15")
    assert next_departure(T("2027-04-11T01:46"), "22:00", "02:00", 45) == T("2027-04-11T22:00")


def test_TR13_official_train_schedule(official):
    train = official.transfers["TR-TYO-KYO-01"]  # 06:00-21:30 every 30 minutes
    assert transfer_departure_ok(train, T("2027-04-12T21:30"))
    assert not transfer_departure_ok(train, T("2027-04-12T21:45"))
    assert not transfer_departure_ok(train, T("2027-04-12T09:10"))
    assert transfer_arrival(train, T("2027-04-12T09:00")) == T("2027-04-12T11:15")


def test_transfer_area_wildcard(official):
    kyoto_bus = next(t for t in official.airport_transfers[("KIX", "Kyoto")])
    assert transfer_serves_area(kyoto_bus, "Kyoto", "Gion") and transfer_serves_area(kyoto_bus, "Kyoto", "Arashiyama")
    asakusa = official.transfers["TR-NRT-ASA-TAXI"]
    assert transfer_serves_area(asakusa, "Tokyo", "Asakusa") and not transfer_serves_area(asakusa, "Tokyo", "Ginza")
    assert not transfer_serves_area(asakusa, "Kyoto", "Asakusa")


# ------------------------------------------------------------------ time zones / FL-11

def test_fixed_offsets_match_iana_database():
    zoneinfo = pytest.importorskip("zoneinfo")
    try:
        zones = {name: zoneinfo.ZoneInfo(name) for name in UTC_OFFSET_MINUTES}
    except zoneinfo.ZoneInfoNotFoundError:
        pytest.skip("OS has no IANA tz data; fixed table is authoritative per DATA_DICTIONARY")
    for name, zone in zones.items():
        for day in (date(2027, 3, 31), date(2027, 4, 10), date(2027, 4, 20)):
            off = datetime(day.year, day.month, day.day, 12, tzinfo=zone).utcoffset()
            assert off == timedelta(minutes=UTC_OFFSET_MINUTES[name])


def test_utc_round_trip_and_unknown_zone():
    local = T("2027-04-10T10:30")
    utc = to_utc(local, "Asia/Tokyo")
    assert utc == datetime(2027, 4, 10, 1, 30, tzinfo=timezone.utc)
    assert from_utc(utc, "Asia/Kolkata") == T("2027-04-10T07:00")
    assert from_utc(utc, "Asia/Tokyo") == local
    with pytest.raises(ValueError):
        to_utc(local, "Europe/Paris")


def test_FL11_duration_from_time_zones_not_naive_subtraction(official):
    # BLR 2027-04-09T23:50 IST -> NRT 2027-04-10T10:30 JST style flight.
    flight = next(f for f in official.flights.values() if f["from"] == "BLR" and f["to"] == "NRT")
    naive = (parse_local(flight["arrival_local"]) - parse_local(flight["departure_local"])).total_seconds() / 60
    dep, arr = flight_utc(flight, official.airports)
    assert (arr - dep) == timedelta(minutes=flight["duration_minutes"])
    assert naive != flight["duration_minutes"]  # naive local subtraction is off by the 3h30 zone gap


def test_FL11_all_official_flights_chronological(official):
    assert all(flight_is_chronological(f, official.airports) for f in official.flights.values())


def test_FL11_inconsistent_flight_detected(official):
    flight = dict(next(iter(official.flights.values())))
    flight["duration_minutes"] += 1
    assert not flight_is_chronological(flight, official.airports)
