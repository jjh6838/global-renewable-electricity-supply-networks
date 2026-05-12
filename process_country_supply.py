#!/usr/bin/env python3
"""
# ==================================================================================================
# SCRIPT PURPOSE: Detailed Country-Level Electricity Supply and Demand Network Analysis
# ==================================================================================================
#
# WHAT THIS CODE DOES:
# This script performs a granular, network-based analysis of electricity supply and demand for a
# single country. It models how electricity flows from generation facilities (supply) to population
# centers (demand) through the electrical grid.
#
# WHY THIS IS NEEDED:
# While other scripts project national totals, this script provides spatial detail. It helps answer:
# - Which population centers are served by which power plants?
# - What are the network distances for electricity transmission?
# - Are there parts of the grid that are critical for connecting supply and demand?
# - Which areas might be underserved based on network topology?
#
# KEY STEPS:
# 1. Load Geospatial Data: Imports administrative boundaries, population density rasters,
#    power plant locations, and electrical grid lines for the specified country.
# 2. Project Demand: Allocates national-level electricity demand projections (for 2024, 2030, 2050)
#    down to fine-grained population centroids.
# 3. Build Network Graph: Constructs a comprehensive `networkx` graph representing the entire
#    electrical network, including grid lines, facilities, and centroids as nodes. Grid gaps are
#    stitched earlier during data loading to minimize work here.
# 4. Calculate Distances: Computes the shortest path network distance from every population
#    centroid to all nearby power plants. This is a computationally intensive step that uses
#    parallel processing.
# 5. Allocate Supply: Implements a supply-demand matching algorithm. It iterates through power
#    plants (largest first) and allocates their electricity to the nearest population centers
#    until the plant's capacity is exhausted or the centers' demands are met.
#
# OUTPUTS:
# - Test Mode: A single GeoPackage (.gpkg) file containing detailed layers for centroids,
#   facilities, grid lines, and the specific polylines of active supply routes.
# - Production Mode: Lightweight Parquet files for each layer, optimized for large-scale analysis.
# ==================================================================================================
"""

import warnings
import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
import networkx as nx
from pathlib import Path
import argparse
import sys
import os
import math
from collections import defaultdict
from affine import Affine
from shapely.geometry import Point, LineString, MultiLineString, MultiPoint, GeometryCollection
from shapely.ops import unary_union, split, linemerge
from shapely.strtree import STRtree
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import heapq
import time
import traceback
from contextlib import contextmanager

try:
    import pyogrio
    HAS_PYOGRIO = True
except ImportError:
    HAS_PYOGRIO = False

try:
    import momepy
    from momepy import preprocessing as momepy_preprocessing
    HAS_MOMEPY = True
    HAS_MOMEPY_SPLIT_LINES = hasattr(momepy_preprocessing, "split_lines")
except ImportError:
    HAS_MOMEPY = False
    HAS_MOMEPY_SPLIT_LINES = False
    momepy_preprocessing = None

# Suppress warnings
# warnings.filterwarnings("ignore")

# Import shared configuration
from config import (
    POP_AGGREGATION_FACTOR, TARGET_RESOLUTION_ARCSEC,
    COMMON_CRS, ANALYSIS_YEAR, DEMAND_TYPES, SUPPLY_FACTOR,
    GRID_STITCH_DISTANCE_KM, NODE_SNAP_TOLERANCE_M,
    MAX_CONNECTION_DISTANCE_M, FACILITY_SEARCH_RADIUS_KM,
    NO_GRID_CONNECTION_RADIUS_KM,
    get_bigdata_path,
)

# Configuration logging guard to avoid duplicate prints when imported by worker processes
_CONFIG_PRINTED = False

def print_configuration_banner(test_mode=False, scenario_suffix=""):
    """Emit configuration details once per process."""
    global _CONFIG_PRINTED
    if _CONFIG_PRINTED:
        return

    print("=" * 60)
    print("CONFIGURATION PARAMETERS")
    print("=" * 60)
    print(f"Common CRS: {COMMON_CRS}")
    print(f"Analysis Year: {ANALYSIS_YEAR}")
    if scenario_suffix:
        print(f"Scenario: {scenario_suffix}")
    print(f"Demand Types: {', '.join(DEMAND_TYPES)}")
    print(
        f"Population Aggregation Factor: {POP_AGGREGATION_FACTOR}x (native 30\" -> {POP_AGGREGATION_FACTOR*30}\")"
    )
    print(f"Grid Stitch Distance: {GRID_STITCH_DISTANCE_KM} km")
    print(f"Node Snap Tolerance: {NODE_SNAP_TOLERANCE_M} m")
    print(f"Max Connection Distance: {MAX_CONNECTION_DISTANCE_M/1000:.1f} km")
    print(f"Facility Search Radius: {FACILITY_SEARCH_RADIUS_KM} km")
    print(f"Supply Factor (Sensitivity Analysis): {SUPPLY_FACTOR*100:.0f}%")
    print("=" * 60)
    print()

    _CONFIG_PRINTED = True


# Get optimal number of workers based on SLURM allocation or available CPUs
# Use SLURM_CPUS_PER_TASK if running on cluster, otherwise use os.cpu_count()
slurm_cpus = os.environ.get('SLURM_CPUS_PER_TASK')
if slurm_cpus:
    MAX_WORKERS = int(slurm_cpus)
    print(f"Parallel processing configured for {MAX_WORKERS} workers (from SLURM allocation)")
else:
    MAX_WORKERS = min(40, max(1, os.cpu_count() or 1))
    print(f"Parallel processing configured for {MAX_WORKERS} workers (from system CPU count)")

# Cache for storing calculated paths to avoid re-computing shortest paths for the same node pairs
path_cache = {}

@contextmanager
def timer(name):
    """Simple timer context manager"""
    start = time.time()
    try:
        yield
    finally:
        elapsed = time.time() - start
        print(f"  [{name}]: {elapsed:.2f}s")


def project_with_cache(gdf, target_crs):
    """Project a GeoDataFrame using an in-memory cache to avoid repeated to_crs calls."""
    if gdf is None or len(gdf) == 0:
        return gdf

    cache = gdf.attrs.setdefault('_projection_cache', {})
    cache_key = str(target_crs)
    if cache_key in cache:
        return cache[cache_key]

    if gdf.crs == target_crs:
        cache[cache_key] = gdf
    else:
        cache[cache_key] = gdf.to_crs(target_crs)
    return cache[cache_key]

def get_utm_crs(lon, lat):
    """Get appropriate UTM CRS for given coordinates. UTM is essential for accurate distance calculations in meters."""
    utm_zone = int((lon + 180) / 6) + 1
    return f"EPSG:{32600 + utm_zone}" if lat >= 0 else f"EPSG:{32700 + utm_zone}"

def haversine_distance_km(lat1, lon1, lat2, lon2):
    """Calculate haversine distance between two points in kilometers.
    
    This function computes the great-circle distance between two points
    on a sphere given their latitudes and longitudes.
    
    Args:
        lat1, lon1: Latitude and longitude of the first point (degrees)
        lat2, lon2: Latitude and longitude of the second point (degrees)
    
    Returns:
        float: Distance in kilometers
    """
    R = 6371  # Earth's radius in kilometers
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlambda = np.radians(lon2 - lon1)
    
    a = np.sin(dphi/2)**2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlambda/2)**2
    return 2 * R * np.arcsin(np.sqrt(a))

def get_country_bbox(admin_boundaries, buffer=0.1):
    """Get bounding box for a country with buffer"""
    bounds = admin_boundaries.total_bounds
    return [bounds[0] - buffer, bounds[1] - buffer, bounds[2] + buffer, bounds[3] + buffer]

def aggregate_raster(data, transform, factor):
    """Aggregate a raster array by the provided factor using block sums."""
    if factor <= 1:
        return data, transform

    rows, cols = data.shape
    new_rows = math.ceil(rows / factor)
    new_cols = math.ceil(cols / factor)

    pad_rows = new_rows * factor - rows
    pad_cols = new_cols * factor - cols
    if pad_rows or pad_cols:
        data = np.pad(data, ((0, pad_rows), (0, pad_cols)), mode='constant', constant_values=0)

    reshaped = data.reshape(new_rows, factor, new_cols, factor)
    aggregated = reshaped.sum(axis=(1, 3))
    new_transform = transform * Affine.scale(factor, factor)

    return aggregated, new_transform

def load_admin_boundaries(country_iso3):
    """Load administrative boundaries for a specific country from the GADM dataset."""
    gadm_file = os.path.join(get_bigdata_path('bigdata_gadm'), 'gadm_410-levels.gpkg')
    admin_boundaries = gpd.read_file(gadm_file, layer="ADM_0")
    country_data = admin_boundaries[admin_boundaries['GID_0'] == country_iso3]
    
    if country_data.empty:
        raise ValueError(f"No boundaries found for country {country_iso3}")
    
    return country_data

def load_population_centroids(country_bbox, admin_boundaries):
    """Load and process population centroids from the GHS-POP raster data.
    This is optimized to only process pixels with non-zero population, which significantly speeds up processing for sparsely populated areas.
    
    IMPORTANT: The window is aligned to POP_AGGREGATION_FACTOR boundaries so that
    aggregated pixel centers match the global CMIP6 grid (WPD/PVOUT layers).
    """
    minx, miny, maxx, maxy = country_bbox
    # Source: https://human-settlement.emergency.copernicus.eu/download.php?ds=pop
    # Select: 2025 eporch, 30 arcsec, WGS84(4326)

    pop_file = os.path.join(get_bigdata_path('bigdata_settlements_jrc'), 'GHS_POP_E2025_GLOBE_R2023A_4326_30ss_V1_0.tif')
    with rasterio.open(pop_file) as src:
        # Get initial window from bounds
        window = rasterio.windows.from_bounds(minx, miny, maxx, maxy, src.transform)
        
        # Align window to aggregation factor boundaries so pixel centers match global grid
        # This ensures settlement centroids align with CMIP6 WPD/PVOUT layers
        col_off = int(window.col_off // POP_AGGREGATION_FACTOR) * POP_AGGREGATION_FACTOR
        row_off = int(window.row_off // POP_AGGREGATION_FACTOR) * POP_AGGREGATION_FACTOR
        # Expand width/height to cover original extent (round up to next agg boundary)
        col_end = int(np.ceil((window.col_off + window.width) / POP_AGGREGATION_FACTOR)) * POP_AGGREGATION_FACTOR
        row_end = int(np.ceil((window.row_off + window.height) / POP_AGGREGATION_FACTOR)) * POP_AGGREGATION_FACTOR
        width = col_end - col_off
        height = row_end - row_off
        
        # Create aligned window
        window = rasterio.windows.Window(col_off, row_off, width, height)
        
        pop_data = src.read(1, window=window)
        windowed_transform = rasterio.windows.transform(window, src.transform)

        # Aggregate 30" raster to 300" (~10x10) blocks for performance smoother centroids
        pop_data, windowed_transform = aggregate_raster(pop_data, windowed_transform, POP_AGGREGATION_FACTOR)
        print(f"Aggregated population raster to {POP_AGGREGATION_FACTOR * 30}\" resolution")

        # OPTIMIZATION: Only process non-zero pixels
        nonzero_mask = pop_data > 0
        nonzero_indices = np.where(nonzero_mask)

        total_pixels = pop_data.size
        nonzero_pixels = len(nonzero_indices[0])
        print(f"Population raster: {nonzero_pixels:,} populated pixels out of {total_pixels:,} total ({nonzero_pixels/total_pixels*100:.1f}%)")
        
        if nonzero_pixels == 0:
            print("No population in this area")
            return gpd.GeoDataFrame(geometry=[], crs=COMMON_CRS)
        
        # Convert row/col indices to coordinates
        rows = nonzero_indices[0]
        cols = nonzero_indices[1]
        
        # Transform pixel coordinates to geographic coordinates
        xs, ys = rasterio.transform.xy(windowed_transform, rows, cols)
        
        # Create GeoDataFrame directly
        centroids_gdf = gpd.GeoDataFrame(
            {
                'geometry': gpd.points_from_xy(xs, ys),
                'Population_centroid': pop_data[nonzero_mask]
            },
            crs=COMMON_CRS
        )
        
        # Filter to country boundaries
        centroids_gdf = gpd.sjoin(centroids_gdf, admin_boundaries, how='inner', predicate='within')
        centroids_gdf = centroids_gdf.drop(columns=['index_right'])
        
        print(f"Loaded {len(centroids_gdf):,} population centroids within country boundaries")
        return centroids_gdf

def load_population_and_demand_projections(centroids_gdf, country_iso3):
    """
    Loads country-level population and demand projections for 2024, 2030, and 2050.
    It then allocates these national totals down to the individual population centroids based on their baseline population share.
    This assumes the spatial distribution of population remains constant, while the total numbers change.
    """
    print("Loading country-level population projections...")
    
    # Calculate baseline 2025 population from spatial data
    total_country_population_2025 = centroids_gdf["Population_centroid"].sum()
    print(f"Baseline population from spatial data (JRC 2025): {total_country_population_2025:,.0f}")

    # Initialize population projections (will be overwritten if data is found)
    pop_2024 = 0
    pop_2030 = 0
    pop_2050 = 0
    used_baseline = False
    
    try:
        pop_projections_df = pd.read_excel("outputs_processed_data/p1_b_ember_2024_30_50.xlsx")
        
        # Filter for this country using ISO3_code column
        country_pop_data = None
        if 'ISO3_code' in pop_projections_df.columns:
            country_pop_match = pop_projections_df[pop_projections_df['ISO3_code'] == country_iso3]
            if not country_pop_match.empty:
                country_pop_data = country_pop_match.iloc[0]
                print(f"Found population projections for {country_iso3}")
            else:
                print(f"Warning: No population projections found for {country_iso3}")
        
        # Extract country-level population totals for each year
        if country_pop_data is not None:
            # Convert from thousands to actual population counts
            pop_2024 = country_pop_data.get('PopTotal_2024', 0) * 1000
            pop_2030 = country_pop_data.get('PopTotal_2030', 0) * 1000
            pop_2050 = country_pop_data.get('PopTotal_2050', 0) * 1000
            used_baseline = False 
            
            print(f"Country population projections:")
            print(f"  2024: {pop_2024:,.0f}")
            print(f"  2030: {pop_2030:,.0f}")
            print(f"  2050: {pop_2050:,.0f}")
            
    except Exception as e:
        print(f"Warning: Could not load population projections for {country_iso3}: {e}")
        used_baseline = True 

    if used_baseline:
        print("Warning: Need to review p1_b_ember_2024_30_50.xlsx to add missing population data")

    # Load national demand data for this specific country
    print("Loading demand projections...")
    try:
        demand_df = pd.read_excel("outputs_processed_data/p1_b_ember_2024_30_50.xlsx")

        # Filter for this country using ISO3_code column
        if 'ISO3_code' in demand_df.columns:
            country_demand = demand_df[demand_df['ISO3_code'] == country_iso3]
            if country_demand.empty:
                print(f"Warning: No demand data found for {country_iso3} in demand file")
                demand_df = pd.DataFrame()
            else:
                demand_df = country_demand
                print(f"Loaded demand data for {country_iso3}: {len(demand_df)} records")
        else:
            print(f"Warning: ISO3_code column not found in demand data")
            demand_df = pd.DataFrame()
            
    except Exception as e:
        print(f"Warning: Could not load demand data for {country_iso3}: {e}")
        demand_df = pd.DataFrame()

    # Define demand types - only calculate for ANALYSIS_YEAR
    demand_types = DEMAND_TYPES
    year = ANALYSIS_YEAR
    country_populations = {2024: pop_2024, 2030: pop_2030, 2050: pop_2050}

    # First, calculate population share for each centroid based on JRC spatial population share
    population_share = centroids_gdf["Population_centroid"] / total_country_population_2025

    # Calculate spatially distributed population for ANALYSIS_YEAR
    print(f"\nAllocating population projections to centroids for {year}...")
    total_country_population_year = country_populations[year]
    
    # Allocate projected population to each centroid using JRC spatial population share
    pop_col = f"Population_{year}_centroid"
    centroids_gdf[pop_col] = population_share * total_country_population_year
    
    print(f"  {year} population allocated: {centroids_gdf[pop_col].sum():,.0f} total")

    # Calculate total demand for each centroid for ANALYSIS_YEAR
    print(f"\nProcessing energy demand for year {year}...")
    
    # Calculate total national demand for this year (projected generation)
    total_national_demand = 0
    demand_breakdown = {}  # Debug: track contribution by type
    if not demand_df.empty:
        for demand_type in demand_types:
            col = f"{demand_type}_{year}_MWh"
            if col in demand_df.columns:
                demand_value = demand_df[col].iloc[0] if not pd.isna(demand_df[col].iloc[0]) else 0
                demand_breakdown[demand_type] = demand_value
                total_national_demand += demand_value
            else:
                print(f"    Warning: Column '{col}' not found in demand data")
    
    # Debug: print breakdown
    if demand_breakdown:
        print(f"  Demand breakdown for {year}:")
        for dtype, val in demand_breakdown.items():
            if val > 0:
                print(f"    {dtype}: {val:,.0f} MWh")
    
    # If no demand data, use a default per capita value
    if total_national_demand == 0:
        # Default: 0 MWh per person per year (Can put global average 3.2 MWh per person per year if needed but all data have been calculated)
        total_national_demand = total_country_population_year * 0
        print(f"Using default demand for {country_iso3} in {year}: {total_national_demand:,.0f} MWh")
    else:
        print(f"Total national demand for {year}: {total_national_demand:,.0f} MWh")
    
    # Calculate centroid demand on a prorata basis
    # Demand_centroid = Population_Share_centroid × National_Demand_year
    demand_col = f"Total_Demand_{year}_centroid"
    centroids_gdf[demand_col] = population_share * total_national_demand
    
    # Also allocate demand by energy type to each centroid
    for demand_type in demand_types:
        type_demand_col = f"{demand_type}_{year}_centroid"
        type_national_demand = demand_breakdown.get(demand_type, 0)
        centroids_gdf[type_demand_col] = population_share * type_national_demand
    
    print(f"  Allocated {total_national_demand:,.0f} MWh across {len(centroids_gdf)} centroids")
    print(f"  Per capita demand: {total_national_demand/total_country_population_year:.2f} MWh/person/year")

    # Filter out centroids with zero population
    centroids_filtered = centroids_gdf[centroids_gdf["Population_centroid"] > 0].copy()
    
    print(f"\nFiltered centroids: {len(centroids_filtered)} with population > 0")
    
    return centroids_filtered

def load_siting_clusters(scenario, country_iso3):
    """Load siting clusters from parquet if available."""
    clusters_path = Path(f"outputs_per_country/parquet/{scenario}/siting_clusters_{country_iso3}.parquet")
    
    if not clusters_path.exists():
        return None
    
    try:
        clusters_df = pd.read_parquet(clusters_path)
        print(f"Loaded {len(clusters_df)} siting clusters from {clusters_path}")
        return clusters_df
    except Exception as e:
        print(f"Warning: Could not load siting clusters: {e}")
        return None

def load_energy_facilities(country_iso3, year=2024, scenario=None):
    """Load energy facilities for a specific country and year from the processed facility-level data.
    If scenario is provided and siting data exists, load existing facilities parquet and append siting clusters."""
    sheet_mapping = {2024: 'Grouped_cur_fac_lvl', 2030: 'Grouped_2030_fac_lvl', 2050: 'Grouped_2050_fac_lvl'}
    sheet_name = sheet_mapping.get(year, 'Grouped_cur_fac_lvl')
    
    # Check if siting data exists - if so, load existing facilities parquet instead of Excel
    if scenario and country_iso3:
        siting_summary_path = Path(f"outputs_per_country/parquet/{scenario}/{ANALYSIS_YEAR}_siting_{int(SUPPLY_FACTOR*100)}%_{country_iso3}.xlsx")
        if siting_summary_path.exists():
            existing_facilities_path = Path(f"outputs_per_country/parquet/{scenario}/facilities_{country_iso3}.parquet")
            
            # Load existing facilities if parquet exists, otherwise start with empty GeoDataFrame
            if existing_facilities_path.exists():
                print(f"Loading existing facilities from {existing_facilities_path}")
                facilities_gdf = gpd.read_parquet(existing_facilities_path)
                
                # Preserve remaining_mwh from first run (0 if fully used, >0 if still has capacity)
                # Only reset supplied_mwh since we're doing a fresh allocation
                facilities_gdf['supplied_mwh'] = 0.0
                # remaining_mwh stays as-is from first run
                print(f"Loaded {len(facilities_gdf)} existing facilities (preserving remaining_mwh from first run)")
            else:
                # No base facilities parquet (country had 0 existing facilities)
                # Create empty GeoDataFrame to append siting clusters to
                print(f"No existing facilities parquet for {country_iso3} (country has no base facilities)")
                facilities_gdf = gpd.GeoDataFrame(geometry=[], crs=COMMON_CRS)
            
            # Load and append siting clusters
            clusters_df = load_siting_clusters(scenario, country_iso3)
            
            if clusters_df is not None and not clusters_df.empty:
                # Verify required columns exist
                required_cols = ['cluster_id', 'Grouped_Type', 'center_lat', 'center_lon', 'remaining_mwh']
                missing_cols = [col for col in required_cols if col not in clusters_df.columns]
                if missing_cols:
                    print(f"Warning: Siting clusters missing required columns: {missing_cols}")
                    print(f"Available columns: {list(clusters_df.columns)}")
                    return facilities_gdf
                
                # Map siting clusters to facility format
                cluster_facilities = pd.DataFrame({
                    'GID_0': country_iso3,
                    'Country Code': country_iso3,
                    'GEM unit/phase ID': clusters_df['cluster_id'].astype(str),
                    'Grouped_Type': clusters_df['Grouped_Type'],
                    'Latitude': clusters_df['center_lat'],
                    'Longitude': clusters_df['center_lon'],
                    'Adjusted_Capacity_MW': np.nan,
                    'total_mwh': clusters_df['remaining_mwh'],
                    'available_total_mwh': clusters_df['remaining_mwh'] * SUPPLY_FACTOR,
                    'supplied_mwh': 0.0,
                    'remaining_mwh': clusters_df['remaining_mwh'] * SUPPLY_FACTOR  # Initialize with capacity, not 0!
                })
                
                # Create geometry for cluster facilities
                cluster_geometry = gpd.points_from_xy(cluster_facilities['Longitude'], cluster_facilities['Latitude'])
                cluster_facilities_gdf = gpd.GeoDataFrame(cluster_facilities, geometry=cluster_geometry, crs=COMMON_CRS)
                
                # Append cluster facilities to existing facilities
                facilities_gdf = pd.concat([facilities_gdf, cluster_facilities_gdf], ignore_index=True)
                print(f"Added {len(cluster_facilities_gdf)} siting clusters to facilities (total: {len(facilities_gdf)})")
            
            return facilities_gdf
    
    # If no siting data or facilities parquet doesn't exist, load from Excel
    try:
        facilities_df = pd.read_excel("outputs_processed_data/p1_a_ember_gem_2024_fac_lvl.xlsx", sheet_name=sheet_name)
        country_facilities = facilities_df[facilities_df['Country Code'] == country_iso3].copy()
        
        if country_facilities.empty:
            facilities_gdf = gpd.GeoDataFrame()
        else:
            geometry = gpd.points_from_xy(country_facilities['Longitude'], country_facilities['Latitude'])
            facilities_gdf = gpd.GeoDataFrame(country_facilities, geometry=geometry, crs=COMMON_CRS)
        
        print(f"Loaded {len(facilities_gdf)} base facilities for {country_iso3} from Excel")
        return facilities_gdf
        
    except Exception as e:
        print(f"Error loading facilities: {e}")
        return gpd.GeoDataFrame()

def stitch_grid_segments(grid_lines_gdf, admin_boundaries, max_distance_km=GRID_STITCH_DISTANCE_KM):
    """
    Connect disconnected grid segments before network creation using MST approach.
    Builds a temporary graph, finds disconnected components, and stitches them within
    the distance threshold. Returns the original grid plus new stitch LineStrings.
    """
    if grid_lines_gdf.empty or max_distance_km <= 0:
        return grid_lines_gdf

    admin_union = admin_boundaries.to_crs(COMMON_CRS).geometry.union_all()
    centroid = admin_union.centroid
    utm_crs = get_utm_crs(centroid.x, centroid.y)

    grid_utm = grid_lines_gdf.to_crs(utm_crs)
    grid_graph = nx.Graph()

    def iter_line_geometries(geom):
        """Yield only LineString geometries from possibly nested multi-part inputs."""
        if geom is None or geom.is_empty:
            return []
        if isinstance(geom, LineString):
            return [geom]
        if isinstance(geom, MultiLineString):
            return list(geom.geoms)
        if isinstance(geom, GeometryCollection):
            lines = []
            for sub in geom.geoms:
                lines.extend(iter_line_geometries(sub))
            return lines
        return []

    # Build temporary graph from grid lines
    for geom in grid_utm.geometry:
        if geom is None or geom.is_empty:
            continue
        lines = iter_line_geometries(geom)
        for line in lines:
            coords = list(line.coords)
            if len(coords) < 2:
                continue
            start = (coords[0][0], coords[0][1])
            end = (coords[-1][0], coords[-1][1])
            grid_graph.add_node(start, type='grid_line')
            grid_graph.add_node(end, type='grid_line')
            grid_graph.add_edge(start, end, weight=line.length, edge_type='grid_infrastructure')

    # Find all components (including isolated nodes)
    # Previously filtered to len(comp) > 1, but now include isolated nodes
    # since facilities are aggregated to 10km cells matching stitch distance
    components = list(nx.connected_components(grid_graph))
    significant_components = [comp for comp in components if len(comp) >= 1]
    
    if len(significant_components) <= 1:
        return grid_lines_gdf
    
    # MST-based stitching: connect components starting from largest
    # OPTIMIZATION: Use KDTree for spatial indexing to avoid O(n²·m²) nested loops
    max_distance_m = max_distance_km * 1000
    component_sizes = sorted(enumerate(significant_components), key=lambda x: len(x[1]), reverse=True)
    connected_idxs = [component_sizes[0][0]]
    unconnected_idxs = [idx for idx, _ in component_sizes[1:]]
    
    # Build spatial index for connected component nodes
    from scipy.spatial import cKDTree
    
    def get_component_nodes_array(component):
        """Extract valid 2D nodes from component as numpy array."""
        nodes = [n for n in component if isinstance(n, tuple) and len(n) == 2]
        return np.array(nodes) if nodes else np.empty((0, 2))
    
    stitch_edges = []
    while unconnected_idxs:
        # Build KDTree from all currently connected component nodes
        connected_nodes = []
        for cidx in connected_idxs:
            connected_nodes.extend([n for n in significant_components[cidx] 
                                   if isinstance(n, tuple) and len(n) == 2])
        
        if not connected_nodes:
            break
            
        connected_tree = cKDTree(np.array(connected_nodes))
        
        best_connection = None
        best_distance = float('inf')
        best_idx = None
        
        # For each unconnected component, find nearest connected node using KDTree
        for uidx in unconnected_idxs:
            unconnected_nodes = get_component_nodes_array(significant_components[uidx])
            if len(unconnected_nodes) == 0:
                continue
            
            # Query KDTree for nearest neighbor from each unconnected node
            distances, indices = connected_tree.query(unconnected_nodes, k=1)
            min_idx = np.argmin(distances)
            min_distance = distances[min_idx]
            
            if min_distance < best_distance:
                best_distance = min_distance
                best_connection = (tuple(unconnected_nodes[min_idx]), tuple(connected_nodes[indices[min_idx]]))
                best_idx = uidx
        
        if best_connection and best_distance <= max_distance_m:
            stitch_edges.append(LineString([best_connection[0], best_connection[1]]))
            connected_idxs.append(best_idx)
            unconnected_idxs.remove(best_idx)
        else:
            break

    if not stitch_edges:
        return grid_lines_gdf

    # Create stitches with consistent column naming, matching all columns from input
    stitch_data = {
        'line_type': ['component_stitch'] * len(stitch_edges),
        'line_id': [f'stitch_{i}' for i in range(len(stitch_edges))]
    }
    
    # Preserve or initialize all columns from input grid to avoid nulls in concatenation
    if not grid_lines_gdf.empty:
        for col in grid_lines_gdf.columns:
            if col not in stitch_data and col != 'geometry':
                if col == 'GID_0' and 'GID_0' in grid_lines_gdf.columns:
                    stitch_data['GID_0'] = [grid_lines_gdf['GID_0'].iloc[0]] * len(stitch_edges)
                elif col == 'distance_km':
                    # Calculate actual distances for stitches
                    stitch_data['distance_km'] = [edge.length / 1000.0 for edge in stitch_edges]
                else:
                    # Default null for other columns
                    stitch_data[col] = [None] * len(stitch_edges)
    
    stitches_gdf = gpd.GeoDataFrame(
        stitch_data,
        geometry=stitch_edges,
        crs=utm_crs
    ).to_crs(grid_lines_gdf.crs or COMMON_CRS)

    print(f"Stitched {len(stitch_edges)} grid connections at {max_distance_km}km threshold")
    return pd.concat([grid_lines_gdf, stitches_gdf], ignore_index=True)

def load_siting_networks(scenario, country_iso3):
    """Load siting networks from parquet if available."""
    networks_path = Path(f"outputs_per_country/parquet/{scenario}/siting_networks_{country_iso3}.parquet")
    
    if not networks_path.exists():
        return None
    
    try:
        networks_df = pd.read_parquet(networks_path)
        # Convert to GeoDataFrame if geometry column exists
        if 'geometry' in networks_df.columns:
            # Handle WKB geometry bytes - convert to shapely geometries
            from shapely import wkb
            if isinstance(networks_df['geometry'].iloc[0], bytes):
                networks_df['geometry'] = networks_df['geometry'].apply(lambda x: wkb.loads(x) if pd.notna(x) else None)
            networks_gdf = gpd.GeoDataFrame(networks_df, geometry='geometry', crs=COMMON_CRS)
        else:
            print(f"Warning: No geometry column in siting networks parquet")
            return None
        
        print(f"Loaded {len(networks_gdf)} siting network segments from {networks_path}")
        return networks_gdf
    except Exception as e:
        print(f"Warning: Could not load siting networks: {e}")
        return None


def load_grid_lines(country_bbox, admin_boundaries, scenario=None, country_iso3=None):
    """Load and clip grid lines from GridFinder data. Uses parallel processing for very large countries to speed up clipping.
    If scenario is provided and siting data exists, load existing grid_lines parquet and append siting networks."""
    minx, miny, maxx, maxy = country_bbox
    admin_union = admin_boundaries.to_crs(COMMON_CRS).geometry.union_all()
    grid_country = None

    # Check if siting data exists - if so, load existing grid_lines parquet instead of raw GridFinder
    if scenario and country_iso3:
        siting_summary_path = Path(f"outputs_per_country/parquet/{scenario}/{ANALYSIS_YEAR}_siting_{int(SUPPLY_FACTOR*100)}%_{country_iso3}.xlsx")
        if siting_summary_path.exists():
            # Load existing grid_lines parquet file
            existing_grid_path = Path(f"outputs_per_country/parquet/{scenario}/grid_lines_{country_iso3}.parquet")
            if existing_grid_path.exists():
                print(f"Loading existing grid lines from {existing_grid_path}")
                grid_country = gpd.read_parquet(existing_grid_path)
                print(f"Loaded {len(grid_country)} existing grid line segments")
                
                # Ensure existing grid has proper line_type (handle legacy data)
                if 'line_type' not in grid_country.columns:
                    grid_country['line_type'] = 'grid_infrastructure'
                else:
                    # Fill any null/missing line_type values with grid_infrastructure
                    grid_country['line_type'] = grid_country['line_type'].fillna('grid_infrastructure')
                
                # Load and append siting networks
                networks_gdf = load_siting_networks(scenario, country_iso3)
                
                if networks_gdf is not None and not networks_gdf.empty:
                    # Map siting networks to grid lines format, matching existing columns
                    network_lines_data = {
                        'GID_0': country_iso3,
                        'line_type': 'siting_networks',
                        'line_id': 'siting_' + networks_gdf.index.astype(str),
                        'distance_km': networks_gdf.get('distance_km', 0),
                        'geometry': networks_gdf.geometry
                    }
                    
                    # Add any other columns from existing grid with default values
                    for col in grid_country.columns:
                        if col not in network_lines_data and col != 'geometry':
                            network_lines_data[col] = None  # Will be filled appropriately
                    
                    network_lines_gdf = gpd.GeoDataFrame(network_lines_data, geometry='geometry', crs=COMMON_CRS)
                    
                    # Ensure column order matches existing grid
                    common_cols = [c for c in grid_country.columns if c in network_lines_gdf.columns]
                    network_lines_gdf = network_lines_gdf[common_cols]
                    
                    # Append siting networks to grid lines
                    grid_country = pd.concat([grid_country, network_lines_gdf], ignore_index=True)
                    print(f"Added {len(network_lines_gdf)} siting network segments to grid (total: {len(grid_country)})")
                    
                    # Fill any remaining nulls in line_type
                    grid_country['line_type'] = grid_country['line_type'].fillna('grid_infrastructure')

                if not grid_country.empty and 'geometry' in grid_country.columns:
                    geom_types = grid_country.geometry.geom_type.value_counts(dropna=False)
                    print("Combined grid geometry types before stitch:")
                    for gtype, count in geom_types.items():
                        print(f"  {gtype}: {count}")

                # Stitch the combined grid to connect siting networks with existing infrastructure
                grid_country = stitch_grid_segments(grid_country, admin_boundaries, GRID_STITCH_DISTANCE_KM)
                return grid_country

    # If no siting data or grid_lines parquet doesn't exist, load from GridFinder
    grid_file = os.path.join(get_bigdata_path('bigdata_gridfinder'), 'grid.gpkg')
    try:
        if HAS_PYOGRIO:
            try:
                print("Reading grid lines via pyogrio with spatial mask...")
                grid_country = pyogrio.read_dataframe(
                    grid_file,
                    mask=admin_union,
                    use_arrow=True
                )
            except Exception as pyogrio_error:
                print(f"pyogrio read failed ({pyogrio_error}); falling back to GeoPandas")
                grid_country = None

        if grid_country is None:
            try:
                grid_lines = gpd.read_file(grid_file, bbox=(minx, miny, maxx, maxy))
            except TypeError:
                grid_lines = gpd.read_file(grid_file)
                grid_lines = grid_lines.cx[minx:maxx, miny:maxy]

            if len(grid_lines) > 10000:
                print(f"Large grid dataset ({len(grid_lines)} lines) - using parallel clipping")
                chunk_size = 2000
                chunks = [grid_lines.iloc[i:i+chunk_size] for i in range(0, len(grid_lines), chunk_size)]

                def clip_chunk(chunk):
                    return gpd.clip(chunk, admin_boundaries)

                with ThreadPoolExecutor(max_workers=min(16, MAX_WORKERS)) as executor:
                    clipped_chunks = list(executor.map(clip_chunk, chunks))

                grid_country = pd.concat(clipped_chunks, ignore_index=True)
            else:
                grid_country = gpd.clip(grid_lines, admin_boundaries)

        if grid_country.empty:
            print("Warning: No grid data found after clipping")
            return grid_country

        if grid_country.crs is None:
            grid_country = grid_country.set_crs(COMMON_CRS)

        grid_country = grid_country.to_crs(COMMON_CRS).reset_index(drop=True)
        print(f"Loaded {len(grid_country)} base grid line segments from GridFinder")

        grid_country = stitch_grid_segments(grid_country, admin_boundaries, GRID_STITCH_DISTANCE_KM)
        return grid_country

    except Exception as e:
        print(f"Error loading grid data: {e}")
        return gpd.GeoDataFrame()


def _collect_intersection_points(geometry):
    """Extract representative point geometries from a shapely intersection result."""
    if geometry is None or geometry.is_empty:
        return []

    geom_type = geometry.geom_type
    if geom_type == "Point":
        return [geometry]
    if geom_type == "MultiPoint":
        return list(geometry.geoms)
    if geom_type in ("LineString", "LinearRing"):
        coords = list(geometry.coords)
        if not coords:
            return []
        return [Point(coords[0]), Point(coords[-1])]
    if geom_type == "MultiLineString":
        points = []
        for sub in geometry.geoms:
            points.extend(_collect_intersection_points(sub))
        return points
    if geom_type == "GeometryCollection":
        points = []
        for sub in geometry.geoms:
            points.extend(_collect_intersection_points(sub))
        return points
    return []


def _build_splitter(points, line, tolerance=1e-6):
    """Create a MultiPoint splitter for a line, filtering duplicates and endpoints."""
    if not points:
        return None

    unique = {}
    line_length = line.length
    if line_length == 0:
        return None

    for pt in points:
        if not isinstance(pt, Point):
            continue
        proj = line.project(pt)
        if proj < tolerance or proj > line_length - tolerance:
            continue  # Skip endpoints to avoid zero-length segments
        key = (round(pt.x, 6), round(pt.y, 6))
        if key not in unique:
            unique[key] = pt

    if not unique:
        return None

    pts = list(unique.values())
    return pts[0] if len(pts) == 1 else MultiPoint(pts)


def _split_lines_with_strtree(lines, min_lines=500):
    """Use STRtree to lazily split lines only where intersections exist."""
    num_lines = len(lines)
    if num_lines < min_lines:
        return None

    try:
        tree = STRtree(lines)
        query_bulk = getattr(tree, "query_bulk", None)
        if query_bulk is None:
            return None
        idx_pairs = query_bulk(lines, predicate="intersects")
    except Exception as exc:
        raise RuntimeError(exc)

    if idx_pairs.size == 0:
        return None

    intersections = {}
    for idx1, idx2 in zip(idx_pairs[0], idx_pairs[1]):
        i = int(idx1)
        j = int(idx2)
        if i >= j:
            continue  # Avoid duplicates and self-pairs
        inter = lines[i].intersection(lines[j])
        if inter.is_empty:
            continue
        points = _collect_intersection_points(inter)
        if not points:
            continue
        intersections.setdefault(i, []).extend(points)
        intersections.setdefault(j, []).extend(points)

    if not intersections:
        return None

    split_segments = []
    skipped = 0
    for idx, line in enumerate(lines):
        splitter = _build_splitter(intersections.get(idx, []), line)
        if splitter is None:
            split_segments.append(line)
            continue
        try:
            result = split(line, splitter)
        except Exception:
            split_segments.append(line)
            skipped += 1
            continue

        geoms = list(result.geoms) if hasattr(result, "geoms") else [result]
        for geom in geoms:
            if isinstance(geom, LineString):
                split_segments.append(geom)
            elif isinstance(geom, MultiLineString):
                split_segments.extend(list(geom.geoms))

    print(f"  STRtree split produced {len(split_segments)} segments (fallback skips: {skipped})")
    return split_segments

def split_intersecting_edges(lines):
    """
    Splits all lines at their intersection points. Prefers a fast vectorized approach (momepy) when available,
    with graceful fallback to the legacy chunked strategy for extremely large datasets.
    """
    lines = list(lines) if not isinstance(lines, list) else lines
    num_lines = len(lines)
    if num_lines == 0:
        return []

    if HAS_MOMEPY_SPLIT_LINES:
        try:
            gdf = gpd.GeoDataFrame({'geometry': lines})
            split_gdf = momepy_preprocessing.split_lines(gdf)
            print(f"  Fast split (momepy) produced {len(split_gdf)} segments")
            # Flatten multi-part outputs into individual LineStrings (recursive).
            def _flatten_geom(geom):
                if geom is None or geom.is_empty:
                    return
                if isinstance(geom, LineString):
                    yield geom
                elif isinstance(geom, MultiLineString):
                    yield from geom.geoms
                elif isinstance(geom, GeometryCollection):
                    for sub in geom.geoms:
                        yield from _flatten_geom(sub)

            result = []
            for geom in split_gdf.geometry:
                result.extend(_flatten_geom(geom))
            return result
        except Exception as e:
            print(f"  Warning: momepy split failed ({e}); falling back to STRtree/legacy splitter")
    elif HAS_MOMEPY:
        print("  momepy installed but split_lines API not available; using STRtree/legacy splitter")

    if num_lines >= 500:
        try:
            strtree_segments = _split_lines_with_strtree(lines, min_lines=500)
            if strtree_segments is not None:
                return strtree_segments
        except RuntimeError as e:
            print(f"  Warning: STRtree split failed ({e}); using legacy splitter")

    if num_lines > 10000:  # Very large grid networks (like JPN, CHN)
        print(f"Processing {num_lines:,} grid lines using parallel chunked approach with {MAX_WORKERS} workers...")
        chunk_size = 500
        chunks = [lines[i:i+chunk_size] for i in range(0, num_lines, chunk_size)]
        
        def process_chunk_parallel(chunk_lines):
            chunk_merged = unary_union(chunk_lines)
            chunk_segments = []
            if isinstance(chunk_merged, (LineString, MultiLineString)):
                segments = [chunk_merged] if isinstance(chunk_merged, LineString) else list(chunk_merged.geoms)
                for segment in segments:
                    if not isinstance(segment, LineString):
                        continue
                    coords = list(segment.coords)
                    for j in range(len(coords) - 1):
                        chunk_segments.append(LineString([coords[j], coords[j + 1]]))
            return chunk_segments
        
        all_segments = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(process_chunk_parallel, chunk) for chunk in chunks]
            for i, future in enumerate(futures):
                all_segments.extend(future.result())
                if (i + 1) % 10 == 0 or (i + 1) == len(futures):
                    processed = min((i + 1) * chunk_size, num_lines)
                    print(f"    Processed {processed:,}/{num_lines:,} grid lines...")
        print(f"Split into {len(all_segments):,} segments in parallel")
        return all_segments

    if num_lines > 1000:
        print(f"Processing {num_lines:,} grid lines using {MAX_WORKERS} parallel workers...")
        # Optimize batch size for better load balancing
        batch_size = max(50, num_lines // (MAX_WORKERS * 2))  # 2x workers for better distribution
        batches = [lines[i:i+batch_size] for i in range(0, num_lines, batch_size)]

        def process_batch(batch_lines):
            batch_merged = unary_union(batch_lines)
            if isinstance(batch_merged, (LineString, MultiLineString)):
                segments = [batch_merged] if isinstance(batch_merged, LineString) else list(batch_merged.geoms)
                batch_segments = []
                for segment in segments:
                    if not isinstance(segment, LineString):
                        continue
                    coords = list(segment.coords)
                    for i in range(len(coords) - 1):
                        batch_segments.append(LineString([coords[i], coords[i + 1]]))
                return batch_segments
            return []

        all_segments = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(process_batch, batch) for batch in batches]
            for i, future in enumerate(futures):
                batch_segments = future.result()
                all_segments.extend(batch_segments)
                if (i + 1) % max(1, len(batches) // 10) == 0 or (i + 1) == len(batches):
                    print(f"  Processed grid batch {i+1}/{len(batches)}")
        print(f"  Parallel split complete: {len(all_segments):,} segments from {len(batches)} batches")
        return all_segments

    merged = unary_union(lines)
    if isinstance(merged, (LineString, MultiLineString)):
        segments = [merged] if isinstance(merged, LineString) else list(merged.geoms)
        final_segments = []
        for segment in segments:
            if not isinstance(segment, LineString):
                continue
            coords = list(segment.coords)
            for i in range(len(coords) - 1):
                final_segments.append(LineString([coords[i], coords[i + 1]]))
        return final_segments
    return []

def create_network_graph(facilities_gdf, grid_lines_gdf, centroids_gdf):
    """
    Creates the master network graph.
    This function combines grid lines, power plants (facilities), and population centers (centroids) into a single graph.
    - Grid line intersections and endpoints become 'grid_line' nodes.
    - Facilities and centroids are added as their own node types.
    - Facilities and centroids are connected to their nearest node on the grid.
    - Grid stitching now occurs upstream, so this function assumes inputs are already well-connected.
    This function is heavily optimized using spatial indexing (cKDTree) for fast nearest-neighbor searches.
    """
    # Get UTM CRS for accurate distance calculations
    center_lon = facilities_gdf.geometry.union_all().centroid.x if not facilities_gdf.empty else grid_lines_gdf.geometry.union_all().centroid.x
    center_lat = facilities_gdf.geometry.union_all().centroid.y if not facilities_gdf.empty else grid_lines_gdf.geometry.union_all().centroid.y
    utm_crs = get_utm_crs(center_lon, center_lat)
    
    print(f"Projecting to {utm_crs}")
    
    # Project to UTM (cached to avoid repeated to_crs work)
    facilities_utm = project_with_cache(facilities_gdf, utm_crs)
    grid_lines_utm = project_with_cache(grid_lines_gdf, utm_crs)
    centroids_utm = project_with_cache(centroids_gdf, utm_crs)
    
    # Initialize graph
    G = nx.Graph()

    def iter_line_geometries(geom):
        if geom is None or geom.is_empty:
            return []
        if isinstance(geom, LineString):
            return [geom]
        if isinstance(geom, MultiLineString):
            return list(geom.geoms)
        if isinstance(geom, GeometryCollection):
            lines = []
            for sub in geom.geoms:
                lines.extend(iter_line_geometries(sub))
            return lines
        return []

    # Process grid lines — flatten any multi-part geometries
    single_lines = []
    for geom in grid_lines_utm.geometry:
        single_lines.extend(iter_line_geometries(geom))
    
    # OPTIMIZATION 1: Timer for splitting
    print(f"Splitting {len(single_lines)} grid lines at intersections...")
    split_start = time.time()
    split_lines = split_intersecting_edges(single_lines)
    print(f"  Split complete in {time.time() - split_start:.2f}s: {len(single_lines)} -> {len(split_lines)} segments")
    
    snap_tolerance = NODE_SNAP_TOLERANCE_M

    def snap_coord(coord):
        if snap_tolerance <= 0:
            return coord
        return (
            round(coord[0] / snap_tolerance) * snap_tolerance,
            round(coord[1] / snap_tolerance) * snap_tolerance,
        )

    # Create nodes/edges from line endpoints
    grid_nodes = set()
    grid_edges = []
    for raw_line in split_lines:
        if raw_line is None or raw_line.is_empty:
            continue
        # Defensively flatten any multi-part geometries that slipped through
        sub_lines = iter_line_geometries(raw_line)
        for line in sub_lines:
            coords = list(line.coords)
            if len(coords) < 2:
                continue
            start = snap_coord(coords[0])
            end = snap_coord(coords[-1])
            if start == end:
                continue
            grid_nodes.add(start)
            grid_nodes.add(end)
            grid_edges.append((start, end, line))

    print(f"Adding {len(grid_nodes)} snapped grid nodes to graph...")

    # Add nodes to graph
    for node in grid_nodes:
        G.add_node(node, type='grid_line')
    
    # Add facility nodes
    facility_nodes = []
    facility_node_set = set()
    # Add facility nodes - allow multiple facilities at same location by using facility index
    facility_nodes = []
    for idx, point in zip(facilities_gdf.index, facilities_utm.geometry):
        node_coord = (point.x, point.y, f'fac_{idx}')  # Add facility index to make unique
        facility_nodes.append(node_coord)
        G.add_node(node_coord, type='facility', facility_idx=idx, coord=(point.x, point.y))
    
    # Add centroid nodes
    centroid_nodes = []
    centroid_node_set = set()
    for idx, point in zip(centroids_gdf.index, centroids_utm.geometry):
        node_coord = (point.x, point.y)
        if node_coord in centroid_node_set:
            continue
        centroid_node_set.add(node_coord)
        centroid_nodes.append(node_coord)
        G.add_node(node_coord, type='pop_centroid', centroid_idx=idx)
    
    print(f"Added {len(facility_nodes)} facility nodes and {len(centroid_nodes)} centroid nodes")
    
    # Add edges from grid lines
    print(f"Adding {len(grid_edges)} grid edges...")
    for start, end, line_geom in grid_edges:
        if line_geom is None or line_geom.is_empty:
            continue
        edge_length = line_geom.length
        G.add_edge(start, end, weight=edge_length, weight_cached=edge_length, edge_type='grid_infrastructure', geometry=line_geom)
    
    # OPTIMIZATION 2: Use spatial index for finding nearest grid nodes
    print("Building spatial index for grid nodes...")
    from scipy.spatial import cKDTree
    
    # Convert grid nodes to array for KDTree
    grid_node_list = list(grid_nodes)
    grid_node_array = np.array(grid_node_list)
    
    # Build KDTree for fast nearest neighbor search
    grid_tree = cKDTree(grid_node_array)
    
    # OPTIMIZATION 3: Vectorized connection of facilities to grid
    print(f"Connecting {len(facilities_utm)} facilities to nearest grid nodes...")
    connect_start = time.time()
    
    # threshold for connecting facilities/centroids to grid
    max_distance = MAX_CONNECTION_DISTANCE_M
    
    # Process facilities
    if facility_nodes:
        # Extract (x, y) coordinates for KDTree query (facility_nodes are now tuples with facility ID)
        facility_coords = np.array([G.nodes[node]['coord'] for node in facility_nodes])
        
        # Find nearest grid node for each facility using KDTree
        distances, indices = grid_tree.query(facility_coords, k=1)
        
        # Add edges for facilities within threshold
        for node, coord_2d, dist, idx in zip(facility_nodes, facility_coords, distances, indices):
            if dist <= max_distance:
                nearest_grid = grid_node_list[idx]
                connector = LineString([coord_2d, nearest_grid])
                G.add_edge(node, nearest_grid, weight=dist, weight_cached=dist, edge_type='grid_to_facility', geometry=connector)
    
    print(f"  Facilities connected in {time.time() - connect_start:.2f}s")
    
    # Track isolated facilities (facilities not connected to grid)
    isolated_facilities = []
    for node in facility_nodes:
        if G.degree(node) == 0:  # No edges = isolated
            isolated_facilities.append(node)
    
    if isolated_facilities:
        print(f"  Warning: {len(isolated_facilities)} facilities are isolated (no grid within {max_distance/1000:.1f}km)")
        print(f"  These facilities will be treated as virtual grid nodes for direct centroid connections")
    
    # OPTIMIZATION 4: Always process centroids in batches to keep memory bounded
    print(f"Connecting {len(centroids_utm)} centroids to nearest grid nodes...")
    connect_start = time.time()
    
    # Build combined spatial index: grid nodes + isolated facility nodes
    # This allows centroids to connect to facilities directly if no grid is nearby
    if isolated_facilities:
        # Add isolated facility coordinates to the connection pool
        isolated_fac_coords = np.array([G.nodes[node]['coord'] for node in isolated_facilities])
        combined_nodes = np.vstack([grid_node_array, isolated_fac_coords])
        combined_node_list = grid_node_list + isolated_facilities
        combined_tree = cKDTree(combined_nodes)
        print(f"  Using combined index: {len(grid_node_list)} grid nodes + {len(isolated_facilities)} isolated facilities")
    else:
        combined_tree = grid_tree
        combined_node_list = grid_node_list
    
    if centroid_nodes:
        centroid_list = centroid_nodes[:]
        batch_size = 5000 if len(centroid_list) > 5000 else len(centroid_list)
        processed = 0
        for i in range(0, len(centroid_list), batch_size):
            batch_end = min(i + batch_size, len(centroid_list))
            batch_coords = np.array(centroid_list[i:batch_end])
            distances, indices = combined_tree.query(batch_coords, k=1)
            for coord, dist, idx in zip(centroid_list[i:batch_end], distances, indices):
                if dist <= max_distance:
                    nearest_node = combined_node_list[idx]
                    connector = LineString([coord, nearest_node[:2] if len(nearest_node) == 3 else nearest_node])
                    # Determine edge type based on whether nearest node is facility or grid
                    edge_type = 'centroid_to_facility' if nearest_node in isolated_facilities else 'centroid_to_grid'
                    G.add_edge(coord, nearest_node, weight=dist, weight_cached=dist, edge_type=edge_type, geometry=connector)
            processed = batch_end
            if processed % 10000 == 0 or processed == len(centroid_list):
                print(f"    Processed {processed:,}/{len(centroid_list):,} centroids...")
    
    print(f"  Centroids connected in {time.time() - connect_start:.2f}s")
    
    # Store UTM CRS for coordinate conversion
    G.graph['utm_crs'] = utm_crs
    print(f"Network created: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges (stitching handled upstream)")

    return G


def allocate_supply_no_grid(facilities_gdf, centroids_gdf, demand_col, radius_km=NO_GRID_CONNECTION_RADIUS_KM):
    """
    Allocate supply from facilities to centroids without grid infrastructure.
    
    This function handles countries like MUS where facilities exist but no grid data
    is available. Each facility independently supplies nearby settlements within
    a specified radius using Euclidean (great-circle) distance.
    
    The allocation strategy is:
    1. Each facility operates independently (no inter-facility connections)
    2. Facilities are processed largest-first
    3. For each facility, centroids within radius_km are sorted by distance (closest first)
    4. Allocation continues until facility capacity is exhausted (Option C)
    
    Args:
        facilities_gdf: GeoDataFrame with facility locations and 'total_mwh' column
        centroids_gdf: GeoDataFrame with centroid locations and demand columns
        demand_col: Column name for demand (e.g., 'Total_Demand_2030_centroid')
        radius_km: Maximum Euclidean connection distance in km (default from config)
    
    Returns:
        tuple: (updated_centroids_gdf, updated_facilities_gdf, 
                facility_remaining, facility_supplied, active_connections)
    """
    print(f"\n  === No-Grid Euclidean Allocation ===")
    print(f"  Connection radius: {radius_km} km")
    print(f"  Facilities: {len(facilities_gdf)}, Centroids: {len(centroids_gdf)}")
    
    # Initialize tracking dictionaries
    facility_remaining = {}
    facility_supplied = {}
    
    # Initialize facility columns
    for idx in facilities_gdf.index:
        total_mwh = facilities_gdf.loc[idx, 'total_mwh'] if 'total_mwh' in facilities_gdf.columns else 0
        facility_remaining[idx] = float(total_mwh) if pd.notna(total_mwh) else 0.0
        facility_supplied[idx] = 0.0
    
    # Initialize centroid columns
    centroids_gdf = centroids_gdf.copy()
    centroids_gdf['supply_received_mwh'] = 0.0
    centroids_gdf['supplying_facility_distance'] = ''
    centroids_gdf['supplying_facility_type'] = ''
    centroids_gdf['supplying_facility_gem_id'] = ''
    centroids_gdf['supply_status'] = 'Not Filled'
    
    # Initialize facilities output columns
    facilities_gdf = facilities_gdf.copy()
    facilities_gdf['original_total_mwh'] = facilities_gdf['total_mwh'] if 'total_mwh' in facilities_gdf.columns else 0
    facilities_gdf['available_total_mwh'] = 0.0  # Will be updated after allocation
    
    # Get centroid coordinates
    centroid_coords = []
    for idx in centroids_gdf.index:
        geom = centroids_gdf.loc[idx, 'geometry']
        centroid_coords.append({
            'idx': idx,
            'lat': geom.y,
            'lon': geom.x,
            'demand': float(centroids_gdf.loc[idx, demand_col]) if pd.notna(centroids_gdf.loc[idx, demand_col]) else 0.0
        })
    
    # Get facility coordinates and capacities
    facility_list = []
    for idx in facilities_gdf.index:
        geom = facilities_gdf.loc[idx, 'geometry']
        capacity = facility_remaining[idx]
        facility_list.append({
            'idx': idx,
            'lat': geom.y,
            'lon': geom.x,
            'capacity': capacity,
            'gem_id': str(facilities_gdf.loc[idx, 'GEM unit/phase ID']) if pd.notna(facilities_gdf.loc[idx].get('GEM unit/phase ID')) else '',
            'facility_type': facilities_gdf.loc[idx].get('Grouped_Type', '')
        })
    
    # Sort facilities by capacity (largest first)
    facility_list.sort(key=lambda x: x['capacity'], reverse=True)
    
    active_connections = []
    supply_tracking = {c['idx']: {'received': 0.0, 'distances': [], 'types': [], 'gem_ids': []} 
                       for c in centroid_coords}
    
    # Pre-calculate distances from each facility to all centroids
    print(f"  Calculating distances from {len(facility_list)} facilities to {len(centroid_coords)} centroids...")
    
    facility_to_centroids = {}
    for fac in facility_list:
        fac_centroids = []
        for cent in centroid_coords:
            dist_km = haversine_distance_km(fac['lat'], fac['lon'], cent['lat'], cent['lon'])
            if dist_km <= radius_km:
                fac_centroids.append({
                    'centroid_idx': cent['idx'],
                    'distance_km': dist_km,
                    'centroid_lat': cent['lat'],
                    'centroid_lon': cent['lon']
                })
        # Sort by distance (closest first)
        fac_centroids.sort(key=lambda x: x['distance_km'])
        facility_to_centroids[fac['idx']] = fac_centroids
    
    # Allocate supply
    total_allocated = 0.0
    facilities_with_connections = 0
    
    for fac in facility_list:
        fac_idx = fac['idx']
        remaining_capacity = facility_remaining[fac_idx]
        
        if remaining_capacity <= 0:
            continue
        
        centroids_in_range = facility_to_centroids.get(fac_idx, [])
        if not centroids_in_range:
            continue
        
        facilities_with_connections += 1
        
        for cent_info in centroids_in_range:
            if remaining_capacity <= 0:
                break
            
            cent_idx = cent_info['centroid_idx']
            demand = centroid_coords[list(c['idx'] for c in centroid_coords).index(cent_idx)]['demand']
            received = supply_tracking[cent_idx]['received']
            remaining_demand = demand - received
            
            if remaining_demand <= 0:
                continue
            
            # Allocate supply
            allocated = min(remaining_demand, remaining_capacity)
            supply_tracking[cent_idx]['received'] += allocated
            supply_tracking[cent_idx]['distances'].append(f"{cent_info['distance_km']:.2f}")
            supply_tracking[cent_idx]['types'].append(fac['facility_type'])
            supply_tracking[cent_idx]['gem_ids'].append(fac['gem_id'])
            
            facility_remaining[fac_idx] -= allocated
            facility_supplied[fac_idx] += allocated
            remaining_capacity -= allocated
            total_allocated += allocated
            
            # Track active connection
            active_connections.append({
                'centroid_idx': cent_idx,
                'facility_gem_id': fac['gem_id'],
                'centroid_lat': cent_info['centroid_lat'],
                'centroid_lon': cent_info['centroid_lon'],
                'facility_lat': fac['lat'],
                'facility_lon': fac['lon'],
                'distance_km': cent_info['distance_km'],
                'supply_mwh': allocated,
                'facility_type': fac['facility_type'],
                'path_nodes': [(fac['lon'], fac['lat']), (cent_info['centroid_lon'], cent_info['centroid_lat'])],
                'path_segments': []
            })
    
    # Update centroid columns
    for cent_idx, tracking in supply_tracking.items():
        centroids_gdf.loc[cent_idx, 'supply_received_mwh'] = tracking['received']
        centroids_gdf.loc[cent_idx, 'supplying_facility_distance'] = ', '.join(tracking['distances'])
        centroids_gdf.loc[cent_idx, 'supplying_facility_type'] = ', '.join(tracking['types'])
        centroids_gdf.loc[cent_idx, 'supplying_facility_gem_id'] = ', '.join(tracking['gem_ids'])
        
        # Update supply status
        demand = float(centroids_gdf.loc[cent_idx, demand_col]) if pd.notna(centroids_gdf.loc[cent_idx, demand_col]) else 0
        if demand <= 0:
            centroids_gdf.loc[cent_idx, 'supply_status'] = 'No Demand'
        elif tracking['received'] >= demand:
            centroids_gdf.loc[cent_idx, 'supply_status'] = 'Filled'
        elif tracking['received'] > 0:
            centroids_gdf.loc[cent_idx, 'supply_status'] = 'Partially Filled'
        else:
            centroids_gdf.loc[cent_idx, 'supply_status'] = 'Not Filled'
    
    # Update facility available_total_mwh (amount that was actually allocatable)
    for fac_idx in facilities_gdf.index:
        facilities_gdf.loc[fac_idx, 'available_total_mwh'] = facility_supplied[fac_idx]
    
    # Summary statistics
    total_demand = sum(c['demand'] for c in centroid_coords)
    total_supply = sum(fac['capacity'] for fac in facility_list)
    filled_centroids = sum(1 for idx, t in supply_tracking.items() 
                          if t['received'] >= centroid_coords[list(c['idx'] for c in centroid_coords).index(idx)]['demand'] 
                          and centroid_coords[list(c['idx'] for c in centroid_coords).index(idx)]['demand'] > 0)
    partial_centroids = sum(1 for t in supply_tracking.values() if 0 < t['received'] < sum(c['demand'] for c in centroid_coords if c['idx'] in supply_tracking))
    
    print(f"  === Allocation Summary ===")
    print(f"  Total demand: {total_demand:,.0f} MWh")
    print(f"  Total facility capacity: {total_supply:,.0f} MWh")
    print(f"  Total allocated: {total_allocated:,.0f} MWh ({100*total_allocated/total_demand:.1f}% of demand)" if total_demand > 0 else "  Total allocated: 0 MWh")
    print(f"  Facilities with connections: {facilities_with_connections}/{len(facility_list)}")
    print(f"  Active connections: {len(active_connections)}")
    
    return centroids_gdf, facilities_gdf, facility_remaining, facility_supplied, active_connections


def find_available_facilities_within_radius_kdtree(centroid_utm_coords, facility_tree, facilities_gdf, radius_km=FACILITY_SEARCH_RADIUS_KM):
    """
    Find facilities within a radius of a centroid using precomputed KDTree.
    This is much faster than reprojecting facilities for each centroid.
    
    Args:
        centroid_utm_coords: tuple (x, y) in UTM meters
        facility_tree: precomputed cKDTree of facility UTM coordinates
        facilities_gdf: original facilities GeoDataFrame (for returning subset)
        radius_km: search radius in kilometers
    
    Returns:
        List of facility indices within radius
    """
    if facilities_gdf.empty:
        return []
    
    # Query KDTree for facilities within radius (radius in meters)
    idxs = facility_tree.query_ball_point(centroid_utm_coords, r=radius_km * 1000.0)
    return idxs

def create_polyline_layer(active_connections, network_graph, country_iso3):
    """
    Creates a polyline layer showing the actual network paths for active supply connections.
    This visualizes exactly how electricity is flowing from a specific plant to a specific population center.
    
    For no-grid (Euclidean) connections, path_nodes are already in WGS84 coordinates.
    """
    all_geometries = []
    all_attributes = []
    
    if not active_connections:
        return gpd.GeoDataFrame()
    
    # Get UTM CRS from network graph if available, otherwise default to WGS84 
    # (Euclidean connections use WGS84 coordinates directly)
    if network_graph is not None:
        utm_crs = network_graph.graph.get('utm_crs', 'EPSG:3857')
        is_euclidean_mode = False
    else:
        utm_crs = COMMON_CRS  # WGS84 - Euclidean connections are already in this CRS
        is_euclidean_mode = True

    for connection in active_connections:
        centroid_idx = connection.get('centroid_idx')
        facility_gem_id = connection.get('facility_gem_id')
        path_nodes = connection.get('path_nodes', [])
        path_segments = connection.get('path_segments', [])
        
        try:
            path_geom = None
            segment_geoms = []
            for segment in path_segments:
                segment_geom = segment.get('geometry')
                if segment_geom is None:
                    from_node = segment.get('from_node')
                    to_node = segment.get('to_node')
                    if from_node is not None and to_node is not None:
                        segment_geom = LineString([from_node, to_node])
                if segment_geom is not None and not segment_geom.is_empty:
                    segment_geoms.append(segment_geom)

            if segment_geoms:
                try:
                    path_geom = linemerge(segment_geoms)
                except Exception:
                    path_geom = segment_geoms[0]

            if path_geom is None and path_nodes and len(path_nodes) >= 2:
                path_geom = LineString(path_nodes)

            if path_geom is None or path_geom.is_empty:
                continue

            # For Euclidean mode, coordinates are already in WGS84, no transformation needed
            if is_euclidean_mode:
                path_wgs84 = path_geom
            else:
                path_wgs84 = gpd.GeoSeries([path_geom], crs=utm_crs).to_crs(COMMON_CRS).iloc[0]
            all_geometries.append(path_wgs84)
            all_attributes.append({
                'centroid_idx': centroid_idx,
                'facility_gem_id': facility_gem_id,
                'facility_type': connection.get('facility_type'),
                'distance_km': connection.get('distance_km'),
                'supply_mwh': connection.get('supply_mwh'),
                'connection_id': f"C{centroid_idx}_F{facility_gem_id}",
                'active_supply': 'Yes'
            })
        except Exception as e:
            print(f"Warning: Failed to create polyline for centroid {centroid_idx} to facility {facility_gem_id}: {e}")
    
    if all_geometries:
        polylines_layer = gpd.GeoDataFrame(all_attributes, geometry=all_geometries, crs=COMMON_CRS)
        polylines_layer['GID_0'] = country_iso3
        
        # Safe column selection to avoid KeyErrors
        preferred = ['geometry', 'GID_0', 'connection_id', 'centroid_idx', 'facility_gem_id', 'facility_type', 'distance_km', 'supply_mwh', 'active_supply']
        existing = [c for c in preferred if c in polylines_layer.columns]
        others = [c for c in polylines_layer.columns if c not in existing]
        polylines_layer = polylines_layer[existing + others]
        return polylines_layer
    
    return gpd.GeoDataFrame()

def calculate_network_distance_manual(network_graph, start_node, end_node):
    """
    Calculates the shortest path distance between two nodes in the network graph using Dijkstra's algorithm.
    Results are cached to avoid redundant calculations, which provides a significant performance boost.
    """
    # Create cache key (sort nodes to ensure bidirectional caching)
    cache_key = tuple(sorted([start_node, end_node]))
    
    # Check cache first
    if cache_key in path_cache:
        return path_cache[cache_key].copy() if path_cache[cache_key] else None
    
    try:
        # Original path calculation
        path_nodes = nx.shortest_path(network_graph, start_node, end_node, weight='weight')
        
        # Calculate distances as before
        total_distance = 0
        path_segments = []
        
        for i in range(len(path_nodes) - 1):
            current_node = path_nodes[i]
            next_node = path_nodes[i + 1]
            
            # Get edge data
            edge_data = network_graph[current_node][next_node]
            segment_weight = edge_data.get('weight_cached', edge_data.get('weight', 0))
            edge_type = edge_data.get('edge_type', 'unknown')
            segment_geom = edge_data.get('geometry')
            
            total_distance += segment_weight
            path_segments.append({
                'from_node': current_node,
                'to_node': next_node,
                'weight': segment_weight,
                'edge_type': edge_type,
                'geometry': segment_geom
            })
        
        result = {
            'distance_km': total_distance / 1000.0,
            'path_nodes': path_nodes,
            'path_segments': path_segments,
            'total_segments': len(path_segments)
        }
        
        # Cache the result
        path_cache[cache_key] = result
        return result
        
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        # Cache negative result too
        path_cache[cache_key] = None
        return None

def _dijkstra_to_targets(network_graph, source_node, target_nodes, max_distance=None):
    """Run Dijkstra from a single source until all target nodes are settled."""
    if not target_nodes:
        return {}, {}

    queue = [(0.0, source_node)]
    best_dist = {source_node: 0.0}
    parents = {source_node: None}
    remaining = set(target_nodes)
    reached = {}

    while queue and remaining:
        dist, node = heapq.heappop(queue)
        if dist > best_dist.get(node, float('inf')):
            continue

        if node in remaining:
            remaining.remove(node)
            reached[node] = dist
            if not remaining:
                break

        for neighbor, edge_data in network_graph[node].items():
            weight = edge_data.get('weight', edge_data.get('weight_cached', 1.0)) or 0.0
            new_dist = dist + weight
            if max_distance is not None and new_dist > max_distance:
                continue
            if new_dist < best_dist.get(neighbor, float('inf')):
                best_dist[neighbor] = new_dist
                parents[neighbor] = node
                heapq.heappush(queue, (new_dist, neighbor))

    return reached, parents


def _reconstruct_path(node, parents):
    path = []
    current = node
    while current is not None:
        path.append(current)
        current = parents.get(current)
    return list(reversed(path))


def _build_csr_graph(network_graph):
    """Build/cache a scipy CSR adjacency for the network graph.

    Returns (csr_matrix, node_to_idx, idx_to_node).
    Edge weight uses the same fallback as the Python Dijkstra:
    edge_data['weight'] -> edge_data['weight_cached'] -> 1.0, then None -> 0.0.
    """
    cached = network_graph.graph.get('_csr_cache')
    if cached is not None:
        return cached

    from scipy.sparse import csr_matrix

    nodes = list(network_graph.nodes())
    node_to_idx = {n: i for i, n in enumerate(nodes)}
    n = len(nodes)

    # Pre-size arrays: every undirected edge contributes 2 directed entries.
    m = network_graph.number_of_edges()
    rows = np.empty(2 * m, dtype=np.int32)
    cols = np.empty(2 * m, dtype=np.int32)
    data = np.empty(2 * m, dtype=np.float64)

    k = 0
    # Match original Python Dijkstra behavior:
    #  - 0-weight edges ARE traversable in the original. scipy.csgraph treats 0
    #    as a structural zero (missing edge), so we clamp 0 -> tiny epsilon so
    #    the edge survives CSR encoding.
    #  - NaN/None weights are NOT traversed by the original (`dist + NaN = NaN`,
    #    `NaN < anything = False`). We drop them for the same effect.
    ZERO_WEIGHT_EPS = 1e-9
    for u, v, edata in network_graph.edges(data=True):
        w = edata.get('weight')
        if w is None:
            w = edata.get('weight_cached')
        try:
            w = float(w) if w is not None else None
        except (TypeError, ValueError):
            w = None
        if w is None or not np.isfinite(w):
            continue
        if w <= 0.0:
            w = ZERO_WEIGHT_EPS
        ui = node_to_idx[u]
        vi = node_to_idx[v]
        rows[k] = ui; cols[k] = vi; data[k] = w; k += 1
        rows[k] = vi; cols[k] = ui; data[k] = w; k += 1

    csr = csr_matrix((data[:k], (rows[:k], cols[:k])), shape=(n, n))
    network_graph.graph['_csr_cache'] = (csr, node_to_idx, nodes)
    return network_graph.graph['_csr_cache']


def _reconstruct_paths_for_active_connections(active_connections, network_graph):
    """Fill in path_nodes/path_segments for active supply connections.

    Distance calculation skips path tracking to save memory; this runs one
    scipy Dijkstra (with predecessors) per supplying facility and reconstructs
    paths for the centroids that actually received supply from it.
    """
    if not active_connections or network_graph is None:
        return

    from scipy.sparse.csgraph import dijkstra

    csr, node_to_idx, idx_to_node = _build_csr_graph(network_graph)

    # Group connections by facility node so we run scipy Dijkstra at most once per facility
    by_facility = defaultdict(list)
    for conn in active_connections:
        fac_node = conn.get('_facility_node')
        if fac_node is None:
            continue
        by_facility[fac_node].append(conn)

    for fac_node, conns in by_facility.items():
        fac_idx = node_to_idx.get(fac_node)
        if fac_idx is None:
            continue

        # No `limit` here either — these are confirmed-active connections; the
        # distance was already accepted upstream and we just need predecessors
        # to rebuild the same shortest path the distance pass implied.
        dist_arr, predecessors = dijkstra(
            csr, indices=fac_idx, return_predecessors=True
        )

        for conn in conns:
            cen_node = conn.get('_centroid_node')
            if cen_node is None:
                continue
            cen_idx = node_to_idx.get(cen_node)
            if cen_idx is None or not np.isfinite(dist_arr[cen_idx]):
                continue

            # Walk predecessors back to source
            path_idx = []
            cur = cen_idx
            # scipy uses -9999 as sentinel for "no predecessor"
            while cur != -9999 and cur != fac_idx:
                path_idx.append(cur)
                cur = int(predecessors[cur])
                if cur == -9999:
                    break
            if cur == fac_idx:
                path_idx.append(fac_idx)
            path_nodes = [idx_to_node[i] for i in reversed(path_idx)]

            conn['path_nodes'] = path_nodes
            conn['network_path'] = path_nodes
            conn['path_segments'] = _build_path_segments_from_nodes(path_nodes, network_graph)


def calculate_facility_distances(centroids_gdf, facilities_gdf, network_graph):
    """Compute shortest-path distances from each facility to nearby centroids.

    Uses scipy.sparse.csgraph.dijkstra (C-level, releases GIL) for the inner
    shortest-path computation. Path reconstruction is deferred to active
    supply connections only (see _reconstruct_paths_for_active_connections).
    """
    from scipy.spatial import cKDTree
    from scipy.sparse.csgraph import dijkstra as csgraph_dijkstra

    num_centroids = len(centroids_gdf)
    num_facilities = len(facilities_gdf)
    print(f"Calculating distances for {num_centroids:,} centroids to {num_facilities:,} facilities...")

    # Build node lookups
    centroid_mapping = {}
    facility_mapping = {}
    for node, data in network_graph.nodes(data=True):
        if data.get('type') == 'pop_centroid':
            centroid_mapping[data.get('centroid_idx')] = node
        elif data.get('type') == 'facility':
            facility_mapping[data.get('facility_idx')] = node

    if not centroid_mapping or not facility_mapping:
        return []

    utm_crs = network_graph.graph.get('utm_crs')
    if not utm_crs:
        center_point = centroids_gdf.geometry.union_all().centroid
        utm_crs = get_utm_crs(center_point.x, center_point.y)

    centroids_utm = project_with_cache(centroids_gdf, utm_crs)
    facilities_utm = project_with_cache(facilities_gdf, utm_crs)

    centroid_indices = list(centroids_gdf.index)
    centroid_coords = np.vstack([
        centroids_utm.geometry.x.values,
        centroids_utm.geometry.y.values
    ]).T if len(centroids_gdf) else np.empty((0, 2))
    centroid_tree = cKDTree(centroid_coords) if len(centroid_coords) else None

    centroid_idx_by_pos = {pos: idx for pos, idx in enumerate(centroid_indices)}

    search_radius_m = FACILITY_SEARCH_RADIUS_KM * 1000.0

    # Build CSR graph once (cached on network_graph)
    print("  Building CSR adjacency for scipy Dijkstra...")
    csr_build_start = time.time()
    csr, node_to_idx, idx_to_node = _build_csr_graph(network_graph)
    print(f"  CSR built in {time.time() - csr_build_start:.2f}s ({csr.shape[0]:,} nodes, {csr.nnz:,} directed entries)")

    # Precompute centroid node -> scipy index and centroid_idx (only those connected to graph)
    centroid_node_to_csridx = {}
    csridx_to_centroid_idx = {}
    for cidx, node in centroid_mapping.items():
        gi = node_to_idx.get(node)
        if gi is not None:
            centroid_node_to_csridx[node] = gi
            csridx_to_centroid_idx[gi] = cidx
    centroid_csr_indices_array = np.fromiter(csridx_to_centroid_idx.keys(), dtype=np.int64) if csridx_to_centroid_idx else np.empty(0, dtype=np.int64)
    centroid_csr_idx_list = list(csridx_to_centroid_idx.keys())
    centroid_idx_aligned = [csridx_to_centroid_idx[i] for i in centroid_csr_idx_list]

    centroid_results = defaultdict(list)

    facility_items = list(facility_mapping.items())

    def process_facility_batch(batch_facilities):
        """Run scipy single-source Dijkstra for each facility in the batch."""
        batch_results = defaultdict(list)

        for facility_idx, facility_node in batch_facilities:
            if facility_node is None:
                continue

            fac_csr_idx = node_to_idx.get(facility_node)
            if fac_csr_idx is None:
                continue

            # Spatial pre-filter: which centroids could possibly be in range?
            facility_geom_utm = facilities_utm.loc[facility_idx].geometry
            if centroid_tree is not None:
                candidate_positions = centroid_tree.query_ball_point(
                    (facility_geom_utm.x, facility_geom_utm.y), r=search_radius_m
                )
            else:
                candidate_positions = list(range(len(centroid_indices)))

            if not candidate_positions:
                continue

            candidate_centroid_idxs = [centroid_idx_by_pos[pos] for pos in candidate_positions]
            candidate_csr_idxs = [
                centroid_node_to_csridx[centroid_mapping[cidx]]
                for cidx in candidate_centroid_idxs
                if cidx in centroid_mapping and centroid_mapping[cidx] in centroid_node_to_csridx
            ]
            if not candidate_csr_idxs:
                continue

            # scipy Dijkstra (single-source, full graph). NOTE: we intentionally do NOT
            # pass `limit=search_radius_m` here — the original Python Dijkstra terminated
            # once all candidate centroids were reached but did not cap distances, and
            # network paths often exceed the Euclidean candidate radius. Capping would
            # silently drop valid (facility, centroid) pairs and change allocation results.
            dist_arr = csgraph_dijkstra(
                csr, indices=fac_csr_idx, return_predecessors=False
            )

            cand_arr = np.asarray(candidate_csr_idxs, dtype=np.int64)
            cand_dists = dist_arr[cand_arr]
            mask = np.isfinite(cand_dists)
            if not mask.any():
                continue

            for cidx_csr, dist_m in zip(cand_arr[mask], cand_dists[mask]):
                centroid_idx = csridx_to_centroid_idx.get(int(cidx_csr))
                if centroid_idx is None:
                    continue
                batch_results[centroid_idx].append({
                    'facility_idx': facility_idx,
                    'distance_km': float(dist_m) / 1000.0,
                    'path_nodes': [],
                    'path_segments': [],
                })

        return batch_results

    if len(facility_items) > 20 and num_centroids > 1000:
        n_workers = min(MAX_WORKERS, len(facility_items))
        print(f"  Using parallel processing for {len(facility_items)} facilities with {n_workers} workers...")
        batch_size = max(1, len(facility_items) // n_workers)
        facility_batches = [facility_items[i:i+batch_size] for i in range(0, len(facility_items), batch_size)]

        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(facility_batches))) as executor:
            futures = [executor.submit(process_facility_batch, batch) for batch in facility_batches]
            for i, future in enumerate(futures):
                batch_result = future.result()
                for centroid_idx, distances in batch_result.items():
                    centroid_results[centroid_idx].extend(distances)

                if (i + 1) % max(1, len(futures) // 10) == 0 or (i + 1) == len(futures):
                    facilities_processed = min((i + 1) * batch_size, len(facility_items))
                    print(f"    Processed {facilities_processed}/{len(facility_items)} facilities...")
    else:
        # Serial processing for small datasets (avoid threading overhead)
        batch_result = process_facility_batch(facility_items)
        for centroid_idx, distances in batch_result.items():
            centroid_results[centroid_idx].extend(distances)

    results = []
    for centroid_idx in centroids_gdf.index:
        distances = centroid_results.get(centroid_idx, [])
        distances.sort(key=lambda x: x['distance_km'])
        results.append({'centroid_idx': centroid_idx, 'distances': distances})

    print(f"Distance calculation completed for {len(results)} centroids")
    return results

def create_grid_lines_layer(grid_lines_gdf, network_graph, active_connections):
    """Creates the final grid lines layer, including original infrastructure and newly created connection lines (e.g., facility-to-grid, stitches)."""
    all_geometries = []
    all_attributes = []
    
    # Add original grid segments - preserve existing attributes (including siting networks)
    if not grid_lines_gdf.empty:
        has_line_type = 'line_type' in grid_lines_gdf.columns
        has_line_id = 'line_id' in grid_lines_gdf.columns
        has_distance = 'distance_km' in grid_lines_gdf.columns
        has_gid0 = 'GID_0' in grid_lines_gdf.columns

        for idx, row in grid_lines_gdf.iterrows():
            geom = row.geometry
            
            # Preserve existing attributes if they exist, otherwise use defaults
            attrs = {}
            if has_line_type:
                attrs['line_type'] = row['line_type']
            else:
                attrs['line_type'] = 'grid_infrastructure'
            
            if has_line_id:
                attrs['line_id'] = row['line_id']
            else:
                attrs['line_id'] = f'grid_{idx}'
            
            if has_distance and pd.notna(row['distance_km']):
                attrs['distance_km'] = row['distance_km']
            else:
                # Calculate distance if not provided
                center = geom.centroid
                utm = get_utm_crs(center.x, center.y)
                try:
                    attrs['distance_km'] = gpd.GeoSeries([geom], crs=COMMON_CRS).to_crs(utm).iloc[0].length / 1000.0
                except Exception:
                    attrs['distance_km'] = None
            
            # Preserve GID_0 if it exists
            if has_gid0:
                attrs['GID_0'] = row['GID_0']
            
            all_geometries.append(geom)
            all_attributes.append(attrs)
    
    # Add connection edges from graph
    if network_graph is not None:
        utm_crs = network_graph.graph.get('utm_crs', 'EPSG:3857')
        for _n1, _n2, ed in network_graph.edges(data=True):
            et = ed.get('edge_type', 'unknown')
            if et in ['centroid_to_grid', 'grid_to_facility', 'component_stitch']:
                geom_utm = ed.get('geometry')
                if geom_utm is None or geom_utm.is_empty:
                    continue
                try:
                    geom = gpd.GeoSeries([geom_utm], crs=utm_crs).to_crs(COMMON_CRS).iloc[0]
                except Exception:
                    continue
                dist_km = (ed.get('weight', geom_utm.length) or 0) / 1000.0
                all_geometries.append(geom)
                all_attributes.append({'line_type': et, 'line_id': f'{et}_{len(all_attributes)}', 'distance_km': dist_km})
    
    if all_geometries:
        return gpd.GeoDataFrame(all_attributes, geometry=all_geometries, crs=COMMON_CRS)
    return gpd.GeoDataFrame()

def create_all_layers(centroids_gdf, facilities_gdf, grid_lines_gdf, network_graph, active_connections, country_iso3, facility_supplied=None, facility_remaining=None):
    """Assembles all the final GeoDataFrame layers for output."""
    layers = {}
    
    # Centroids layer - population centers with demand and supply allocation results
    # Use ANALYSIS_YEAR for population and demand columns
    centroid_columns = ['geometry', 'GID_0', 'Population_centroid',
                        f'Population_{ANALYSIS_YEAR}_centroid',
                        f'Total_Demand_{ANALYSIS_YEAR}_centroid',
                        'supplying_facility_distance', 'supplying_facility_type', 'supplying_facility_gem_id', 
                        'supply_received_mwh', 'supply_status']
    available_columns = [c for c in centroid_columns if c in centroids_gdf.columns]
    centroids_layer = centroids_gdf[available_columns].copy()
    
    # Add centroid_idx column for spatial analysis tracking
    centroids_layer['centroid_idx'] = centroids_layer.index
    
    layers['centroids'] = centroids_layer
    
    # Grid lines layer - electrical grid infrastructure and connections
    grid_lines_layer = create_grid_lines_layer(grid_lines_gdf, network_graph, active_connections)
    if not grid_lines_layer.empty:
        grid_lines_layer['GID_0'] = country_iso3
        cols = ['geometry', 'GID_0'] + [c for c in grid_lines_layer.columns if c not in ['geometry', 'GID_0']]
        layers['grid_lines'] = grid_lines_layer[cols]
    
    # Facilities layer - energy generation facilities with capacity, production, and allocation data
    if not facilities_gdf.empty:
        # Select base columns, including available_total_mwh if it exists
        base_columns = ['geometry', 'GEM unit/phase ID', 'Grouped_Type', 'Latitude', 'Longitude', 'Adjusted_Capacity_MW', 'total_mwh']
        if 'available_total_mwh' in facilities_gdf.columns:
            base_columns.append('available_total_mwh')
        
        facilities_simplified = facilities_gdf[base_columns].copy()
        facilities_simplified['GID_0'] = country_iso3
        
        # total_mwh is now directly available from the facility data
        # No need to calculate using capacity factors
        
        # Add supply allocation tracking columns
        if facility_supplied is not None and facility_remaining is not None:
            facilities_simplified['supplied_mwh'] = facilities_simplified.index.map(lambda idx: facility_supplied.get(idx, 0.0))
            facilities_simplified['remaining_mwh'] = facilities_simplified.index.map(lambda idx: facility_remaining.get(idx, 0.0))
        else:
            # Default values if no allocation data available
            facilities_simplified['supplied_mwh'] = 0.0
            facilities_simplified['remaining_mwh'] = facilities_simplified.get('available_total_mwh', facilities_simplified['total_mwh'])
        
        cols = ['geometry', 'GID_0'] + [c for c in facilities_simplified.columns if c not in ['geometry', 'GID_0']]
        layers['facilities'] = facilities_simplified[cols]
    
    # Polylines layer - active supply connections between centroids and facilities
    polylines_layer = create_polyline_layer(active_connections, network_graph, country_iso3)
    if not polylines_layer.empty:
        layers['polylines'] = polylines_layer
    
    return layers

def create_summary_statistics(country_iso3, centroids_gdf, facilities_gdf, facility_supplied, facility_remaining, demand_col, total_available_supply, original_total_capacity=None, demand_by_type_no_facilities=None):
    """Create summary statistics DataFrame for Excel export."""
    
    # Configuration parameters
    config_params = [
        ('Country', country_iso3),
        ('Analysis_Year', ANALYSIS_YEAR),
        ('Common_CRS', COMMON_CRS),
        ('Demand_Types', ', '.join(DEMAND_TYPES)),
        ('Population_Aggregation_Factor', f'{POP_AGGREGATION_FACTOR}x (native 30" → {POP_AGGREGATION_FACTOR*30}")'),
        ('Grid_Stitch_Distance_km', GRID_STITCH_DISTANCE_KM),
        ('Node_Snap_Tolerance_m', NODE_SNAP_TOLERANCE_M),
        ('Max_Connection_Distance_km', MAX_CONNECTION_DISTANCE_M / 1000),
        ('Facility_Search_Radius_km', FACILITY_SEARCH_RADIUS_KM),
        ('Supply_Factor_Pct', SUPPLY_FACTOR * 100)
    ]
    
    # Calculate demand and supply statistics
    centroid_demand_mwh = centroids_gdf[demand_col].sum()
    # Sum of adjusted facility capacity (available_total_mwh = total_mwh * SUPPLY_FACTOR)
    total_available_supply_mwh = total_available_supply
    # Sum of actually supplied to centroids
    total_supplied_mwh = sum(facility_supplied.values()) if facility_supplied else 0
    total_unsupplied_mwh = sum(facility_remaining.values()) if facility_remaining else 0
    demand_coverage_pct = (total_supplied_mwh / centroid_demand_mwh * 100) if centroid_demand_mwh > 0 else 0
    additional_needed_mwh = max(0, centroid_demand_mwh - total_supplied_mwh)
    
    # Calculate centroid status counts
    status_counts = centroids_gdf['supply_status'].value_counts()
    total_centroids = len(centroids_gdf)
    centroids_filled = status_counts.get('Filled', 0)
    centroids_partially = status_counts.get('Partially Filled', 0)
    centroids_not_filled = status_counts.get('Not Filled', 0)
    centroids_no_demand = status_counts.get('No Demand', 0)
    
    # Demand and supply summary
    # Calculate target supply based on SUPPLY_FACTOR
    target_available_supply = centroid_demand_mwh * SUPPLY_FACTOR
    gap_vs_target = total_available_supply_mwh - target_available_supply
    gap_pct = (gap_vs_target / target_available_supply * 100) if target_available_supply > 0 else 0
    
    demand_supply_params = [
        ('Country', country_iso3),
        ('Analysis_Year', ANALYSIS_YEAR),
        ('', ''),  # Blank row for readability
        ('--- DEMAND ---', ''),
        ('Centroid_Demand_MWh', centroid_demand_mwh),
        ('', ''),  # Blank row
        ('--- SUPPLY FACTOR ---', ''),
        ('Supply_Factor_Pct', SUPPLY_FACTOR * 100),
        ('Target_Available_Supply_MWh', target_available_supply),
        ('  (Calculation)', f'Demand * Factor = {centroid_demand_mwh:,.0f} * {SUPPLY_FACTOR} = {target_available_supply:,.0f}'),
        ('', ''),  # Blank row
        ('--- ACTUAL SUPPLY ---', ''),
        ('Original_Facility_Capacity_MWh', original_total_capacity if original_total_capacity is not None else 0),
        ('Available_Supply_MWh (adjusted by factor)', total_available_supply_mwh),
        ('Gap_vs_Target_MWh', gap_vs_target),
        ('Gap_vs_Target_Pct', gap_pct),
        ('', ''),  # Blank row
        ('--- SUPPLY ALLOCATION ---', ''),
        ('Actually_Supplied_MWh', total_supplied_mwh),
        ('Unsupplied_MWh', total_unsupplied_mwh),
        ('Demand_Coverage_Pct', demand_coverage_pct),
        ('Additional_Needed_MWh', additional_needed_mwh),
        ('', ''),  # Blank row
        ('--- CENTROID STATUS ---', ''),
        ('Centroids_Filled', centroids_filled),
        ('Centroids_Partially_Filled', centroids_partially),
        ('Centroids_Not_Filled', centroids_not_filled),
        ('Centroids_No_Demand', centroids_no_demand),
        ('Total_Centroids', total_centroids),
        ('Pct_Filled', (centroids_filled / total_centroids * 100) if total_centroids > 0 else 0),
        ('Pct_Partially_Filled', (centroids_partially / total_centroids * 100) if total_centroids > 0 else 0),
        ('Pct_Not_Filled', (centroids_not_filled / total_centroids * 100) if total_centroids > 0 else 0),
        ('Pct_No_Demand', (centroids_no_demand / total_centroids * 100) if total_centroids > 0 else 0),
        ('', ''),  # Blank row
        ('--- NOTE ---', ''),
        ('After siting and re-running supply:', ''),
        ('Expected Available_Supply_MWh', target_available_supply),
        ('Current gap to close (MWh)', abs(gap_vs_target)),
        ('Current gap to close (Pct)', abs(gap_pct))
    ]
    
    # Calculate by energy type
    energy_type_params = [('', ''), ('--- BREAKDOWN BY ENERGY TYPE ---', '')]
    if not facilities_gdf.empty and facility_supplied:
        for energy_type in DEMAND_TYPES:
            # Get demand for this energy type from centroids
            type_demand_col = f"{energy_type}_{ANALYSIS_YEAR}_centroid"
            if type_demand_col in centroids_gdf.columns:
                type_demand = centroids_gdf[type_demand_col].sum()
            else:
                type_demand = 0
            
            facilities_of_type = facilities_gdf[facilities_gdf['Grouped_Type'] == energy_type]
            # Total_MWh_{type} = sum of available_total_mwh (adjusted by factor)
            if 'available_total_mwh' in facilities_of_type.columns:
                total_mwh = float(facilities_of_type['available_total_mwh'].fillna(0).sum())
            else:
                # Fallback if column doesn't exist yet
                total_mwh = float(facilities_of_type['total_mwh'].fillna(0).sum() * SUPPLY_FACTOR)
            # Supplied_MWh_{type} = sum of actually supplied (from network analysis)
            supplied_mwh = sum(facility_supplied.get(idx, 0) for idx in facilities_of_type.index)
            
            # Calculate target for this energy type
            target_type_supply = type_demand * SUPPLY_FACTOR
            gap_type = total_mwh - target_type_supply
            
            # Add demand and supply info with targets
            energy_type_params.append(('', ''))  # Blank row
            energy_type_params.append((f'--- {energy_type} ---', ''))
            energy_type_params.append((f'Demand_MWh_{energy_type}', type_demand))
            energy_type_params.append((f'Target_Supply_MWh_{energy_type}', target_type_supply))
            energy_type_params.append((f'  (Calculation)', f'Demand * Factor = {type_demand:,.0f} * {SUPPLY_FACTOR} = {target_type_supply:,.0f}'))
            energy_type_params.append((f'Available_MWh_{energy_type}', total_mwh))
            energy_type_params.append((f'Gap_vs_Target_{energy_type}', gap_type))
            energy_type_params.append((f'Supplied_MWh_{energy_type}', supplied_mwh))
            
            # Flag if this type has zero facilities but non-zero demand
            if demand_by_type_no_facilities and energy_type in demand_by_type_no_facilities:
                energy_type_params.append((f'WARNING_NO_FACILITIES_{energy_type}', demand_by_type_no_facilities[energy_type]))
                energy_type_params.append((f'  Action required', 'Run process_country_siting.py'))
    
    # Combine all parameters
    all_params = config_params + demand_supply_params + energy_type_params
    
    # Create DataFrame
    summary_df = pd.DataFrame(all_params, columns=['Parameter', 'Value'])
    
    return summary_df

def process_country_supply(country_iso3, output_dir="outputs_per_country", test_mode=False):
    """Main function to process supply analysis for a single country, orchestrating all steps."""
    # Clear path cache for each country
    global path_cache, _CONFIG_PRINTED
    path_cache = {}
    _CONFIG_PRINTED = False  # Reset to allow banner to print for each scenario
    
    scenario_suffix = f"{ANALYSIS_YEAR}_supply_{int(SUPPLY_FACTOR*100)}%"
    
    # Check if siting data exists (determines workflow approach)
    # Auto-detected from existing siting data file
    scenario = f"{ANALYSIS_YEAR}_supply_{int(SUPPLY_FACTOR*100)}%"
    siting_summary_path = Path(f"outputs_per_country/parquet/{scenario}/{ANALYSIS_YEAR}_siting_{int(SUPPLY_FACTOR*100)}%_{country_iso3}.xlsx")
    has_siting_data = siting_summary_path.exists()
    
    if has_siting_data:
        print("=" * 60)
        print("SITING DATA DETECTED - USING ADD_V2 WORKFLOW")
        print("=" * 60)
        print("Will load existing facilities and grid, then append siting clusters and networks")
        print()
    
    print_configuration_banner(test_mode, scenario_suffix)
    
    print(f"\nProcessing {country_iso3}... (Mode: {'TEST' if test_mode else 'PRODUCTION'})")
    total_start = time.time()
    
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)
    
    try:
        # Load inputs with timing
        with timer("Load admin boundaries"):
            admin_boundaries = load_admin_boundaries(country_iso3)
            country_bbox = get_country_bbox(admin_boundaries)
        
        with timer("Load population centroids"):
            centroids_gdf = load_population_centroids(country_bbox, admin_boundaries)
        
        with timer("Load facilities"):
            facilities_gdf = load_energy_facilities(country_iso3, ANALYSIS_YEAR, scenario=scenario)
        
        with timer("Load grid lines"):
            grid_lines_gdf = load_grid_lines(country_bbox, admin_boundaries, scenario=scenario, country_iso3=country_iso3)
        
        print(f"Loaded: {len(centroids_gdf)} centroids, {len(facilities_gdf)} facilities, {len(grid_lines_gdf)} grid lines")
        
        # Country tag
        centroids_gdf['GID_0'] = country_iso3
        
        # Demand projections
        with timer("Load population and demand projections"):
            centroids_gdf = load_population_and_demand_projections(centroids_gdf, country_iso3)
        
        # Default outputs
        network_graph = None
        active_connections = []
        facility_supplied = None
        facility_remaining = None
        
        # Process network and allocate supply if facilities and grid exist
        if not facilities_gdf.empty and not grid_lines_gdf.empty:
            # Create network graph
            with timer("Create network graph"):
                network_graph = create_network_graph(facilities_gdf, grid_lines_gdf, centroids_gdf)
            
            # Calculate distances between centroids and facilities
            with timer("Calculate facility distances"):
                centroid_facility_distances = calculate_facility_distances(centroids_gdf, facilities_gdf, network_graph)
            
            # Initialize facility remaining capacities (MWh/year) and track supplied amounts
            # SUPPLY_FACTOR determines what % of demand is available as supply
            # Each facility's total_mwh is based on its proportional share of ENERGY-TYPE-SPECIFIC demand
            demand_col = f'Total_Demand_{ANALYSIS_YEAR}_centroid'
            total_demand = centroids_gdf[demand_col].sum()
            
            # Preserve original facility capacity before reallocation
            facilities_gdf['original_total_mwh'] = facilities_gdf['total_mwh']
            original_total_capacity = facilities_gdf['total_mwh'].sum()
            
            # Calculate type-specific allocations using dictionaries to preserve index order
            facility_remaining = {}
            facility_supplied = {}
            new_total_mwh_dict = {}
            new_available_total_mwh_dict = {}
            
            # Track demand for energy types with zero facilities (to be handled by siting)
            demand_by_type_no_facilities = {}
            
            for energy_type in DEMAND_TYPES:
                # Get demand for this energy type
                type_demand_col = f"{energy_type}_{ANALYSIS_YEAR}_centroid"
                if type_demand_col in centroids_gdf.columns:
                    type_demand = centroids_gdf[type_demand_col].sum()
                else:
                    type_demand = 0
                
                # Get facilities of this type
                facilities_of_type = facilities_gdf[facilities_gdf['Grouped_Type'] == energy_type]
                
                if len(facilities_of_type) == 0:
                    # No facilities for this type - demand should be addressed by siting
                    if type_demand > 0:
                        demand_by_type_no_facilities[energy_type] = type_demand
                        print(f"  WARNING: {energy_type} has demand of {type_demand:,.0f} MWh but ZERO existing facilities")
                        print(f"           This gap should be addressed by process_country_siting.py")
                    continue
                
                if type_demand == 0:
                    # No demand for this type
                    for idx in facilities_of_type.index:
                        new_total_mwh_dict[idx] = 0
                        new_available_total_mwh_dict[idx] = 0
                        facility_remaining[idx] = 0
                        facility_supplied[idx] = 0.0
                    continue
                
                # Calculate total original capacity for facilities of this type
                type_facility_capacity = float(facilities_of_type['total_mwh'].fillna(0).sum())
                
                # Allocate type-specific demand proportionally across facilities of this type
                for idx, original_facility_mwh in zip(facilities_of_type.index, facilities_of_type['total_mwh'].fillna(0).to_numpy()):
                    
                    if type_facility_capacity > 0:
                        facility_share = original_facility_mwh / type_facility_capacity
                        new_total_mwh = type_demand * facility_share
                    else:
                        new_total_mwh = 0
                    
                    # available_total_mwh = total_mwh adjusted by SUPPLY_FACTOR
                    available_total_mwh = new_total_mwh * SUPPLY_FACTOR
                    
                    new_total_mwh_dict[idx] = new_total_mwh
                    new_available_total_mwh_dict[idx] = available_total_mwh
                    facility_remaining[idx] = available_total_mwh
                    facility_supplied[idx] = 0.0
            
            # Replace total_mwh with energy-type-specific demand-based allocation (preserving index order)
            facilities_gdf['total_mwh'] = facilities_gdf.index.map(lambda idx: new_total_mwh_dict.get(idx, 0))
            # Add available_total_mwh as adjusted by SUPPLY_FACTOR
            facilities_gdf['available_total_mwh'] = facilities_gdf.index.map(lambda idx: new_available_total_mwh_dict.get(idx, 0))
            
            total_available_supply = sum(new_available_total_mwh_dict.values())
            
            # Calculate total demand that has no facilities (should be handled by siting)
            total_demand_no_facilities = sum(demand_by_type_no_facilities.values())
            
            if total_demand_no_facilities > 0:
                print(f"\n{'='*60}")
                print(f"SITING REQUIREMENT SUMMARY")
                print(f"{'='*60}")
                print(f"Total demand with zero existing facilities: {total_demand_no_facilities:,.0f} MWh")
                print(f"This represents {(total_demand_no_facilities / total_demand * 100):.1f}% of total demand")
                print(f"Breakdown by energy type:")
                for energy_type, demand_val in demand_by_type_no_facilities.items():
                    print(f"  {energy_type}: {demand_val:,.0f} MWh")
                print(f"{'='*60}\n")
            
            if SUPPLY_FACTOR < 1.0:
                print(f"Sensitivity analysis: Available supply = {SUPPLY_FACTOR*100:.0f}% of demand = {total_available_supply:,.0f} MWh")
                print(f"  Distributed proportionally across {len(facilities_gdf)} facilities by capacity share")
            
            # Prepare centroid columns for supply allocation
            centroids_gdf['supplying_facility_distance'] = ''
            centroids_gdf['supplying_facility_type'] = ''
            centroids_gdf['supplying_facility_gem_id'] = ''
            centroids_gdf['supply_received_mwh'] = 0.0
            centroids_gdf['supply_status'] = 'Not Filled'
            
            demand_col = f'Total_Demand_{ANALYSIS_YEAR}_centroid'
            
            # VECTORIZED SUPPLY ALLOCATION
            with timer("Allocate supply (Vectorized)"):
                centroids_gdf, active_connections = allocate_supply_vectorized(
                    centroids_gdf, facilities_gdf, centroid_facility_distances,
                    facility_remaining, facility_supplied, demand_col, network_graph
                )
            
            # Update supply status for all centroids (vectorized)
            demand_arr = centroids_gdf[demand_col].fillna(0).to_numpy(dtype=np.float64)
            received_arr = centroids_gdf['supply_received_mwh'].fillna(0).to_numpy(dtype=np.float64)
            status_arr = np.where(
                demand_arr <= 0, 'No Demand',
                np.where(
                    received_arr >= demand_arr, 'Filled',
                    np.where(received_arr > 0, 'Partially Filled', 'Not Filled')
                )
            )
            centroids_gdf['supply_status'] = status_arr
        else:
            # No grid infrastructure - use Euclidean allocation if we have facilities
            # Initialize variables needed for summary statistics
            demand_col = f'Total_Demand_{ANALYSIS_YEAR}_centroid'
            total_demand = centroids_gdf[demand_col].sum() if demand_col in centroids_gdf.columns else 0
            total_available_supply = total_demand * SUPPLY_FACTOR
            original_total_capacity = facilities_gdf['total_mwh'].sum() if not facilities_gdf.empty and 'total_mwh' in facilities_gdf.columns else 0
            demand_by_type_no_facilities = {}
            
            if not facilities_gdf.empty:
                # We have facilities but no grid - use Euclidean allocation
                print(f"\n{'='*60}")
                print(f"  NO GRID DATA - Using Euclidean allocation")
                print(f"  {len(facilities_gdf)} facilities will supply centroids within {NO_GRID_CONNECTION_RADIUS_KM} km radius")
                print(f"{'='*60}")
                
                # Apply supply factor to facility capacities if needed
                if SUPPLY_FACTOR < 1.0:
                    facilities_gdf['total_mwh'] = facilities_gdf['total_mwh'] * SUPPLY_FACTOR
                    print(f"  Sensitivity analysis: Facility capacities scaled to {SUPPLY_FACTOR*100:.0f}%")
                
                # Run Euclidean allocation
                centroids_gdf, facilities_gdf, facility_remaining, facility_supplied, active_connections = \
                    allocate_supply_no_grid(
                        facilities_gdf, 
                        centroids_gdf, 
                        demand_col, 
                        radius_km=NO_GRID_CONNECTION_RADIUS_KM
                    )
                
                # Track demand by type for summary
                for energy_type in DEMAND_TYPES:
                    type_demand_col = f"{energy_type}_{ANALYSIS_YEAR}_centroid"
                    if type_demand_col in centroids_gdf.columns:
                        type_demand = centroids_gdf[type_demand_col].sum()
                        if type_demand > 0:
                            demand_by_type_no_facilities[energy_type] = type_demand
            else:
                # No facilities and no grid - just initialize empty columns
                centroids_gdf['supplying_facility_distance'] = np.nan
                centroids_gdf['supplying_facility_type'] = ''
                centroids_gdf['supplying_facility_gem_id'] = ''
                centroids_gdf['supply_received_mwh'] = 0.0
                centroids_gdf['supply_status'] = 'Not Filled'
                active_connections = []
                facility_remaining = {}
                facility_supplied = {}
                
                print(f"\n  WARNING: No facilities and no grid data available")
                print(f"  Total demand: {total_demand:,.0f} MWh cannot be distributed")
        
        # Create all layers
        with timer("Create output layers"):
            layers = create_all_layers(centroids_gdf, facilities_gdf, grid_lines_gdf, network_graph, active_connections, country_iso3, facility_supplied, facility_remaining)
        
        # Define demand column for summary statistics
        demand_col_year = f'Total_Demand_{ANALYSIS_YEAR}_centroid'
        
        # Write outputs based on mode
        with timer("Save outputs"):
            scenario_suffix = f"{ANALYSIS_YEAR}_supply_{int(SUPPLY_FACTOR*100)}%"
            
            if test_mode:
                # Test mode: Full GPKG output with scenario suffix in filename
                # Add _add_v2 suffix if siting data was merged
                file_suffix = "_add_v2" if has_siting_data else ""
                output_file = output_path / f"{scenario_suffix}_{country_iso3}{file_suffix}.gpkg"
                
                for layer_name, layer_data in layers.items():
                    layer_data.to_file(output_file, layer=layer_name, driver="GPKG")
                
                print(f"Test mode: Full GPKG saved to {output_file}")
                
                # Also save Excel summary for test mode (same directory as GPKG)
                excel_file = output_path / f"{scenario_suffix}_{country_iso3}{file_suffix}.xlsx"
                
                # Create summary statistics DataFrame
                summary_data = create_summary_statistics(
                    country_iso3, centroids_gdf, facilities_gdf, 
                    facility_supplied, facility_remaining, demand_col_year, total_available_supply,
                    original_total_capacity=original_total_capacity,
                    demand_by_type_no_facilities=demand_by_type_no_facilities
                )
                
                with pd.ExcelWriter(excel_file, engine='openpyxl') as writer:
                    summary_data.to_excel(writer, sheet_name='Summary', index=False)
                    # Auto-adjust column widths
                    worksheet = writer.sheets['Summary']
                    worksheet.column_dimensions['A'].width = 35
                    worksheet.column_dimensions['B'].width = 25
                
                print(f"Test mode: Excel summary saved to {excel_file}")
                output_result = str(output_file)
            else:
                # Production mode: Full Parquet files for global analysis (all columns retained)
                # Use _add_v2 suffix if siting data was merged
                if has_siting_data:
                    parquet_dir = output_path / "parquet" / f"{scenario_suffix}_add_v2"
                    file_suffix = "_add_v2"
                else:
                    parquet_dir = output_path / "parquet" / scenario_suffix
                    file_suffix = ""
                
                parquet_dir.mkdir(parents=True, exist_ok=True)
                
                output_files = []
                for layer_name, layer_data in layers.items():
                    if not layer_data.empty:
                        parquet_file = parquet_dir / f"{layer_name}_{country_iso3}{file_suffix}.parquet"
                        layer_data.to_parquet(parquet_file, compression='snappy')
                        output_files.append(str(parquet_file))
                        print(f"  Saved {layer_name}: {len(layer_data)} records, {len(layer_data.columns)} columns → {parquet_file.name}")
                
                # Also save Excel summary for production mode (same directory as parquet files)
                excel_file = parquet_dir / f"{scenario_suffix}_{country_iso3}{file_suffix}.xlsx"
                
                # Create summary statistics DataFrame
                summary_data = create_summary_statistics(
                    country_iso3, centroids_gdf, facilities_gdf,
                    facility_supplied, facility_remaining, demand_col_year, total_available_supply,
                    original_total_capacity=original_total_capacity,
                    demand_by_type_no_facilities=demand_by_type_no_facilities
                )
                
                with pd.ExcelWriter(excel_file, engine='openpyxl') as writer:
                    summary_data.to_excel(writer, sheet_name='Summary', index=False)
                    # Auto-adjust column widths
                    worksheet = writer.sheets['Summary']
                    worksheet.column_dimensions['A'].width = 35
                    worksheet.column_dimensions['B'].width = 25
                
                print(f"Production mode: {len(output_files)} Parquet files saved to {parquet_dir}")
                print(f"Production mode: Excel summary saved to {excel_file}")
                output_result = str(parquet_dir)
        
        # Generate summary statistics for ANALYSIS_YEAR
        print(f"\n{'='*60}")
        print(f"SUPPLY ANALYSIS SUMMARY FOR {country_iso3} ({ANALYSIS_YEAR})")
        if SUPPLY_FACTOR < 1.0:
            print(f"*** SENSITIVITY ANALYSIS: {SUPPLY_FACTOR*100:.0f}% supply available ***")
        print(f"{'='*60}")
        
        # Calculate demand statistics
        demand_col_year = f'Total_Demand_{ANALYSIS_YEAR}_centroid'
        total_demand_mwh = centroids_gdf[demand_col_year].sum()
        total_available_supply_mwh = total_demand_mwh * SUPPLY_FACTOR
        
        print(f"Total demand (100%): {total_demand_mwh:,.0f} MWh")
        print(f"Available supply ({SUPPLY_FACTOR*100:.0f}% of demand): {total_available_supply_mwh:,.0f} MWh")
        
        # Calculate supply statistics from facilities
        if facility_supplied is not None and facility_remaining is not None:
            total_supplied_mwh = sum(facility_supplied.values())
            total_remaining_mwh = sum(facility_remaining.values())
            
            # % of available supply that was actually distributed
            supplied_pct_of_available = (total_supplied_mwh / total_available_supply_mwh * 100) if total_available_supply_mwh > 0 else 0
            
            # Demand coverage = actually supplied / total demand
            demand_coverage_pct = (total_supplied_mwh / total_demand_mwh * 100) if total_demand_mwh > 0 else 0
            
            print(f"Actually supplied: {total_supplied_mwh:,.0f} MWh ({supplied_pct_of_available:.1f}% of available supply)")
            print(f"Unsupplied (from available): {total_remaining_mwh:,.0f} MWh")
            print(f"Demand coverage: {demand_coverage_pct:.1f}%")
            
            # Additional needed to meet 100% demand
            total_additionally_needed_mwh = max(0, total_demand_mwh - total_supplied_mwh)
            print(f"Additional needed for 100% demand: {total_additionally_needed_mwh:,.0f} MWh")
        else:
            print("Total supplied energy: 0 MWh (no facilities processed)")
            print("Total remaining capacity: 0 MWh (no facilities processed)")
            print(f"Total additionally needed: {total_demand_mwh:,.0f} MWh")
        
        # Calculate centroid status statistics
        status_counts = centroids_gdf['supply_status'].value_counts()
        total_centroids = len(centroids_gdf)
        no_demand_centroids = status_counts.get('No Demand', 0)
        demand_centroids = total_centroids - no_demand_centroids

        print(f"\nCentroid supply status:")
        for status in ['Filled', 'Partially Filled', 'Not Filled', 'No Demand']:
            count = status_counts.get(status, 0)
            # For coverage, use demand-bearing centroids; report No Demand against total
            denom = demand_centroids if status != 'No Demand' else total_centroids
            pct = (count / denom * 100) if denom > 0 else 0
            print(f"  {status}: {count:,} centroids ({pct:.1f}%)")

        if demand_centroids > 0:
            filled_like = status_counts.get('Filled', 0) + status_counts.get('Partially Filled', 0)
            coverage_demand_only = filled_like / demand_centroids * 100
            print(f"  Coverage (demand>0 only): {coverage_demand_only:.1f}% of {demand_centroids:,} centroids")
        
        print(f"{'='*60}")
        
        # Print total execution time
        total_elapsed = time.time() - total_start
        minutes = int(total_elapsed // 60)
        seconds = total_elapsed % 60
        print(f"\n*** TOTAL EXECUTION TIME: {minutes}m {seconds:.1f}s ({total_elapsed:.1f}s) ***")
        
        print(f"Results saved to {output_result}")
        print(f"Processing completed using {MAX_WORKERS} parallel workers")
        return output_result
    except Exception as e:
        total_elapsed = time.time() - total_start
        print(f"Error processing {country_iso3} after {total_elapsed:.1f}s: {e}")
        traceback.print_exc()
        return None


def _build_path_segments_from_nodes(path_nodes, network_graph):
    if network_graph is None or not path_nodes or len(path_nodes) < 2:
        return []

    segments = []
    for i in range(len(path_nodes) - 1):
        start = path_nodes[i]
        end = path_nodes[i + 1]
        edge_data = network_graph.get_edge_data(start, end) if network_graph.has_node(start) and network_graph.has_node(end) else None
        edge_type = 'unknown'
        geometry = None

        if edge_data:
            edge_type = edge_data.get('edge_type', 'unknown')
            geometry = edge_data.get('geometry')

        if geometry is None or geometry.is_empty:
            geometry = LineString([start, end])

        segments.append({
            'from_node': start,
            'to_node': end,
            'edge_type': edge_type,
            'geometry': geometry
        })

    return segments


def allocate_supply_vectorized(centroids_gdf, facilities_gdf, centroid_facility_distances, 
                               facility_remaining, facility_supplied, demand_col, network_graph=None):
    """
    Core supply allocation logic. This function matches supply (facilities) to demand (centroids).
    The logic is as follows:
    1. Iterate through facilities, starting with the one with the largest capacity.
    2. For the current facility, find all population centroids that need power and are connected to it.
    3. Sort these centroids by the network distance to the facility (closest first).
    4. Allocate the facility's power to these centroids one by one until the facility's capacity is used up.
    5. Repeat for the next largest facility.
    This version is optimized for performance using array operations where possible.
    """
    import numpy as np
    
    # Reset index to ensure we have sequential indices for array operations
    centroids_gdf_reset = centroids_gdf.reset_index(drop=True)
    
    # Create index mapping from original to reset indices
    original_to_reset = {orig_idx: reset_idx for reset_idx, orig_idx in enumerate(centroids_gdf.index)}
    
    # Pre-compute demand (full 100% demand) and initialize received arrays
    num_centroids = len(centroids_gdf_reset)
    centroid_demands = centroids_gdf_reset[demand_col].fillna(0).values.astype(np.float64)
    centroid_received = np.zeros(num_centroids, dtype=np.float64)
    
    # Initialize tracking lists for facility info (for comma-separated strings)
    supplying_distances = [[] for _ in range(num_centroids)]
    supplying_types = [[] for _ in range(num_centroids)]
    supplying_gem_ids = [[] for _ in range(num_centroids)]
    active_connections = []
    
    # Create facility list sorted by capacity (preserving original logic)
    facility_capacities = []
    gem_col = 'GEM unit/phase ID' if 'GEM unit/phase ID' in facilities_gdf.columns else None
    type_col = 'Grouped_Type' if 'Grouped_Type' in facilities_gdf.columns else None

    # Build facility_idx -> network node lookup once for path reconstruction later
    facility_node_by_idx = {}
    centroid_node_by_idx = {}
    if network_graph is not None:
        for node, ndata in network_graph.nodes(data=True):
            ntype = ndata.get('type')
            if ntype == 'facility':
                facility_node_by_idx[ndata.get('facility_idx')] = node
            elif ntype == 'pop_centroid':
                centroid_node_by_idx[ndata.get('centroid_idx')] = node

    for idx, geom in zip(facilities_gdf.index, facilities_gdf.geometry):
        total_capacity = facility_remaining.get(idx, 0)
        if total_capacity > 0:
            if gem_col is not None:
                gem_val = facilities_gdf.at[idx, gem_col]
                gem_id = str(gem_val) if pd.notna(gem_val) else ''
            else:
                gem_id = ''
            facility_type = facilities_gdf.at[idx, type_col] if type_col is not None else ''
            facility_capacities.append({
                'facility_idx': idx,
                'total_capacity': total_capacity,
                'gem_id': gem_id,
                'facility_type': facility_type,
                'geometry': geom
            })
    
    # Sort by capacity (largest first) - matches original behavior
    facility_capacities.sort(key=lambda x: x['total_capacity'], reverse=True)
    print(f"Processing {len(facility_capacities)} facilities by capacity (largest first)...")

    # Build fast lookup from facility -> reachable centroids (sorted by network distance once)
    facility_to_centroids = defaultdict(list)
    for item in centroid_facility_distances:
        original_centroid_idx = item['centroid_idx']
        reset_centroid_idx = original_to_reset.get(original_centroid_idx)
        if reset_centroid_idx is None:
            continue
        for distance_info in item.get('distances', []):
            facility_idx = distance_info.get('facility_idx')
            facility_to_centroids[facility_idx].append({
                'centroid_idx': reset_centroid_idx,
                'original_idx': original_centroid_idx,
                'distance_to_this_facility': distance_info.get('distance_km'),
                'path_nodes': distance_info.get('path_nodes', []),
                'path_segments': distance_info.get('path_segments', [])
            })

    for centroid_list in facility_to_centroids.values():
        centroid_list.sort(key=lambda x: x['distance_to_this_facility'])
    
    # Process each facility (preserving original allocation logic)
    for facility_info in facility_capacities:
        facility_idx = facility_info['facility_idx']
        remaining_capacity = facility_remaining.get(facility_idx, 0)
        
        if remaining_capacity <= 0:
            continue
        
        centroid_candidates = facility_to_centroids.get(facility_idx, [])
        if not centroid_candidates:
            continue

        # Allocate to centroids (already sorted by distance)
        for centroid_info in centroid_candidates:
            if remaining_capacity <= 0:
                break
            
            reset_centroid_idx = centroid_info['centroid_idx']
            remaining_demand = centroid_demands[reset_centroid_idx] - centroid_received[reset_centroid_idx]
            if remaining_demand <= 0:
                continue
            distance_km = centroid_info['distance_to_this_facility']
            
            # Allocate supply
            allocated = min(remaining_demand, remaining_capacity)
            centroid_received[reset_centroid_idx] += allocated
            facility_remaining[facility_idx] -= allocated
            facility_supplied[facility_idx] += allocated
            remaining_capacity -= allocated
            
            # Track facility info for this centroid
            supplying_distances[reset_centroid_idx].append(f"{distance_km:.2f}")
            supplying_types[reset_centroid_idx].append(facility_info['facility_type'])
            supplying_gem_ids[reset_centroid_idx].append(facility_info['gem_id'])
            
            # Track active connection (use original index for connection tracking)
            centroid_geom = centroids_gdf_reset.iloc[reset_centroid_idx].geometry
            path_nodes = centroid_info['path_nodes']
            path_segments = centroid_info.get('path_segments') or _build_path_segments_from_nodes(path_nodes, network_graph)

            active_connections.append({
                'centroid_idx': centroid_info['original_idx'],  # Use original index for output
                'facility_gem_id': facility_info['gem_id'],
                'centroid_lat': centroid_geom.y,
                'centroid_lon': centroid_geom.x,
                'facility_lat': facility_info['geometry'].y,
                'facility_lon': facility_info['geometry'].x,
                'network_path': path_nodes,
                'supply_mwh': allocated,
                'distance_km': distance_km,
                'facility_type': facility_info['facility_type'],
                'path_nodes': path_nodes,
                'path_segments': path_segments,
                # Internal handles for deferred path reconstruction
                '_facility_node': facility_node_by_idx.get(facility_idx),
                '_centroid_node': centroid_node_by_idx.get(centroid_info['original_idx']),
            })
    
    # Vectorized write-back to centroids_gdf (replaces per-row .loc assignments)
    received_series = pd.Series(centroid_received, index=centroids_gdf_reset.index)
    distance_series = pd.Series([', '.join(s) for s in supplying_distances], index=centroids_gdf_reset.index)
    type_series = pd.Series([', '.join(s) for s in supplying_types], index=centroids_gdf_reset.index)
    gem_series = pd.Series([', '.join(s) for s in supplying_gem_ids], index=centroids_gdf_reset.index)

    # Map reset positions back to the original centroids_gdf index order
    centroids_gdf['supply_received_mwh'] = received_series.values
    centroids_gdf['supplying_facility_distance'] = distance_series.values
    centroids_gdf['supplying_facility_type'] = type_series.values
    centroids_gdf['supplying_facility_gem_id'] = gem_series.values

    # Reconstruct paths for active connections only (one scipy Dijkstra per supplying facility)
    if network_graph is not None and active_connections:
        _reconstruct_paths_for_active_connections(active_connections, network_graph)
        # Strip internal handles to keep downstream output clean
        for conn in active_connections:
            conn.pop('_facility_node', None)
            conn.pop('_centroid_node', None)

    return centroids_gdf, active_connections

def main():
    """Parses command-line arguments and runs the main processing function."""
    parser = argparse.ArgumentParser(
        description='Process supply analysis for a country. '
                    'ADD_V2 workflow is auto-enabled when siting data exists '
                    '(i.e., {YEAR}_siting_{SUPPLY}%_{ISO3}.xlsx in outputs_per_country/parquet/).'
    )
    parser.add_argument('country_iso3', help='ISO3 country code')
    parser.add_argument('--output-dir', default='outputs_per_country', help='Output directory')
    parser.add_argument('--test', action='store_true', 
                       help='Test mode: generate full GPKG with all layers for detailed analysis')
    parser.add_argument('--run-all-scenarios', action='store_true',
                       help='Run all supply scenarios: 100%%, 90%%, 80%%, 70%%, 60%%')
    parser.add_argument('--supply-factor', type=float, default=None,
                       help='Run a single supply factor (e.g., 0.9 for 90%%). Overrides --run-all-scenarios.')
    
    args = parser.parse_args()
    
    global SUPPLY_FACTOR
    all_success = True
    
    # Determine which scenarios to run
    if args.supply_factor is not None:
        # Single specific supply factor takes precedence
        if not 0.0 < args.supply_factor <= 1.0:
            print(f"Error: --supply-factor must be between 0 and 1 (e.g., 0.9 for 90%)")
            return 1
        supply_factors = [args.supply_factor]
        print(f"\n[INFO] Running single supply scenario: {int(args.supply_factor*100)}%")
    elif args.run_all_scenarios:
        supply_factors = [1.0, 0.9, 0.8, 0.7, 0.6]
        print("\n" + "="*60)
        print("RUNNING ALL SUPPLY SCENARIOS: 100%, 90%, 80%, 70%, 60%")
        print("="*60)
    else:
        supply_factors = [SUPPLY_FACTOR]  # Use the global SUPPLY_FACTOR constant
    
    for supply_factor in supply_factors:
        SUPPLY_FACTOR = supply_factor
        
        if len(supply_factors) > 1:
            print(f"\n\n{'#'*60}")
            print(f"# PROCESSING SUPPLY SCENARIO: {int(SUPPLY_FACTOR*100)}%")
            print(f"{'#'*60}\n")
        
        result = process_country_supply(args.country_iso3, args.output_dir, test_mode=args.test)
        
        if result:
            print(f"\n✓ Successfully processed {args.country_iso3} at {int(SUPPLY_FACTOR*100)}% supply")
        else:
            print(f"\n✗ Failed to process {args.country_iso3} at {int(SUPPLY_FACTOR*100)}% supply")
            all_success = False
    
    if len(supply_factors) > 1:
        print("\n" + "="*60)
        print("ALL SCENARIOS COMPLETE")
        print("="*60)
        if all_success:
            print(f"✓ Successfully processed all {len(supply_factors)} scenarios for {args.country_iso3}")
        else:
            print(f"⚠ Some scenarios failed for {args.country_iso3}")
    
    return 0 if all_success else 1

if __name__ == "__main__":
    sys.exit(main())