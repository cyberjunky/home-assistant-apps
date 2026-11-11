"""The lookup data the parser reads, loaded from the database tables.

The lists live in database tables (see Database.LOOKUP_TABLES), filled once from
src/defaults/*.csv and editable on the Data tab. Database.load_lookups() puts their current
contents here, at start and after every change, so the module-level parsing functions
(message_fields, parser, vehicle_types) need no database handle. Until a database is
opened the store is empty.
"""

import re
from typing import Any

# The kinds of words in the parser_words table (the table's schema, not data): street
# endings, small words inside a street name, words before an address that are no part of
# it, words at the end that are no town, words never taken as a street
PARSER_WORD_KINDS = ("street_suffix", "street_connector", "not_street_word", "not_city_word", "street_skip_word")


def region_key(region: str) -> str:
    """A safety region name as compared: "Veiligheidsregio Gelderland Midden" -> "gelderlandmidden"."""
    key = re.sub(r"[^a-z]", "", (region or "").casefold().replace("â", "a"))
    return key.removeprefix("veiligheidsregio").removeprefix("regio")


_data: dict[str, Any] = {
    "incident_rules": [],
    "priority_rules": [],
    "parser_words": {},
    "vehicle_regions": {},
    "region_provinces": {},
    "vehicle_type_codes": {},
}
# Bumped on every load, so derived data (vehicle_types' patterns) knows to rebuild
version = 0


def load(values: dict[str, Any]) -> None:
    """Replace lookup data (from Database.load_lookups)."""
    global version
    _data.update(values)
    version += 1


def get(name: str) -> Any:
    return _data[name]
