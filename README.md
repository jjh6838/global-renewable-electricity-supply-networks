# A global geospatial dataset of renewable electricity supply and network infrastructure for 2024, 2030 and 2050

Python workflow accompanying a paper of the same title. The workflow provides a comprehensive geospatial pipeline for country-level electricity supply–demand analysis, renewable siting, and climate-aware resource viability across 189 countries and three model years (2024, 2030, 2050).

[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Table of Contents

- [Overview](#overview)
- [Workflow Summary](#workflow-summary)
- [Input Datasets](#input-datasets)
- [Data Records](#data-records)
- [Project Structure](#project-structure)
- [Installation and Environment](#installation-and-environment)
- [Quick Start](#quick-start)
- [Script Reference](#script-reference)
- [Configuration Guide](#configuration-guide)
- [Workflow Examples](#workflow-examples)
- [Outputs and Naming Conventions](#outputs-and-naming-conventions)
- [HPC Guide](#hpc-guide)
- [Troubleshooting](#troubleshooting)
- [Annex A: Pre-Data Processing (p1_a to p1_f)](#annex-a-pre-data-processing-p1_a-to-p1_f)
- [Citation and License](#citation-and-license)

## Overview

This repository contains the Python workflow accompanying a paper that presents a global geospatial dataset of modelled renewable electricity supply and transmission-network infrastructure for **189 countries** and three model years (**2024, 2030, 2050**), at **300 arc-second resolution** (approximately 10 km at the equator). The workflow integrates observed electricity generation facilities, transmission-network proxies, national electricity-generation statistics, settlement-population data, renewable-resource baselines, and CMIP6 future-resource projections into 567 country-year runs.

For each country and model year, the workflow produces four core archived layers — generation facilities, settlement-centroid electricity requirements and supply, modelled transmission-network paths, and a national summary table — plus global renewable viability screening layers for solar, wind, and hydropower in 2030 and 2050. The dataset is intended to support energy-access assessment, renewable electricity planning, infrastructure resilience analysis, and integration with local or national datasets.

The workflow is scenario-aware and supports:

- Population coverage factor analysis at 100%, 90%, 80%, 70%, 60% (configurable; published dataset uses 100%).
- Single custom factor via `--supply-factor`.
- Multi-year submission mode (2024, 2030, 2050) in HPC wrapper scripts.
- Optional second supply run that auto-detects siting outputs and writes `_add_v2` outputs (final post-siting reallocation).

## Workflow Summary

The dataset is generated through a modular eight-stage workflow run separately for each country and model year. Stage numbers correspond to the Methods section of the accompanying manuscript.

| Stage | Description | Implementing scripts |
|------:|-------------|----------------------|
| (1) | Input harmonization and preprocessing across the six input dataset categories (see [Input Datasets](#input-datasets)); harmonizes ISO3 codes, resamples rasters to 300 arc-second, prepares facility and network layers. | `p1_a_ember_gem_2024.py`, `p1_c_prep_landcover.py` |
| (2) | Construction of national electricity-use proxies and technology-specific electricity-generation values for 2024, 2030, and 2050, using baseline statistics, NDC commitments, and IEA WEO Announced Pledges Scenario. | `p1_b_ember_2024_30_50.py` |
| (3) | Spatial downscaling of national electricity-use proxies to populated settlement-centroids in proportion to population shares. | `process_country_supply.py` (downscaling step) |
| (4) | Initial network-based supply allocation linking available generation facilities to settlement-centroids via GridFinder-derived transmission paths. | `process_country_supply.py` |
| (5) | Estimation of residual electricity requirements and additional generation capacity needs by technology (2030 and 2050 only). | `process_country_siting.py` (residual logic) |
| (6) | Technology-specific renewable viability screening for solar, wind, and hydropower using baseline + CMIP6-delta resource layers and land-cover filters. | `p1_d_viable_solar.py`, `p1_e_viable_wind.py`, `p1_f_viable_hydro.py` |
| (7) | Renewable electricity siting: additional facility records generated through requirement-weighted clustering and viability-layer matching. | `process_country_siting.py` |
| (8) | Final network-based supply reallocation with additional facilities, producing the archived `_add_v2` outputs for 2030 and 2050. | `process_country_supply.py` (second pass) |

For 2024, only stages (1)–(4) are applied; the published 2024 outputs do not use the `_add_v2` suffix because no additional renewable siting or final reallocation is performed.

## Project Structure

High-level structure and what each area is used for:

```text
.
├── config.py
├── environment.yml
├── countries_list.txt
├── p1_a_ember_gem_2024.py
├── p1_b_ember_2024_30_50.py
├── p1_c_prep_landcover.py
├── p1_d_viable_solar.py
├── p1_e_viable_wind.py
├── p1_f_utils_hydro.py
├── p1_f_viable_hydro.py
├── process_country_supply.py
├── process_country_siting.py
├── process_completion.py
├── combine_one_results.py
├── combine_global_results.py
├── generate_hpc_scripts.py
├── submit_all_parallel.sh
├── submit_all_parallel_siting.sh
├── submit_one_direct.sh
├── submit_one_direct_siting.sh
├── submit_workflow.sh
├── bigdata_*/
├── data_*/
├── outputs_per_country/
├── outputs_global/
├── outputs_processed_data/
└── outputs_arcgis/
```

## Installation and Environment

### Prerequisites

- Python 3.12+
- Conda or Mamba
- Adequate storage for climate and geospatial datasets

### Conda Environment

The environment file defines the conda environment name as p1_etl.

```bash
conda env create -f environment.yml
conda activate p1_etl
python -c "import geopandas, networkx, rasterio, affine; print('Environment OK')"
```

## Quick Start

> **Prerequisite:** All input datasets must already be staged locally before running any workflow command. Populate every `bigdata_*` and `data_*` folder listed in [Input Datasets](#input-datasets) using the sources in Table 1; small `data_*` files are distributed with the repository, while `bigdata_*` folders are user-supplied. Then create and activate the conda environment as shown in [Installation and Environment](#installation-and-environment).

### Local Single-Country End-to-End

```bash
conda activate p1_etl

# 1) Supply analysis
python process_country_supply.py KEN

# 2) Siting analysis
python process_country_siting.py KEN

# 3) Optional second supply run to integrate siting outputs (_add_v2)
python process_country_supply.py KEN

# 4) Build country GeoPackage
python combine_one_results.py KEN
```

### Scenario Sweeps

```bash
# All built-in supply factors
python process_country_supply.py KEN --run-all-scenarios
python process_country_siting.py KEN --run-all-scenarios

# One custom supply factor
python process_country_supply.py KEN --supply-factor 0.9
python process_country_siting.py KEN --supply-factor 0.9
```

## Script Reference

### Core Configuration

- config.py
  - Central parameters (year, supply factor, thresholds, network/siting settings).
  - Bigdata path resolution via get_bigdata_path with SLURM-aware behavior and retry.
  - Environment-variable overrides for analysis year and cluster path controls.

### Data Preparation and Projections

- p1_a_ember_gem_2024.py *(Stage 1)*
  - Harmonizes Ember country aggregates with GEM facility-level records.
  - Produces country-level and facility-level baselines for downstream analysis.
  - Handles country code/name mapping and fuel-type harmonization.

- p1_b_ember_2024_30_50.py *(Stage 2)*
  - Builds 2030/2050 projections using UN population growth, NDC targets, and IEA assumptions.
  - Applies disaggregation logic to distribute broad renewable targets across technologies.
  - Exports projected generation/capacity tables for downstream country processing.

- p1_c_prep_landcover.py *(Stage 1)*
  - Downloads ESA CCI Land Cover 2022 from CDS.
  - Converts and upscales to the 300 arcsec grid aligned with analysis outputs.
  - Produces landcover_2022_10arcsec.tif and landcover_2022_300arcsec.tif.

- p1_d_viable_solar.py *(Stage 6b)*
  - CMIP6 delta method for PVOUT projections.
  - Computes future PVOUT using baseline x climate delta and model ensemble mean.
  - Exports projected, uncertainty, delta, baseline, and viability-filtered outputs.

- p1_e_viable_wind.py *(Stage 6c)*
  - ERA5 + CMIP6 delta method for WPD projections.
  - Converts projected wind speeds to WPD and computes ensemble uncertainty.
  - Exports projected, uncertainty, delta, baseline, and viability-filtered outputs.

- p1_f_viable_hydro.py *(Stage 6d)*
  - Unified hydro processing in three parts:
    - Runoff delta generation from ERA5-Land + CMIP6.
    - RiverATLAS discharge projection.
    - Viable hydro centroid extraction with landcover and discharge filters.

### Country Analysis

- process_country_supply.py *(Stages 3, 4, 8)*
  - Main country-level supply-demand network analysis.
  - Supports single scenario, all scenarios, or one custom supply factor.
  - Auto-enables add_v2 workflow when matching siting workbook is detected.

  Usage:

  ```bash
  python process_country_supply.py <ISO3> [--output-dir outputs_per_country] [--test] [--run-all-scenarios] [--supply-factor 0.X]
  ```

  Notes:

  - --supply-factor overrides --run-all-scenarios.
  - Valid supply-factor range is (0, 1].

- process_country_siting.py *(Stages 5, 7)*
  - Identifies underserved settlements and proposes siting clusters/networks.
  - Supports single scenario, all scenarios, or one custom supply factor.

  Usage:

  ```bash
  python process_country_siting.py <ISO3> [--output-dir outputs_per_country] [--run-all-scenarios] [--supply-factor 0.X]
  ```

### Result Combination

- combine_one_results.py
  - Converts country parquet outputs into a country GeoPackage.
  - Auto-detects _add_v2 scenario folders when present.
  - Optionally adds available CMIP6 raster layers to the GeoPackage.

  Usage:

  ```bash
  python combine_one_results.py <ISO3> [--scenario YEAR_supply_PCT%] [--base-dir outputs_per_country]
  ```

- combine_global_results.py
  - Combines all country outputs into scenario-level global GeoPackages.
  - Can auto-detect scenarios or run on selected countries.

  Usage:

  ```bash
  python combine_global_results.py [--input-dir outputs_per_country] [--scenario SCENARIO] [--countries ISO3 ISO3 ...] [--countries-file countries.txt] [--output out.gpkg]
  ```

- process_completion.py *(post-run audit)*
  - Audits per-country outputs after large multi-country runs and finalizes the working country set.
  - Scans the standard scenario folders (e.g. `2024_supply_100%`, `2030_supply_100%_add_v2`, `2050_supply_100%_add_v2`) for the expected summary spreadsheets and parquet layers, identifies countries with missing or non-usable spatial results, and writes a CSV audit report (`process_completion_audit.csv`).
  - Default behaviour is **dry-run**. Use `--apply` to remove incomplete country outputs and update `countries_list.txt` accordingly.

  Usage:

  ```bash
  python process_completion.py                  # dry-run audit only
  python process_completion.py --apply          # apply removals and refresh countries_list.txt
  ```

### HPC Script Generation and Submission

- generate_hpc_scripts.py
  - Generates and refreshes parallel supply/siting batch scripts and wrappers.

  Usage:

  ```bash
  python generate_hpc_scripts.py --create-parallel
  python generate_hpc_scripts.py --create-parallel-siting
  ```

- submit_all_parallel.sh
  - Submit all supply jobs.
  - Supports --run-all-years, --run-all-scenarios, and --supply-factor.

- submit_all_parallel_siting.sh
  - Submit all siting jobs.
  - Supports --run-all-years, --run-all-scenarios, and --supply-factor.

- submit_one_direct.sh
  - Submit one supply job by ISO3.
  - Supports optional --tier override and scenario flags.

- submit_one_direct_siting.sh
  - Submit one siting job by ISO3.
  - Supports optional --tier override and scenario flags.

- submit_workflow.sh
  - SLURM batch wrapper for the final global combination stage.
  - Runs `combine_global_results.py` on the cluster (default request: 64 GB RAM, 40 CPUs, 12 h on the `Medium` partition) to merge all per-country Parquet outputs in `outputs_per_country/` into per-scenario global GeoPackages in `outputs_global/`.
  - Auto-detects scenarios from `outputs_per_country/parquet/`. Submit with `sbatch submit_workflow.sh` after all per-country supply and siting jobs have completed.
  - Only required for users producing the merged global `.gpkg` files; can be skipped if you only need per-country outputs. Edit the SBATCH header and the hardcoded conda path near the top of the file to match your cluster.

## Configuration Guide

All major runtime settings are in config.py.

### Key Parameters

- ANALYSIS_YEAR
- SUPPLY_FACTOR
- POP_AGGREGATION_FACTOR
- TARGET_RESOLUTION_ARCSEC
- GRID_STITCH_DISTANCE_KM
- NODE_SNAP_TOLERANCE_M
- MAX_CONNECTION_DISTANCE_M
- FACILITY_SEARCH_RADIUS_KM
- CLUSTER_RADIUS_KM
- GRID_DISTANCE_THRESHOLD_KM
- DROP_PERCENTAGE
- SOLAR_PVOUT_THRESHOLD
- WIND_WPD_THRESHOLD
- HYDRO_MIN_DISCHARGE_VIABLE_M3S
- VIABILITY_SEARCH_RADIUS_KM
- VIABILITY_FALLBACK_FOR_2024

### Current Default Highlights

- ANALYSIS_YEAR = 2024
- SUPPLY_FACTOR = 1.0
- POP_AGGREGATION_FACTOR = 10
- SOLAR_PVOUT_THRESHOLD = 3.0
- WIND_WPD_THRESHOLD = 25
- HYDRO_MIN_DISCHARGE_VIABLE_M3S = 1.0
- VIABILITY_SEARCH_RADIUS_KM = 100.0
- VIABILITY_FALLBACK_FOR_2024 = True

### Manuscript-aligned defaults for the published dataset

The published dataset uses the broad-screening thresholds and parameters listed below. All are configurable via [config.py](config.py). Stage numbers refer to the [Workflow Summary](#workflow-summary).

- **Population coverage factor (stage 3):** 100% — share of the national electricity-use proxy spatially downscaled to populated settlement-centroids. Not an observed electrification rate.
- **Network thresholds (stage 4):** 1 km for node snapping, component stitching, and facility/centroid attachment.
- **Solar viability (stage 6b):** PVOUT ≥ 3.0 kWh/kWp/day; eligible ESA CCI land-cover classes 10, 20, 30, 40 (cropland), 130 (grassland), 150 (sparse vegetation), 200 (bare).
- **Wind viability (stage 6c):** WPD ≥ 25 W/m² at 100 m (air density ρ = 1.225 kg/m³); same onshore land-cover classes as solar; offshore screening permitted within EEZ.
- **Hydro viability (stage 6d):** discharge ≥ 1.0 m³/s; gradient ≥ 1.5 m/km; elevation ≥ 20 m; dry-to-mean monthly discharge ratio ≥ 0.2; Strahler stream order ≥ 2; land-cover exclusions: classes 190 (urban), 200 (bare), 220 (permanent snow/ice).
- **Siting (stage 7):** viability search radius 300 km; requirement-weighted K-means clustering with component-aware splitting.
- **CMIP6 ensemble (stages 6b–6d):** CESM2, EC-Earth3-Veg-LR, MPI-ESM1-2-LR under SSP2-4.5; delta method applied to baseline resource layers; ensemble mean used with inter-model range retained for validation.

### Regeneration Guidance

If you change resolution or viability thresholds, re-run:

1. p1_c_prep_landcover.py if landcover grid prerequisites changed.
2. p1_d_viable_solar.py, p1_e_viable_wind.py, p1_f_viable_hydro.py.
3. Country processing scripts.
4. Combination scripts.

## Input Datasets

The workflow integrates six categories of input datasets. The GitHub repository provides the Python workflow together with selected small supporting data files (`data_*` folders). Large input datasets prefixed with `bigdata_` are **not** distributed through the repository and must be populated separately using datasets obtained from their original sources. If alternative file names or directory structures are used, the corresponding Python loading scripts should be updated accordingly.

Table 1 below reproduces the manuscript input-dataset table.

### Table 1. Input datasets, workflow purpose, and local folder structure

**Administrative and maritime boundaries**

| Dataset | Main purpose in workflow | Data type | Folder | Source |
|---|---|---|---|---|
| Administrative boundary data | Country clipping and attribution | Polygon | `bigdata_gadm` | GADM / Version 4.1 |
| Maritime EEZ boundary data | Offshore support where relevant | Polygon | `bigdata_eez` | Flanders Marine Institute / Version 12 |

**Population and national electricity-generation scenarios**

| Dataset | Main purpose in workflow | Data type | Folder | Source |
|---|---|---|---|---|
| GHS-POP gridded settlement population data | Spatial downscaling of electricity requirements to settlement-centroids | Raster; Point | `bigdata_settlements_jrc` | JRC / 2025 modified version |
| Population projection data | National population scaling for 2030 and 2050 | Table by country-year | `data_pop_un` | UN / Accessed in 2025 |
| Country classification data | Benchmark assignment for countries without NDCs | Table by country | `data_country_class_wb` | World Bank / Accessed in 2025 |
| Baseline national electricity-generation statistics | Baseline technology-specific electricity-generation values used to define the national electricity-use proxy and initial supply envelope | Table by country-year | `data_energy_ember` | IEA data compiled by Ember Energy / Accessed in 2025 |
| Future electricity-generation scenarios | 2030 and 2050 scenario construction | Table by country-year | `data_energy_projections_iea` | IEA / 2024 modified version |
| NDC renewable electricity commitments | Renewable electricity-generation inputs for 2030 scenario construction | Table by country-year | `data_energy_ember` | NDC data compiled by Ember Energy / Accessed in 2025 |

**Electricity generation facility and transmission-network data**

| Dataset | Main purpose in workflow | Data type | Folder | Source |
|---|---|---|---|---|
| Electricity generation facility data | Locations, status, installed capacity, and estimated generation of electricity generation facilities | Point | `data_facilities_gem` | GEM / Accessed in 2025 |
| Electricity transmission-network data | Network routing between generation facilities and settlement-centroids | Polyline | `bigdata_gridfinder` | GridFinder / Updated with the 2024 data |

**Land cover and renewable-site reference data**

| Dataset | Main purpose in workflow | Data type | Folder | Source |
|---|---|---|---|---|
| Land cover data | Suitability filtering for renewable siting | Raster | `bigdata_landcover_cds` | CDS / 2025 modified version |
| Existing renewable-site reference data | Auxiliary support for renewable-site screening | Point | `bigdata_solar_wind_ms` | Microsoft Global Renewables Watch / 2024 Q2 version |

**Baseline renewable-resource data**

| Dataset | Main purpose in workflow | Data type | Folder | Source |
|---|---|---|---|---|
| Solar — Photovoltaic Power Potential (PVOUT) data | Baseline solar productivity for viability screening | Raster | `bigdata_solar_pvout` | World Bank Global Solar Atlas / Accessed in 2025 |
| Wind — ERA5 wind speed data | Baseline wind productivity and suitability screening | Raster | `bigdata_wind_atlas` | CDS / 2025 modified version |
| Hydro — ERA5-Land runoff data | Baseline hydrological conditions for runoff adjustment | Raster | `bigdata_hydro_era5_land` | CDS / 2025 modified version |
| Hydro — River network and attribute data | River-based hydropower screening | Polyline | `bigdata_hydro_atlas` | HydroATLAS / Accessed in 2025 |

**Future renewable-resource projections**

| Dataset | Main purpose in workflow | Data type | Folder | Source |
|---|---|---|---|---|
| Solar — CMIP6 solar-resource projections | Future solar viability screening | Raster | `bigdata_solar_cmip6` | CDS / 2025 modified version |
| Wind — CMIP6 wind-resource projections | Future wind viability screening | Raster | `bigdata_wind_cmip6` | CDS / 2025 modified version |
| Hydro — CMIP6 runoff projections | Future hydropower viability screening | Raster; Point | `bigdata_hydro_cmip6` | CDS / 2025 modified version |

*Abbreviations:* GADM, Global Administrative Areas; EEZ, Exclusive Economic Zone; JRC, European Commission Joint Research Centre; GHS-POP, Global Human Settlement Layer population grids; UN, United Nations; IEA, International Energy Agency; WEO, World Energy Outlook; NDC, Nationally Determined Contribution; GEM, Global Energy Monitor; CDS, Copernicus Climate Data Store; ECMWF, European Centre for Medium-Range Weather Forecasts; ERA5, ECMWF Fifth Reanalysis; CMIP6, Sixth Coupled Model Intercomparison Project.

### Expected filenames in each folder

The loader scripts expect the following files. Adjust the loader paths in [config.py](config.py) and the relevant `p1_*` scripts if your local layout differs.

- `bigdata_gadm/gadm_410-levels.gpkg`
- `bigdata_eez/eez_v12.gpkg`, `bigdata_eez/eez_boundaries_v12.gpkg`
- `bigdata_settlements_jrc/GHS_POP_E2025_GLOBE_R2023A_4326_30ss_V1_0.tif`
- `bigdata_gridfinder/grid.gpkg`
- `bigdata_landcover_cds/outputs/landcover_2022_300arcsec.tif` (produced by `p1_c_prep_landcover.py`)
- `bigdata_solar_wind_ms/solar_all_2024q2_v1.gpkg`, `bigdata_solar_wind_ms/wind_all_2024q2_v1.gpkg`
- `bigdata_solar_pvout/PVOUT.tif`
- `bigdata_wind_atlas/gasp_flsclassnowake_100m.tif`
- `bigdata_hydro_atlas/RiverATLAS_Data_v10.gdb`
- `bigdata_hydro_era5_land/downloads/` (ERA5-Land monthly runoff)
- `bigdata_solar_cmip6/`, `bigdata_wind_cmip6/`, `bigdata_hydro_cmip6/` — each with `downloads/`, `extracted/`, `outputs/` subfolders populated by `p1_d`/`p1_e`/`p1_f`
- `data_energy_ember/yearly_full_release_long_format*.csv` and NDC targets workbook
- `data_energy_projections_iea/WEO_2024_PG_Assumptions_STEPSandNZE_Scenario.xlsb`, `WEO2024_AnnexA_Free_Dataset_*.csv`, `iea_iso3_mapping.csv`
- `data_pop_un/WPP2024_TotalPopulationBySex*.csv`
- `data_country_class_wb/*` (World Bank country classification table)
- `data_facilities_gem/*` (GEM facility tables)

## Data Records

The archived dataset is deposited at **[Zenodo DOI]** (placeholder — replaced on publication). Records are organized as standardized country-year outputs for 189 countries × 3 model years (2024, 2030, 2050). Each country-year output contains four core components: generation facility layers, settlement-centroid electricity requirement and supply layers, transmission-network layers with routed supply paths, and a country-level summary table. Global supporting renewable viability screening layers for solar, wind, and hydropower are also included for 2030 and 2050.

Geospatial vector layers are provided in **Parquet** (`.parquet`) format; country-level summaries in **Excel** (`.xlsx`) format. The renewable viability screening layers are provided as global supporting records for 2030 and 2050 only, with one Parquet and one GeoTIFF (`.tif`) file per technology (solar, wind, hydropower) — 12 global files total: 2 years × 2 formats × 3 technologies.

### File naming

Folder pattern for final post-siting outputs:

- `2030_supply_100%_add_v2/` and `2050_supply_100%_add_v2/`
  - `100%` = population coverage factor used for requirement downscaling.
  - `_add_v2` = final network-based supply reallocation in stage (8) has been completed.
- `2024_supply_100%/` (no `_add_v2` suffix) — the 2024 baseline is built from the initial allocation in stage (4); no additional siting is applied.
- When the workflow is run for 2030 or 2050, the corresponding folders **without** `_add_v2` are generated as intermediate outputs after stage (4) and are not part of the final archive unless retained.

Example final files for the Republic of Korea (ISO3 = `KOR`) in `2050_supply_100%_add_v2/`:

- `facilities_KOR_add_v2.parquet` — generation facility layers
- `centroids_KOR_add_v2.parquet` — settlement-centroid electricity requirement and supply layers
- `polylines_KOR_add_v2.parquet` — transmission-network layers with routed supply paths
- `2050_supply_100%_KOR_add_v2.xlsx` — country-level summary table

Global renewable viability screening files identify the technology and model year directly in the file name, e.g. `HYDRO_VIABLE_CENTROIDS_2050.parquet`, with equivalent files for solar and wind in both Parquet and GeoTIFF.

### Table 2. Core archived output components and selected key fields

**Electricity generation facility layers** (Point vector, Parquet)

| Field | Description | Unit / format |
|---|---|---|
| `OBJECTID` | Internal feature identifier | ID |
| `GID_0` | ISO3 country code | e.g. `KOR` |
| `GEM_unit_phase_ID` | Facility identifier from GEM or newly generated facility ID | Facility ID |
| `Grouped_Type` | Harmonized facility technology type | One of: solar, wind, hydro, other renewables, nuclear, fossil |
| `Latitude`, `Longitude` | Facility location coordinates | Decimal degrees |
| `Adjusted_Capacity_MW` | Installed or scenario-adjusted facility capacity | MW |
| `total_mwh` | Estimated annual electricity generation | MWh |
| `available_total_mwh` | Annual electricity available for allocation | MWh |
| `supplied_mwh` | Electricity allocated through network-based supply allocation | MWh |
| `remaining_mwh` | Unallocated electricity remaining after allocation | MWh |

**Settlement-centroid electricity requirement and supply layers** (Point vector, Parquet)

| Field | Description | Unit / format |
|---|---|---|
| `fid` | Internal feature identifier | ID |
| `GID_0` | ISO3 country code | e.g. `KOR` |
| `centroid_idx` | Unique settlement-centroid identifier | Settlement-centroid ID |
| `Population_centroid` | Baseline population in the grid cell | Persons |
| `Population_[year]_centroid` | Projected population in the model year | Persons |
| `Total_Demand_[year]_centroid` | Electricity requirement allocated to the settlement-centroid | MWh |
| `supplying_facility_distance` | Distance to linked supplying facility or facilities | km |
| `supplying_facility_type` | Technology type(s) of linked supplying facility or facilities | One or more of: solar, wind, hydro, other renewables, nuclear, fossil |
| `supplying_facility_gem_id` | Identifier(s) of linked supplying facility or facilities | Facility ID(s) |
| `supply_received_mwh` | Electricity supplied to the settlement-centroid | MWh |
| `supply_status` | Allocation status of the settlement-centroid | One of: Filled, Partially Filled, Not Filled |

**Modelled transmission-network layers** (Polyline vector, Parquet)

| Field | Description | Unit / format |
|---|---|---|
| `fid` | Internal feature identifier | ID |
| `GID_0` | ISO3 country code | e.g. `KOR` |
| `connection_id` | Unique settlement-centroid–facility connection identifier | Path ID (centroid ID → facility ID) |
| `centroid_idx` | Linked settlement-centroid identifier | Settlement ID |
| `facility_gem_id` | Linked facility identifier | Facility ID |
| `facility_type` | Technology type of linked facility | One of: solar, wind, hydro, other renewables, nuclear, fossil |
| `distance_km` | Length of routed supply path | km |
| `supply_mwh` | Electricity routed through the connection | MWh |
| `active_supply` | Indicator for whether the connection carries allocated supply | Yes / No |
| `Population_[year]_centroid` | Population of the linked settlement-centroid for the model year | Persons |

**National summary tables** (Excel `.xlsx`)

| Field | Description | Unit / format |
|---|---|---|
| Country and model year | Country name, ISO3 code, model year | Text, ISO3, year |
| Configuration parameters | Key workflow settings, including assumed population coverage factor | Text, numeric |
| Demand totals | Total national electricity requirements | MWh |
| Available supply | Total electricity available from available generation facilities | MWh |
| Supplied electricity | Total electricity allocated to settlement-centroids | MWh |
| Unsupplied electricity | Remaining unmet electricity requirements after allocation | MWh |
| Demand coverage | Share of electricity requirements supplied by the modelled allocation | Percent |
| Technology breakdown | Requirements, available supply, and supplied electricity by technology type | MWh |
| Settlement status counts | Number of filled, partially filled, and not-filled settlement-centroids | Count |

### Global renewable viability screening layers (supporting records)

Twelve global files are archived: 2 model years (2030, 2050) × 2 file formats (`.parquet`, `.tif`) × 3 technologies (solar, wind, hydro). File names include the technology and model year, e.g. `SOLAR_VIABLE_CENTROIDS_2030.parquet`, `WIND_VIABLE_CENTROIDS_2050.tif`, `HYDRO_VIABLE_CENTROIDS_2050.parquet`. The Parquet files contain only cells or river reaches retained as viable in stage (6); the GeoTIFF files store the projected resource value at viable cells and 0 elsewhere on the common 300 arc-second grid.

**Solar and wind viable centroids** (`SOLAR_VIABLE_CENTROIDS_{year}.parquet`, `WIND_VIABLE_CENTROIDS_{year}.parquet`) — Point vector

| Field | Description | Unit / format |
|---|---|---|
| `geometry` | Cell-centre point on the 300 arc-second grid | Point, EPSG:4326 |
| `source` | Technology label | `solar` or `wind` |
| `value_{year}` | Projected resource value for the model year (ensemble mean) | Solar: kWh/kWp/day (PVOUT). Wind: W/m² at 100 m (WPD) |
| `value_baseline` | Baseline observation-constrained resource value | Same unit as `value_{year}` |
| `delta` | CMIP6-derived relative change factor (projected / baseline) | Ratio |
| `uncertainty` | Inter-model range across the CMIP6 ensemble | Same unit as `value_{year}` |
| `is_ms_viable` | Flag: cell contains a Microsoft Global Renewables Watch reference site | Boolean |
| `is_lc_valid` | Flag: cell passes the land-cover filter (see stages 6b / 6c) | Boolean |
| `meets_threshold` | Flag: cell meets the productivity threshold (PVOUT ≥ 3.0 kWh/kWp/day or WPD ≥ 25 W/m²) | Boolean |
| `is_viable` | Final viability flag: `is_ms_viable` OR (`is_lc_valid` AND `meets_threshold`); always True in the archived file | Boolean |

**Hydro viable centroids** (`HYDRO_VIABLE_CENTROIDS_{year}.parquet`) — Point vector at RiverATLAS river-reach centroids

| Field | Description | Unit / format |
|---|---|---|
| `geometry` | River-reach centroid point | Point, EPSG:4326 |
| `HYRIV_ID` | RiverATLAS river-reach identifier | ID |
| `dis_m3_pyr` | Baseline mean annual discharge (RiverATLAS) | m³/s |
| `dis_m3_pmn` | Baseline minimum monthly discharge (RiverATLAS) | m³/s |
| `dis_m3_pmx` | Baseline maximum monthly discharge (RiverATLAS) | m³/s |
| `delta` | CMIP6-derived relative runoff change factor (projected / baseline) | Ratio |
| `dis_m3_pyr_projected` | Projected mean annual discharge for the model year | m³/s |
| `flow_reliability` | Ratio of minimum to mean monthly discharge | Ratio |
| `ORD_STRA` | Strahler stream order | Integer |
| `sgr_dk_rav` | River gradient | m/km |
| `ele_mt_cav` | Reach elevation | m |
| `UPLAND_SKM` | Upstream catchment area | km² |

**Viability screening GeoTIFFs** (`{TECH}_VIABLE_CENTROIDS_{year}.tif`) — Raster on the 300 arc-second grid; viable cells carry the projected resource value (PVOUT, WPD, or projected discharge sampled to the grid), and non-viable cells carry 0.

## Workflow Examples

### Example 1: Single Country, Single Scenario

```bash
python process_country_supply.py KEN
python process_country_siting.py KEN
python combine_one_results.py KEN
```

### Example 2: Single Country, All Scenarios

```bash
python process_country_supply.py KEN --run-all-scenarios
python process_country_siting.py KEN --run-all-scenarios
python combine_one_results.py KEN --scenario 2024_supply_100%
```

### Example 3: All Countries on HPC

```bash
python generate_hpc_scripts.py --create-parallel
python generate_hpc_scripts.py --create-parallel-siting

# Normalize line endings (CRLF -> LF) before granting execute bits on Linux HPC
sed -i 's/\r$//' submit_*.sh parallel_scripts/*.sh parallel_scripts_siting/*.sh

chmod +x submit_*.sh parallel_scripts/*.sh parallel_scripts_siting/*.sh

./submit_all_parallel.sh --run-all-years --run-all-scenarios
./submit_all_parallel_siting.sh --run-all-years --run-all-scenarios

# Optional add_v2 integration pass
./submit_all_parallel.sh --run-all-years --run-all-scenarios

# Final global combination: merges all per-country Parquet outputs into per-scenario
# global GeoPackages in outputs_global/. Run only after all per-country jobs above
# have completed successfully.
sbatch submit_workflow.sh
```

## Outputs and Naming Conventions

The canonical field schema for each archived layer is documented in [Data Records](#data-records) (Table 2). This section describes the on-disk folder and file patterns used by the workflow.

### Scenario Folder Pattern

- outputs_per_country/parquet/{YEAR}_supply_{PCT}%/
- outputs_per_country/parquet/{YEAR}_supply_{PCT}%_add_v2/

The `_add_v2` suffix indicates that the final network-based supply reallocation in stage (8) has been completed. For 2024 outputs, the suffix is not used because the baseline year is built directly from the initial allocation in stage (4); no additional renewable siting is applied. Folders without `_add_v2` produced for 2030 or 2050 are intermediate outputs after stage (4) and are not part of the published archive unless retained.

### Typical Country Files

- centroids_{ISO3}.parquet
- facilities_{ISO3}.parquet
- grid_lines_{ISO3}.parquet
- polylines_{ISO3}.parquet
- siting_clusters_{ISO3}.parquet
- siting_networks_{ISO3}.parquet
- {YEAR}_siting_{PCT}%_{ISO3}.xlsx

### GeoPackages

- outputs_per_country/{scenario}_{ISO3}.gpkg
- outputs_per_country/{scenario}_{ISO3}_add.gpkg
- outputs_per_country/{scenario}_{ISO3}_add_v2.gpkg
- outputs_global/{scenario}_global.gpkg

### Logs

- outputs_per_country/parquet/{scenario}/logs/parallel_*.out
- outputs_per_country/parquet/{scenario}/logs/siting_*.out
- outputs_per_country/parquet/logs_run_all_scenarios/parallel_*.out
- outputs_per_country/parquet/logs_run_all_scenarios/siting_*.out
- outputs_per_country/parquet/logs_run_all_scenarios_add_v2/parallel_*.out
- outputs_per_country/logs/workflow_*.out

## HPC Guide

### Expected runtime per country

Runtime varies strongly with country size, network density, and cluster node spec. As a rough guide:

- **Small countries** (e.g. island states, small EEZ): a single supply or siting job typically completes in **under 1 minute**.
- **Medium countries**: typically **minutes to a few hours** per job.
- **Largest countries** (e.g. CHN, USA, RUS, BRA, IND, CAN): the supply job alone can take **more than 3 days** on a single node depending on cluster spec, primarily driven by network graph construction and shortest-path allocation across very large transmission networks.

When submitting in bulk, allocate the largest countries to higher-tier nodes via the `--tier` option on the single-country submit scripts (see below).

### Submit All Supply Jobs

```bash
./submit_all_parallel.sh
./submit_all_parallel.sh --run-all-scenarios
./submit_all_parallel.sh --supply-factor 0.9
./submit_all_parallel.sh --run-all-years
./submit_all_parallel.sh --run-all-years --run-all-scenarios
./submit_all_parallel.sh --run-all-years --supply-factor 0.9
```

### Submit All Siting Jobs

```bash
./submit_all_parallel_siting.sh
./submit_all_parallel_siting.sh --run-all-scenarios
./submit_all_parallel_siting.sh --supply-factor 0.9
./submit_all_parallel_siting.sh --run-all-years
```

### Submit Single Country

```bash
./submit_one_direct.sh KEN
./submit_one_direct.sh KEN --run-all-scenarios
./submit_one_direct.sh KEN --supply-factor 0.9
./submit_one_direct.sh CHN --tier 1

./submit_one_direct_siting.sh KEN
./submit_one_direct_siting.sh KEN --run-all-scenarios
./submit_one_direct_siting.sh KEN --supply-factor 0.9
./submit_one_direct_siting.sh CHN --tier 1
```

### Cluster Data Path Notes

Wrappers export BIGDATA_ROOT, BIGDATA_LOCAL_ROOT, BIGDATA_RETRY_COUNT, and BIGDATA_RETRY_SLEEP_SEC.
The config resolver prefers cluster storage on SLURM jobs and local data for interactive runs.

## Troubleshooting

### Common Issues

- Invalid supply factor
  - Use --supply-factor in (0, 1], for example 0.9.

- Missing bigdata paths on cluster
  - Verify BIGDATA_ROOT and shared mount availability.
  - Confirm get_bigdata_path resolves correctly in your environment.

- add_v2 not produced
  - Ensure siting workbook exists with exact pattern:
    - {YEAR}_siting_{PCT}%_{ISO3}.xlsx

- Linux script execution problems
  - Ensure executable bits are set:
  - chmod +x submit_*.sh parallel_scripts/*.sh parallel_scripts_siting/*.sh

- Raster layers not appearing in GIS
  - Check whether source CMIP6 TIFFs exist in bigdata_* outputs.
  - Re-run combination script after climate outputs are generated.

## Annex A: Pre-Data Processing (p1_a to p1_f)

This annex captures the upstream data preparation pipeline in detail.
These scripts are typically run before country-level supply and siting analysis.

### p1_a_ember_gem_2024.py *(Stage 1)*

Purpose:

- Harmonize Ember country-level electricity aggregates with GEM facility-level records.
- Build a baseline dataset that preserves national totals while enabling spatial analysis.

Key inputs:

- data_energy_ember/yearly_full_release_long_format*.csv
- data_facilities_gem/*
- country code mappings in script + pycountry

Key outputs:

- outputs_processed_data/p1_a_ember_gem_2024.xlsx
- outputs_processed_data/p1_a_ember_gem_2024_fac_lvl.xlsx

Typical run:

```bash
python p1_a_ember_gem_2024.py
```

### p1_b_ember_2024_30_50.py *(Stage 2)*

Purpose:

- Generate 2030 and 2050 scenario projections from the p1_a baseline.
- Integrate population growth, NDC-style targets, and IEA assumptions.

Key inputs:

- outputs_processed_data/p1_a_ember_gem_2024.xlsx
- data_pop_un/WPP2024_TotalPopulationBySex*.csv
- data_energy_ember/targets_download*.xlsx
- data_energy_projections_iea/*

Key outputs:

- outputs_processed_data/p1_b_ember_2024_30_50.xlsx

Typical run:

```bash
python p1_b_ember_2024_30_50.py
```

### p1_c_prep_landcover.py *(Stage 1)*

Purpose:

- Download ESA CCI Land Cover 2022 from CDS.
- Convert/extract and upscale to analysis-aligned 300 arcsec grid.

Key inputs:

- CDS API credentials (cdsapi)

Key outputs:

- bigdata_landcover_cds/extracted/C3S-LC-L4-LCCS-Map-300m-P1Y-2022-v2.1.1.nc
- bigdata_landcover_cds/outputs/landcover_2022_10arcsec.tif
- bigdata_landcover_cds/outputs/landcover_2022_300arcsec.tif

Typical run:

```bash
python p1_c_prep_landcover.py
python p1_c_prep_landcover.py --force
```

### p1_d_viable_solar.py *(Stage 6b)*

Purpose:

- Build climate-adjusted PVOUT projections using CMIP6 delta method.
- Generate solar viability layers and centroids for downstream siting.

Key inputs:

- bigdata_solar_pvout/PVOUT.tif
- CMIP6 rsds downloads (historical + SSP245)
- bigdata_landcover_cds/outputs/landcover_2022_300arcsec.tif
- bigdata_solar_wind_ms/solar_all_2024q2_v1.gpkg

Key outputs (under bigdata_solar_cmip6/outputs):

- PVOUT_{2030,2050}_300arcsec.tif
- PVOUT_UNCERTAINTY_{2030,2050}_300arcsec.tif
- PVOUT_DELTA_{2030,2050}_300arcsec.tif
- PVOUT_baseline_300arcsec.tif
- SOLAR_VIABLE_CENTROIDS_{2030,2050}.tif
- matching parquet layers

Typical run:

```bash
python p1_d_viable_solar.py
python p1_d_viable_solar.py --download-only
python p1_d_viable_solar.py --process-only
```

### p1_e_viable_wind.py *(Stage 6c)*

Purpose:

- Build climate-adjusted wind projections (WPD) from ERA5 baseline and CMIP6 deltas.
- Generate wind viability layers and centroids.

Key inputs:

- ERA5 100m wind monthly means
- CMIP6 near-surface wind downloads (historical + SSP245)
- bigdata_wind_atlas/gasp_flsclassnowake_100m.tif
- bigdata_landcover_cds/outputs/landcover_2022_300arcsec.tif
- bigdata_solar_wind_ms/wind_all_2024q2_v1.gpkg

Key outputs (under bigdata_wind_cmip6/outputs):

- WPD100_{2030,2050}_300arcsec.tif
- WPD100_UNCERTAINTY_{2030,2050}_300arcsec.tif
- WPD100_DELTA_{2030,2050}_300arcsec.tif
- WPD100_baseline_300arcsec.tif
- WIND_VIABLE_CENTROIDS_{2030,2050}.tif
- matching parquet layers

Typical run:

```bash
python p1_e_viable_wind.py
python p1_e_viable_wind.py --download-only
python p1_e_viable_wind.py --process-only
```

### p1_f_utils_hydro.py *(Stage 6d — helpers)*

Purpose:

- Shared helper utilities for hydro processing.
- Centralizes download, transformation, delta, and export helper functions used by p1_f_viable_hydro.py.

Typical usage:

- Imported by p1_f_viable_hydro.py (not usually run directly).

### p1_f_viable_hydro.py *(Stage 6d)*

Purpose:

- Unified hydro workflow with three stages:
  - runoff delta computation (ERA5-Land + CMIP6),
  - RiverATLAS projection,
  - viable hydro centroid extraction.

Key inputs:

- RiverATLAS_Data_v10.gdb
- ERA5-Land runoff monthly data
- CMIP6 total_runoff data
- bigdata_landcover_cds/outputs/landcover_2022_300arcsec.tif

Key outputs (under bigdata_hydro_cmip6/outputs):

- HYDRO_RUNOFF_baseline_300arcsec.*
- HYDRO_RUNOFF_DELTA_{2030,2050}_300arcsec.*
- HYDRO_RUNOFF_UNCERTAINTY_{2030,2050}_300arcsec.*
- RiverATLAS_baseline_polyline.parquet
- RiverATLAS_{2030,2050}_polyline.parquet
- HYDRO_VIABLE_CENTROIDS_{2030,2050}.parquet

Typical run:

```bash
python p1_f_viable_hydro.py
python p1_f_viable_hydro.py --download-only
python p1_f_viable_hydro.py --process-only
```

### Suggested pre-processing order

```bash
python p1_a_ember_gem_2024.py
python p1_b_ember_2024_30_50.py
python p1_c_prep_landcover.py
python p1_d_viable_solar.py
python p1_e_viable_wind.py
python p1_f_viable_hydro.py
```

## Citation and License

- **Relevant paper:** DOI to be added on publication.
- Citation metadata: [CITATION.cff](CITATION.cff)
- License: [LICENSE](LICENSE)
