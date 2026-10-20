"""Build the ALBUM tag. Rules are in docs/TAGGING.md.

Examples:
    2022-07-09: Saratoga Springs, NY
    2024-05-24: West Palm Beach, FL siriusxm
    2018-07-10: Toronto, ON
    2024-04-25: London, UK
    2024-04-07: Oslo, Norway
"""
from __future__ import annotations

import re
from datetime import date

US_STATES: dict[str, str] = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV", "new hampshire": "NH",
    "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN",
    "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "washington dc": "DC", "washington d.c.": "DC", "puerto rico": "PR",
}

CA_PROVINCES: dict[str, str] = {
    "alberta": "AB", "british columbia": "BC", "manitoba": "MB", "new brunswick": "NB",
    "newfoundland": "NL", "newfoundland and labrador": "NL", "nova scotia": "NS",
    "northwest territories": "NT", "nunavut": "NU", "ontario": "ON", "prince edward island": "PE",
    "quebec": "QC", "québec": "QC", "saskatchewan": "SK", "yukon": "YT",
}

US_CODES = frozenset(US_STATES.values())
CA_CODES = frozenset(CA_PROVINCES.values())
# Codes shared by a US state and a Canadian province do not exist, except none; NL is Canada only here.

UK_NAMES = frozenset({"england", "scotland", "wales", "northern ireland", "uk", "u.k.",
                      "united kingdom", "great britain", "gb"})

# alias (lowercase) -> display name used in the ALBUM tag
COUNTRIES: dict[str, str] = {
    "canada": "Canada", "mexico": "Mexico", "méxico": "Mexico",
    "ireland": "Ireland", "republic of ireland": "Ireland", "eire": "Ireland",
    "france": "France", "germany": "Germany", "deutschland": "Germany", "deu": "Germany",
    "italy": "Italy", "italia": "Italy", "ita": "Italy",
    "spain": "Spain", "esp": "Spain", "portugal": "Portugal", "prt": "Portugal",
    "netherlands": "Netherlands", "the netherlands": "Netherlands", "holland": "Netherlands",
    "belgium": "Belgium", "bel": "Belgium", "luxembourg": "Luxembourg",
    "switzerland": "Switzerland", "austria": "Austria", "denmark": "Denmark", "dnk": "Denmark",
    "norway": "Norway", "nor": "Norway", "sweden": "Sweden", "swe": "Sweden",
    "finland": "Finland", "iceland": "Iceland", "poland": "Poland", "czech republic": "Czech Republic",
    "czechia": "Czech Republic", "hungary": "Hungary", "greece": "Greece", "croatia": "Croatia",
    "turkey": "Turkey", "russia": "Russia", "australia": "Australia", "aus": "Australia",
    "new zealand": "New Zealand", "japan": "Japan", "china": "China", "south korea": "South Korea",
    "brazil": "Brazil", "argentina": "Argentina", "chile": "Chile", "colombia": "Colombia",
    "costa rica": "Costa Rica", "jamaica": "Jamaica", "bahamas": "Bahamas",
    "south africa": "South Africa", "israel": "Israel", "singapore": "Singapore",
    "united arab emirates": "United Arab Emirates", "uae": "United Arab Emirates",
    "puerto rico": "Puerto Rico",
    # ISO codes seen in info files that cannot be mistaken for a US state / province
    "can": "Canada", "mex": "Mexico", "mx": "Mexico", "nld": "Netherlands", "dk": "Denmark",
    "cz": "Czech Republic", "cze": "Czech Republic", "fra": "France", "irl": "Ireland",
    "che": "Switzerland", "aut": "Austria", "pol": "Poland", "hun": "Hungary", "gbr": "UK",
    "nzl": "New Zealand", "jpn": "Japan", "bra": "Brazil", "arg": "Argentina",
}

USA_NAMES = frozenset({"usa", "u.s.a.", "us", "u.s.", "united states", "united states of america", "america"})

# Venue-ish city names that show up in info files where a real city is meant.
CITY_ALIASES: dict[str, str] = {
    "nyc": "New York", "new york city": "New York", "new york, new york": "New York",
}


def _key(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().strip(".").lower()) if s else ""


def region_code(token: str | None) -> str | None:
    """'Virginia' / 'va' / 'Ontario' -> 'VA' / 'ON'; None if not a US state or Canadian province."""
    if not token:
        return None
    t = token.strip()
    if re.fullmatch(r"[A-Za-z]{2}", t) and t.upper() in (US_CODES | CA_CODES):
        return t.upper()
    k = _key(t)
    if k in {"d.c", "dc"}:
        return "DC"
    return US_STATES.get(k) or CA_PROVINCES.get(k)


def country_name(token: str | None) -> str | None:
    """Canonical country for the album: 'UK' for GB nations, else the English name; None if unknown."""
    if not token:
        return None
    k = _key(token)
    if k in UK_NAMES:
        return "UK"
    if k in USA_NAMES:
        return "USA"
    return COUNTRIES.get(k)


def is_uk_subregion(token: str | None) -> bool:
    return _key(token or "") in UK_NAMES


def album_name(show_date: date, city: str | None, region: str | None, country: str | None, suffix: str = "",
               fallback: str | None = None) -> str:
    """`YYYY-MM-DD: City, ST` (US/Canada), `City, UK`, or `City, Country`, plus ` <suffix>`.

    With no location, `fallback` (the venue or show title) stands in, as the author's own
    tags do for e.g. `2015-07-04: SPAC`."""
    place = _place(city, region, country) or (fallback or "").strip() or None
    out = f"{show_date.isoformat()}: {place}" if place else show_date.isoformat()
    suffix = (suffix or "").strip().lower()
    return f"{out} {suffix}" if suffix else out


def _place(city: str | None, region: str | None, country: str | None) -> str:
    city = re.sub(r"\s+", " ", city).strip() if city else ""
    city = CITY_ALIASES.get(city.lower(), city)
    reg = region_code(region) if region else None
    ctry = country_name(country) if country else None
    if reg:
        return f"{city}, {reg}" if city else reg
    if ctry and ctry != "USA":
        if is_uk_subregion(country):
            ctry = "UK"
        return f"{city}, {ctry}" if city else ctry
    if country and not ctry:
        return f"{city}, {country.strip()}" if city else country.strip()
    return city
