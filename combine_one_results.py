import geopandas as gpd
from pathlib import Path
import argparse
import os
import re
import config
from config import get_bigdata_path

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def get_year_from_scenario(scenario: str) -> int:
    """
    Extract year (2030 or 2050) from scenario string.
    E.g., '2030_supply_100%' -> 2030, '2050_supply_100%_add_v2' -> 2050
    """
    match = re.match(r'^(\d{4})', scenario)
    if match:
        return int(match.group(1))
    return 2030  # Default to 2030 if not found


def get_cmip6_layer_paths(year: int) -> dict:
    """
    Get paths to CMIP6 WPD, PVOUT, and HYDRO parquet files for given year.
    Returns dict with layer names as keys and paths as values.
    Layer names match the output file naming convention.
    """
    wind_dir = Path(get_bigdata_path("bigdata_wind_cmip6")) / "outputs"
    solar_dir = Path(get_bigdata_path("bigdata_solar_cmip6")) / "outputs"
    hydro_dir = Path(get_bigdata_path("bigdata_hydro_cmip6")) / "outputs"
    suffix = "300arcsec"
    
    return {
        # Wind layers (clip to GADM + EEZ)
        f"WPD100_{year}_{suffix}": wind_dir / f"WPD100_{year}_{suffix}.parquet",
        f"WPD100_UNCERTAINTY_{year}_{suffix}": wind_dir / f"WPD100_UNCERTAINTY_{year}_{suffix}.parquet",
        f"WPD100_DELTA_{year}_{suffix}": wind_dir / f"WPD100_DELTA_{year}_{suffix}.parquet",
        f"WPD100_baseline_{suffix}": wind_dir / f"WPD100_baseline_{suffix}.parquet",
        # Solar layers (clip to GADM only)
        f"PVOUT_{year}_{suffix}": solar_dir / f"PVOUT_{year}_{suffix}.parquet",
        f"PVOUT_UNCERTAINTY_{year}_{suffix}": solar_dir / f"PVOUT_UNCERTAINTY_{year}_{suffix}.parquet",
        f"PVOUT_DELTA_{year}_{suffix}": solar_dir / f"PVOUT_DELTA_{year}_{suffix}.parquet",
        f"PVOUT_baseline_{suffix}": solar_dir / f"PVOUT_baseline_{suffix}.parquet",
        # Hydro runoff layers (clip to GADM only - land-based)
        f"HYDRO_RUNOFF_baseline_{suffix}": hydro_dir / f"HYDRO_RUNOFF_baseline_{suffix}.parquet",
        f"HYDRO_RUNOFF_{year}_{suffix}": hydro_dir / f"HYDRO_RUNOFF_{year}_{suffix}.parquet",
        f"HYDRO_RUNOFF_DELTA_{year}_{suffix}": hydro_dir / f"HYDRO_RUNOFF_DELTA_{year}_{suffix}.parquet",
        f"HYDRO_RUNOFF_UNCERTAINTY_{year}_{suffix}": hydro_dir / f"HYDRO_RUNOFF_UNCERTAINTY_{year}_{suffix}.parquet",
        # HydroATLAS river reach layers (clip to GADM only) - stored in hydro_dir
        f"RiverATLAS_{year}_polyline": hydro_dir / f"RiverATLAS_{year}_polyline.parquet",
        "RiverATLAS_baseline_polyline": hydro_dir / "RiverATLAS_baseline_polyline.parquet",
        # Unified viable centroids
        f"SOLAR_VIABLE_CENTROIDS_{year}": solar_dir / f"SOLAR_VIABLE_CENTROIDS_{year}.parquet",
        f"WIND_VIABLE_CENTROIDS_{year}": wind_dir / f"WIND_VIABLE_CENTROIDS_{year}.parquet",
        f"HYDRO_VIABLE_CENTROIDS_{year}": hydro_dir / f"HYDRO_VIABLE_CENTROIDS_{year}.parquet",
    }


def get_cmip6_tif_paths(year: int) -> dict:
    """
    Get paths to CMIP6 TIF files for given year.
    Only includes year-matching files + baseline.
    """
    wind_dir = Path(get_bigdata_path("bigdata_wind_cmip6")) / "outputs"
    solar_dir = Path(get_bigdata_path("bigdata_solar_cmip6")) / "outputs"
    hydro_dir = Path(get_bigdata_path("bigdata_hydro_cmip6")) / "outputs"
    suffix = "300arcsec"
    
    return {
        # Wind TIFs
        f"WPD100_{year}_{suffix}": wind_dir / f"WPD100_{year}_{suffix}.tif",
        f"WPD100_UNCERTAINTY_{year}_{suffix}": wind_dir / f"WPD100_UNCERTAINTY_{year}_{suffix}.tif",
        f"WPD100_DELTA_{year}_{suffix}": wind_dir / f"WPD100_DELTA_{year}_{suffix}.tif",
        f"WPD100_baseline_{suffix}": wind_dir / f"WPD100_baseline_{suffix}.tif",
        f"WIND_VIABLE_CENTROIDS_{year}": wind_dir / f"WIND_VIABLE_CENTROIDS_{year}.tif",
        # Solar TIFs
        f"PVOUT_{year}_{suffix}": solar_dir / f"PVOUT_{year}_{suffix}.tif",
        f"PVOUT_UNCERTAINTY_{year}_{suffix}": solar_dir / f"PVOUT_UNCERTAINTY_{year}_{suffix}.tif",
        f"PVOUT_DELTA_{year}_{suffix}": solar_dir / f"PVOUT_DELTA_{year}_{suffix}.tif",
        f"PVOUT_baseline_{suffix}": solar_dir / f"PVOUT_baseline_{suffix}.tif",
        f"SOLAR_VIABLE_CENTROIDS_{year}": solar_dir / f"SOLAR_VIABLE_CENTROIDS_{year}.tif",
        # Hydro runoff TIFs
        f"HYDRO_RUNOFF_baseline_{suffix}": hydro_dir / f"HYDRO_RUNOFF_baseline_{suffix}.tif",
        f"HYDRO_RUNOFF_{year}_{suffix}": hydro_dir / f"HYDRO_RUNOFF_{year}_{suffix}.tif",
        f"HYDRO_RUNOFF_DELTA_{year}_{suffix}": hydro_dir / f"HYDRO_RUNOFF_DELTA_{year}_{suffix}.tif",
        f"HYDRO_RUNOFF_UNCERTAINTY_{year}_{suffix}": hydro_dir / f"HYDRO_RUNOFF_UNCERTAINTY_{year}_{suffix}.tif",
    }


def add_tifs_to_gpkg(year: int, gpkg_path: Path) -> int:
    """
    Add CMIP6 TIF files as raster layers to GPKG.
    
    Args:
        year: Target year (2030 or 2050)
        gpkg_path: Path to the GPKG file to add rasters to
        
    Returns:
        Number of TIFs added
    """
    import sqlite3
    import subprocess

    def gpkg_table_exists(table_name: str) -> bool:
        """Return True if a table already exists in the target GeoPackage."""
        if not gpkg_path.exists():
            return False
        try:
            with sqlite3.connect(gpkg_path) as conn:
                cursor = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND lower(name)=lower(?) LIMIT 1",
                    (table_name,),
                )
                return cursor.fetchone() is not None
        except Exception as e:
            print(f"[WARN] Could not inspect GeoPackage tables: {e}")
            return False
    
    tif_paths = get_cmip6_tif_paths(year)
    added_count = 0
    
    for layer_name, tif_path in tif_paths.items():
        if not tif_path.exists():
            print(f"[WARN] TIF not found: {tif_path.name}")
            continue
        
        try:
            # Use gdal_translate to add raster to GPKG
            # Add _raster suffix to avoid conflict with vector layer names
            raster_layer_name = tif_path.stem + "_raster"
            
            if gpkg_table_exists(raster_layer_name):
                print(f"[INFO] Skipping existing raster table: {raster_layer_name}")
                continue

            # Build gdal_translate command
            cmd = [
                "gdal_translate",
                "-of", "GPKG",
                "-co", f"RASTER_TABLE={raster_layer_name}",
                "-co", "APPEND_SUBDATASET=YES",
                str(tif_path),
                str(gpkg_path)
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                print(f"[INFO] Added TIF to GPKG: {raster_layer_name}")
                added_count += 1
            else:
                print(f"[WARN] Failed to add {tif_path.name}: {result.stderr.strip()}")
                
        except Exception as e:
            print(f"[WARN] Failed to add {tif_path.name}: {e}")
    
    return added_count


def load_country_boundary(iso3: str) -> tuple:
    """
    Load country boundaries from GADM and EEZ datasets.
    
    Returns:
        tuple: (gadm_geometry, eez_geometry) - either can be None if not found
    """
    gadm_geom = None
    eez_geom = None
    
    # Load GADM boundary (land)
    gadm_path = Path(get_bigdata_path("bigdata_gadm")) / "gadm_410-levels.gpkg"
    if gadm_path.exists():
        try:
            gadm = gpd.read_file(gadm_path, layer="ADM_0", where=f"GID_0 = '{iso3}'")
            if not gadm.empty:
                gadm_geom = gadm.union_all()
                print(f"[INFO] Loaded GADM boundary for {iso3}")
        except Exception as e:
            print(f"[WARN] Failed to load GADM for {iso3}: {e}")
    
    # Load EEZ boundary (maritime)
    eez_path = Path(get_bigdata_path("bigdata_eez")) / "eez_v12.gpkg"
    if eez_path.exists():
        try:
            eez = gpd.read_file(eez_path, where=f"ISO_TER1 = '{iso3}'")
            if not eez.empty:
                eez_geom = eez.union_all()
                print(f"[INFO] Loaded EEZ boundary for {iso3}")
        except Exception as e:
            print(f"[WARN] Failed to load EEZ for {iso3}: {e}")
    
    return gadm_geom, eez_geom


def load_cmip6_layers_clipped(iso3: str, year: int) -> dict:
    """
    Load CMIP6 WPD and PVOUT layers, clipped to country boundaries.
    - WPD layers: clipped to GADM + EEZ union (offshore wind)
    - PVOUT layers: clipped to GADM only (no floating solar)
    
    Returns:
        dict: {layer_name: GeoDataFrame} for layers that exist and have data
    """
    cmip6_paths = get_cmip6_layer_paths(year)
    
    # Check if any CMIP6 files exist
    existing_files = {k: v for k, v in cmip6_paths.items() if v.exists()}
    if not existing_files:
        print(f"[WARN] No CMIP6 parquet files found. Run p1_d_viable_solar.py, p1_e_viable_wind.py, and p1_f_viable_hydro.py first.")
        return {}
    
    # Load country boundaries
    gadm_geom, eez_geom = load_country_boundary(iso3)
    
    if gadm_geom is None:
        print(f"[WARN] No GADM boundary found for {iso3}, skipping CMIP6 layers")
        return {}
    
    # Create boundary geometries
    gadm_only = gadm_geom
    gadm_plus_eez = gadm_geom.union(eez_geom) if eez_geom else gadm_geom
    
    # Create GeoDataFrames for clipping
    gadm_gdf = gpd.GeoDataFrame(geometry=[gadm_only], crs="EPSG:4326")
    gadm_eez_gdf = gpd.GeoDataFrame(geometry=[gadm_plus_eez], crs="EPSG:4326")
    
    clipped_layers = {}
    
    for layer_name, parquet_path in cmip6_paths.items():
        if not parquet_path.exists():
            print(f"[WARN] CMIP6 file not found: {parquet_path.name}")
            continue
        
        try:
            print(f"[INFO] Loading CMIP6 layer '{layer_name}' from {parquet_path.name}")
            gdf = gpd.read_parquet(parquet_path)
            
            # Determine which boundary to use
            if layer_name.startswith("WPD100") or layer_name.startswith("WIND_VIABLE"):
                # Wind: use GADM + EEZ (offshore wind)
                clip_gdf = gadm_eez_gdf
                boundary_type = "GADM+EEZ"
            else:
                # Solar, Hydro, Runoff, RiverATLAS: use GADM only (land-based)
                clip_gdf = gadm_gdf
                boundary_type = "GADM"
            
            # Check geometry type and use appropriate clipping method
            geom_type = gdf.geometry.iloc[0].geom_type if not gdf.empty else None
            
            if geom_type in ('LineString', 'MultiLineString'):
                # For line geometries (RiverATLAS), use clip() to cut lines at boundary
                clipped = gpd.clip(gdf, clip_gdf)
            else:
                # For point geometries, use spatial join (faster)
                clipped = gpd.sjoin(gdf, clip_gdf, predicate="within", how="inner")
                if "index_right" in clipped.columns:
                    clipped = clipped.drop(columns=["index_right"])
            
            if not clipped.empty:
                clipped_layers[layer_name] = clipped
                print(f"[INFO] Clipped '{layer_name}' to {boundary_type}: {len(clipped):,} features")
            else:
                print(f"[WARN] No data after clipping '{layer_name}' to {boundary_type}")
                
        except Exception as e:
            print(f"[WARN] Failed to load/clip CMIP6 layer '{layer_name}': {e}")
    
    return clipped_layers


# =============================================================================
# MAIN FUNCTION
# =============================================================================

def parquet_to_gpkg(base_dir, scenario, iso3):
    """
    Combine four Parquet files into one GPKG with four layers.
    Input directory: outputs_per_country/parquet/<scenario>/
    Output GPKG:     outputs_per_country/<scenario>_<iso3>.gpkg (basic 4 layers)
                     outputs_per_country/<scenario>_<iso3>_add.gpkg (if additional layers exist)
                     outputs_per_country/<scenario>_<iso3>_add_v2.gpkg (if _add_v2 files exist)
    """

    base_dir = Path(base_dir)
    
    # Handle scenario names that already end with _add_v2
    if scenario.endswith("_add_v2"):
        base_scenario = scenario[:-7]  # Remove _add_v2 suffix
        in_dir_v2 = base_dir / "parquet" / scenario  # e.g., parquet/2030_supply_100%_add_v2/
        in_dir = base_dir / "parquet" / base_scenario  # e.g., parquet/2030_supply_100%/
    else:
        base_scenario = scenario
        in_dir = base_dir / "parquet" / scenario  # e.g., parquet/2030_supply_100%/
        in_dir_v2 = base_dir / "parquet" / f"{scenario}_add_v2"  # e.g., parquet/2030_supply_100%_add_v2/
    
    # Check for _add_v2 files - check _add_v2 folder first, then base scenario folder
    if in_dir_v2.exists():
        add_v2_files = {
            "centroids":    in_dir_v2 / f"centroids_{iso3}_add_v2.parquet",
            "polylines":    in_dir_v2 / f"polylines_{iso3}_add_v2.parquet",
            "grid_lines":   in_dir_v2 / f"grid_lines_{iso3}_add_v2.parquet",
            "facilities":   in_dir_v2 / f"facilities_{iso3}_add_v2.parquet",
        }
    else:
        add_v2_files = {
            "centroids":    in_dir / f"centroids_{iso3}_add_v2.parquet",
            "polylines":    in_dir / f"polylines_{iso3}_add_v2.parquet",
            "grid_lines":   in_dir / f"grid_lines_{iso3}_add_v2.parquet",
            "facilities":   in_dir / f"facilities_{iso3}_add_v2.parquet",
        }
    
    has_add_v2 = any(fp.exists() for fp in add_v2_files.values())
    
    # Four core layers
    core_files = {
        "centroids":    in_dir / f"centroids_{iso3}.parquet",
        "polylines":    in_dir / f"polylines_{iso3}.parquet",
        "grid_lines":   in_dir / f"grid_lines_{iso3}.parquet",
        "facilities":   in_dir / f"facilities_{iso3}.parquet",
    }
    
    # Additional layers (siting results)
    additional_files = {
        "siting_clusters": in_dir / f"siting_clusters_{iso3}.parquet",
        "siting_settlements": in_dir / f"siting_settlements_{iso3}.parquet",
        "siting_networks": in_dir / f"siting_networks_{iso3}.parquet",
    }
    
    # Check if any additional layers exist
    has_additional = any(fp.exists() for fp in additional_files.values())
    
    # Debug output
    print(f"\n[DEBUG] Checking files for {iso3} in scenario: {scenario}")
    print(f"  has_add_v2: {has_add_v2}")
    print(f"  has_additional (siting): {has_additional}")
    if has_additional:
        print(f"  Found siting layers:")
        for name, fp in additional_files.items():
            if fp.exists():
                print(f"    - {name}: {fp.name}")
    
    # Determine output filename and files to process
    if has_add_v2:
        # Use base_scenario (without _add_v2 suffix) for output naming
        out_gpkg = base_dir / f"{base_scenario}_{iso3}_add_v2.gpkg"
        files = add_v2_files
    elif has_additional:
        out_gpkg = base_dir / f"{scenario}_{iso3}_add.gpkg"
        files = {**core_files, **additional_files}
    else:
        out_gpkg = base_dir / f"{scenario}_{iso3}.gpkg"
        files = core_files

    # Remove old GPKG if exists (avoid append/mixed layers)
    if out_gpkg.exists():
        out_gpkg.unlink()

    for layer, fp in files.items():
        if not fp.exists():
            print(f"[WARN] Missing file for {layer}: {fp}")
            continue

        print(f"[INFO] Writing layer '{layer}' from {fp}")
        gdf = gpd.read_parquet(fp)

        # enforce CRS if needed:
        # gdf = gdf.set_crs("EPSG:4326", allow_override=True)

        gdf.to_file(out_gpkg, layer=layer, driver="GPKG")

    # -------------------------------------------------------------------------
    # Add CMIP6 layers (WPD and PVOUT) - always included regardless of stage
    # -------------------------------------------------------------------------
    year = get_year_from_scenario(scenario)
    print(f"\n[INFO] Loading CMIP6 layers for year {year}...")
    
    cmip6_layers = load_cmip6_layers_clipped(iso3, year)
    
    if cmip6_layers:
        print(f"\n--- Writing CMIP6 layers ---")
        for layer_name, gdf in cmip6_layers.items():
            print(f"[INFO] Writing CMIP6 layer '{layer_name}': {len(gdf):,} points")
            gdf.to_file(out_gpkg, layer=layer_name, driver="GPKG")
        print(f"[INFO] Added {len(cmip6_layers)} CMIP6 layers to GPKG")
    else:
        print(f"[WARN] No CMIP6 layers added (files not found or empty)")

    # Add CMIP6 TIF files as raster layers in GPKG
    print(f"\n--- Adding CMIP6 TIF rasters to GPKG ---")
    tif_count = add_tifs_to_gpkg(year, out_gpkg)
    print(f"[INFO] Added {tif_count} TIF rasters to GPKG")

    print(f"[DONE] Created {out_gpkg}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Combine Parquet files into GPKG for a single country. "
                    "Auto-detects _add_v2 files: checks parquet/{scenario}_add_v2/ folder first, "
                    "then falls back to parquet/{scenario}/. Output: {scenario}_{ISO3}.gpkg, "
                    "{scenario}_{ISO3}_add.gpkg (with siting), or {scenario}_{ISO3}_add_v2.gpkg (after 2nd supply run)."
    )
    default_scenario = config.get_supply_scenario_name(config.ANALYSIS_YEAR, config.SUPPLY_FACTOR)
    parser.add_argument("iso3", help="ISO3 country code (e.g., KEN)")
    parser.add_argument("--scenario", default=default_scenario, 
                       help=f"Scenario name (default: {default_scenario}). Can also use {{year}}_supply_{{factor}}%%_{{scenario}}pct_add_v2.")
    parser.add_argument("--base-dir", default="outputs_per_country", help="Base directory (default: outputs_per_country)")
    
    args = parser.parse_args()
    
    parquet_to_gpkg(
        base_dir=args.base_dir,
        scenario=args.scenario,
        iso3=args.iso3
    )
