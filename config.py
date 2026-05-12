"""
Shared configuration constants across all project scripts.
Change values here and they will be used by all dependent scripts.

Used by:
  - process_country_supply.py
  - process_country_siting.py
  - p1_d_viable_solar.py
  - p1_e_viable_wind.py
  - p1_f_viable_hydro.py
  - combine_one_results.py
  - combine_global_results.py
"""
import os
import time

# =============================================================================
# PATH CONFIGURATION (Local vs Cluster)
# =============================================================================

def get_bigdata_path(folder_name):
    """
    Get the correct path for bigdata folders.
    On SLURM jobs, prefers the shared cluster path to avoid local empty-folder masking.
    Otherwise checks local path first, then cluster fallback.
    Includes retry logic to handle transient NFS mount delays on compute nodes.

    Args:
        folder_name: Name of the bigdata folder (e.g., 'bigdata_gadm')

    Returns:
        str: Path to the folder
    """
    local_path = folder_name
    cluster_path = f"/soge-home/projects/mistral/ji/{folder_name}" #This is configurable based on your cluster setup
    running_on_slurm = os.environ.get("SLURM_JOB_ID") is not None

    def folder_has_data(path):
        if not os.path.isdir(path):
            return False
        with os.scandir(path) as it:
            return any(True for _ in it)

    # On HPC jobs, prefer shared cluster storage with retry for transient NFS issues.
    if running_on_slurm:
        for attempt in range(3):
            if folder_has_data(cluster_path):
                return cluster_path
            if attempt < 2:
                time.sleep(2)
        # All retries failed — warn loudly before falling through
        print(f"[WARNING] get_bigdata_path: cluster path '{cluster_path}' not accessible "
              f"after 3 attempts (SLURM_JOB_ID={os.environ.get('SLURM_JOB_ID')})")
    if folder_has_data(local_path):
        return local_path
    if folder_has_data(cluster_path):
        return cluster_path
    # Return local path as default (will trigger appropriate error if needed)
    print(f"[WARNING] get_bigdata_path: no data found for '{folder_name}' in any location")
    return local_path

# =============================================================================
# COMMON SETTINGS
# =============================================================================
COMMON_CRS = "EPSG:4326"  # WGS84 for input/output
ANALYSIS_YEAR = 2024  # Year for supply-demand analysis: 2024, 2030, or 2050
DEMAND_TYPES = ["Solar", "Wind", "Hydro", "Other Renewables", "Nuclear", "Fossil"]

# Supply factor for sensitivity analysis: each facility supplies X% of its capacity
# Default value used when running single scenario (no --run-all-scenarios flag)
# When --run-all-scenarios is used, this is overridden with [1.0, 0.9, 0.8, 0.7, 0.6]
SUPPLY_FACTOR = 1.0

# =============================================================================
# GRID RESOLUTION (for population centroids and CMIP6 outputs)
# =============================================================================
# GHS-POP native resolution is 30 arcsec (~1 km)
# POP_AGGREGATION_FACTOR aggregates native pixels into larger cells
# Final resolution = 30 * POP_AGGREGATION_FACTOR arcsec
#
# Examples:
#   Factor 10 → 300 arcsec (~9 km at equator)
#   Factor 5  → 150 arcsec (~4.5 km at equator)
#   Factor 2  → 60 arcsec (~1.8 km at equator)
#
# IMPORTANT: After changing POP_AGGREGATION_FACTOR, you MUST regenerate:
#   1. CMIP6 solar outputs: python p1_d_viable_solar.py --process-only
#   2. CMIP6 wind outputs:  python p1_e_viable_wind.py --process-only
#   3. CMIP6 hydro outputs: python p1_f_viable_hydro.py --process-only
#   4. All country supply outputs: python process_country_supply.py <ISO3>
#   5. Combined GPKGs: python combine_one_results.py <ISO3>

POP_AGGREGATION_FACTOR = 10

# Derived constants (do not modify directly)
GHS_POP_NATIVE_RESOLUTION_ARCSEC = 30
TARGET_RESOLUTION_ARCSEC = GHS_POP_NATIVE_RESOLUTION_ARCSEC * POP_AGGREGATION_FACTOR

# =============================================================================
# NETWORK / GRID SETTINGS (process_country_supply.py)
# =============================================================================
GRID_STITCH_DISTANCE_KM = 30  # Distance threshold (km) for stitching raw grid segments
NODE_SNAP_TOLERANCE_M = 100  # Snap distance (meters, UTM) for clustering nearby grid nodes
MAX_CONNECTION_DISTANCE_M = 50000  # Max distance (meters) for connecting facilities/centroids to grid
FACILITY_SEARCH_RADIUS_KM = 300  # Max radius (km) to search for facilities from each centroid

# Fallback for countries with no grid data (e.g., MUS, MDV)
# When grid data is missing, create Euclidean connections from facilities to settlements
# within this radius. This assumes implicit local distribution networks exist.
NO_GRID_CONNECTION_RADIUS_KM = 300  # Max radius (km) for direct facility-to-settlement connections

# =============================================================================
# SITING ANALYSIS SETTINGS (process_country_siting.py)
# =============================================================================
CLUSTER_RADIUS_KM = 50  # K-means clustering radius parameter
CLUSTER_MIN_SAMPLES = 1  # Minimum samples for DBSCAN geographic component detection
GRID_DISTANCE_THRESHOLD_KM = 50  # Threshold (km) to classify clusters as "remote" vs "near grid"
DROP_PERCENTAGE = 0.01  # Drop bottom X% of settlements by demand (0.01 = 1%)
MIN_SETTLEMENTS_PER_COMPONENT = 1  # Min settlements in a geographic component to create synthetic facilities
                                   # Set to 1 to support small island nations (e.g., MDV with 4 settlements)

# =============================================================================
# LAND COVER FILTERING SETTINGS (p1_c, p1_d, p1_e, p1_f)
# =============================================================================
# ESA CCI Land Cover 2022 class codes for viable energy siting
# See: https://cds.climate.copernicus.eu/datasets/satellite-land-cover
#
# Solar viable classes:
#   10-40: Cropland (rainfed, irrigated, mosaic)
#   130: Grassland
#   150: Sparse vegetation
#   200: Bare areas
LANDCOVER_VALID_SOLAR = [10, 20, 30, 40, 130, 150, 200]

# Wind viable classes (same as solar - open terrain)
LANDCOVER_VALID_WIND = [10, 20, 30, 40, 130, 150, 200]

# =============================================================================
# RESOURCE VIABILITY THRESHOLDS (p1_d_viable_solar.py, p1_e_viable_wind.py)
# =============================================================================
# Minimum PVOUT (kWh/kWp/day) for viable solar centroids
# Global Solar Atlas typical range: 2.5-6.0 kWh/kWp/day
SOLAR_PVOUT_THRESHOLD = 3.0

# Minimum WPD at 100m (W/m²) for viable wind centroids
# Global Wind Atlas typical range: 100-1000 W/m²
#
# NOTE: This threshold is set much lower than the typical 150 W/m² viability
# cutoff due to TWO systematic underestimation effects in our methodology:
#
# ═══════════════════════════════════════════════════════════════════════════
# ISSUE 1: Jensen's inequality — E[U³] >> (E[U])³
# ═══════════════════════════════════════════════════════════════════════════
# Wind power depends on U³, but we compute WPD from monthly-mean speeds:
#   WPD_computed = 0.5 * ρ * (mean(U))³
#   WPD_true     = 0.5 * ρ * mean(U³)
#
# Wind speed distributions are right-skewed; a few high-wind hours contribute
# disproportionately to energy. The ratio E[U³]/(E[U])³ is the Energy Pattern
# Factor (EPF), typically 1.4–2.0 for wind sites.
#
# References:
#   Justus, C.G., Hargraves, W.R., Mikhail, A., Graber, D. (1978).
#   "Methods for Estimating Wind Speed Frequency Distributions."
#   Journal of Applied Meteorology, 17(3), 350-353.
#   doi:10.1175/1520-0450(1978)017<0350:MFEWSF>2.0.CO;2
#   → EPF ≈ 1.9 for Weibull shape parameter k=2
#
#   Carta, J.A., Ramirez, P., Velazquez, S. (2009).
#   "A review of wind speed probability distributions used in wind energy analysis."
#   Renewable and Sustainable Energy Reviews, 13(5), 933-955.
#   doi:10.1016/j.rser.2008.05.005
#   → EPF = 1.4–2.0 depending on site variability
#
# ═══════════════════════════════════════════════════════════════════════════
# ISSUE 2: Vector-mean cancellation — sqrt(ū² + v̄²) << mean(sqrt(u² + v²))
# ═══════════════════════════════════════════════════════════════════════════
# ERA5 monthly-means provide mean u and v components separately. We compute:
#   U_computed = sqrt(mean(u)² + mean(v)²)
#
# But the true scalar mean speed is:
#   U_true = mean(sqrt(u² + v²))
#
# When wind direction varies within the month, the vector components cancel
# (e.g., easterlies and westerlies average to near-zero u), severely under-
# estimating scalar speed. This effect is worst in:
#   - Monsoon regions (seasonal wind reversals)
#   - Coastal areas (land-sea breeze oscillations)
#   - Mountain passes (diurnal flow reversals)
#   - Trade wind boundaries
#
# References:
#   Pryor, S.C., Barthelmie, R.J. (2010).
#   "Climate change impacts on wind energy: A review."
#   Renewable and Sustainable Energy Reviews, 14(1), 430-437.
#   doi:10.1016/j.rser.2009.07.028
#   → Documents directional variability effects on wind resource assessment
#
#   Staffell, I., Pfenninger, S. (2016).
#   "Using bias-corrected reanalysis to simulate current and future wind power output."
#   Energy, 114, 1224-1239.
#   doi:10.1016/j.energy.2016.08.068
#   → Shows reanalysis wind speed biases of 15-30% in complex terrain
#
# ═══════════════════════════════════════════════════════════════════════════
# COMBINED CORRECTION FACTOR
# ═══════════════════════════════════════════════════════════════════════════
# Conservative estimates:
#   - Jensen's inequality (EPF):     1.5–2.0×
#   - Vector cancellation:           1.5–3.0× (regime-dependent)
#   - Combined:                      ~3–6× underestimation
#
# With 150 W/m² as the typical viability threshold for true WPD:
#   threshold_computed = 150 / (correction_factor)
#   threshold_computed = 150 / 6 ≈ 25 W/m²  (conservative)
#
# A computed WPD of 25 W/m² likely corresponds to 75–150 W/m² true WPD.
WIND_WPD_THRESHOLD = 25

# Hydro EXCLUDED land cover classes (blacklist approach):
# Rivers can flow through any land cover, so we EXCLUDE unsuitable areas
# rather than requiring specific water-adjacent classes.
#   190: Urban areas (development conflicts, land use issues)
#   200: Bare areas (harsh desert conditions, limited access)
#   220: Permanent snow and ice (inaccessible, harsh conditions)
LANDCOVER_EXCLUDE_HYDRO = [190, 200, 220]

# Legacy: Kept for reference but no longer used in filtering
# LANDCOVER_VALID_HYDRO = [160, 170, 180, 210]  # water/wetland only - too restrictive

# =============================================================================
# HYDRO SITING THRESHOLDS
# =============================================================================
# These thresholds filter river reaches to identify viable hydro sites.
# Power potential: P = ρ × g × Q × H (where Q=discharge, H=head)
#
# HYDRO_MIN_DISCHARGE_VIABLE_M3S: Minimum projected discharge (m³/s)
#   - Small hydro: 1-10 m³/s, Large hydro: >100 m³/s
#   - Below this, not enough water for power generation
HYDRO_MIN_DISCHARGE_VIABLE_M3S = 1.0

# HYDRO_MIN_GRADIENT_M_KM: Minimum river gradient (m drop per km)
#   - Coastal/lowland rivers: 0.1-1 m/km (poor for hydro)
#   - Mountain rivers: 10-50+ m/km (excellent for hydro)
#   - 1.5 m/km allows low-head run-of-river while requiring meaningful head
HYDRO_MIN_GRADIENT_M_KM = 1.5

# HYDRO_MIN_ELEVATION_M: Minimum site elevation (m above sea level)
#   - Excludes coastal/delta areas where gradient is negligible
#   - 20m is above tidal influence but allows viable lowland valleys
HYDRO_MIN_ELEVATION_M = 20.0

# HYDRO_MIN_FLOW_RELIABILITY: Minimum ratio of min/mean monthly discharge
#   - dis_m3_pmn / dis_m3_pyr >= threshold
#   - Higher = more reliable flow year-round
#   - 0.2 = minimum flow is at least 20% of mean (seasonal rivers OK)
#   - 0.5 = more reliable flow (less seasonal variation)
HYDRO_MIN_FLOW_RELIABILITY = 0.2

# HYDRO_MIN_STREAM_ORDER: Minimum Strahler stream order for viable sites
#   - Order 1-2: Small headwater streams (micro-hydro only, <100 kW)
#   - Order 3-4: Small rivers (small hydro, 100 kW - 10 MW)
#   - Order 5-6: Medium rivers (medium hydro, 10-100 MW)
#   - Order 7+: Large rivers (large hydro, >100 MW)
#   - Setting to 2 includes smaller streams viable for small/micro hydro
HYDRO_MIN_STREAM_ORDER_VIABLE = 2

# =============================================================================
# VIABILITY INTEGRATION SETTINGS (process_country_siting.py)
# =============================================================================
# Search radius (km) around each cluster center to query viable CMIP6 centroids.
# 100 km covers ~10× the 300 arcsec (~9.25 km) grid spacing, ensuring viable
# candidates are found even in data-sparse regions and small island states.
VIABILITY_SEARCH_RADIUS_KM = 100.0

# When ANALYSIS_YEAR=2024 no dedicated viable-centroid layer exists.
# If True, automatically use the 2030 layer and record the substitution in
# the viability_source_year / viability_fallback_used output columns.
VIABILITY_FALLBACK_FOR_2024 = True
