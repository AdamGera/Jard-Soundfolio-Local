"""Rebuild the bundled capital-name index: uv run --with geonamescache==3.0.2 tools/build_place_names.py"""
import json
from importlib.metadata import version
from pathlib import Path
import re

from geonamescache import GeonamesCache


def main():
    if version("geonamescache") != "3.0.2":
        raise SystemExit("Use geonamescache==3.0.2 to reproduce the bundled data.")
    geo = GeonamesCache()
    countries = geo.get_countries()
    aliases = {}
    # Names can be shared by settlements in the same country. Prefer the populous
    # match, and require the country's capital to occur explicitly in its names.
    for city in sorted(geo.get_cities().values(), key=lambda v: -v["population"]):
        country = countries.get(city["countrycode"], {})
        capital = country.get("capital", "")
        names = [city["name"], *city["alternatenames"]]
        if not capital or capital in aliases or capital not in names:
            continue
        local = {s for s in names if len(s) <= 30 and len(s.split()) <= 3 and
                 re.fullmatch(r"[\u05d0-\u05ea\u0621-\u064a\u064b-\u0652\u05b0-\u05c7 -]+", s)}
        aliases[capital] = sorted(local)
    data = {"source": "GeoNames via geonamescache 3.0.2, selected capital names and alternate spellings",
            "attribution": "GeoNames (https://www.geonames.org/), CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)",
            "names": aliases}
    output = Path(__file__).resolve().parents[1] / "place_names.json"
    output.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
