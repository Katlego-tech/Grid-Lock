"""Grid cells: the one H3 resolution-9 implementation every service imports.

docs/design/domain-model.md section 3: "grid_cell is an H3 resolution-9 cell as a
15-character lowercase hex string. It is computed by exactly one function,
gridlock_contracts.geo.cell_for(lat, lon) -- never re-implemented."
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from typing import cast

import h3  # pyright: ignore[reportMissingTypeStubs]

# h3 ships no type stubs. Its three functions used here, typed once, so the rest of this
# module (and every caller) is checked strictly.
_latlng_to_cell = cast(Callable[[float, float, int], str], h3.latlng_to_cell)  # pyright: ignore[reportUnknownMemberType]
_is_valid_cell = cast(Callable[[str], bool], h3.is_valid_cell)  # pyright: ignore[reportUnknownMemberType]
_get_resolution = cast(Callable[[str], int], h3.get_resolution)  # pyright: ignore[reportUnknownMemberType]

RESOLUTION = 9

# Every res-9 cell's string form starts 89; the database's h3_cell domain checks the same.
_CELL_FORMAT = re.compile(r"89[0-9a-f]{13}")


def cell_for(lat: float, lon: float) -> str:
    """The H3 resolution-9 cell containing (lat, lon), as lowercase hex.

    Raises ValueError for a point that is not on the globe. h3 itself does not: it returns
    a real-looking cell for a latitude of 91 or -95, which would place a report somewhere
    nobody said it was.
    """
    if not (math.isfinite(lat) and math.isfinite(lon)):
        raise ValueError(f"coordinates must be finite numbers, got ({lat}, {lon})")
    if not -90 <= lat <= 90:
        raise ValueError(f"latitude {lat} is outside [-90, 90]")
    if not -180 <= lon <= 180:
        raise ValueError(f"longitude {lon} is outside [-180, 180]")
    return _latlng_to_cell(lat, lon, RESOLUTION)


def is_cell(value: str) -> bool:
    """True only for a valid H3 resolution-9 cell in the canonical lowercase form.

    h3 accepts upper case too, but incidents are grouped by this string, so two spellings
    of one cell would be two places.
    """
    return (
        _CELL_FORMAT.fullmatch(value) is not None
        and _is_valid_cell(value)
        and _get_resolution(value) == RESOLUTION
    )
