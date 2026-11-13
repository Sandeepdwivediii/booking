# Data Dictionary: Problem 1 (Holiday Package)

This file explains the mock data in this pack: what each file holds, what one record means, how the files link, and how your app uses them.

- All data is **synthetic** (invented). Real names are used only for cities, airports, and public landmarks.
- The data is **read-only**. Do not change the files. Judges use the same files.
- Flights, rooms, and availability cover **1 to 20 April 2027** only.
- **All prices are in INR** (whole rupees). There is no currency conversion.
- **All times are local times** of the place. See section 5.

---

## 1. Files at a glance

| File | One record is | Records | Your app uses it to |
|---|---|---|---|
| `data/catalog/destinations.json` | The list of departure cities, destination countries, their cities, and the links between cities | 1 object | Fill the request form (M1). Choose cities and the order to visit them (M2). |
| `data/catalog/airports.json` | One airport | 10 | Find the airports that serve a city. Get the time zone. |
| `data/catalog/flights.json` | One flight on one date | 469 | Choose the arrival flight and the return flight |
| `data/catalog/hotels.json` | One hotel | 49 | Choose a hotel in each city |
| `data/catalog/rooms.json` | One room type in one hotel | 125 | Choose the room type, check who fits, get the price per night |
| `data/catalog/availability.json` | One room type on one night | 2,500 | Check that enough rooms are free on every night |
| `data/catalog/transfers.json` | One transfer service (taxi, bus, train) | 66 | Airport to hotel, hotel to airport, and city to city |
| `data/catalog/attractions.json` | One attraction | 56 | Plan visits on each day |
| `data/trip_format_example.json` | A short example of an exported package | 1 | See the JSON shape of the export (section 4) |
| `images/` | One photo (JPG) | 66 | Show hotel and attraction photos (optional). Credits: `images/ATTRIBUTIONS.md`. |

---

## 2. How the files connect

```
destinations.json
  departure_cities[].airport ──────────────► flights.from  (outbound)  /  flights.to  (return)
  countries[].cities[].name ───┬───────────► airports.serves_cities  ◄── flights.to / flights.from
                               ├───────────► hotels.city ──► rooms.hotel_id ──► availability.room_id
                               ├───────────► attractions.city
                               └───────────► transfers.city (+ transfers.area = hotels.area)
  countries[].links[].transfer_ids ────────► transfers.transfer_id  (city to city)
```

### Which file answers which question

| Question | Read | How |
|---|---|---|
| Which countries and cities can the traveler visit? | `destinations.json` | `countries[].cities[].name` |
| Which airports serve Kyoto? | `airports.json` | Airports where `serves_cities` contains `"Kyoto"` (only `KIX`). The same list is in `destinations.json` → `cities[].airports`. |
| Which flights arrive on 10 April from Bengaluru to a Kyoto airport? | `flights.json` | `from` = `BLR`, `to` = `KIX`, and the date part of `arrival_local` = `2027-04-10` |
| Which hotels are in Kyoto? | `hotels.json` | `city` = `"Kyoto"` |
| Which room types does a hotel have, and the price? | `rooms.json` | `hotel_id` = the hotel. Price: `price_per_night`. |
| Are 2 rooms of a type free from 11 to 15 April? | `availability.json` | Records with that `room_id` and `date` 11, 12, 13, and 14 April. Each needs `rooms_left` ≥ 2. The check-out day (15 April) is not a night. |
| How does the traveler get from KIX to a hotel in Gion, Kyoto? | `transfers.json` | `airport` = `KIX`, `city` = `"Kyoto"`, and `area` = `"Gion"` or `area` = `null` (all areas) |
| How does the traveler get from Tokyo to Kyoto? | `destinations.json`, `transfers.json` | The link `from_city` = Tokyo, `to_city` = Kyoto gives `transfer_ids`. Read those records in `transfers.json`. |
| What can the traveler visit in Kyoto on a Monday? | `attractions.json` | `city` = `"Kyoto"`, `open_days` contains `MON`, and the date is not in `closed_dates` |

---

## 3. Files in detail

### `data/catalog/destinations.json`

**What it is:** the start point of the app. It lists where a traveler can start, where they can go, and how cities connect.

**Why you need it:** the request form takes its departure cities and countries from here (M1). Your app chooses the cities for the package from `countries[].cities` (M2). A move from one city to the next needs a link from `countries[].links`.

```json
{
  "departure_cities": [{ "airport": "BLR", "city": "Bengaluru" }, ...],
  "countries": [{
    "code": "JP", "name": "Japan", "timezone": "Asia/Tokyo",
    "cities": [{ "name": "Tokyo", "airports": ["NRT", "HND"] }, { "name": "Kyoto", "airports": ["KIX"] }, ...],
    "links": [{ "from_city": "Tokyo", "to_city": "Kyoto", "transfer_ids": ["TR-TYO-KYO-01"] }, ...]
  }, ...],
  "rules": { "min_nights": 3, "max_nights": 10, "min_nights_per_city": 2,
             "data_window": { "first_night": "2027-04-01", "last_departure": "2027-04-20" } }
}
```

| Field | Meaning | How you use it |
|---|---|---|
| `departure_cities` | Cities in India where a trip can start: `{ "airport", "city" }` (Bengaluru, Delhi, Mumbai, Chennai) | Departure city list in the form. The airport code is the `from` of the arrival flight and the `to` of the return flight. |
| `countries` | Destination countries: Japan and United Arab Emirates | Destination country list in the form |
| `countries[].code`, `name` | Country code and name | Display. The `name` is the value the traveler picks. |
| `countries[].timezone` | Time zone of the country | Local times in this country use this zone |
| `countries[].cities` | Cities your app can choose: `{ "name", "airports" }` | The cities of the package come from this list |
| `countries[].links` | One-way connections between two cities: `{ "from_city", "to_city", "transfer_ids" }` | On a city-change day, use one of the `transfer_ids` |
| `rules` | `min_nights`, `max_nights`, `min_nights_per_city`, and `data_window` (`first_night`, `last_departure`) | Validate the request and the city plan |

### `data/catalog/airports.json`

**What it is:** every airport in the data, in India and in the destination countries.

**Why you need it:** a flight must land at an airport that **serves** the first city, and the return flight must leave from an airport that serves the last city. `serves_cities` tells you which.

```json
{ "code": "KIX", "name": "Kansai International Airport", "city": "Osaka", "country": "JP",
  "timezone": "Asia/Tokyo", "lat": 34.432, "lng": 135.2304, "serves_cities": ["Osaka", "Kyoto"] }
```

| Field | Meaning | How you use it |
|---|---|---|
| `code` | IATA airport code | Matches `flights.from`, `flights.to`, and `transfers.airport` |
| `name`, `city`, `country` | Airport name, the city it is in, and country code (`IN`, `JP`, `AE`) | Display |
| `timezone` | IANA time zone | All flight times at this airport are local to this zone |
| `lat`, `lng` | Location | Optional (maps) |
| `serves_cities` | Cities a traveler can reach from this airport | Rule: the arrival airport serves the first city; the return airport serves the last city |

What to know:
- The 4 Indian airports (`BLR`, `DEL`, `BOM`, `MAA`) have no `serves_cities` field. They are departure airports only.
- `ITM` (Osaka Itami) has `serves_cities: []`: it has no international flights.
- Kyoto has no airport. `KIX` serves Osaka and Kyoto.
- `DXB` serves Dubai and Abu Dhabi. `AUH` serves Abu Dhabi.

### `data/catalog/flights.json`

**What it is:** flights between India and Japan or the UAE, in both directions. One record is one flight on one date.

**Why you need it:** the package has one arrival flight (India → destination) and one return flight (destination → India).

```json
{ "flight_id": "FL-711", "airline": "AirVeda", "flight_number": "AV 127", "from": "BLR", "to": "KIX",
  "departure_local": "2027-04-03T06:30:00", "arrival_local": "2027-04-03T20:30:00", "stops": 1,
  "duration_minutes": 630, "price_per_seat": { "amount": 25200, "currency": "INR" }, "seats_left": 9, "cabin": "ECONOMY" }
```

| Field | Meaning | How you use it |
|---|---|---|
| `flight_id` | Unique ID of this flight on this date | Store it in the package (`catalog_id`) |
| `airline`, `flight_number` | Display only. One flight number repeats on several dates, each with its own `flight_id`. | Display |
| `from`, `to` | Airport codes | Arrival flight: `from` = departure city airport, `to` serves the first city. Return flight: the other way. |
| `departure_local` | Departure time, local time at the `from` airport | Return flight: the date part is the last day of the trip |
| `arrival_local` | Arrival time, local time at the `to` airport | Arrival flight: the date part is the holiday start date. The airport pickup is at least 60 minutes later. |
| `stops` | `0` = direct, `1` = one stop | Display. One-stop flights are allowed. |
| `duration_minutes` | Total travel time, stops included | Display. "Fastest" package (G4): flight minutes + transfer-between-cities minutes. Do not compute it from the two local times (they are in different time zones). |
| `price_per_seat` | Price of one seat. Adults and children pay the same. | Flight price = `price_per_seat` × all travelers |
| `seats_left` | Seats you can still book | Rule: `seats_left` ≥ travelers (children need seats) |
| `cabin` | Always `ECONOMY` | Display |

What to know:
- An overnight flight from India leaves the evening before the holiday start date. The start date is the **arrival** date.
- 240 flights go to the destination countries. 229 flights return to India.

### `data/catalog/hotels.json`

**What it is:** fictional hotels in the 5 destination cities (Tokyo 13, Kyoto 12, Osaka 10, Dubai 8, Abu Dhabi 6).

**Why you need it:** the package has one hotel in each city. The hotel `area` links the hotel to its airport transfers.

```json
{ "hotel_id": "HTL-TYO-001", "name": "Ginza Pearl Hotel", "aliases": [], "city": "Tokyo", "area": "Ginza",
  "lat": 35.6717, "lng": 139.765, "rating": 8.6, "stars": 4, "check_in_from": "15:00", "check_out_until": "11:00",
  "family_friendly": true, "image": "images/hotels/HOTEL-TWIN-1.jpg" }
```

| Field | Meaning | How you use it |
|---|---|---|
| `hotel_id` | Unique ID | Links to `rooms.hotel_id`. Store it in the package (`catalog_id`). |
| `name` | Hotel name | Display |
| `aliases` | Other names. Empty for every hotel in this data. | Optional (search) |
| `city` | City of the hotel | Must be the city of that night in your plan |
| `area` | Part of the city, for example `Ginza` | Find the airport transfer for this area (`transfers.area`) |
| `lat`, `lng` | Location | Optional (maps) |
| `rating` | Guest rating, 6.9 to 9.4 (scale 0–10) | Display, sort. Not used for "best rated". |
| `stars` | Hotel class, 2 to 5 | "Best rated" package (G4): highest average `stars` per night |
| `check_in_from`, `check_out_until` | Local check-in and check-out times | Display only. Judges do not check them. |
| `family_friendly` | `true` = good for families with children | Optional (filter or sort) |
| `image` | Path to a photo in `images/hotels/` | Optional. Hotels are fictional, so 10 generic photos are shared by type. |

### `data/catalog/rooms.json`

**What it is:** the room types of each hotel (1 to 3 per hotel), with capacity and price.

**Why you need it:** in each city, the package uses one room type for all nights. The capacity decides how many rooms the travelers need.

```json
{ "room_id": "RM-TYO-001-A", "hotel_id": "HTL-TYO-001", "room_type": "TWIN", "name": "Twin Room",
  "max_adults": 2, "max_children": 0, "price_per_night": { "amount": 6440, "currency": "INR" }, "refundable": false }
```

| Field | Meaning | How you use it |
|---|---|---|
| `room_id` | Unique ID of the room type | Links to `availability.room_id`. Store it in the package (`room_id`). |
| `hotel_id` | The hotel | Links to `hotels.hotel_id` |
| `room_type`, `name` | `SINGLE`, `TWIN`, `DOUBLE`, `TRIPLE`, `FAMILY`, `FAMILY_SUITE`, and the display name | Display |
| `max_adults`, `max_children` | Capacity of **one** room | Room fit rule: `k` rooms fit if adults ≤ k × `max_adults`, adults + children ≤ k × (`max_adults` + `max_children`), and adults ≥ k |
| `price_per_night` | Price of one room for one night | Hotel price = `price_per_night` × rooms × nights |
| `refundable` | Free cancellation | Display only |

Example of the room fit rule: 2 adults + 1 child need 2 `TWIN` rooms (`max_adults` 2, `max_children` 0), or 1 `FAMILY` room (`max_adults` 2, `max_children` 2).

### `data/catalog/availability.json`

**What it is:** how many rooms of each type are free on each night. One record per room type per night, 1 to 20 April 2027.

**Why you need it:** every night of the stay needs enough free rooms of the chosen type.

```json
{ "room_id": "RM-TYO-001-A", "date": "2027-04-01", "rooms_left": 7 }
```

| Field | Meaning | How you use it |
|---|---|---|
| `room_id` | The room type | Links to `rooms.room_id` |
| `date` | The night: the date you sleep there | A stay from 11 to 15 April uses the nights of 11, 12, 13, and 14 April |
| `rooms_left` | Free rooms of this type that night. `0` = full. | Rule: `rooms_left` ≥ rooms needed, on every night |

### `data/catalog/transfers.json`

**What it is:** every way to move on the ground: airport taxis and buses, trains and buses between cities, and city taxis.

**Why you need it:** the package needs an airport transfer at the start and at the end, and a transfer between cities on each city-change day.

Airport transfer example:

```json
{ "transfer_id": "TR-NRT-ASA-TAXI", "type": "AIRPORT_TAXI", "name": "Narita airport taxi to Asakusa",
  "airport": "NRT", "city": "Tokyo", "area": "Asakusa", "from_point": "NRT", "to_point": "Asakusa area hotels",
  "max_passengers": 4, "service_from": "06:00", "service_until": "23:00", "duration_minutes": 75,
  "pricing": "PER_VEHICLE", "price": { "amount": 13440, "currency": "INR" } }
```

Transfer between cities example:

```json
{ "transfer_id": "TR-DXB-AUH-BUS", "type": "INTERCITY_BUS", "from_city": "Dubai", "to_city": "Abu Dhabi",
  "door_to_door": false, "frequency_minutes": 30, "pricing": "PER_PERSON", ... }
```

| Field | Meaning | How you use it |
|---|---|---|
| `transfer_id`, `name` | Unique ID and display name | Store the ID in the package (`catalog_id`) |
| `type` | Airport: `AIRPORT_TAXI`, `AIRPORT_VAN`, `AIRPORT_BUS`. Between cities: `TRAIN`, `INTERCITY_BUS`, `INTERCITY_TAXI`. Inside a city: `CITY_TAXI`, `CITY_BUS`. | Choose the right kind for each leg |
| `airport` | Airport transfers: the airport code. Others: `null`. | Match the arrival or return airport |
| `city`, `area` | Hotels this transfer serves. `area: null` = all areas of the city. | Match the hotel's `city` and `area` |
| `from_city`, `to_city` | Transfers between cities only | Match the two cities of a city-change day |
| `door_to_door` | Transfers between cities only. `true` = hotel to hotel. `false` or missing = station to station. | Station to station: a `CITY_TAXI` to and from the station is optional |
| `from_point`, `to_point` | Text that describes the two ends | Display |
| `max_passengers` | Capacity of one vehicle (taxi 4, van 8). Buses and trains: 99. | `PER_VEHICLE`: vehicles = travelers ÷ `max_passengers`, rounded up |
| `service_from`, `service_until` | The pickup time must be inside this window (local time). Trains and buses: first and last departure. | Rule: pickup time inside the window |
| `frequency_minutes` | Trains and intercity buses: one departure every N minutes in the window | Pick a departure time |
| `duration_minutes` | Travel time | Arrival time at the hotel or station |
| `pricing` | `PER_VEHICLE` or `PER_PERSON` | Choose the price formula |
| `price`, `child_price` | Price. `child_price` (only on some records) is for children. | `PER_PERSON`: adults × `price` + children × `child_price` (or `price` if there is none). `PER_VEHICLE`: `price` × vehicles. |

What to know:
- Airport transfers run in **both directions**: airport → hotel and hotel → airport. The service window applies to the pickup time in both directions.
- Some airport transfers have `area: null` and serve every hotel in the city: `KIX` to Kyoto, `DXB` to Abu Dhabi, and the `AUH` bus.
- Transfers between cities have `airport`, `city`, and `area` set to `null`. Use `from_city` and `to_city`.

### `data/catalog/attractions.json`

**What it is:** sights in the destination cities, with opening times and prices.

**Why you need it:** the package plans visits on each day, in the city of that night's hotel, at times when the attraction is open.

```json
{ "attraction_id": "ATT-KYO-001", "name": "Kiyomizu-dera", "name_ja": "清水寺", "city": "Kyoto", "category": "TEMPLE",
  "lat": 34.9949, "lng": 135.785, "open_days": ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"],
  "open_from": "06:00", "open_until": "18:00", "aliases": ["Kiyomizu Temple"], "visit_minutes": 90,
  "child_friendly": true, "accessible": false, "price_per_person": { "amount": 280, "currency": "INR" },
  "child_price": { "amount": 112, "currency": "INR" }, "image": "images/attractions/ATT-KYO-001.jpg" }
```

| Field | Meaning | How you use it |
|---|---|---|
| `attraction_id` | Unique ID | Store it in the package (`catalog_id`) |
| `name`, `name_ja` | English name. Japanese name (`null` outside Japan). | Display |
| `aliases` | Other common names. Typos are not in this list. | Optional (search, quick request S1) |
| `city` | City of the attraction | Rule: the same city as that night's hotel |
| `category` | For example `TEMPLE`, `MUSEUM`, `THEME_PARK`, `BEACH` | Display, filter |
| `lat`, `lng` | Location | Optional (maps) |
| `open_days` | Weekdays it opens: `MON` … `SUN` | Rule: open on the day of the visit |
| `open_from`, `open_until` | Local opening hours | Rule: the whole visit is inside the hours |
| `closed_dates` | Optional. Extra dates when it is closed. | Rule: no visit on these dates |
| `visit_minutes` | Time a visit takes | The visit ends at start + `visit_minutes` |
| `child_friendly`, `accessible` | Booleans. `accessible: false` = many stairs or long walks. | Optional (filter) |
| `price_per_person`, `child_price` | Entry price. `child_price` only on some records. | Price = adults × `price_per_person` + children × `child_price` (or `price_per_person` if there is none) |
| `image` | Path to a photo of the place in `images/attractions/` | Optional |

What to know:
- 2 attractions are in **Nara**. Nara has no hotels, so the rule "same city as that night's hotel" means a package cannot include them.

### `images/`

Real photos with free licenses (Wikimedia Commons). Each attraction has a photo of the real place. Hotels are fictional, so they share 10 generic photos. If your app shows photos, also show the credits from `images/ATTRIBUTIONS.md`.

---

## 4. Shared trip format (Problem 1)

Your app exports the package as one JSON file in this format (handout section 6). These are the minimum fields. You can add more fields. Use `null` when a value is not known.

### Trip

| Field | Type | Meaning |
|---|---|---|
| `trip_id` | string | Your own ID for the package |
| `traveler` | object | `{ "name", "home_airport", "adults", "children", "child_ages" }` |
| `start_date`, `end_date` | date | First and last day of the trip (the holiday start date and the return day) |
| `country` | string | The destination country, for example `Japan` |
| `budget_inr` | number | The traveler's budget |
| `total_inr` | number | Sum of `price_inr` of all items |
| `items` | list | The package items (below) |

### Trip item

| Field | Type | Meaning | Required |
|---|---|---|---|
| `item_id` | string | Unique inside the trip | Yes |
| `type` | string | `flight`, `hotel`, `transfer`, `attraction` | Yes |
| `name` | string | Name shown to the traveler | Yes |
| `city` | string | City of the item. For a flight: the arrival city. | Yes |
| `status` | string | `PLANNED`, or `BOOKED` after confirm | Yes |
| `catalog_id` | string | ID of the catalog record (`flight_id`, `hotel_id`, `transfer_id`, `attraction_id`) | Yes |
| `start` | string | Local date `YYYY-MM-DD` or local date-time `YYYY-MM-DDTHH:MM`. Flight: departure. Hotel: check-in date. Transfer: pickup. Attraction: visit start. | Yes |
| `end` | string or `null` | Local date or date-time. Flight: arrival. Hotel: check-out date. Attraction: visit end. Transfer: `null` or arrival. | Yes |
| `room_id` | string or `null` | Hotel items: the room type from `rooms.json`. Other items: `null`. | Yes |
| `quantity` | number | Seats, rooms, vehicles, or tickets | Yes |
| `price_inr` | number | Total price of this item in INR | Yes |
| `booking` | object or `null` | After confirm: `{ "reference": "1234567890" }` (the reservation number as a string) | After confirm |
| `sources` | list | Always an empty list `[]` in Problem 1 | Yes |
| `warnings` | list of strings | Problems with this item, for example "closed on Monday". Empty list when none. | Optional |

`data/trip_format_example.json` is a short example (2 items) of the shape. It is not a complete package.

---

## 5. Conventions

| Topic | Rule |
|---|---|
| Times | Local time of the place. The strings have no UTC offset. Compare times at one place only in that place's local time. |
| Time zones | India `Asia/Kolkata` (UTC+05:30), Japan `Asia/Tokyo` (UTC+09:00), UAE `Asia/Dubai` (UTC+04:00). No daylight saving time. |
| Dates and date-times | `YYYY-MM-DD` and `YYYY-MM-DDTHH:MM:SS` |
| Clock times | `HH:MM`. `24:00` = midnight at the end of the day. A window like `06:00`–`00:30` runs past midnight. Both ends of a window are included. |
| Nights | A stay from check-in 11 April to check-out 15 April has 4 nights: 11, 12, 13, 14 April |
| Prices | `{ "amount": 6440, "currency": "INR" }`. Always INR, whole rupees. |
| Children | Age 0 to 17. A child needs a flight seat and counts as a passenger. |
| IDs | Stable text IDs, for example `FL-711`, `HTL-TYO-001`, `RM-TYO-001-A`, `TR-NRT-ASA-TAXI`, `ATT-KYO-001`. Use them as keys. |

---

## 6. Data FAQ

**Is a missing field a mistake?** No. Optional fields (`closed_dates`, `child_price`, `from_city`, `to_city`, `frequency_minutes`, `door_to_door`, `serves_cities`) appear only where they apply.

**Why is `arrival_local` minus `departure_local` not equal to `duration_minutes`?** The two times are local times in different time zones. Use `duration_minutes` for travel time.

**Does a child pay a lower flight price?** No. Every seat costs `price_per_seat`. Children pay `child_price` only on transfers and attractions that have it.

**Can a package use several room types in one city?** No. One hotel and one room type for all nights in a city. Several rooms of that type are fine.

**Which field decides the "best rated" package (G4)?** `stars`. Average it over the nights: 2 nights at 5 stars + 4 nights at 3 stars = (2 × 5 + 4 × 3) ÷ 6 = 3.67. The guest `rating` does not count.

**Which times count for the "fastest" package (G4)?** The `duration_minutes` of the 2 flights, plus the `duration_minutes` of each transfer between cities (the record with `from_city` and `to_city`). Airport transfers and city taxis to stations do not count.

**Are hotel check-in and check-out times checked?** No. They are for display.

**Do we need flights outside 1–20 April?** No. There are none. A request outside the window has no package: say so.

**Can we add our own data (for example user notes)?** Yes, in your own database. Do not change the files in `data/`.
