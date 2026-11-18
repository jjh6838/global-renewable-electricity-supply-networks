#!/usr/bin/env python3
"""
Combine all country supply analysis results into global dataset
Production mode only - processes Parquet files for efficient global analysis
"""
import pandas as pd
import geopandas as gpd
from pathlib import Path
import argparse
import sys
import os
import re
import logging
from tqdm import tqdm
from datetime import datetime


from config import get_bigdata_path

# =============================================================================
# HELPER FUNCTIONS FOR CMIP6 LAYERS
# =============================================================================


def get_year_from_scenario(scenario: str) -> int:
    """
    Extract year (2030 or 2050) from scenario string.
    E.g., '2030_supply_100%' -> 2030, '2050_supply_100%_add_v2' -> 2050
    """
    if scenario is None:
        return 2030
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
        # Wind layers
        f"WPD100_{year}_{suffix}": wind_dir / f"WPD100_{year}_{suffix}.parquet",
        f"WPD100_UNCERTAINTY_{year}_{suffix}": wind_dir / f"WPD100_UNCERTAINTY_{year}_{suffix}.parquet",
        f"WPD100_DELTA_{year}_{suffix}": wind_dir / f"WPD100_DELTA_{year}_{suffix}.parquet",
        f"WPD100_baseline_{suffix}": wind_dir / f"WPD100_baseline_{suffix}.parquet",
        # Solar layers
        f"PVOUT_{year}_{suffix}": solar_dir / f"PVOUT_{year}_{suffix}.parquet",
        f"PVOUT_UNCERTAINTY_{year}_{suffix}": solar_dir / f"PVOUT_UNCERTAINTY_{year}_{suffix}.parquet",
        f"PVOUT_DELTA_{year}_{suffix}": solar_dir / f"PVOUT_DELTA_{year}_{suffix}.parquet",
        f"PVOUT_baseline_{suffix}": solar_dir / f"PVOUT_baseline_{suffix}.parquet",
        # Hydro runoff layers (gridded)
        f"HYDRO_RUNOFF_baseline_{suffix}": hydro_dir / f"HYDRO_RUNOFF_baseline_{suffix}.parquet",
        f"HYDRO_RUNOFF_{year}_{suffix}": hydro_dir / f"HYDRO_RUNOFF_{year}_{suffix}.parquet",
        f"HYDRO_RUNOFF_DELTA_{year}_{suffix}": hydro_dir / f"HYDRO_RUNOFF_DELTA_{year}_{suffix}.parquet",
        f"HYDRO_RUNOFF_UNCERTAINTY_{year}_{suffix}": hydro_dir / f"HYDRO_RUNOFF_UNCERTAINTY_{year}_{suffix}.parquet",
        # HydroATLAS river reach layers - stored in hydro_dir
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


def add_tifs_to_gpkg(year: int, gpkg_path: Path, logger) -> int:
    """
    Add CMIP6 TIF files as raster layers to GPKG.
    
    Args:
        year: Target year (2030 or 2050)
        gpkg_path: Path to the GPKG file to add rasters to
        logger: Logger instance
        
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
            logger.warning(f"Could not inspect GeoPackage tables: {e}")
            return False
    
    tif_paths = get_cmip6_tif_paths(year)
    added_count = 0
    
    for layer_name, tif_path in tif_paths.items():
        if not tif_path.exists():
            logger.warning(f"TIF not found: {tif_path.name}")
            continue
        
        try:
            # Use gdal_translate to add raster to GPKG
            # Add _raster suffix to avoid conflict with vector layer names
            raster_layer_name = tif_path.stem + "_raster"
            
            if gpkg_table_exists(raster_layer_name):
                logger.info(f"Skipping existing raster table: {raster_layer_name}")
                continue

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
                logger.info(f"Added TIF to GPKG: {raster_layer_name}")
                added_count += 1
            else:
                logger.warning(f"Failed to add {tif_path.name}: {result.stderr.strip()}")
                
        except Exception as e:
            logger.warning(f"Failed to add {tif_path.name}: {e}")
    
    return added_count


def load_cmip6_layers_global(year: int, logger) -> dict:
    """
    Load CMIP6 WPD and PVOUT layers globally (no clipping).
    
    Returns:
        dict: {layer_name: GeoDataFrame} for layers that exist
    """
    cmip6_paths = get_cmip6_layer_paths(year)
    
    # Check if any CMIP6 files exist
    existing_files = {k: v for k, v in cmip6_paths.items() if v.exists()}
    if not existing_files:
        logger.warning("No CMIP6 parquet files found. Run p1_d_viable_solar.py, p1_e_viable_wind.py, and p1_f_viable_hydro.py first.")
        return {}
    
    loaded_layers = {}
    
    for layer_name, parquet_path in cmip6_paths.items():
        if not parquet_path.exists():
            logger.warning(f"CMIP6 file not found: {parquet_path.name}")
            continue
        
        try:
            logger.info(f"Loading CMIP6 layer '{layer_name}' from {parquet_path.name}")
            gdf = gpd.read_parquet(parquet_path)
            loaded_layers[layer_name] = gdf
            logger.info(f"Loaded '{layer_name}': {len(gdf):,} points")
                
        except Exception as e:
            logger.warning(f"Failed to load CMIP6 layer '{layer_name}': {e}")
    
    return loaded_layers


# =============================================================================
# LOGGING AND CORE FUNCTIONS
# =============================================================================

def setup_logging(log_file=None):
    """Setup logging configuration"""
    if log_file is None:
        log_file = Path("outputs_global/logs/combine_results.log")
    
    # Create log directory
    log_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout)
        ]
    )
    return logging.getLogger(__name__)

def find_parquet_files(input_dir, layer_name, scenario_subfolder=None):
    """Find all parquet files for a specific layer across all countries"""
    input_path = Path(input_dir)
    parquet_files = []
    
    # Look for parquet files in the specified scenario subfolder or main parquet directory
    if scenario_subfolder:
        parquet_dir = input_path / "parquet" / scenario_subfolder
    else:
        parquet_dir = input_path / "parquet"
    
    if parquet_dir.exists():
        pattern = f"{layer_name}_*.parquet"
        matching_files = list(parquet_dir.glob(pattern))
        parquet_files.extend(matching_files)
    
    return parquet_files

def combine_layer_data(input_dir, layer_name, countries_list=None, scenario_subfolder=None):
    """Combine data from a specific layer across all countries (Parquet files only)"""
    
    input_path = Path(input_dir)
    
    # Find all parquet files for this layer
    parquet_files = find_parquet_files(input_path, layer_name, scenario_subfolder)
    
    if countries_list:
        # Filter to only specified countries
        filtered_files = []
        for file_path in parquet_files:
            country_code = file_path.stem.split('_')[-1]
            if country_code in countries_list:
                filtered_files.append(file_path)
        parquet_files = filtered_files
    
    if not parquet_files:
        logging.warning(f"No Parquet files found for layer {layer_name}")
        return None
    
    logging.info(f"Combining {layer_name} layer from {len(parquet_files)} Parquet files")
    
    # Read and combine data from this layer
    layer_data_list = []
    failed_countries = []
    
    for file_path in tqdm(parquet_files, desc=f"Processing {layer_name}"):
        try:
            country_code = file_path.stem.split('_')[-1]
            
            # Read the parquet file using geopandas for proper geometry handling
            try:
                layer_data = gpd.read_parquet(file_path)
                logging.info(f"{layer_name} from {country_code} loaded as GeoDataFrame with geometry")
            except Exception:
                # Fallback to pandas if geopandas fails
                layer_data = pd.read_parquet(file_path)
                # Check if there's a geometry column and convert it
                if 'geometry' in layer_data.columns:
                    from shapely import wkt
                    # Try to convert from WKT if stored as text
                    try:
                        layer_data['geometry'] = layer_data['geometry'].apply(wkt.loads)
                        layer_data = gpd.GeoDataFrame(layer_data, crs='EPSG:4326')
                        logging.info(f"{layer_name} from {country_code} converted from WKT to GeoDataFrame")
                    except Exception:
                        logging.info(f"{layer_name} from {country_code} has geometry column but conversion failed")
                else:
                    logging.info(f"{layer_name} from {country_code} has no geometry column (production mode)")
            
            if layer_data.empty:
                logging.warning(f"Empty {layer_name} data for {country_code}")
                continue
            
            layer_data_list.append(layer_data)
            logging.info(f"Loaded {layer_name} from {country_code}: {len(layer_data)} records")
            
        except Exception as e:
            failed_countries.append(country_code)
            logging.error(f"Failed to load {layer_name} from {country_code}: {e}")
    
    if not layer_data_list:
        logging.error(f"No valid {layer_name} data found")
        return None
    
    # Combine all layer data
    logging.info(f"Combining {layer_name} data from {len(layer_data_list)} countries...")
    combined_data = pd.concat(layer_data_list, ignore_index=True)
    
    # Only ensure it's a GeoDataFrame if it has geometry
    if 'geometry' in combined_data.columns and not combined_data['geometry'].isna().all():
        combined_data = gpd.GeoDataFrame(combined_data, crs='EPSG:4326')
        logging.info(f"Combined {layer_name}: {len(combined_data)} total records (with geometry)")
    else:
        logging.info(f"Combined {layer_name}: {len(combined_data)} total records (tabular data only)")
    
    if failed_countries:
        logging.warning(f"Failed to process {layer_name} from {len(failed_countries)} countries: {failed_countries}")
    
    return combined_data

def generate_global_summary(combined_layers, logger):
    """Generate and log global summary statistics"""
    logger.info("\n" + "="*80)
    logger.info("GLOBAL SUPPLY ANALYSIS SUMMARY")
    logger.info("="*80)
    
    # Get centroids and facilities data
    centroids_gdf = combined_layers.get('centroids')
    facilities_gdf = combined_layers.get('facilities')
    
    if centroids_gdf is not None:
        # Country count
        countries = centroids_gdf['GID_0'].nunique()
        logger.info(f"Countries processed: {countries}")
        
        # Supply status summary
        if 'supply_status' in centroids_gdf.columns:
            status_counts = centroids_gdf['supply_status'].value_counts()
            total_centroids = len(centroids_gdf)
            
            logger.info(f"\nGlobal centroid supply status:")
            logger.info(f"  Total centroids: {total_centroids:,}")
            for status in ['Filled', 'Partially Filled', 'Not Filled', 'No Demand']:
                count = status_counts.get(status, 0)
                pct = (count / total_centroids * 100) if total_centroids > 0 else 0
                logger.info(f"  {status}: {count:,} centroids ({pct:.1f}%)")
        
        # Facility type summary
        if 'supplying_facility_type' in centroids_gdf.columns:
            # Parse comma-separated facility types
            all_types = []
            for types_str in centroids_gdf['supplying_facility_type'].dropna():
                if types_str and types_str != '':
                    types = [t.strip() for t in str(types_str).split(',')]
                    all_types.extend(types)
            
            if all_types:
                type_counts = pd.Series(all_types).value_counts()
                logger.info(f"\nSupplying facility types:")
                for ftype, count in type_counts.head(10).items():
                    if ftype:  # Skip empty strings
                        logger.info(f"  {ftype}: {count:,} connections")
    
    if facilities_gdf is not None:
        # Facility summary
        facility_countries = facilities_gdf['GID_0'].nunique()
        total_facilities = len(facilities_gdf)
        logger.info(f"\nGlobal facilities:")
        logger.info(f"  Countries with facilities: {facility_countries}")
        logger.info(f"  Total facilities: {total_facilities:,}")
        
        if 'Grouped_Type' in facilities_gdf.columns:
            facility_type_counts = facilities_gdf['Grouped_Type'].value_counts()
            logger.info(f"  Facility types:")
            for ftype, count in facility_type_counts.items():
                logger.info(f"    {ftype}: {count:,} facilities")
        
        if 'total_mwh' in facilities_gdf.columns:
            total_capacity = facilities_gdf['total_mwh'].sum()
            logger.info(f"  Total global capacity: {total_capacity:,.0f} MWh/year")
    
    # Layer summary
    logger.info(f"\nLayers successfully combined:")
    for layer_name, layer_data in combined_layers.items():
        logger.info(f"  {layer_name}: {len(layer_data):,} records")
    
    logger.info("="*80)

def combine_global_results(input_dir="outputs_per_country", output_file="outputs_global/global_supply_analysis.gpkg", 
                         countries_list=None, scenario_subfolder=None):
    """
    Combine all country results into global datasets by layer (Parquet input mode)
    
    Args:
        input_dir: Directory containing country subdirectories with Parquet files
        output_file: Output file (supports .gpkg, .parquet extensions)
        countries_list: Optional list of countries to include
        scenario_subfolder: Subfolder name under parquet directory (e.g., '2050_supply_100%')
    """
    
    # Setup logging
    logger = setup_logging()
    logger.info(f"Starting global results combination at {datetime.now()}")
    
    input_path = Path(input_dir)
    output_path = Path(output_file)
    output_dir = output_path.parent
    
    # Create input and output directories if they don't exist
    input_path.mkdir(exist_ok=True)
    output_dir.mkdir(exist_ok=True, parents=True)
    
    if not input_path.exists():
        logger.error(f"Input directory {input_dir} does not exist")
        return False
    
    # Check for Parquet files - look in scenario subfolder if specified
    if scenario_subfolder:
        parquet_dir = input_path / "parquet" / scenario_subfolder
    else:
        parquet_dir = input_path / "parquet"
    
    has_parquet = parquet_dir.exists() and list(parquet_dir.glob("*.parquet"))
    
    if not has_parquet:
        logger.error(f"No Parquet files found in {parquet_dir}")
        if scenario_subfolder:
            logger.error(f"Expected structure: outputs_per_country/parquet/{scenario_subfolder}/layer_ISO3.parquet")
        else:
            logger.error("Expected structure: outputs_per_country/parquet/layer_ISO3.parquet")
        return False
    
    logger.info(f"Processing scenario: {scenario_subfolder if scenario_subfolder else 'default'}")
    
    # Determine output format from file extension
    output_format = 'gpkg' if output_file.endswith('.gpkg') else 'parquet'
    
    logger.info(f"Processing Parquet files, Output format: {output_format}")
    
    # Define core layers to combine
    core_layers = ['centroids', 'facilities', 'grid_lines', 'polylines']
    
    # Check if this is an ADD_V2 scenario (siting data merged into core layers)
    has_add_v2_data = scenario_subfolder and '_add_v2' in scenario_subfolder
    
    # Determine layers to combine and update output filename if needed
    layers_to_combine = core_layers
    
    if has_add_v2_data:
        # Update output filename to add _add_v2 before extension
        if output_format == 'gpkg':
            output_path_obj = Path(output_file)
            # Only add suffix if not already present
            if '_add_v2' not in output_path_obj.stem:
                output_file = str(output_path_obj.parent / f"{output_path_obj.stem}_add_v2{output_path_obj.suffix}")
                output_path = Path(output_file)
                logger.info(f"ADD_V2 scenario detected - output will be saved as: {output_file}")
            else:
                output_path = Path(output_file)
        else:
            output_path = Path(output_file)
    else:
        output_path = Path(output_file)
    
    # Combine each layer
    combined_layers = {}
    
    for layer_name in layers_to_combine:
        logger.info(f"\n--- Processing {layer_name} layer ---")
        
        combined_data = combine_layer_data(
            input_dir=input_dir,
            layer_name=layer_name,
            countries_list=countries_list,
            scenario_subfolder=scenario_subfolder
        )
        
        if combined_data is not None and not combined_data.empty:
            combined_layers[layer_name] = combined_data
            logger.info(f"Successfully combined {layer_name}: {len(combined_data)} records")
        else:
            logger.warning(f"No data available for {layer_name} layer")
    
    if not combined_layers:
        logger.error("No layers were successfully combined")
        return False
    
    # -------------------------------------------------------------------------
    # Add CMIP6 layers (WPD and PVOUT) - always included regardless of stage
    # -------------------------------------------------------------------------
    year = get_year_from_scenario(scenario_subfolder)
    logger.info(f"\n--- Loading CMIP6 layers for year {year} ---")
    
    cmip6_layers = load_cmip6_layers_global(year, logger)
    
    if cmip6_layers:
        logger.info(f"Adding {len(cmip6_layers)} CMIP6 layers to combined output")
        combined_layers.update(cmip6_layers)
    else:
        logger.warning("No CMIP6 layers added (files not found or empty)")
    
    # Save combined data based on output format
    if output_format == 'gpkg':
        # Save as GPKG with multiple layers (only for data with geometry)
        logger.info(f"Saving global analysis as GPKG: {output_path}")
        
        layers_with_geometry = {}
        layers_without_geometry = {}
        
        for layer_name, layer_data in combined_layers.items():
            logger.info(f"Processing {layer_name}: {len(layer_data)} records")
            
            # Check if it has geometry and is a GeoDataFrame
            if isinstance(layer_data, gpd.GeoDataFrame) and 'geometry' in layer_data.columns:
                layers_with_geometry[layer_name] = layer_data
                layer_data.to_file(output_path, layer=layer_name, driver="GPKG")
                logger.info(f"Saved {layer_name} to GPKG (with geometry)")
            else:
                layers_without_geometry[layer_name] = layer_data
                # Save as CSV for non-geometry data
                csv_file = output_dir / f"global_{layer_name}.csv"
                layer_data.to_csv(csv_file, index=False)
                logger.info(f"Saved {layer_name} to CSV (tabular data): {csv_file.name}")
        
        if layers_with_geometry:
            logger.info(f"Global GPKG saved with {len(layers_with_geometry)} geometry layers")
        if layers_without_geometry:
            logger.info(f"Additional {len(layers_without_geometry)} layers saved as CSV files")
        
        # Add CMIP6 TIF files as raster layers in GPKG
        logger.info(f"\n--- Adding CMIP6 TIF rasters to GPKG ---")
        tif_count = add_tifs_to_gpkg(year, output_path, logger)
        logger.info(f"Added {tif_count} TIF rasters to GPKG")
    
    else:
        # Save as individual Parquet files
        logger.info(f"Saving global analysis as Parquet files in: {output_dir}")
        
        for layer_name, layer_data in combined_layers.items():
            parquet_file = output_dir / f"global_{layer_name}.parquet"
            layer_data.to_parquet(parquet_file)
            logger.info(f"Saved {layer_name}: {len(layer_data)} records → {parquet_file.name}")
    
    # Generate global summary
    generate_global_summary(combined_layers, logger)
    
    logger.info(f"Global combination completed successfully at {datetime.now()}")
    return True

def main():
    """Main function for command line usage"""
    parser = argparse.ArgumentParser(description='Combine country supply analysis results into global dataset (Production mode - Parquet files)')
    parser.add_argument('--input-dir', default='outputs_per_country', 
                       help='Input directory with country subdirectories containing Parquet files')
    parser.add_argument('--output', default=None, 
                       help='Output file (.gpkg for visualization, .parquet for data). Auto-generated if not specified.')
    parser.add_argument('--scenario', default=None,
                       help='Scenario subfolder name under parquet directory (e.g., "2050_supply_100%%_80pct")')
    parser.add_argument('--countries', nargs='+',
                       help='Optional list of ISO3 country codes to process (e.g., --countries USA CHN IND)')
    parser.add_argument('--countries-file', 
                       help='File with list of countries to process (optional)')
    
    args = parser.parse_args()
    
    # Setup logging
    logger = setup_logging()
    logger.info("="*60)
    logger.info("STARTING GLOBAL RESULTS COMBINATION (PRODUCTION MODE)")
    logger.info("="*60)
    
    # Auto-detect scenario subfolders if not specified
    scenario_subfolder = args.scenario
    scenarios_to_process = []
    
    if not scenario_subfolder:
        # Look for all scenario subfolders in the parquet directory
        parquet_base = Path(args.input_dir) / "parquet"
        if parquet_base.exists():
            subfolders = sorted(
                d.name for d in parquet_base.iterdir()
                if d.is_dir() and re.fullmatch(
                    r"\d{4}_supply_\d+%(?:_(?:100|80|60)pct)?(?:_add_v2)?", d.name
                )
            )
            if subfolders:
                scenarios_to_process = subfolders
                logger.info(f"Found {len(scenarios_to_process)} scenario subfolders: {scenarios_to_process}")
            else:
                logger.warning("No scenario subfolders found, will use default parquet directory")
                scenarios_to_process = [None]
    else:
        scenarios_to_process = [scenario_subfolder]
    
    # Load countries list if specified
    countries_list = None
    if args.countries:
        countries_list = [c.strip().upper() for c in args.countries if c and c.strip()]
        logger.info(f"Processing specific countries from --countries: {len(countries_list)} countries")

    if args.countries_file and Path(args.countries_file).exists():
        file_countries = [c.strip().upper() for c in Path(args.countries_file).read_text().splitlines() if c.strip()]
        if countries_list:
            countries_list = sorted(set(countries_list).union(file_countries))
            logger.info(f"Combined countries from --countries and {args.countries_file}: {len(countries_list)} countries")
        else:
            countries_list = file_countries
            logger.info(f"Processing specific countries from {args.countries_file}: {len(countries_list)} countries")
    
    # Process each scenario
    all_success = True
    for scenario in scenarios_to_process:
        logger.info(f"\n{'='*80}")
        logger.info(f"PROCESSING SCENARIO: {scenario if scenario else 'default'}")
        logger.info(f"{'='*80}\n")
        
        # Generate output filename based on scenario
        if args.output:
            output_file = args.output
        else:
            if scenario:
                output_file = f"outputs_global/{scenario}_global.gpkg"
            else:
                output_file = "outputs_global/global_supply_analysis.gpkg"
        
        logger.info(f"Output file: {output_file}")
        
        success = combine_global_results(
            input_dir=args.input_dir,
            output_file=output_file,
            countries_list=countries_list,
            scenario_subfolder=scenario
        )
        
        if not success:
            all_success = False
            logger.error(f"Failed to process scenario: {scenario}")
        else:
            logger.info(f"Successfully processed scenario: {scenario}")
    
    success = all_success
    
    if success:
        logger.info("Global results combination completed successfully!")
        if len(scenarios_to_process) == 1:
            print(f"\n🎉 Success! Global analysis saved to: {output_file}")
            if output_file.endswith('.gpkg'):
                print("💡 You can now open this GPKG file in QGIS for global visualization!")
        else:
            print(f"\n🎉 Success! Processed {len(scenarios_to_process)} scenarios in outputs_global/")
            print("💡 You can now open the GPKG files in QGIS for global visualization!")
    else:
        logger.error("Global results combination failed!")
        print("\n❌ Failed to create global analysis. Check the logs for details.")
    
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())
