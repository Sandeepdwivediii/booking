"""Phase 1: catalog loader validation, indexes, read-only guarantees and options()."""

from __future__ import annotations

import json

import pytest

from app.contracts import CatalogUnavailableError, DomainPort
from app.domain.adapter import Domain, create_domain
from app.domain.catalog import CATALOG_FILES, load_catalog
from tests.domain_fixtures.catalog_builder import OFFICIAL_CATALOG_DIR, synthetic_catalog, write_catalog


@pytest.fixture
def cat(tmp_path):
    return load_catalog(write_catalog(tmp_path, synthetic_catalog()))


def _errors(tmp_path, data, **kw) -> list[str]:
    write_catalog(tmp_path, data, **kw)
    with pytest.raises(CatalogUnavailableError) as exc:
        load_catalog(tmp_path)
    return exc.value.details["errors"]


# ------------------------------------------------------------------ validation failures

@pytest.mark.parametrize("name", CATALOG_FILES)
def test_missing_file_fails(tmp_path, name):
    assert f"missing catalog file: {name}.json" in _errors(tmp_path, synthetic_catalog(), omit=(name,))


def test_missing_directory_fails(tmp_path):
    with pytest.raises(CatalogUnavailableError):
        load_catalog(tmp_path / "nope")


def test_corrupt_json_fails(tmp_path):
    write_catalog(tmp_path, synthetic_catalog())
    (tmp_path / "flights.json").write_text("[{not json", encoding="utf-8")
    with pytest.raises(CatalogUnavailableError) as exc:
        load_catalog(tmp_path)
    assert "flights.json is not valid UTF-8 JSON" in exc.value.details["errors"][0]


def test_wrong_top_level_type_fails(tmp_path):
    data = synthetic_catalog()
    data["hotels"] = {"hotel_id": "x"}
    assert "hotels.json must contain a JSON list" in _errors(tmp_path, data)


@pytest.mark.parametrize("name, key", [
    ("flights", "flight_id"), ("hotels", "hotel_id"), ("rooms", "room_id"),
    ("transfers", "transfer_id"), ("attractions", "attraction_id"), ("airports", "code"),
])
def test_duplicate_natural_id_fails(tmp_path, name, key):
    data = synthetic_catalog()
    data[name].append(dict(data[name][-1]))
    dup = data[name][-1][key]
    assert any(f"duplicate {key} {dup!r}" in e for e in _errors(tmp_path, data))


def test_duplicate_availability_night_fails(tmp_path):
    data = synthetic_catalog()
    data["availability"].append(dict(data["availability"][0]))
    assert any("duplicate row for ('RM-T1-A', '2027-04-10')" in e for e in _errors(tmp_path, data))


@pytest.mark.parametrize("mutate, expected", [
    (lambda d: d["availability"].append({"room_id": "RM-GHOST", "date": "2027-04-10", "rooms_left": 1}),
     "unknown room_id 'RM-GHOST'"),
    (lambda d: d["flights"][0].update({"to": "XXX"}), "unknown airport"),
    (lambda d: d["destinations"]["countries"][0]["links"][0].update({"transfer_ids": ["TR-NOPE"]}),
     "unknown transfer_id 'TR-NOPE'"),
    (lambda d: d["destinations"]["countries"][0]["links"][0].update({"from_city": "Kyoto", "to_city": "Tokyo"}),
     "is not an intercity transfer for that direction"),
    (lambda d: d["hotels"][0].update({"city": "Nara"}), "is not a destination city"),
    (lambda d: d["destinations"]["departure_cities"].append({"airport": "XYZ", "city": "Nowhere"}),
     "needs a known airport"),
    (lambda d: d["airports"][1].update({"serves_cities": ["Atlantis"]}), "serves unknown city 'Atlantis'"),
    (lambda d: d["destinations"]["countries"][0]["cities"][0].update({"airports": ["KIX"]}),
     "unknown or does not serve it"),
    (lambda d: d["transfers"][0].update({"airport": "ZZZ"}), "needs a known airport and a city"),
    (lambda d: d["transfers"][0].update({"type": "HOVERCRAFT"}), "unknown type 'HOVERCRAFT'"),
    (lambda d: d["transfers"][0].update({"pricing": "PER_GROUP"}), "unknown pricing"),
    (lambda d: d["transfers"][0].update({"service_until": "25:00"}), "service window must be HH:MM"),
    (lambda d: d["rooms"][0].update({"price_per_night": {"amount": 10.5, "currency": "INR"}}),
     "price_per_night must be an INR integer amount"),
    (lambda d: d["flights"][0].update({"price_per_seat": {"amount": 100, "currency": "USD"}}),
     "price_per_seat must be an INR integer amount"),
    (lambda d: d["flights"][0].update({"seats_left": -1}), "seats_left/duration_minutes"),
    (lambda d: d["availability"][0].update({"rooms_left": True}), "rooms_left must be an int >= 0"),
    (lambda d: d["availability"][0].update({"date": "2027-02-30"}), "bad date"),
    (lambda d: d["attractions"][0].update({"open_days": ["MON", "FUNDAY"]}), "open_days"),
    (lambda d: d["attractions"][0].update({"closed_dates": "2027-04-12"}), "closed_dates must be a list"),
    (lambda d: d["flights"][0].pop("seats_left"), "missing fields ['seats_left']"),
    (lambda d: d["destinations"].pop("rules"), "destinations.json needs"),
])
def test_cross_reference_and_value_errors(tmp_path, mutate, expected):
    data = synthetic_catalog()
    mutate(data)
    errs = _errors(tmp_path, data)
    assert any(expected in e for e in errs), errs


def test_reports_all_problems_not_just_first(tmp_path):
    data = synthetic_catalog()
    data["rooms"][0]["hotel_id"] = "HTL-X"
    data["flights"][0]["to"] = "XXX"
    write_catalog(tmp_path, data)
    with pytest.raises(CatalogUnavailableError) as exc:
        load_catalog(tmp_path)
    assert exc.value.details["error_count"] >= 2


# ------------------------------------------------------------------ indexes

def test_flight_indexes_use_local_dates(cat):
    # Departs India 9 Apr, arrives Japan 10 Apr (FL-01): indexed under both local dates.
    assert [f["flight_id"] for f in cat.flights_by_arrival[("BLR", "NRT", "2027-04-10")]] == ["FL-1"]
    assert [f["flight_id"] for f in cat.flights_by_departure[("BLR", "NRT", "2027-04-09")]] == ["FL-1"]
    assert ("BLR", "NRT", "2027-04-10") not in cat.flights_by_departure


def test_flight_index_is_cheapest_first_and_deterministic(tmp_path):
    data = synthetic_catalog()
    extra = dict(data["flights"][0], flight_id="FL-0", price_per_seat={"amount": 20000, "currency": "INR"})
    data["flights"].append(extra)
    cat = load_catalog(write_catalog(tmp_path, data))
    assert [f["flight_id"] for f in cat.flights_by_arrival[("BLR", "NRT", "2027-04-10")]] == ["FL-0", "FL-1"]


def test_airports_serving_city_comes_from_serves_cities(cat):
    assert cat.airports_serving_city == {"Tokyo": ("NRT",), "Kyoto": ("KIX",)}
    assert "BLR" not in {a for v in cat.airports_serving_city.values() for a in v}


def test_hotel_room_and_availability_indexes(cat):
    assert [h["hotel_id"] for h in cat.hotels_by_city["Tokyo"]] == ["HTL-T1"]
    assert [r["room_id"] for r in cat.rooms_by_hotel["HTL-K1"]] == ["RM-K1-A"]
    assert cat.rooms_left("RM-T1-A", "2027-04-14") == 2
    assert cat.rooms_left("RM-T1-A", "2027-04-15") is None  # missing row -> None, never a default


def test_transfer_indexes_are_directed(cat):
    assert [t["transfer_id"] for t in cat.intercity_transfers[("Tokyo", "Kyoto")]] == ["TR-TYO-KYO"]
    assert ("Kyoto", "Tokyo") not in cat.intercity_transfers  # links are not reversible (CT-05)
    assert [t["transfer_id"] for t in cat.airport_transfers[("NRT", "Tokyo")]] == ["TR-NRT-GNZ"]
    assert cat.airport_transfers[("KIX", "Kyoto")][0]["area"] is None  # wildcard kept as None
    assert [t["transfer_id"] for t in cat.city_transfers["Tokyo"]] == ["TR-TYO-TAXI"]


def test_optional_fields_normalized(cat):
    assert cat.attractions["ATT-K1"]["closed_dates"] == ()  # absent -> empty (AT-16)
    assert cat.attractions["ATT-T1"]["closed_dates"] == ("2027-04-12",)
    assert "child_price" not in cat.attractions["ATT-K1"]  # absent stays absent (no invented price)
    assert cat.attractions["ATT-K1"]["price_per_person"]["amount"] == 0  # free is 0, not missing


def test_catalog_is_read_only(cat):
    with pytest.raises(TypeError):
        cat.flights["FL-1"]["seats_left"] = 99
    with pytest.raises(TypeError):
        cat.availability[("RM-T1-A", "2027-04-10")] = 99
    with pytest.raises(AttributeError):
        cat.flights = {}
    assert cat.flights["FL-1"]["seats_left"] == 3


def test_loading_does_not_modify_source_files(tmp_path):
    write_catalog(tmp_path, synthetic_catalog())
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}
    load_catalog(tmp_path)
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()} == before


# ------------------------------------------------------------------ options() + adapter

def test_options_matches_contract_shape(cat):
    opts = cat.options()
    assert opts == {
        "departure_cities": [{"airport": "BLR", "city": "Bengaluru"}],
        "countries": [{"name": "Japan", "code": "JP", "timezone": "Asia/Tokyo", "cities": ["Tokyo", "Kyoto"]}],
        "rules": {"min_nights": 3, "max_nights": 10, "min_nights_per_city": 2,
                  "first_night": "2027-04-01", "last_departure": "2027-04-20"},
    }
    json.dumps(opts)  # plain JSON types only


def test_options_returns_fresh_dicts(cat):
    first = cat.options()
    first["countries"][0]["cities"].append("Nara")
    first["rules"]["min_nights"] = 1
    assert cat.options()["countries"][0]["cities"] == ["Tokyo", "Kyoto"]
    assert cat.options()["rules"]["min_nights"] == 3


def test_official_options():
    opts = create_domain(str(OFFICIAL_CATALOG_DIR)).options()
    assert [d["airport"] for d in opts["departure_cities"]] == ["BLR", "DEL", "BOM", "MAA"]
    assert {c["name"]: c["cities"] for c in opts["countries"]} == {
        "Japan": ["Tokyo", "Kyoto", "Osaka"], "United Arab Emirates": ["Dubai", "Abu Dhabi"]}
    assert opts["rules"]["first_night"] == "2027-04-01" and opts["rules"]["last_departure"] == "2027-04-20"


def test_adapter_satisfies_port_and_unbuilt_methods_do_not_fake(cat):
    domain = Domain(cat)
    assert isinstance(domain, DomainPort)
    for call in (lambda: domain.build({}, "t"), lambda: domain.validate({}, "initial"),
                 lambda: domain.alternatives({}, "hotel", {}), lambda: domain.change({}, {})):
        with pytest.raises(NotImplementedError):
            call()
