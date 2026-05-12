#!/bin/bash
# Submit all parallel jobs immediately (SLURM will queue them automatically)
# Usage: ./submit_all_parallel.sh [--run-all-years] [--run-all-scenarios] [--supply-factor <value>]

    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    cd "$SCRIPT_DIR"

# --- Parse arguments ---
RUN_ALL_SCENARIOS=""
RUN_ALL_YEARS=""
SUPPLY_FACTOR=""
SBATCH_EXPORT=""

# Resolve model year from config.py (override with ANALYSIS_YEAR env var if set)
ANALYSIS_YEAR="${ANALYSIS_YEAR:-$(python - <<'PY'
from config import ANALYSIS_YEAR
print(ANALYSIS_YEAR)
PY
)}"

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
    SCENARIO="${ANALYSIS_YEAR}_supply_${SCENARIO_PCT}%"
    echo "[INFO] Running single scenario: $SUPPLY_FACTOR (supply factor ${SCENARIO_PCT}%)"
elif [ -n "$RUN_ALL_SCENARIOS" ]; then
    SBATCH_EXPORT="--export=ALL,RUN_ALL_SCENARIOS=1"
    SCENARIO="all_scenarios"
    echo "[INFO] Running ALL scenarios (100%, 90%, 80%, 70%, 60%)"
else
    SCENARIO="${ANALYSIS_YEAR}_supply_100%"
    echo "[INFO] Running default scenario: 100%"
fi

# Create scenario-specific log directory (route to add_v2 logs on second supply run)
if [ -z "$RUN_ALL_SCENARIOS" ]; then
    YEAR_PART="${SCENARIO%%_supply_*}"
    PCT_PART="${SCENARIO##*_supply_}"
    if compgen -G "outputs_per_country/parquet/${SCENARIO}/${YEAR_PART}_siting_${PCT_PART}_*.xlsx" > /dev/null; then
        LOG_DIR="outputs_per_country/parquet/${SCENARIO}_add_v2/logs"
        echo "[INFO] Detected siting outputs for ${SCENARIO}; using add_v2 log directory"
    else
        LOG_DIR="outputs_per_country/parquet/${SCENARIO}/logs"
    fi
else
    if compgen -G "outputs_per_country/parquet/${ANALYSIS_YEAR}_supply_100%/${ANALYSIS_YEAR}_siting_100%_*.xlsx" > /dev/null ||        compgen -G "outputs_per_country/parquet/${ANALYSIS_YEAR}_supply_90%/${ANALYSIS_YEAR}_siting_90%_*.xlsx" > /dev/null ||        compgen -G "outputs_per_country/parquet/${ANALYSIS_YEAR}_supply_80%/${ANALYSIS_YEAR}_siting_80%_*.xlsx" > /dev/null ||        compgen -G "outputs_per_country/parquet/${ANALYSIS_YEAR}_supply_70%/${ANALYSIS_YEAR}_siting_70%_*.xlsx" > /dev/null ||        compgen -G "outputs_per_country/parquet/${ANALYSIS_YEAR}_supply_60%/${ANALYSIS_YEAR}_siting_60%_*.xlsx" > /dev/null; then
        LOG_DIR="outputs_per_country/parquet/logs_run_all_scenarios_add_v2"
        echo "[INFO] Detected siting outputs for run-all scenarios; using add_v2 log directory"
    else
        LOG_DIR="outputs_per_country/parquet/logs_run_all_scenarios"
    fi
fi
mkdir -p "$LOG_DIR"
echo "[INFO] Logs will be saved to: ${LOG_DIR}/"

# --- Conda bootstrap ---
export PATH=/soge-home/users/lina4376/miniconda3/bin:$PATH
source /soge-home/users/lina4376/miniconda3/etc/profile.d/conda.sh

conda activate p1_etl

echo "[INFO] Submitting 40 parallel jobs..."
echo "[INFO] SLURM will automatically queue and manage job execution (max 8 running at once)"
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
echo "  Tier 1 (CHN):                   1 country/script  | Long partition (168h)        | 95G, 40 CPUs"
echo "  Tier 2 (USA, IND, BRA, etc.):   1 country/script  | Long partition (168h)        | 95G, 40 CPUs"
echo "  Tier 3 (CAN, MEX, AUS, etc.):   1 country/script  | Medium partition (48h)       | 95G, 40 CPUs"
echo "  Tier 4 (TUR, NGA, COL, etc.):   2 countries/script | Short partition (12h)        | 25G, 40 CPUs"
echo "  Tier 5 (all others):           12 countries/script | Short partition (12h)        | 25G, 40 CPUs"
