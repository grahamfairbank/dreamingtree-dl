from datetime import date

import pytest

from app.albumname import album_name, country_name, region_code

D = date(2022, 7, 9)


@pytest.mark.parametrize("city,region,country,suffix,want", [
    ("Saratoga Springs", "NY", None, "", "2022-07-09: Saratoga Springs, NY"),
    ("West Palm Beach", "FL", None, "siriusxm", "2022-07-09: West Palm Beach, FL siriusxm"),
    ("Madison", "WI", "USA", "sbd", "2022-07-09: Madison, WI sbd"),
    ("Toronto", "ON", None, "", "2022-07-09: Toronto, ON"),
    ("Vancouver", "British Columbia", "Canada", "", "2022-07-09: Vancouver, BC"),
    ("London", None, "UK", "", "2022-07-09: London, UK"),
    ("London", None, "England", "", "2022-07-09: London, UK"),
    ("Edinburgh", None, "Scotland", "fm", "2022-07-09: Edinburgh, UK fm"),
    ("Cardiff", None, "Wales", "", "2022-07-09: Cardiff, UK"),
    ("Belfast", None, "Northern Ireland", "", "2022-07-09: Belfast, UK"),
    ("Oslo", None, "Norway", "", "2022-07-09: Oslo, Norway"),
    ("Oslo", None, "NOR", "", "2022-07-09: Oslo, Norway"),
    ("Dublin", None, "Ireland", "webcast", "2022-07-09: Dublin, Ireland webcast"),
    ("Virginia Beach", "virginia", None, "matrix", "2022-07-09: Virginia Beach, VA matrix"),
    ("Kanata", None, "Canada", "", "2022-07-09: Kanata, Canada"),
])
def test_album_name(city, region, country, suffix, want):
    assert album_name(D, city, region, country, suffix) == want


def test_suffix_is_lowercased_and_empty_has_no_trailing_space():
    assert album_name(D, "Madison", "WI", None, "SBD") == "2022-07-09: Madison, WI sbd"
    assert not album_name(D, "Madison", "WI", None, "").endswith(" ")


def test_nyc_alias():
    assert album_name(D, "New York City", "NY", None) == "2022-07-09: New York, NY"


def test_tables():
    assert region_code("Texas") == "TX" and region_code("tx") == "TX"
    assert region_code("Québec") == "QC" and region_code("D.C.") == "DC"
    assert region_code("Germany") is None and region_code("ZZ") is None
    assert country_name("Deutschland") == "Germany" and country_name("USA") == "USA"
    assert country_name("Great Britain") == "UK"
