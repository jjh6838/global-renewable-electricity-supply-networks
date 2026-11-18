#!/bin/bash
# Submit all parallel jobs immediately (SLURM will queue them automatically)
# Usage: ./submit_all_parallel.sh [--run-all-years] [--run-all-supply-factors|--run-all-scenarios] [--supply-factor <value>] [--scenario 100pct|80pct|60pct]

    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    cd "$SCRIPT_DIR"

# --- Parse arguments ---
RUN_ALL_SCENARIOS=""
RUN_ALL_YEARS=""
SUPPLY_FACTOR=""
SCENARIO_NAME=""
SBATCH_EXPORT=""

# Resolve model year from config.py (override with ANALYSIS_YEAR env var if set)
ANALYSIS_YEAR="${ANALYSIS_YEAR:-$(python - <<'PY'
from config import ANALYSIS_YEAR
print(ANALYSIS_YEAR)
PY
)}"

while [ $# -gt 0 ]; do
    case $1 in
        --run-all-supply-factors|--run-all-scenarios)
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
        --scenario)
            if [ -z "$2" ] || [[ "$2" == --* ]]; then
                echo "Error: --scenario requires a value (100pct, 80pct, or 60pct)"
                exit 1
            fi
            SCENARIO_NAME="$2"
            shift 2
            ;;
        -*)
            echo "Unknown option: $1"
            echo "Usage: $0 [--run-all-years] [--run-all-supply-factors|--run-all-scenarios] [--supply-factor <value>] [--scenario 100pct|80pct|60pct]"
            exit 1
            ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: $0 [--run-all-years] [--run-all-supply-factors|--run-all-scenarios] [--supply-factor <value>] [--scenario 100pct|80pct|60pct]"
            exit 1
            ;;
    esac
done

# Convenience mode: submit 2024, 2030, 2050 in one command.
if [ -n "$RUN_ALL_YEARS" ]; then
    echo "[INFO] Running all years (2024, 2030, 2050)"
    YEAR_ARGS=()
    if [ -n "$SCENARIO_NAME" ]; then YEAR_ARGS+=(--scenario "$SCENARIO_NAME"); fi
    if [ -n "$SUPPLY_FACTOR" ]; then
        YEAR_ARGS+=(--supply-factor "$SUPPLY_FACTOR")
    elif [ -n "$RUN_ALL_SCENARIOS" ]; then
        YEAR_ARGS+=(--run-all-scenarios)
    fi
    for YEAR in 2024 2030 2050; do
        echo ""
        echo "[INFO] Submitting year: $YEAR"
        ANALYSIS_YEAR="$YEAR" "$0" "${YEAR_ARGS[@]}"
    done
    exit 0
fi

# Build SBATCH_EXPORT based on flags
SITING_RUN=0

if [ -n "$SUPPLY_FACTOR" ] && [ -n "$RUN_ALL_SCENARIOS" ]; then
    echo "Error: --supply-factor cannot be combined with --run-all-supply-factors"
    exit 1
fi
if [ -n "${SCENARIO_NAME:-}" ]; then
    case "$SCENARIO_NAME" in
        100pct|100%|1.0|100) WORKBOOK_SCENARIO="100pct" ;;
        80pct|80%|0.8|80) WORKBOOK_SCENARIO="80pct" ;;
        60pct|60%|0.6|60) WORKBOOK_SCENARIO="60pct" ;;
        *) echo "Error: unsupported scenario '$SCENARIO_NAME'"; exit 1 ;;
    esac
    export P1_B_SCENARIO_FILE="outputs_processed_data/p1_b_ember_2024_30_50_${WORKBOOK_SCENARIO}.xlsx"
else
    export P1_B_SCENARIO_FILE="${P1_B_SCENARIO_FILE:-outputs_processed_data/p1_b_ember_2024_30_50_100pct.xlsx}"
fi
SCENARIO_INFO=$(python - "${SUPPLY_FACTOR:-1.0}" <<'PY'
import sys
from config import ANALYSIS_YEAR, get_projection_scenario_name, get_supply_scenario_name
factor = float(sys.argv[1])
if not 0 < factor <= 1:
    raise ValueError("Supply Factor must be greater than 0 and at most 1")
print(get_projection_scenario_name(), get_supply_scenario_name(ANALYSIS_YEAR, factor))
PY
) || exit 1
read -r WORKBOOK_SCENARIO SCENARIO <<< "$SCENARIO_INFO"
SBATCH_EXPORT="--export=ALL,ANALYSIS_YEAR=$ANALYSIS_YEAR,P1_B_SCENARIO_FILE=$P1_B_SCENARIO_FILE"
if [ -n "$RUN_ALL_SCENARIOS" ]; then
    SBATCH_EXPORT="$SBATCH_EXPORT,RUN_ALL_SCENARIOS=1,SUPPLY_FACTOR="
    LOG_DIR="outputs_per_country/parquet/logs_run_all_scenarios_${WORKBOOK_SCENARIO}"
    SCENARIO_DESC="$WORKBOOK_SCENARIO, all supply factors"
else
    SUPPLY_FACTOR="${SUPPLY_FACTOR:-1.0}"
    SBATCH_EXPORT="$SBATCH_EXPORT,SUPPLY_FACTOR=$SUPPLY_FACTOR,RUN_ALL_SCENARIOS=0"
    LOG_NAME="logs_${WORKBOOK_SCENARIO}"
    if [ "$SITING_RUN" = 1 ]; then LOG_NAME="logs"; fi
    OUTPUT_SCENARIO="$SCENARIO"
    SUPPLY_PCT="${SCENARIO#*_supply_}"
    SUPPLY_PCT="${SUPPLY_PCT%%_*}"
    if [ "$SITING_RUN" = 0 ] && compgen -G "outputs_per_country/parquet/${SCENARIO}/${ANALYSIS_YEAR}_siting_${SUPPLY_PCT}_${ISO3:-*}.xlsx" > /dev/null; then
        OUTPUT_SCENARIO="${SCENARIO}_add_v2"
    fi
    LOG_DIR="outputs_per_country/parquet/${OUTPUT_SCENARIO}/${LOG_NAME}"
    SCENARIO_DESC="$WORKBOOK_SCENARIO, supply factor $SUPPLY_FACTOR"
fi
echo "[INFO] Running: $SCENARIO_DESC"

mkdir -p "$LOG_DIR"
echo "[INFO] Logs will be saved to: ${LOG_DIR}/"

# --- Conda bootstrap ---
export PATH=/soge-home/users/lina4376/miniconda3/bin:$PATH
source /soge-home/users/lina4376/miniconda3/etc/profile.d/conda.sh

conda activate p1_etl

# Bigdata path controls propagated to all sbatch jobs via --export=ALL
export BIGDATA_ROOT="/soge-home/projects/mistral/ji"
export BIGDATA_LOCAL_ROOT="$SCRIPT_DIR"
export BIGDATA_RETRY_COUNT="10"
export BIGDATA_RETRY_SLEEP_SEC="5"

echo "[INFO] Submitting 40 parallel jobs..."
echo "[INFO] SLURM will automatically queue and manage job execution (max 8 running at once)"
echo "[INFO] BIGDATA_ROOT=${BIGDATA_ROOT}"
echo "[INFO] BIGDATA_RETRY_COUNT=${BIGDATA_RETRY_COUNT}, BIGDATA_RETRY_SLEEP_SEC=${BIGDATA_RETRY_SLEEP_SEC}"
if [ -n "$RUN_ALL_SCENARIOS" ]; then
    echo "[INFO] Each job will run 5 scenarios (100%, 90%, 80%, 70%, 60%)"
fi
echo ""

# Submit all jobs
for i in {01..40}; do
    echo "[$(date +%H:%M:%S)] Submitting job $i..."
    sbatch --output="${LOG_DIR}/parallel_${i}_%j.out" \
           --error="${LOG_DIR}/parallel_${i}_%j.err" \
           $SBATCH_EXPORT parallel_scripts/submit_parallel_${i}.sh
    sleep 1  # Small delay to avoid overwhelming scheduler
done

echo ""
echo "[INFO] All 40 jobs submitted!"
echo ""
echo "Monitor with:"
echo "  squeue -u \$USER"
echo "  watch -n 60 'squeue -u \$USER'"
echo ""
echo "Check completion:"
echo "  find outputs_per_country/parquet -name '*.parquet' | wc -l"
echo ""
echo "Resource allocation summary (tiered partition strategy):"
echo "  USA first, CHN second: dedicated Long nodes (168h), 112 CPUs, all node memory (--mem=0)"
echo "  Submission order does not enforce execution order; USA and CHN may run concurrently."
echo "  Remaining Tier 2 (IND, BRA, etc.): 1 country/script | Long partition (168h)     | 95G, 40 CPUs"
echo "  Tier 3 (CAN, MEX, AUS, etc.):   1 country/script  | Medium partition (48h)       | 95G, 40 CPUs"
echo "  Tier 4 (TUR, NGA, COL, etc.):   2 countries/script | Short partition (12h)        | 25G, 40 CPUs"
echo "  Tier 5 (all others):           12 countries/script | Short partition (12h)        | 25G, 40 CPUs"
