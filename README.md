# Global Electricity Supply-Demand Analysis Framework

A comprehensive geospatial pipeline for country-level electricity supply-demand analysis, renewable siting, and climate-aware resource viability.

[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Table of Contents

- [Overview](#overview)
- [Current Workflow Model](#current-workflow-model)
- [Project Structure](#project-structure)
- [Installation and Environment](#installation-and-environment)
- [Quick Start](#quick-start)
- [Script Reference](#script-reference)
- [Configuration Guide](#configuration-guide)
- [Data Requirements](#data-requirements)
- [Workflow Examples](#workflow-examples)
- [Outputs and Naming Conventions](#outputs-and-naming-conventions)
- [HPC Guide](#hpc-guide)
- [Data Schema Reference](#data-schema-reference)
- [Troubleshooting](#troubleshooting)
- [Annex A: Pre-Data Processing (p1_a to p1_f)](#annex-a-pre-data-processing-p1_a-to-p1_f)
- [Citation and License](#citation-and-license)

## Overview

This repository supports end-to-end analysis of electricity systems and renewable expansion pathways by:

1. Harmonizing national statistics and facility-level datasets.
2. Projecting 2030 and 2050 scenarios using population and policy assumptions.
3. Allocating demand spatially and connecting supply via network analysis.
4. Identifying underserved settlements and siting additional infrastructure.
5. Integrating climate-adjusted solar, wind, and hydro viability layers.
6. Exporting country and global GIS-ready outputs.

## Current Workflow Model

The workflow is scenario-aware and supports:

- Supply factor analysis at 100%, 90%, 80%, 70%, 60%.
- Single custom supply factor via --supply-factor.
- Multi-year submission mode (2024, 2030, 2050) in HPC wrapper scripts.
- Optional second supply run that auto-detects siting outputs and writes _add_v2 outputs.

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
├── combine_one_results.py
├── combine_global_results.py
├── generate_hpc_scripts.py
├── submit_all_parallel.sh
├── submit_all_parallel_siting.sh
├── submit_one_direct.sh
├── submit_one_direct_siting.sh
├── submit_workflow.sh
├── figure_scripts/
│   ├── p1_na_results_data_etl.py
│   ├── p1_na_fig12.py
│   ├── p1_na_fig34.py
│   ├── p1_na_fig34_alt1.py
│   ├── p1_na_fig56.py
│   ├── p1_na_fig7.py
│   ├── p1_na_fig8.py
│   ├── p1_z_fig_validation1.py
│   ├── p1_z_fig_validation2.py
│   └── p1_z_fig_validation3.py
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

- p1_a_ember_gem_2024.py
  - Harmonizes Ember country aggregates with GEM facility-level records.
  - Produces country-level and facility-level baselines for downstream analysis.
  - Handles country code/name mapping and fuel-type harmonization.

- p1_b_ember_2024_30_50.py
  - Builds 2030/2050 projections using UN population growth, NDC targets, and IEA assumptions.
  - Applies disaggregation logic to distribute broad renewable targets across technologies.
  - Exports projected generation/capacity tables for downstream country processing.

- p1_c_prep_landcover.py
  - Downloads ESA CCI Land Cover 2022 from CDS.
  - Converts and upscales to the 300 arcsec grid aligned with analysis outputs.
  - Produces landcover_2022_10arcsec.tif and landcover_2022_300arcsec.tif.

- p1_d_viable_solar.py
  - CMIP6 delta method for PVOUT projections.
  - Computes future PVOUT using baseline x climate delta and model ensemble mean.
  - Exports projected, uncertainty, delta, baseline, and viability-filtered outputs.

- p1_e_viable_wind.py
  - ERA5 + CMIP6 delta method for WPD projections.
  - Converts projected wind speeds to WPD and computes ensemble uncertainty.
  - Exports projected, uncertainty, delta, baseline, and viability-filtered outputs.

- p1_f_viable_hydro.py
  - Unified hydro processing in three parts:
    - Runoff delta generation from ERA5-Land + CMIP6.
    - RiverATLAS discharge projection.
    - Viable hydro centroid extraction with landcover and discharge filters.

### Country Analysis

- process_country_supply.py
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

- process_country_siting.py
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
  - Cluster wrapper for global combination run.

### Figure and ETL Scripts

Located in figure_scripts:

- p1_na_results_data_etl.py
  - Builds scenario/hazard exposure dataset used by plotting scripts.
- p1_na_fig12.py, p1_na_fig34.py, p1_na_fig34_alt1.py, p1_na_fig56.py, p1_na_fig7.py, p1_na_fig8.py
  - Figure generation scripts for results communication.
- p1_z_fig_validation1.py, p1_z_fig_validation2.py, p1_z_fig_validation3.py
  - Validation-oriented plotting scripts.

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

### Regeneration Guidance

If you change resolution or viability thresholds, re-run:

1. p1_c_prep_landcover.py if landcover grid prerequisites changed.
2. p1_d_viable_solar.py, p1_e_viable_wind.py, p1_f_viable_hydro.py.
3. Country processing scripts.
4. Combination scripts.

## Data Requirements

### Core Spatial Inputs

- bigdata_gadm/gadm_410-levels.gpkg
- bigdata_eez/eez_v12.gpkg
- bigdata_gridfinder/grid.gpkg
- bigdata_settlements_jrc/GHS_POP_*.tif
- bigdata_solar_pvout/PVOUT.tif
- bigdata_wind_atlas/gasp_flsclassnowake_100m.tif
- bigdata_hydro_atlas/RiverATLAS_Data_v10.gdb
- bigdata_solar_wind_ms/solar_all_2024q2_v1.gpkg
- bigdata_solar_wind_ms/wind_all_2024q2_v1.gpkg

### Energy and Population Inputs

- data_energy_ember/yearly_full_release_long_format*.csv
- data_energy_projections_iea/*
- data_pop_un/WPP2024_TotalPopulationBySex*.csv
- data_country_class_wb/*

### CMIP6/ERA5 Working Directories

- bigdata_solar_cmip6/downloads, extracted, outputs
- bigdata_wind_cmip6/downloads, extracted, outputs
- bigdata_hydro_cmip6/downloads, extracted, outputs
- bigdata_hydro_era5_land/downloads
- bigdata_landcover_cds/downloads, extracted, outputs

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

chmod +x submit_*.sh parallel_scripts/*.sh parallel_scripts_siting/*.sh

./submit_all_parallel.sh --run-all-years --run-all-scenarios
./submit_all_parallel_siting.sh --run-all-years --run-all-scenarios

# Optional add_v2 integration pass
./submit_all_parallel.sh --run-all-years --run-all-scenarios

sbatch submit_workflow.sh
```

## Outputs and Naming Conventions

### Scenario Folder Pattern

- outputs_per_country/parquet/{YEAR}_supply_{PCT}%/
- outputs_per_country/parquet/{YEAR}_supply_{PCT}%_add_v2/

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

## Data Schema Reference

This is a practical quick schema reference for commonly used outputs.

### centroids_{ISO3}.parquet

- geometry: point centroid
- population and demand fields for analysis year
- supply allocation and status fields

### facilities_{ISO3}.parquet

- geometry: facility or synthetic facility point
- energy type, generation/capacity metrics
- matching and allocation metadata

### grid_lines_{ISO3}.parquet

- geometry: line segments
- line type and length/distance fields

### polylines_{ISO3}.parquet

- geometry: paths connecting demand-supply via network
- source/target identifiers and distance metrics

### siting_clusters_{ISO3}.parquet

- geometry: cluster points
- cluster demand, type, viability audit fields
- split indicators for LP rebalance outputs when applicable

### siting_networks_{ISO3}.parquet

- geometry: proposed siting network lines
- cluster linkage and topology metadata

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

### p1_a_ember_gem_2024.py

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

### p1_b_ember_2024_30_50.py

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

### p1_c_prep_landcover.py

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

### p1_d_viable_solar.py

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

### p1_e_viable_wind.py

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

### p1_f_utils_hydro.py

Purpose:

- Shared helper utilities for hydro processing.
- Centralizes download, transformation, delta, and export helper functions used by p1_f_viable_hydro.py.

Typical usage:

- Imported by p1_f_viable_hydro.py (not usually run directly).

### p1_f_viable_hydro.py

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

- Citation metadata: [CITATION.cff](CITATION.cff)
- License: [LICENSE](LICENSE)
