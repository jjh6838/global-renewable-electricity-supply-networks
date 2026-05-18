"""
Generate a README preview map for one country-year-scenario.

Default: Republic of Korea (KOR), 2050, supply 100%, _add_v2.
Reads the four archived per-country Parquet layers and writes a single
PNG to sample_maps/preview_{ISO3}_{YEAR}.png suitable for embedding in README.md.

Usage:
    python sample_maps/generate_preview_map.py
    python sample_maps/generate_preview_map.py --iso3 GBR
    python sample_maps/generate_preview_map.py --iso3 TLS --year 2050
"""

import argparse
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from pycountry import countries

# --- CLI ---------------------------------------------------------------------
_parser = argparse.ArgumentParser(description=__doc__)
_parser.add_argument("--iso3", default="KOR", help="ISO3 country code (default: KOR)")
_parser.add_argument("--year", type=int, default=2050, help="Model year (default: 2050)")
_parser.add_argument("--supply-pct", type=int, default=100,
                     help="Supply percent used in scenario folder name (default: 100)")
_parser.add_argument("--no-add-v2", action="store_true",
                     help="Use the base scenario folder instead of _add_v2")
_args = _parser.parse_args()

# --- configuration -----------------------------------------------------------
ISO3 = _args.iso3.upper()
YEAR = _args.year
SUFFIX = "" if _args.no_add_v2 else "_add_v2"
SCENARIO_DIR = f"{YEAR}_supply_{_args.supply_pct}%{SUFFIX}"

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
PARQUET_DIR = PROJECT_ROOT / "outputs_per_country" / "parquet" / SCENARIO_DIR
OUT_DIR = PROJECT_ROOT / "sample_maps"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH = OUT_DIR / f"preview_{ISO3}_{YEAR}.png"

def _country_name(iso3: str) -> str:
    try:
        return countries.lookup(iso3).name
    except LookupError:
        return iso3

COUNTRY_NAME = _country_name(ISO3)

# --- colours (manuscript-aligned, Oxford palette friendly) -------------------
TECH_COLORS = {
    "solar":   "#F5A800",   # amber
    "wind":    "#0F76B6",   # blue
    "hydro":   "#2E8B57",   # green
    "nuclear": "#9467BD",   # purple
    "fossil":  "#555555",   # dark grey
    "other":   "#BBBBBB",   # light grey (Other Renewables / unknown)
}
GRID_COLOR = "#444444"        # existing transmission/distribution
POLYLINE_COLOR = "#C5093B"    # modelled new lines
CENTROID_COLOR = "#222222"

# --- load layers -------------------------------------------------------------
def _load(name: str) -> gpd.GeoDataFrame:
    path = PARQUET_DIR / f"{name}_{ISO3}{SUFFIX}.parquet"
    if not path.exists():
        raise FileNotFoundError(path)
    return gpd.read_parquet(path)

facilities = _load("facilities")
centroids  = _load("centroids")
polylines  = _load("polylines")
grid_lines = _load("grid_lines")

# Keep the archived native CRS (EPSG:4326) so the preview matches the
# coordinate system distributed with the Parquet layers.
target_crs = "EPSG:4326"
facilities = facilities.to_crs(target_crs)
centroids  = centroids.to_crs(target_crs)
polylines  = polylines.to_crs(target_crs)
grid_lines = grid_lines.to_crs(target_crs)

# --- normalise facility technology column ------------------------------------
tech_col_candidates = [c for c in ("Grouped_Type", "technology", "tech", "source", "type") if c in facilities.columns]
tech_col = tech_col_candidates[0] if tech_col_candidates else None

def _tech_color(value: str) -> str:
    if not isinstance(value, str):
        return TECH_COLORS["other"]
    v = value.lower()
    for key in ("solar", "wind", "hydro", "nuclear", "fossil"):
        if key in v:
            return TECH_COLORS[key]
    return TECH_COLORS["other"]

if tech_col is not None:
    facilities = facilities.assign(_color=facilities[tech_col].map(_tech_color))
else:
    facilities = facilities.assign(_color=TECH_COLORS["other"])

# --- plot --------------------------------------------------------------------
fig, ax = plt.subplots(figsize=(9, 10), dpi=200)

grid_lines.plot(ax=ax, color=GRID_COLOR, linewidth=0.35, alpha=0.55, zorder=1)
polylines.plot(ax=ax, color=POLYLINE_COLOR, linewidth=0.5, alpha=0.75, zorder=2)
centroids.plot(ax=ax, color=CENTROID_COLOR, markersize=1.0, alpha=0.30, zorder=3)
facilities.plot(ax=ax, color=facilities["_color"], markersize=22, alpha=0.95,
                edgecolor="white", linewidth=0.5, zorder=10)

# Keep map aspect equal in lat/lon by setting aspect to 1/cos(mean_lat) so the
# country isn't horizontally squashed when plotted in EPSG:4326.
import math
mean_lat = float(centroids.geometry.y.mean())
ax.set_aspect(1.0 / math.cos(math.radians(mean_lat)))

ax.set_axis_off()
ax.set_title(
    f"{COUNTRY_NAME} \u2014 {YEAR} renewable supply and network ({SUFFIX.lstrip('_') or 'base'})",
    fontsize=12, pad=10,
)

# Legend
legend_handles = [
    Line2D([0], [0], marker="o", color="none", markerfacecolor=TECH_COLORS["solar"],
           markersize=7, label="Solar facility"),
    Line2D([0], [0], marker="o", color="none", markerfacecolor=TECH_COLORS["wind"],
           markersize=7, label="Wind facility"),
    Line2D([0], [0], marker="o", color="none", markerfacecolor=TECH_COLORS["hydro"],
           markersize=7, label="Hydro facility"),
    Line2D([0], [0], marker="o", color="none", markerfacecolor=TECH_COLORS["nuclear"],
           markersize=7, label="Nuclear facility"),
    Line2D([0], [0], marker="o", color="none", markerfacecolor=TECH_COLORS["fossil"],
           markersize=7, label="Fossil facility"),
    Line2D([0], [0], marker="o", color="none", markerfacecolor=TECH_COLORS["other"],
           markersize=7, label="Other renewable facility"),
    Line2D([0], [0], color=POLYLINE_COLOR, linewidth=1.5, label="Modelled new line"),
    Line2D([0], [0], color=GRID_COLOR, linewidth=1.0, alpha=0.7,
           label="Existing grid (Gridfinder)"),
    Line2D([0], [0], marker="o", color="none", markerfacecolor=CENTROID_COLOR,
           markersize=4, alpha=0.5, label="Settlement centroid"),
]
ax.legend(handles=legend_handles, loc="lower left", frameon=False, fontsize=8)

fig.tight_layout()
fig.savefig(OUT_PATH, bbox_inches="tight", facecolor="white")
print(f"[OK] Wrote {OUT_PATH}")
