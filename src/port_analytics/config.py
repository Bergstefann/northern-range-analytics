from __future__ import annotations

from pathlib import Path

EUROSTAT_BASE_URL = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"

# Confirmed against the live API — the codes in the original build spec
# (mar_mg_am_pwhd, mar_go_am) do not exist. See docs/data-quality-notes.md.
DATASET_CODES: dict[str, str] = {
    "goods_by_direction": "mar_mg_aa_pwhd",
    "goods_by_cargo_type": "mar_mg_am_pwhc",
}

# Eurostat's own port identifiers (the 'rep_mar' dimension). Includes the
# pre-2022 Antwerpen/Zeebrugge codes alongside the merged Antwerp-Bruges
# code because Eurostat reports the legacy codes for years before the
# 2022 merger and the merged code from 2022 onward, with no overlap year.
# See docs/data-quality-notes.md.
PORT_CODES: list[str] = [
    "BE_0BE003",  # Antwerp-Bruges (merged, 2022+)
    "BE_0BEANR",  # Antwerpen (legacy, through 2021)
    "BE_0BEZEE",  # Zeebrugge (legacy, through 2021)
    "DE_1DEHAM",  # Hamburg
    "NL_0NLRTM",  # Rotterdam
    "PL_0PLGDN",  # Gdansk
]

DEFAULT_LANDING_DIR = Path("data/raw")
