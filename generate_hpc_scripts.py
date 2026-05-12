#!/usr/bin/env python3
"""
# ==================================================================================================
# SCRIPT PURPOSE: Country List Generation and Parallel Script Creation for HPC
# ==================================================================================================
#
# WHAT THIS CODE DOES:
# This script serves two primary functions for a large-scale geospatial data processing workflow:
#
# 1. Get Country List: It generates a definitive list of countries to be processed. It does this by:
#    a) Reading a list of all countries from the main energy projection data.
#    b) Cross-referencing this list with available administrative boundary data (GADM).
#    c) Producing a final `countries_list.txt` containing only countries that exist in BOTH datasets.
#
# 2. Create Parallel Scripts: It automatically generates shell scripts designed to run the main
#    `process_country_supply.py` analysis in parallel on a High-Performance Computing (HPC)
#    cluster that uses the SLURM workload manager.
#
# WHY THIS IS NEEDED:
# - Consistency: Ensures that the workflow only attempts to process countries for which all
#   necessary input data is available.
# - Efficiency: Processing 190 countries sequentially would take weeks. By generating
#   parallel scripts, the workload can be distributed across dozens or hundreds of CPU cores,
#   reducing the total runtime to hours.
# - Resource Optimization: The script uses a "tiered" approach, grouping countries by their
#   expected computational load (e.g., large, complex countries like China get a dedicated
#   script with maximum resources, while many small countries are batched together). This
#   ensures efficient use of cluster resources.
#
# TIER BREAKDOWN (40 total scripts for 190 countries):
#   T1: 1 country (CHN) → 1 script
#   T2: 6 large countries (USA, IND, BRA, DEU, FRA, RUS) → 6 scripts
#   T3: 10 medium-large countries (CAN, MEX, AUS, etc.) → 10 scripts
#   T4: 20 medium countries (TUR, NGA, VEN, ETH, etc.) → 10 scripts (2 per script)
#   T5: ~153 remaining countries → 13 scripts (12 per script)
# ==================================================================================================
"""
import pandas as pd
import geopandas as gpd
from pathlib import Path
import os
import config as project_config
from config import get_bigdata_path

def get_country_list():
    """
    Generates a list of countries by finding the intersection between countries present
    in the energy demand dataset and those with available administrative boundaries in GADM.
    The final list is saved to 'countries_list.txt' for use in the Snakemake workflow.
    """
    demand_file = "outputs_processed_data/p1_b_ember_2024_30_50.xlsx"
    gadm_file = os.path.join(get_bigdata_path('bigdata_gadm'), 'gadm_410-levels.gpkg')
    
    # Check if files exist
    if not Path(demand_file).exists():
        print(f"Error: Demand data file not found: {demand_file}")
        return []
    
    if not Path(gadm_file).exists():
        print(f"Error: GADM boundaries file not found: {gadm_file}")
        return []
    
    print(f"Loading country list from energy demand data: {demand_file}")
    
    try:
        # Load demand data
        demand_df = pd.read_excel(demand_file)
        
        if 'ISO3_code' not in demand_df.columns:
            print(f"Error: 'ISO3_code' column not found in {demand_file}")
            print(f"Available columns: {list(demand_df.columns)}")
            return []
        
        # Get countries from demand data
        demand_countries = demand_df['ISO3_code'].dropna().unique()
        demand_countries = set(str(c).strip() for c in demand_countries if pd.notna(c) and str(c).strip())
        
        print(f"Found {len(demand_countries)} countries in demand data")
        
        # Load GADM boundaries to check which countries exist
        print("Checking which countries have GADM boundaries...")
        admin_df = gpd.read_file(gadm_file, layer="ADM_0", columns=['GID_0'])
        gadm_countries = set(admin_df['GID_0'].dropna().unique())
        gadm_countries = {str(c).strip() for c in gadm_countries if pd.notna(c)}
        
        # Only keep countries that exist in both datasets
        valid_countries = demand_countries & gadm_countries
        missing_countries = demand_countries - gadm_countries
        
        print(f"Countries with both demand data AND boundaries: {len(valid_countries)}")
        if missing_countries:
            print(f"Countries with demand data but NO boundaries (will be skipped): {len(missing_countries)}")
            for country in sorted(list(missing_countries)[:5]):  # Show first 5
                print(f"  - {country}")
            if len(missing_countries) > 5:
                print(f"  ... and {len(missing_countries) - 5} more")
        
        # Sort for consistent ordering
        countries = sorted(list(valid_countries))
        
        print(f"\nFinal country list: {len(countries)} countries will be processed")
        for country in countries[:10]:  # Show first 10
            print(f"  {country}")
        if len(countries) > 10:
            print(f"  ... and {len(countries) - 10} more")
        
        # Save to file for Snakemake
        with open('countries_list.txt', 'w') as f:
            for country in countries:
                f.write(f"{country}\n")
        
        print(f"\nCountry list saved to countries_list.txt")
        print(f"Ready to process {len(countries)} countries in parallel!")
        
        return countries
        
    except Exception as e:
        print(f"Error processing country list: {e}")
        return []

def create_parallel_scripts(num_scripts=40, countries=None):
    """
    Creates a set of shell scripts to process countries in parallel on a SLURM-based HPC cluster.

    This function implements a tiered batching strategy to optimize resource usage. Countries are
    categorized by size and complexity, and scripts are generated with appropriate resource
    requests (memory, CPUs, time). This prevents small jobs from wasting large resource allocations
    and ensures large jobs get the resources they need.
    """
    from pathlib import Path
    
    if countries is None:
        countries = get_country_list()
    
    if not countries:
        print("No countries to process!")
        return False
    
    print(f"Creating {num_scripts} parallel scripts for {len(countries)} countries with tiered approach")
    print(f"[INFO] Cluster optimization: {num_scripts} scripts = {num_scripts} nodes maximum utilization")
    
    # Define tiers based on country size/complexity. This is determined empirically based on
    # the computational intensity (number of centroids, grid lines) of each country.
    # Total: 190 countries → 40 scripts (1 + 6 + 10 + 10 + 13 = 40)
    TIER_1 = {"CHN"}  # 1 country → 1 script. Largest, requires maximum resources. CHN = 3 days
    TIER_2 = {"USA", "IND", "BRA", "DEU", "FRA", "RUS"}  # 6 countries → 6 scripts. Large countries. USA = 20h+, IND = 6h+, others = 2-4h for one scenario.
    TIER_3 = {"CAN", "MEX", "AUS", "ARG", "KAZ", "SAU", "IDN", "IRN", "ZAF", "EGY"}  # 10 countries → 10 scripts. Medium-large countries.
    TIER_4 = {
        "TUR", "NGA", "COL", "PAK", "PER", "DZA", "VEN", "UKR", "ETH", "PHL", "MLI", "TCD", "SDN",
        "SWE", "NOR", "IRQ", "MMR", "JPN", "GHA", "GEO"
    }  # 20 countries → 10 scripts (2 per script). Medium countries.
    
    # Tier-based resource allocation. This configuration is tailored for a specific HPC cluster node specification.
    # The goal is to pack jobs efficiently onto nodes based on computational requirements.
    # Tier 5: ~156 remaining countries → 13 scripts (12 countries per script)
    # Partition strategy: Long (168h) for Tier 1-2, Medium (48h) for Tier 3, Short (12h) for Tiers 4-5
    # Cluster Spec as of 01/2025: Long nodes 40CPUs/900G (cn60,cn64), Medium nodes 40CPUs/100G, Short nodes 40CPUs/100GB
    # Check cluster spec on cluster: sinfo -N -o "%P %N %t %c %m" | sort

    TIER_CONFIG = {
#        "t1": {"max_countries_per_script": 1, "mem": "450G", "cpus": 40, "time": "168:00:00", "partition": "Long", "nodelist": "ouce-cn64"},  # CHN - dedicated node cn60 and 64 - Long, 40 cpus, max 900G
        "t1": {"max_countries_per_script": 1, "mem": "95G", "cpus": 40, "time": "168:00:00", "partition": "Long"},  # CHN 
        "t2": {"max_countries_per_script": 1, "mem": "95G", "cpus": 40, "time": "168:00:00", "partition": "Long"},     # USA, IND, BRA, DEU, FRA - Long partition (7 days)
        "t3": {"max_countries_per_script": 1, "mem": "95G", "cpus": 40, "time": "48:00:00", "partition": "Medium"},      # CAN, MEX, RUS, AUS, etc. - Medium partition (48h). cn62 lacks NFS mount for shared bigdata.
        "t4": {"max_countries_per_script": 2, "mem": "25G", "cpus": 40, "time": "12:00:00", "partition": "Short"},      # TUR, NGA, COL, etc. - two countries per script
        "t5": {"max_countries_per_script": 12, "mem": "25G", "cpus": 40, "time": "12:00:00", "partition": "Short"}     # All others - 12 countries per script
    }
    
    

    def get_tier(country):
        if country in TIER_1:
            return "t1"
        elif country in TIER_2:
            return "t2"
        elif country in TIER_3:
            return "t3"
        elif country in TIER_4:
            return "t4"
        else:
            return "t5"
    
    # Sort countries by tier (biggest first)
    countries_by_tier = {
        "t1": [c for c in countries if get_tier(c) == "t1"],
        "t2": [c for c in countries if get_tier(c) == "t2"],
        "t3": [c for c in countries if get_tier(c) == "t3"],
        "t4": [c for c in countries if get_tier(c) == "t4"],
        "t5": [c for c in countries if get_tier(c) == "t5"]
    }
    
    print("Countries by tier:")
    for tier, tier_countries in countries_by_tier.items():
        config = TIER_CONFIG[tier]
        print(f"  {tier.upper()}: {len(tier_countries)} countries (max {config['max_countries_per_script']} per script)")
        if tier_countries:
            print(f"    Examples: {', '.join(tier_countries[:3])}")
    
    # Create directories
    scripts_dir = Path("parallel_scripts")
    scripts_dir.mkdir(exist_ok=True)
    
    # Create batches with tiered approach.
    # This logic groups countries into batches that will become individual shell scripts.
    # It starts with the biggest countries (Tier 1) and works its way down, ensuring
    # that the most resource-intensive jobs are scheduled first.
    all_batches = []
    script_counter = 1
    
    # Track which countries have been assigned
    assigned_countries = set()
    
    # Process each tier - start with largest countries first
    for tier in ["t1", "t2", "t3", "t4", "t5"]:
        tier_countries = countries_by_tier[tier]
        if not tier_countries:
            continue
            
        config = TIER_CONFIG[tier]
        max_per_script = config["max_countries_per_script"]
        
        # For Tier 5, distribute evenly to avoid overloading the last script
        if tier == "t5" and len(tier_countries) > max_per_script:
            # Calculate optimal batch size to distribute evenly
            scripts_available = num_scripts - script_counter + 1
            num_batches = min(scripts_available, (len(tier_countries) + max_per_script - 1) // max_per_script)
            
            if num_batches > 0:
                # Distribute countries as evenly as possible
                countries_per_batch = len(tier_countries) // num_batches
                extra_countries = len(tier_countries) % num_batches
                
                tier_countries_copy = tier_countries.copy()
                for batch_idx in range(num_batches):
                    # First 'extra_countries' batches get one extra country
                    batch_size = countries_per_batch + (1 if batch_idx < extra_countries else 0)
                    batch = tier_countries_copy[:batch_size]
                    tier_countries_copy = tier_countries_copy[batch_size:]
                    
                    batch_info = {
                        "countries": batch,
                        "tier": tier,
                        "config": config,
                        "script_num": script_counter
                    }
                    all_batches.append(batch_info)
                    assigned_countries.update(batch)
                    script_counter += 1
                    
                    if script_counter > num_scripts:
                        break
        else:
            # For other tiers, use standard batching
            for i in range(0, len(tier_countries), max_per_script):
                batch = tier_countries[i:i + max_per_script]
                batch_info = {
                    "countries": batch,
                    "tier": tier,
                    "config": config,
                    "script_num": script_counter
                }
                all_batches.append(batch_info)
                assigned_countries.update(batch)
                script_counter += 1
                
                # Stop if we reach the max number of scripts
                if script_counter > num_scripts:
                    # If we have too many batches, combine remaining into last script
                    remaining_countries = tier_countries[i + max_per_script:]
                    if remaining_countries:
                        all_batches[-1]["countries"].extend(remaining_countries)
                        assigned_countries.update(remaining_countries)
                    break
        
        if script_counter > num_scripts:
            break
    
    # Check if any countries were not assigned
    unassigned_countries = set(countries) - assigned_countries
    if unassigned_countries:
        print(f"\n[WARNING] {len(unassigned_countries)} countries not assigned in first {num_scripts} scripts")
        print(f"   Creating additional scripts ({num_scripts+1}, {num_scripts+2}, ...) for unassigned countries")
        print(f"   Unassigned countries: {', '.join(sorted(list(unassigned_countries))[:20])}")
        if len(unassigned_countries) > 20:
            print(f"   ... and {len(unassigned_countries) - 20} more")
        
        # Create additional scripts for unassigned countries (using Tier 5 config)
        config = TIER_CONFIG["t5"]
        max_per_script = config["max_countries_per_script"]
        unassigned_list = sorted(unassigned_countries)
        
        for i in range(0, len(unassigned_list), max_per_script):
            batch = unassigned_list[i:i + max_per_script]
            batch_info = {
                "countries": batch,
                "tier": "t5",
                "config": config,
                "script_num": script_counter
            }
            all_batches.append(batch_info)
            script_counter += 1
    
    print(f"\nCreated {len(all_batches)} script batches:")
    
    # Create shell scripts
    for i, batch_info in enumerate(all_batches, 1):
        batch = batch_info["countries"]
        tier = batch_info["tier"]
        config = batch_info["config"]
        
        # Build optional nodelist directive
        nodelist_directive = ""
        if "nodelist" in config and config["nodelist"]:
            nodelist_directive = f"\n#SBATCH --nodelist={config['nodelist']}"
        if "exclude" in config and config["exclude"]:
            nodelist_directive += f"\n#SBATCH --exclude={config['exclude']}"
        
        # The script content includes SLURM directives (#SBATCH) which request the necessary
        # resources (time, memory, CPUs) from the cluster scheduler.
        script_content = f"""#!/bin/bash --login
#SBATCH --job-name=p{i:02d}_{tier}
#SBATCH --partition={config["partition"]}
#SBATCH --time={config["time"]}
#SBATCH --mem={config["mem"]}
#SBATCH --ntasks=1
#SBATCH --nodes=1
#SBATCH --cpus-per-task={config["cpus"]}{nodelist_directive}
#SBATCH --output=outputs_per_country/logs/parallel_{i:02d}_%j.out
#SBATCH --error=outputs_per_country/logs/parallel_{i:02d}_%j.err
#SBATCH --mail-type=END,FAIL

set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

echo "[INFO] Starting parallel script {i}/{len(all_batches)} ({tier.upper()}) at $(date)"
echo "[INFO] Processing {len(batch)} countries in this batch: {', '.join(batch)}"
echo "[INFO] Tier: {tier.upper()} | Memory: {config['mem']} | CPUs: {config['cpus']} | Time: {config['time']}"

# --- directories ---
mkdir -p outputs_per_country/logs outputs_global

# --- Conda bootstrap ---
export PATH=/soge-home/users/lina4376/miniconda3/bin:$PATH
source /soge-home/users/lina4376/miniconda3/etc/profile.d/conda.sh

conda --version
conda activate p1_etl || true

# Use the env's absolute python path to avoid activation issues in batch shells
PY=/soge-home/users/lina4376/miniconda3/envs/p1_etl/bin/python
echo "[INFO] Using Python: $PY"
$PY -c 'import sys; print(sys.executable)'

# Check scenario flags (passed via sbatch --export)
# Use ${{VAR:-}} syntax to avoid 'unbound variable' errors with set -u
SCENARIO_FLAG=""
if [ -n "${{SUPPLY_FACTOR:-}}" ]; then
    SCENARIO_FLAG="--supply-factor $SUPPLY_FACTOR"
    echo "[INFO] Running single scenario: ${{SUPPLY_FACTOR}} (supply factor)"
elif [ "${{RUN_ALL_SCENARIOS:-}}" = "1" ]; then
    SCENARIO_FLAG="--run-all-scenarios"
    echo "[INFO] Running ALL scenarios (100%, 90%, 80%, 70%, 60%)"
fi

# Process countries in this batch
"""
        
        # Add country processing commands
        # Each script will call `process_country_supply.py` for each country in its batch.
        # Includes retry logic (up to 3 attempts) to handle transient NFS mount delays.
        for ci, country in enumerate(batch):
            script_content += f"""
echo "[INFO] Processing {country} ({get_tier(country).upper()})..."
MAX_RETRIES=3
for ATTEMPT in $(seq 1 $MAX_RETRIES); do
    if $PY process_country_supply.py {country} $SCENARIO_FLAG --output-dir outputs_per_country; then
        echo "[SUCCESS] {country} completed (attempt $ATTEMPT)"
        break
    else
        if [ "$ATTEMPT" -lt "$MAX_RETRIES" ]; then
            echo "[WARN] {country} failed on attempt $ATTEMPT/$MAX_RETRIES - retrying in 10s..."
            sleep 10
        else
            echo "[ERROR] {country} failed after $MAX_RETRIES attempts"
        fi
    fi
done
"""
            if ci < len(batch) - 1:
                script_content += """echo "[INFO] Pausing 5s before next country..."
sleep 5
"""
        
        script_content += f"""
echo "[INFO] Batch {i}/{len(all_batches)} ({tier.upper()}) completed at $(date)"
"""
        
        # Write script file
        script_file = scripts_dir / f"submit_parallel_{i:02d}.sh"
        script_file.write_text(script_content, encoding='utf-8', newline='\n')
        script_file.chmod(0o755)
        
        print(f"  Script {i:02d}: {len(batch)} countries ({tier.upper()}) - {', '.join(batch)}")
    
    # Create master submission script
    # This script submits all jobs immediately and returns to prompt
    master_script = f"""#!/bin/bash
# Submit all parallel jobs immediately (SLURM will queue them automatically)
# Usage: ./submit_all_parallel.sh [--run-all-years] [--run-all-scenarios] [--supply-factor <value>]

    SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
    cd "$SCRIPT_DIR"

# --- Parse arguments ---
RUN_ALL_SCENARIOS=""
RUN_ALL_YEARS=""
SUPPLY_FACTOR=""
SBATCH_EXPORT=""

# Resolve model year from config.py (override with ANALYSIS_YEAR env var if set)
ANALYSIS_YEAR="${{ANALYSIS_YEAR:-$(python - <<'PY'
from config import ANALYSIS_YEAR
print(ANALYSIS_YEAR)
PY
)}}"

while [ $# -gt 0 ]; do
    case $1 in
        --run-all-scenarios)
            RUN_ALL_SCENARIOS="1"
            shift
            ;;
        --run-all-years)
            RUN_ALL_YEARS="1"
            shift
            ;;
        --supply-factor)
            if [ -z "$2" ] || [[ "$2" == --* ]]; then
                echo "Error: --supply-factor requires a value (e.g., 0.9)"
                exit 1
            fi
            SUPPLY_FACTOR="$2"
            shift 2
            ;;
        -*)
            echo "Unknown option: $1"
            echo "Usage: $0 [--run-all-years] [--run-all-scenarios] [--supply-factor <value>]"
            exit 1
            ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: $0 [--run-all-years] [--run-all-scenarios] [--supply-factor <value>]"
            exit 1
            ;;
    esac
done

# Convenience mode: submit 2024, 2030, 2050 in one command.
if [ -n "$RUN_ALL_YEARS" ]; then
    echo "[INFO] Running all years (2024, 2030, 2050)"
    for YEAR in 2024 2030 2050; do
        echo ""
        echo "[INFO] Submitting year: $YEAR"
        if [ -n "$SUPPLY_FACTOR" ]; then
            ANALYSIS_YEAR="$YEAR" "$0" --supply-factor "$SUPPLY_FACTOR"
        elif [ -n "$RUN_ALL_SCENARIOS" ]; then
            ANALYSIS_YEAR="$YEAR" "$0" --run-all-scenarios
        else
            ANALYSIS_YEAR="$YEAR" "$0"
        fi
    done
    exit 0
fi

# Build SBATCH_EXPORT based on flags
if [ -n "$SUPPLY_FACTOR" ]; then
    SBATCH_EXPORT="--export=ALL,SUPPLY_FACTOR=$SUPPLY_FACTOR"
    # Convert supply factor to percentage (e.g., 0.9 -> 90)
    SCENARIO_PCT=$(echo "$SUPPLY_FACTOR * 100" | bc | cut -d. -f1)
    SCENARIO="${{ANALYSIS_YEAR}}_supply_${{SCENARIO_PCT}}%"
    echo "[INFO] Running single scenario: $SUPPLY_FACTOR (supply factor ${{SCENARIO_PCT}}%)"
elif [ -n "$RUN_ALL_SCENARIOS" ]; then
    SBATCH_EXPORT="--export=ALL,RUN_ALL_SCENARIOS=1"
    SCENARIO="all_scenarios"
    echo "[INFO] Running ALL scenarios (100%, 90%, 80%, 70%, 60%)"
else
    SCENARIO="${{ANALYSIS_YEAR}}_supply_100%"
    echo "[INFO] Running default scenario: 100%"
fi

# Create scenario-specific log directory (route to add_v2 logs on second supply run)
if [ -z "$RUN_ALL_SCENARIOS" ]; then
    YEAR_PART="${{SCENARIO%%_supply_*}}"
    PCT_PART="${{SCENARIO##*_supply_}}"
    if compgen -G "outputs_per_country/parquet/${{SCENARIO}}/${{YEAR_PART}}_siting_${{PCT_PART}}_*.xlsx" > /dev/null; then
        LOG_DIR="outputs_per_country/parquet/${{SCENARIO}}_add_v2/logs"
        echo "[INFO] Detected siting outputs for ${{SCENARIO}}; using add_v2 log directory"
    else
        LOG_DIR="outputs_per_country/parquet/${{SCENARIO}}/logs"
    fi
else
    if compgen -G "outputs_per_country/parquet/${{ANALYSIS_YEAR}}_supply_100%/${{ANALYSIS_YEAR}}_siting_100%_*.xlsx" > /dev/null || \
       compgen -G "outputs_per_country/parquet/${{ANALYSIS_YEAR}}_supply_90%/${{ANALYSIS_YEAR}}_siting_90%_*.xlsx" > /dev/null || \
       compgen -G "outputs_per_country/parquet/${{ANALYSIS_YEAR}}_supply_80%/${{ANALYSIS_YEAR}}_siting_80%_*.xlsx" > /dev/null || \
       compgen -G "outputs_per_country/parquet/${{ANALYSIS_YEAR}}_supply_70%/${{ANALYSIS_YEAR}}_siting_70%_*.xlsx" > /dev/null || \
       compgen -G "outputs_per_country/parquet/${{ANALYSIS_YEAR}}_supply_60%/${{ANALYSIS_YEAR}}_siting_60%_*.xlsx" > /dev/null; then
        LOG_DIR="outputs_per_country/parquet/logs_run_all_scenarios_add_v2"
        echo "[INFO] Detected siting outputs for run-all scenarios; using add_v2 log directory"
    else
        LOG_DIR="outputs_per_country/parquet/logs_run_all_scenarios"
    fi
fi
mkdir -p "$LOG_DIR"
echo "[INFO] Logs will be saved to: ${{LOG_DIR}}/"

# --- Conda bootstrap ---
export PATH=/soge-home/users/lina4376/miniconda3/bin:$PATH
source /soge-home/users/lina4376/miniconda3/etc/profile.d/conda.sh

conda activate p1_etl

echo "[INFO] Submitting {len(all_batches)} parallel jobs..."
echo "[INFO] SLURM will automatically queue and manage job execution (max 8 running at once)"
if [ -n "$RUN_ALL_SCENARIOS" ]; then
    echo "[INFO] Each job will run 5 scenarios (100%, 90%, 80%, 70%, 60%)"
fi
echo ""

# Submit all jobs
for i in {{01..{len(all_batches):02d}}}; do
    echo "[$(date +%H:%M:%S)] Submitting job $i..."
    sbatch --output="${{LOG_DIR}}/parallel_${{i}}_%j.out" \\
           --error="${{LOG_DIR}}/parallel_${{i}}_%j.err" \\
           $SBATCH_EXPORT parallel_scripts/submit_parallel_${{i}}.sh
    sleep 1  # Small delay to avoid overwhelming scheduler
done

echo ""
echo "[INFO] All {len(all_batches)} jobs submitted!"
echo ""
echo "Monitor with:"
echo "  squeue -u \\$USER"
echo "  watch -n 60 'squeue -u \\$USER'"
echo ""
echo "Check completion:"
echo "  find outputs_per_country/parquet -name '*.parquet' | wc -l"
echo ""
echo "Resource allocation summary (tiered partition strategy):"
echo "  Tier 1 (CHN):                   1 country/script  | Long partition (168h)        | {TIER_CONFIG['t1']['mem']}, {TIER_CONFIG['t1']['cpus']} CPUs"
echo "  Tier 2 (USA, IND, BRA, etc.):   1 country/script  | Long partition (168h)        | {TIER_CONFIG['t2']['mem']}, {TIER_CONFIG['t2']['cpus']} CPUs"
echo "  Tier 3 (CAN, MEX, AUS, etc.):   1 country/script  | Medium partition (48h)       | {TIER_CONFIG['t3']['mem']}, {TIER_CONFIG['t3']['cpus']} CPUs"
echo "  Tier 4 (TUR, NGA, COL, etc.):   2 countries/script | Short partition (12h)        | {TIER_CONFIG['t4']['mem']}, {TIER_CONFIG['t4']['cpus']} CPUs"
echo "  Tier 5 (all others):           12 countries/script | Short partition (12h)        | {TIER_CONFIG['t5']['mem']}, {TIER_CONFIG['t5']['cpus']} CPUs"
"""
    
    master_file = Path("submit_all_parallel.sh")
    master_file.write_text(master_script, encoding='utf-8', newline='\n')
    master_file.chmod(0o755)
    
    print(f"\nCreated {master_file} to submit all parallel jobs")
    
    # Create combination workflow script
    workflow_script = """#!/bin/bash --login
#SBATCH --job-name=combine_global
#SBATCH --partition=Medium
#SBATCH --time=12:00:00
#SBATCH --mem=64G
#SBATCH --ntasks=1
#SBATCH --nodes=1
#SBATCH --cpus-per-task=40
#SBATCH --output=outputs_per_country/logs/workflow_%j.out
#SBATCH --error=outputs_per_country/logs/workflow_%j.err
#SBATCH --mail-type=END,FAIL

set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

echo "[INFO] Starting global results combination at $(date)"
echo "[INFO] Memory: 64GB | CPUs: 40 | Time limit: 12h"

# --- directories ---
mkdir -p outputs_per_country/logs outputs_global

# --- Conda bootstrap ---
export PATH=/soge-home/users/lina4376/miniconda3/bin:$PATH
source /soge-home/users/lina4376/miniconda3/etc/profile.d/conda.sh

conda --version
conda activate p1_etl || true

# Use the env's absolute python path
PY=/soge-home/users/lina4376/miniconda3/envs/p1_etl/bin/python
echo "[INFO] Using Python: $PY"
$PY -c 'import sys; print(sys.executable)'

echo "[INFO] Combining all country results into global outputs..."
echo "[INFO] Auto-detecting scenarios from outputs_per_country/parquet/"

# Run combination script (auto-detects scenarios)
$PY combine_global_results.py --input-dir outputs_per_country

if [ $? -eq 0 ]; then
    echo "[SUCCESS] Global results combination completed at $(date)"
    echo "[INFO] Output files:"
    ls -lh outputs_global/*.gpkg
else
    echo "[ERROR] Global results combination failed at $(date)"
    exit 1
fi
"""
    
    workflow_file = Path("submit_workflow.sh")
    workflow_file.write_text(workflow_script, encoding='utf-8', newline='\n')
    workflow_file.chmod(0o755)
    
    print(f"Created {workflow_file} for combining results")
    
    # Create submit_one_direct.sh for running any single country directly
    direct_script = f"""#!/bin/bash --login
# ==============================================================================
# Run any country with scenario options - useful for filling gaps or re-running
# Usage: ./submit_one_direct.sh <ISO3> [--run-all-scenarios] [--supply-factor <value>]
#        ./submit_one_direct.sh <ISO3> [--tier <1-5>] [options]
#
# Examples:
#   ./submit_one_direct.sh KEN                      # Single country, 100% scenario
#   ./submit_one_direct.sh KEN --run-all-scenarios  # Single country, all 5 scenarios
#   ./submit_one_direct.sh KEN --supply-factor 0.9  # Single country, 90% scenario
#   ./submit_one_direct.sh CHN --tier 1             # Use Tier 1 resources (high memory)
#   ./submit_one_direct.sh USA --tier 2             # Use Tier 2 resources
# ==============================================================================

# --- Parse arguments ---
RUN_ALL_SCENARIOS=""
SUPPLY_FACTOR=""
SBATCH_EXPORT=""
ISO3=""
TIER=""

while [ $# -gt 0 ]; do
    case $1 in
        --run-all-scenarios)
            RUN_ALL_SCENARIOS="1"
            shift
            ;;
        --supply-factor)
            if [ -z "$2" ] || [[ "$2" == --* ]]; then
                echo "Error: --supply-factor requires a value (e.g., 0.9)"
                exit 1
            fi
            SUPPLY_FACTOR="$2"
            shift 2
            ;;
        --tier)
            if [ -z "$2" ] || [[ "$2" == --* ]]; then
                echo "Error: --tier requires a value (1-5)"
                exit 1
            fi
            TIER="$2"
            shift 2
            ;;
        -*)
            echo "Unknown option: $1"
            echo "Usage: $0 <ISO3> [--run-all-scenarios] [--supply-factor <value>] [--tier <1-5>]"
            exit 1
            ;;
        *)
            if [ -z "$ISO3" ]; then
                ISO3="$1"
            else
                echo "Unknown argument: $1"
                echo "Usage: $0 <ISO3> [--run-all-scenarios] [--supply-factor <value>] [--tier <1-5>]"
                exit 1
            fi
            shift
            ;;
    esac
done

# Validate ISO3
if [ -z "$ISO3" ]; then
    echo "Usage: $0 <ISO3> [--run-all-scenarios] [--supply-factor <value>] [--tier <1-5>]"
    echo ""
    echo "Examples:"
    echo "  $0 KEN                      # Single country, 100% scenario, auto-detect tier"
    echo "  $0 KEN --run-all-scenarios  # Single country, all 5 scenarios"
    echo "  $0 KEN --supply-factor 0.9  # Single country, 90% scenario"
    echo "  $0 CHN --tier 1             # Use Tier 1 resources ({TIER_CONFIG['t1']['mem']} memory)"
    echo "  $0 USA --tier 2             # Use Tier 2 resources (95G memory)"
    echo ""
    echo "Tier resources:"
    echo "  T1: {TIER_CONFIG['t1']['mem']}, {TIER_CONFIG['t1']['cpus']} CPUs, {TIER_CONFIG['t1']['time']} ({TIER_CONFIG['t1']['partition']})  - CHN"
    echo "  T2: {TIER_CONFIG['t2']['mem']}, {TIER_CONFIG['t2']['cpus']} CPUs, {TIER_CONFIG['t2']['time']} ({TIER_CONFIG['t2']['partition']})   - USA, IND, BRA, DEU, FRA, RUS"
    echo "  T3: {TIER_CONFIG['t3']['mem']}, {TIER_CONFIG['t3']['cpus']} CPUs, {TIER_CONFIG['t3']['time']} ({TIER_CONFIG['t3']['partition']})  - CAN, MEX, RUS, AUS, etc."
    echo "  T4: {TIER_CONFIG['t4']['mem']}, {TIER_CONFIG['t4']['cpus']} CPUs, {TIER_CONFIG['t4']['time']} ({TIER_CONFIG['t4']['partition']})   - TUR, NGA, VEN, ETH, etc."
    echo "  T5: {TIER_CONFIG['t5']['mem']}, {TIER_CONFIG['t5']['cpus']} CPUs, {TIER_CONFIG['t5']['time']} ({TIER_CONFIG['t5']['partition']})   - All others (default)"
    exit 1
fi

# Convert to uppercase
ISO3=$(echo "$ISO3" | tr '[:lower:]' '[:upper:]')

# --- Auto-detect tier based on country if not specified ---
TIER_1="{' '.join(TIER_1)}"
TIER_2="{' '.join(TIER_2)}"
TIER_3="{' '.join(TIER_3)}"
TIER_4="{' '.join(TIER_4)}"

if [ -z "$TIER" ]; then
    if [[ " $TIER_1 " =~ " $ISO3 " ]]; then
        TIER="1"
    elif [[ " $TIER_2 " =~ " $ISO3 " ]]; then
        TIER="2"
    elif [[ " $TIER_3 " =~ " $ISO3 " ]]; then
        TIER="3"
    elif [[ " $TIER_4 " =~ " $ISO3 " ]]; then
        TIER="4"
    else
        TIER="5"
    fi
    echo "[INFO] Auto-detected tier: T${{TIER}} for ${{ISO3}}"
fi

# --- Set SLURM resources based on tier ---
case $TIER in
    1)
        PARTITION="{TIER_CONFIG['t1']['partition']}"
        TIME="{TIER_CONFIG['t1']['time']}"
        MEM="{TIER_CONFIG['t1']['mem']}"
        CPUS="{TIER_CONFIG['t1']['cpus']}"
        ;;
    2)
        PARTITION="{TIER_CONFIG['t2']['partition']}"
        TIME="{TIER_CONFIG['t2']['time']}"
        MEM="{TIER_CONFIG['t2']['mem']}"
        CPUS="{TIER_CONFIG['t2']['cpus']}"
        ;;
    3)
        PARTITION="{TIER_CONFIG['t3']['partition']}"
        TIME="{TIER_CONFIG['t3']['time']}"
        MEM="{TIER_CONFIG['t3']['mem']}"
        CPUS="{TIER_CONFIG['t3']['cpus']}"
        ;;
    4)
        PARTITION="{TIER_CONFIG['t4']['partition']}"
        TIME="{TIER_CONFIG['t4']['time']}"
        MEM="{TIER_CONFIG['t4']['mem']}"
        CPUS="{TIER_CONFIG['t4']['cpus']}"
        ;;
    5|*)
        PARTITION="{TIER_CONFIG['t5']['partition']}"
        TIME="{TIER_CONFIG['t5']['time']}"
        MEM="{TIER_CONFIG['t5']['mem']}"
        CPUS="{TIER_CONFIG['t5']['cpus']}"
        ;;
esac

echo "[INFO] Resources: Partition=$PARTITION, Time=$TIME, Memory=$MEM, CPUs=$CPUS"

# Resolve model year from config.py (override with ANALYSIS_YEAR env var if set)
ANALYSIS_YEAR="${{ANALYSIS_YEAR:-$(python - <<'PY'
from config import ANALYSIS_YEAR
print(ANALYSIS_YEAR)
PY
)}}"

# --- Determine scenario flag and log directory ---
if [ -n "$SUPPLY_FACTOR" ]; then
    SCENARIO_PCT=$(echo "$SUPPLY_FACTOR * 100" | bc | cut -d. -f1)
    LOG_DIR="outputs_per_country/parquet/${{ANALYSIS_YEAR}}_supply_${{SCENARIO_PCT}}%/logs"
    SCENARIO_DESC="supply factor ${{SCENARIO_PCT}}%"
    SBATCH_EXPORT="--export=ALL,SUPPLY_FACTOR=$SUPPLY_FACTOR"
elif [ -n "$RUN_ALL_SCENARIOS" ]; then
    LOG_DIR="outputs_per_country/parquet/logs_run_all_scenarios"
    SCENARIO_DESC="ALL scenarios (100%, 90%, 80%, 70%, 60%)"
    SBATCH_EXPORT="--export=ALL,RUN_ALL_SCENARIOS=1"
else
    LOG_DIR="outputs_per_country/parquet/${{ANALYSIS_YEAR}}_supply_100%/logs"
    SCENARIO_DESC="100% (default)"
    SBATCH_EXPORT=""
fi

mkdir -p "$LOG_DIR"

echo "[INFO] Country: ${{ISO3}}"
echo "[INFO] Scenario: ${{SCENARIO_DESC}}"
echo "[INFO] Logs: ${{LOG_DIR}}/"

# --- Create temporary SLURM script ---
TEMP_SCRIPT=$(mktemp /tmp/submit_${{ISO3}}_XXXXXX.sh)

cat > "$TEMP_SCRIPT" << 'HEREDOC_HEADER'
#!/bin/bash --login
#SBATCH --mail-type=END,FAIL

set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

HEREDOC_HEADER

cat >> "$TEMP_SCRIPT" << HEREDOC_BODY
echo "[INFO] Starting ${{ISO3}} (T${{TIER}}) at \\$(date)"
echo "[INFO] Resources: ${{PARTITION}} partition, ${{MEM}} memory, ${{CPUS}} CPUs, ${{TIME}} time limit"

# --- directories ---
mkdir -p outputs_per_country/logs outputs_global

# --- Conda bootstrap ---
export PATH=/soge-home/users/lina4376/miniconda3/bin:\\$PATH
source /soge-home/users/lina4376/miniconda3/etc/profile.d/conda.sh

conda --version
conda activate p1_etl || true

# Use the env's absolute python path to avoid activation issues in batch shells
PY=/soge-home/users/lina4376/miniconda3/envs/p1_etl/bin/python
echo "[INFO] Using Python: \\$PY"
\\$PY -c 'import sys; print(sys.executable)'

# Check scenario flags (passed via sbatch --export)
SCENARIO_FLAG=""
if [ -n "\\${{SUPPLY_FACTOR:-}}" ]; then
    SCENARIO_FLAG="--supply-factor \\$SUPPLY_FACTOR"
    echo "[INFO] Running single scenario: \\${{SUPPLY_FACTOR}} (supply factor)"
elif [ "\\${{RUN_ALL_SCENARIOS:-}}" = "1" ]; then
    SCENARIO_FLAG="--run-all-scenarios"
    echo "[INFO] Running ALL scenarios (100%, 90%, 80%, 70%, 60%)"
fi

# Process country (with retry for transient NFS issues)
echo "[INFO] Processing ${{ISO3}} (T${{TIER}})..."
MAX_RETRIES=3
for ATTEMPT in \\$(seq 1 \\$MAX_RETRIES); do
    if \\$PY process_country_supply.py ${{ISO3}} \\$SCENARIO_FLAG --output-dir outputs_per_country; then
        echo "[SUCCESS] ${{ISO3}} completed at \\$(date) (attempt \\$ATTEMPT)"
        break
    else
        if [ "\\$ATTEMPT" -lt "\\$MAX_RETRIES" ]; then
            echo "[WARN] ${{ISO3}} failed on attempt \\$ATTEMPT/\\$MAX_RETRIES - retrying in 10s..."
            sleep 10
        else
            echo "[ERROR] ${{ISO3}} failed after \\$MAX_RETRIES attempts at \\$(date)"
            exit 1
        fi
    fi
done
HEREDOC_BODY

# --- Submit the job ---
echo "[INFO] Submitting ${{ISO3}}..."
sbatch --job-name="d_${{ISO3}}" \\
       --partition="$PARTITION" \\
       --exclude=ouce-cn62 \\
       --time="$TIME" \\
       --mem="$MEM" \\
       --ntasks=1 \\
       --nodes=1 \\
       --cpus-per-task="$CPUS" \\
       --output="${{LOG_DIR}}/${{ISO3}}_%j.out" \\
       --error="${{LOG_DIR}}/${{ISO3}}_%j.err" \\
       $SBATCH_EXPORT \\
       "$TEMP_SCRIPT"

# Clean up temp script after a delay (give sbatch time to read it)
(sleep 5 && rm -f "$TEMP_SCRIPT") &

echo ""
echo "Monitor with:"
echo "  squeue -u \\$USER"
echo "  tail -f ${{LOG_DIR}}/${{ISO3}}_*.out"
"""
    
    direct_file = Path("submit_one_direct.sh")
    direct_file.write_text(direct_script, encoding='utf-8', newline='\n')
    direct_file.chmod(0o755)
    
    print(f"Created {direct_file} for submitting any single country directly")
    
    print(f"\nTotal resource allocation:")
    
    # Calculate total resources
    total_mem = sum(int(batch_info["config"]["mem"].replace("G", "")) for batch_info in all_batches)
    total_cpus = sum(batch_info["config"]["cpus"] for batch_info in all_batches)
    
    print(f"  Total Memory: {total_mem}GB")
    print(f"  Total CPUs: {total_cpus}")
    print(f"  Scripts: {len(all_batches)}")
    
    # Verify all countries are covered
    total_countries_in_batches = sum(len(batch["countries"]) for batch in all_batches)
    unique_countries_in_batches = set()
    for batch in all_batches:
        unique_countries_in_batches.update(batch["countries"])
    
    print(f"\n[INFO] Coverage verification:")
    print(f"  Total countries to process: {len(countries)}")
    print(f"  Countries in batches: {total_countries_in_batches}")
    print(f"  Unique countries in batches: {len(unique_countries_in_batches)}")
    
    if len(unique_countries_in_batches) == len(countries):
        print(f"  [OK] All countries covered!")
    else:
        missing = set(countries) - unique_countries_in_batches
        print(f"  [WARNING] {len(missing)} countries NOT covered!")
        print(f"     Missing: {', '.join(sorted(list(missing))[:10])}")
    
    return True

def create_parallel_siting_scripts(num_scripts=40, countries=None):
    """
    Creates parallel scripts for siting analysis (process_country_siting.py).
    Uses simplified tiering since siting analysis is less resource-intensive.
    """
    from pathlib import Path
    
    if countries is None:
        countries = get_country_list()
    
    if not countries:
        print("No countries to process!")
        return False
    
    print(f"Creating {num_scripts} parallel siting scripts for {len(countries)} countries")
    
    # Simplified tier config for siting (less resource intensive)
    SITING_TIER_CONFIG = {
        "t1": {"max_countries_per_script": 1, "mem": "25G", "cpus": 40, "time": "12:00:00", "partition": "Short"},   # CHN, USA
        "t2": {"max_countries_per_script": 2, "mem": "25G", "cpus": 40, "time": "12:00:00", "partition": "Short"},   # IND, CAN, MEX, etc.
        "t3": {"max_countries_per_script": 11, "mem": "25G", "cpus": 40, "time": "12:00:00", "partition": "Short"}    # All others
    }

    TIER_1 = {"CHN", "USA", "IND"}
    TIER_2 = {"CAN", "MEX", "RUS", "BRA", "AUS", "ARG", "KAZ", "SAU", "IDN"}
    
    def get_tier(country):
        if country in TIER_1:
            return "t1"
        elif country in TIER_2:
            return "t2"
        else:
            return "t3"
    
    # Sort countries by tier
    countries_by_tier = {
        "t1": [c for c in countries if get_tier(c) == "t1"],
        "t2": [c for c in countries if get_tier(c) == "t2"],
        "t3": [c for c in countries if get_tier(c) == "t3"]
    }
    
    print("Countries by tier (siting):")
    for tier, tier_countries in countries_by_tier.items():
        config = SITING_TIER_CONFIG[tier]
        print(f"  {tier.upper()}: {len(tier_countries)} countries (max {config['max_countries_per_script']} per script)")
        if tier_countries:
            print(f"    Examples: {', '.join(tier_countries[:3])}")
    
    # Create directories
    scripts_dir = Path("parallel_scripts_siting")
    scripts_dir.mkdir(exist_ok=True)
    
    # Create batches
    all_batches = []
    script_counter = 1
    
    # Track which countries have been assigned
    assigned_countries = set()
    
    for tier in ["t1", "t2", "t3"]:
        tier_countries = countries_by_tier[tier]
        if not tier_countries:
            continue
            
        config = SITING_TIER_CONFIG[tier]
        max_per_script = config["max_countries_per_script"]
        
        # For Tier 3, distribute evenly
        if tier == "t3" and len(tier_countries) > max_per_script:
            scripts_available = num_scripts - script_counter + 1
            num_batches = min(scripts_available, (len(tier_countries) + max_per_script - 1) // max_per_script)
            
            if num_batches > 0:
                countries_per_batch = len(tier_countries) // num_batches
                extra_countries = len(tier_countries) % num_batches
                
                tier_countries_copy = tier_countries.copy()
                for batch_idx in range(num_batches):
                    batch_size = countries_per_batch + (1 if batch_idx < extra_countries else 0)
                    batch = tier_countries_copy[:batch_size]
                    tier_countries_copy = tier_countries_copy[batch_size:]
                    
                    batch_info = {
                        "countries": batch,
                        "tier": tier,
                        "config": config,
                        "script_num": script_counter
                    }
                    all_batches.append(batch_info)
                    assigned_countries.update(batch)
                    script_counter += 1
                    
                    if script_counter > num_scripts:
                        break
        else:
            for i in range(0, len(tier_countries), max_per_script):
                batch = tier_countries[i:i + max_per_script]
                batch_info = {
                    "countries": batch,
                    "tier": tier,
                    "config": config,
                    "script_num": script_counter
                }
                all_batches.append(batch_info)
                assigned_countries.update(batch)
                script_counter += 1
                
                if script_counter > num_scripts:
                    remaining_countries = tier_countries[i + max_per_script:]
                    if remaining_countries:
                        all_batches[-1]["countries"].extend(remaining_countries)
                        assigned_countries.update(remaining_countries)
                    break
        
        if script_counter > num_scripts:
            break
    
    # Check if any countries were not assigned
    unassigned_countries = set(countries) - assigned_countries
    if unassigned_countries:
        print(f"\n[WARNING] {len(unassigned_countries)} countries not assigned in first {num_scripts} scripts")
        print(f"   Creating additional scripts ({num_scripts+1}, {num_scripts+2}, ...) for unassigned countries")
        print(f"   Unassigned countries: {', '.join(sorted(list(unassigned_countries))[:20])}")
        if len(unassigned_countries) > 20:
            print(f"   ... and {len(unassigned_countries) - 20} more")
        
        # Create additional scripts for unassigned countries (using Tier 3 config)
        config = SITING_TIER_CONFIG["t3"]
        max_per_script = config["max_countries_per_script"]
        unassigned_list = sorted(unassigned_countries)
        
        for i in range(0, len(unassigned_list), max_per_script):
            batch = unassigned_list[i:i + max_per_script]
            batch_info = {
                "countries": batch,
                "tier": "t3",
                "config": config,
                "script_num": script_counter
            }
            all_batches.append(batch_info)
            script_counter += 1
    
    print(f"\nCreated {len(all_batches)} siting script batches:")
    
    # Create shell scripts
    for i, batch_info in enumerate(all_batches, 1):
        batch = batch_info["countries"]
        tier = batch_info["tier"]
        config = batch_info["config"]
        
        # Build optional node directives
        node_directives = ""
        if "nodelist" in config and config["nodelist"]:
            node_directives += f"\n#SBATCH --nodelist={config['nodelist']}"
        if "exclude" in config and config["exclude"]:
            node_directives += f"\n#SBATCH --exclude={config['exclude']}"
        
        script_content = f"""#!/bin/bash --login
#SBATCH --job-name=p{i:02d}s_{tier}
#SBATCH --partition={config["partition"]}
#SBATCH --time={config["time"]}
#SBATCH --mem={config["mem"]}
#SBATCH --ntasks=1
#SBATCH --nodes=1
#SBATCH --cpus-per-task={config["cpus"]}{node_directives}
#SBATCH --output=outputs_per_country/logs/siting_{i:02d}_%j.out
#SBATCH --error=outputs_per_country/logs/siting_{i:02d}_%j.err
#SBATCH --mail-type=END,FAIL

set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

echo "[INFO] Starting siting analysis script {i}/{len(all_batches)} ({tier.upper()}) at $(date)"
echo "[INFO] Processing {len(batch)} countries in this batch: {', '.join(batch)}"
echo "[INFO] Tier: {tier.upper()} | Memory: {config['mem']} | CPUs: {config['cpus']} | Time: {config['time']}"

# --- directories ---
mkdir -p outputs_per_country/logs outputs_global

# --- Conda bootstrap ---
export PATH=/soge-home/users/lina4376/miniconda3/bin:$PATH
source /soge-home/users/lina4376/miniconda3/etc/profile.d/conda.sh

conda --version
conda activate p1_etl || true

# Use the env's absolute python path
PY=/soge-home/users/lina4376/miniconda3/envs/p1_etl/bin/python
echo "[INFO] Using Python: $PY"
$PY -c 'import sys; print(sys.executable)'

# Check scenario flags (passed via sbatch --export)
# Use ${{VAR:-}} syntax to avoid 'unbound variable' errors with set -u
SCENARIO_FLAG=""
if [ -n "${{SUPPLY_FACTOR:-}}" ]; then
    SCENARIO_FLAG="--supply-factor $SUPPLY_FACTOR"
    echo "[INFO] Running single scenario: ${{SUPPLY_FACTOR}} (supply factor)"
elif [ "${{RUN_ALL_SCENARIOS:-}}" = "1" ]; then
    SCENARIO_FLAG="--run-all-scenarios"
    echo "[INFO] Running ALL scenarios (100%, 90%, 80%, 70%, 60%)"
fi

# Process countries in this batch
"""
        
        for ci, country in enumerate(batch):
            script_content += f"""
echo "[INFO] Processing siting analysis for {country} ({get_tier(country).upper()})..."
MAX_RETRIES=3
for ATTEMPT in $(seq 1 $MAX_RETRIES); do
    if $PY process_country_siting.py {country} $SCENARIO_FLAG; then
        echo "[SUCCESS] {country} siting analysis completed (attempt $ATTEMPT)"
        break
    else
        if [ "$ATTEMPT" -lt "$MAX_RETRIES" ]; then
            echo "[WARN] {country} siting failed on attempt $ATTEMPT/$MAX_RETRIES - retrying in 10s..."
            sleep 10
        else
            echo "[ERROR] {country} siting analysis failed after $MAX_RETRIES attempts"
        fi
    fi
done
"""
            if ci < len(batch) - 1:
                script_content += """echo "[INFO] Pausing 5s before next country..."
sleep 5
"""
        
        script_content += f"""
echo "[INFO] Siting batch {i}/{len(all_batches)} ({tier.upper()}) completed at $(date)"
"""
        
        script_file = scripts_dir / f"submit_parallel_siting_{i:02d}.sh"
        script_file.write_text(script_content, encoding='utf-8', newline='\n')
        script_file.chmod(0o755)
        
        print(f"  Script {i:02d}: {len(batch)} countries ({tier.upper()}) - {', '.join(batch)}")
    
    # Create master submission script
    master_script = f"""#!/bin/bash
# Submit all parallel siting analysis jobs
# Usage: ./submit_all_parallel_siting.sh [--run-all-years] [--run-all-scenarios] [--supply-factor <value>]

# --- Parse arguments ---
RUN_ALL_SCENARIOS=""
RUN_ALL_YEARS=""
SUPPLY_FACTOR=""
SBATCH_EXPORT=""

# Resolve model year from config.py (override with ANALYSIS_YEAR env var if set)
ANALYSIS_YEAR="${{ANALYSIS_YEAR:-$(python - <<'PY'
from config import ANALYSIS_YEAR
print(ANALYSIS_YEAR)
PY
)}}"

while [ $# -gt 0 ]; do
    case $1 in
        --run-all-scenarios)
            RUN_ALL_SCENARIOS="1"
            shift
            ;;
        --run-all-years)
            RUN_ALL_YEARS="1"
            shift
            ;;
        --supply-factor)
            if [ -z "$2" ] || [[ "$2" == --* ]]; then
                echo "Error: --supply-factor requires a value (e.g., 0.9)"
                exit 1
            fi
            SUPPLY_FACTOR="$2"
            shift 2
            ;;
        -*)
            echo "Unknown option: $1"
            echo "Usage: $0 [--run-all-years] [--run-all-scenarios] [--supply-factor <value>]"
            exit 1
            ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: $0 [--run-all-years] [--run-all-scenarios] [--supply-factor <value>]"
            exit 1
            ;;
    esac
done

# Convenience mode: submit 2024, 2030, 2050 in one command.
if [ -n "$RUN_ALL_YEARS" ]; then
    echo "[INFO] Running all years (2024, 2030, 2050)"
    for YEAR in 2024 2030 2050; do
        echo ""
        echo "[INFO] Submitting siting year: $YEAR"
        if [ -n "$SUPPLY_FACTOR" ]; then
            ANALYSIS_YEAR="$YEAR" "$0" --supply-factor "$SUPPLY_FACTOR"
        elif [ -n "$RUN_ALL_SCENARIOS" ]; then
            ANALYSIS_YEAR="$YEAR" "$0" --run-all-scenarios
        else
            ANALYSIS_YEAR="$YEAR" "$0"
        fi
    done
    exit 0
fi

# Build SBATCH_EXPORT based on flags and determine log directory
if [ -n "$SUPPLY_FACTOR" ]; then
    SBATCH_EXPORT="--export=ALL,SUPPLY_FACTOR=$SUPPLY_FACTOR"
    # Convert supply factor to percentage (e.g., 0.9 -> 90)
    SCENARIO_PCT=$(echo "$SUPPLY_FACTOR * 100" | bc | cut -d. -f1)
    LOG_DIR="outputs_per_country/parquet/${{ANALYSIS_YEAR}}_supply_${{SCENARIO_PCT}}%/logs"
    echo "[INFO] Running single scenario: $SUPPLY_FACTOR (supply factor ${{SCENARIO_PCT}}%)"
elif [ -n "$RUN_ALL_SCENARIOS" ]; then
    SBATCH_EXPORT="--export=ALL,RUN_ALL_SCENARIOS=1"
    LOG_DIR="outputs_per_country/parquet/logs_run_all_scenarios"
    echo "[INFO] Running ALL scenarios (100%, 90%, 80%, 70%, 60%)"
else
    LOG_DIR="outputs_per_country/parquet/${{ANALYSIS_YEAR}}_supply_100%/logs"
    echo "[INFO] Running default scenario: 100%"
fi

# Create log directory
mkdir -p "$LOG_DIR"
echo "[INFO] Logs will be saved to: ${{LOG_DIR}}/"

# --- Conda bootstrap ---
export PATH=/soge-home/users/lina4376/miniconda3/bin:$PATH
source /soge-home/users/lina4376/miniconda3/etc/profile.d/conda.sh

conda activate p1_etl

echo "[INFO] Submitting {len(all_batches)} parallel siting analysis jobs..."
echo "[INFO] SLURM will automatically queue and manage job execution"
if [ -n "$RUN_ALL_SCENARIOS" ]; then
    echo "[INFO] Each job will run 5 scenarios (100%, 90%, 80%, 70%, 60%)"
fi
echo ""

# Submit all jobs
for i in {{01..{len(all_batches):02d}}}; do
    echo "[$(date +%H:%M:%S)] Submitting siting job $i..."
    sbatch --output="${{LOG_DIR}}/siting_${{i}}_%j.out" \\
           --error="${{LOG_DIR}}/siting_${{i}}_%j.err" \\
           $SBATCH_EXPORT parallel_scripts_siting/submit_parallel_siting_${{i}}.sh
    sleep 1
done

echo ""
echo "[INFO] All {len(all_batches)} siting jobs submitted!"
echo ""
echo "Monitor with:"
echo "  squeue -u \\$USER"
echo "  watch -n 60 'squeue -u \\$USER'"
echo ""
echo "Check completion:"
echo "  find outputs_per_country/parquet -name 'siting_*.parquet' | wc -l"
echo ""
echo "Resource allocation summary (siting analysis):"
echo "  Tier 1 (CHN, USA):              1 country/script  | Medium partition (48h) | {SITING_TIER_CONFIG['t1']['mem']}, {SITING_TIER_CONFIG['t1']['cpus']} CPUs"
echo "  Tier 2 (IND, CAN, etc.):        2 countries/script | Medium partition (24h) | {SITING_TIER_CONFIG['t2']['mem']}, {SITING_TIER_CONFIG['t2']['cpus']} CPUs"
echo "  Tier 3 (all others):           11 countries/script | Short partition (12h)  | {SITING_TIER_CONFIG['t3']['mem']}, {SITING_TIER_CONFIG['t3']['cpus']} CPUs"
"""
    
    master_file = Path("submit_all_parallel_siting.sh")
    master_file.write_text(master_script, encoding='utf-8', newline='\n')
    master_file.chmod(0o755)
    
    print(f"\nCreated {master_file} to submit all parallel siting jobs")
    
    # Create submit_one_direct_siting.sh for running any single country's siting analysis directly
    direct_siting_script = f"""#!/bin/bash --login
# ==============================================================================
# Run siting analysis for any country - useful for filling gaps or re-running
# Usage: ./submit_one_direct_siting.sh <ISO3> [--run-all-scenarios] [--supply-factor <value>]
#        ./submit_one_direct_siting.sh <ISO3> [--tier <1-3>] [options]
#
# Examples:
#   ./submit_one_direct_siting.sh KEN                      # Single country, 100% scenario
#   ./submit_one_direct_siting.sh KEN --run-all-scenarios  # Single country, all 5 scenarios
#   ./submit_one_direct_siting.sh KEN --supply-factor 0.9  # Single country, 90% scenario
#   ./submit_one_direct_siting.sh CHN --tier 1             # Use Tier 1 resources (high memory)
# ==============================================================================

# --- Parse arguments ---
RUN_ALL_SCENARIOS=""
SUPPLY_FACTOR=""
SBATCH_EXPORT=""
ISO3=""
TIER=""

while [ $# -gt 0 ]; do
    case $1 in
        --run-all-scenarios)
            RUN_ALL_SCENARIOS="1"
            shift
            ;;
        --supply-factor)
            if [ -z "$2" ] || [[ "$2" == --* ]]; then
                echo "Error: --supply-factor requires a value (e.g., 0.9)"
                exit 1
            fi
            SUPPLY_FACTOR="$2"
            shift 2
            ;;
        --tier)
            if [ -z "$2" ] || [[ "$2" == --* ]]; then
                echo "Error: --tier requires a value (1-3)"
                exit 1
            fi
            TIER="$2"
            shift 2
            ;;
        -*)
            echo "Unknown option: $1"
            echo "Usage: $0 <ISO3> [--run-all-scenarios] [--supply-factor <value>] [--tier <1-3>]"
            exit 1
            ;;
        *)
            if [ -z "$ISO3" ]; then
                ISO3="$1"
            else
                echo "Unknown argument: $1"
                echo "Usage: $0 <ISO3> [--run-all-scenarios] [--supply-factor <value>] [--tier <1-3>]"
                exit 1
            fi
            shift
            ;;
    esac
done

# Validate ISO3
if [ -z "$ISO3" ]; then
    echo "Usage: $0 <ISO3> [--run-all-scenarios] [--supply-factor <value>] [--tier <1-3>]"
    echo ""
    echo "Examples:"
    echo "  $0 KEN                      # Single country, 100% scenario, auto-detect tier"
    echo "  $0 KEN --run-all-scenarios  # Single country, all 5 scenarios"
    echo "  $0 KEN --supply-factor 0.9  # Single country, 90% scenario"
    echo "  $0 CHN --tier 1             # Use Tier 1 resources (95G memory)"
    echo ""
    echo "Tier resources (siting analysis):"
    echo "  T1: {SITING_TIER_CONFIG['t1']['mem']}, {SITING_TIER_CONFIG['t1']['cpus']} CPUs, {SITING_TIER_CONFIG['t1']['time']} ({SITING_TIER_CONFIG['t1']['partition']})  - CHN, USA, IND"
    echo "  T2: {SITING_TIER_CONFIG['t2']['mem']}, {SITING_TIER_CONFIG['t2']['cpus']} CPUs, {SITING_TIER_CONFIG['t2']['time']} ({SITING_TIER_CONFIG['t2']['partition']})  - CAN, MEX, RUS, BRA, etc."
    echo "  T3: {SITING_TIER_CONFIG['t3']['mem']}, {SITING_TIER_CONFIG['t3']['cpus']} CPUs, {SITING_TIER_CONFIG['t3']['time']} ({SITING_TIER_CONFIG['t3']['partition']})  - All others (default)"
    exit 1
fi

# Convert to uppercase
ISO3=$(echo "$ISO3" | tr '[:lower:]' '[:upper:]')

# --- Auto-detect tier based on country if not specified ---
SITING_TIER_1="{' '.join(TIER_1)}"
SITING_TIER_2="{' '.join(TIER_2)}"

if [ -z "$TIER" ]; then
    if [[ " $SITING_TIER_1 " =~ " $ISO3 " ]]; then
        TIER="1"
    elif [[ " $SITING_TIER_2 " =~ " $ISO3 " ]]; then
        TIER="2"
    else
        TIER="3"
    fi
    echo "[INFO] Auto-detected tier: T${{TIER}} for ${{ISO3}}"
fi

# --- Set SLURM resources based on tier ---
case $TIER in
    1)
        PARTITION="{SITING_TIER_CONFIG['t1']['partition']}"
        TIME="{SITING_TIER_CONFIG['t1']['time']}"
        MEM="{SITING_TIER_CONFIG['t1']['mem']}"
        CPUS="{SITING_TIER_CONFIG['t1']['cpus']}"
        ;;
    2)
        PARTITION="{SITING_TIER_CONFIG['t2']['partition']}"
        TIME="{SITING_TIER_CONFIG['t2']['time']}"
        MEM="{SITING_TIER_CONFIG['t2']['mem']}"
        CPUS="{SITING_TIER_CONFIG['t2']['cpus']}"
        ;;
    3|*)
        PARTITION="{SITING_TIER_CONFIG['t3']['partition']}"
        TIME="{SITING_TIER_CONFIG['t3']['time']}"
        MEM="{SITING_TIER_CONFIG['t3']['mem']}"
        CPUS="{SITING_TIER_CONFIG['t3']['cpus']}"
        ;;
esac

echo "[INFO] Resources: Partition=$PARTITION, Time=$TIME, Memory=$MEM, CPUs=$CPUS"

# Resolve model year from config.py (override with ANALYSIS_YEAR env var if set)
ANALYSIS_YEAR="${{ANALYSIS_YEAR:-$(python - <<'PY'
from config import ANALYSIS_YEAR
print(ANALYSIS_YEAR)
PY
)}}"

# --- Determine scenario flag and log directory ---
if [ -n "$SUPPLY_FACTOR" ]; then
    SCENARIO_PCT=$(echo "$SUPPLY_FACTOR * 100" | bc | cut -d. -f1)
    LOG_DIR="outputs_per_country/parquet/${{ANALYSIS_YEAR}}_supply_${{SCENARIO_PCT}}%/logs"
    SCENARIO_DESC="supply factor ${{SCENARIO_PCT}}%"
    SBATCH_EXPORT="--export=ALL,SUPPLY_FACTOR=$SUPPLY_FACTOR"
elif [ -n "$RUN_ALL_SCENARIOS" ]; then
    LOG_DIR="outputs_per_country/parquet/logs_run_all_scenarios"
    SCENARIO_DESC="ALL scenarios (100%, 90%, 80%, 70%, 60%)"
    SBATCH_EXPORT="--export=ALL,RUN_ALL_SCENARIOS=1"
else
    LOG_DIR="outputs_per_country/parquet/${{ANALYSIS_YEAR}}_supply_100%/logs"
    SCENARIO_DESC="100% (default)"
    SBATCH_EXPORT=""
fi

mkdir -p "$LOG_DIR"

echo "[INFO] Country: ${{ISO3}}"
echo "[INFO] Scenario: ${{SCENARIO_DESC}}"
echo "[INFO] Logs: ${{LOG_DIR}}/"

# --- Create temporary SLURM script ---
TEMP_SCRIPT=$(mktemp /tmp/siting_${{ISO3}}_XXXXXX.sh)

cat > "$TEMP_SCRIPT" << 'HEREDOC_HEADER'
#!/bin/bash --login
#SBATCH --mail-type=END,FAIL

set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

HEREDOC_HEADER

cat >> "$TEMP_SCRIPT" << HEREDOC_BODY
echo "[INFO] Starting siting analysis for ${{ISO3}} (T${{TIER}}) at \\$(date)"
echo "[INFO] Resources: ${{PARTITION}} partition, ${{MEM}} memory, ${{CPUS}} CPUs, ${{TIME}} time limit"

# --- directories ---
mkdir -p outputs_per_country/logs outputs_global

# --- Conda bootstrap ---
export PATH=/soge-home/users/lina4376/miniconda3/bin:\\$PATH
source /soge-home/users/lina4376/miniconda3/etc/profile.d/conda.sh

conda --version
conda activate p1_etl || true

# Use the env's absolute python path to avoid activation issues in batch shells
PY=/soge-home/users/lina4376/miniconda3/envs/p1_etl/bin/python
echo "[INFO] Using Python: \\$PY"
\\$PY -c 'import sys; print(sys.executable)'

# Check scenario flags (passed via sbatch --export)
SCENARIO_FLAG=""
if [ -n "\\${{SUPPLY_FACTOR:-}}" ]; then
    SCENARIO_FLAG="--supply-factor \\$SUPPLY_FACTOR"
    echo "[INFO] Running single scenario: \\${{SUPPLY_FACTOR}} (supply factor)"
elif [ "\\${{RUN_ALL_SCENARIOS:-}}" = "1" ]; then
    SCENARIO_FLAG="--run-all-scenarios"
    echo "[INFO] Running ALL scenarios (100%, 90%, 80%, 70%, 60%)"
fi

# Process siting analysis (with retry for transient NFS issues)
echo "[INFO] Processing siting for ${{ISO3}} (T${{TIER}})..."
MAX_RETRIES=3
for ATTEMPT in \\$(seq 1 \\$MAX_RETRIES); do
    if \\$PY process_country_siting.py ${{ISO3}} \\$SCENARIO_FLAG --output-dir outputs_per_country; then
        echo "[SUCCESS] Siting for ${{ISO3}} completed at \\$(date) (attempt \\$ATTEMPT)"
        break
    else
        if [ "\\$ATTEMPT" -lt "\\$MAX_RETRIES" ]; then
            echo "[WARN] Siting for ${{ISO3}} failed on attempt \\$ATTEMPT/\\$MAX_RETRIES - retrying in 10s..."
            sleep 10
        else
            echo "[ERROR] Siting for ${{ISO3}} failed after \\$MAX_RETRIES attempts at \\$(date)"
            exit 1
        fi
    fi
done
HEREDOC_BODY

# --- Submit the job ---
echo "[INFO] Submitting siting analysis for ${{ISO3}}..."
sbatch --job-name="ds_${{ISO3}}" \\
       --partition="$PARTITION" \\
       --exclude=ouce-cn62 \\
       --time="$TIME" \\
       --mem="$MEM" \\
       --ntasks=1 \\
       --nodes=1 \\
       --cpus-per-task="$CPUS" \\
       --output="${{LOG_DIR}}/siting_${{ISO3}}_%j.out" \\
       --error="${{LOG_DIR}}/siting_${{ISO3}}_%j.err" \\
       $SBATCH_EXPORT \\
       "$TEMP_SCRIPT"

# Note: Temp script is NOT auto-deleted. SLURM copies it internally.
# The /tmp cleanup on compute nodes will handle it eventually.

echo ""
echo "Monitor with:"
echo "  squeue -u \\$USER"
echo "  tail -f ${{LOG_DIR}}/siting_${{ISO3}}_*.out"
"""
    
    direct_siting_file = Path("submit_one_direct_siting.sh")
    direct_siting_file.write_text(direct_siting_script, encoding='utf-8', newline='\n')
    direct_siting_file.chmod(0o755)
    
    print(f"Created {direct_siting_file} for submitting any single country's siting analysis directly")
    
    # Calculate total resources
    total_mem = sum(int(batch_info["config"]["mem"].replace("G", "")) for batch_info in all_batches)
    total_cpus = sum(batch_info["config"]["cpus"] for batch_info in all_batches)
    
    print(f"\nTotal resource allocation (siting):")
    print(f"  Total Memory: {total_mem}GB")
    print(f"  Total CPUs: {total_cpus}")
    print(f"  Scripts: {len(all_batches)}")
    
    # Verify all countries are covered
    total_countries_in_batches = sum(len(batch["countries"]) for batch in all_batches)
    unique_countries_in_batches = set()
    for batch in all_batches:
        unique_countries_in_batches.update(batch["countries"])
    
    print(f"\n[INFO] Coverage verification (siting):")
    print(f"  Total countries to process: {len(countries)}")
    print(f"  Countries in batches: {total_countries_in_batches}")
    print(f"  Unique countries in batches: {len(unique_countries_in_batches)}")
    
    if len(unique_countries_in_batches) == len(countries):
        print(f"  [OK] All countries covered!")
    else:
        missing = set(countries) - unique_countries_in_batches
        print(f"  [WARNING] {len(missing)} countries NOT covered!")
        print(f"     Missing: {', '.join(sorted(list(missing))[:10])}")
    
    return True

if __name__ == "__main__":
    import sys
    # Allows running from the command line with '--create-parallel' to generate the scripts.
    if len(sys.argv) > 1 and sys.argv[1] == "--create-parallel":
        # Create parallel scripts
        countries = get_country_list()
        if countries:
            create_parallel_scripts(countries=countries)
        else:
            print("No countries found!")
    elif len(sys.argv) > 1 and sys.argv[1] == "--create-parallel-siting":
        # Create parallel siting scripts
        countries = get_country_list()
        if countries:
            create_parallel_siting_scripts(countries=countries)
        else:
            print("No countries found!")
    else:
        # Default behavior - just get countries
        countries = get_country_list()
