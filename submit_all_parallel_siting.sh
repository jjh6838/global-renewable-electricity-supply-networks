#!/bin/bash
# Submit all parallel siting analysis jobs
# Usage: ./submit_all_parallel_siting.sh [--run-all-years] [--run-all-scenarios] [--supply-factor <value>]

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
    SBATCH_EXPORT="--export=ALL,ANALYSIS_YEAR=$ANALYSIS_YEAR,SUPPLY_FACTOR=$SUPPLY_FACTOR"
    # Convert supply factor to percentage (e.g., 0.9 -> 90)
    SCENARIO_PCT=$(echo "$SUPPLY_FACTOR * 100" | bc | cut -d. -f1)
    LOG_DIR="outputs_per_country/parquet/${ANALYSIS_YEAR}_supply_${SCENARIO_PCT}%/logs"
    echo "[INFO] Running single scenario: $SUPPLY_FACTOR (supply factor ${SCENARIO_PCT}%)"
elif [ -n "$RUN_ALL_SCENARIOS" ]; then
    SBATCH_EXPORT="--export=ALL,ANALYSIS_YEAR=$ANALYSIS_YEAR,RUN_ALL_SCENARIOS=1"
    LOG_DIR="outputs_per_country/parquet/logs_run_all_scenarios"
    echo "[INFO] Running ALL scenarios (100%, 90%, 80%, 70%, 60%)"
else
    SBATCH_EXPORT="--export=ALL,ANALYSIS_YEAR=$ANALYSIS_YEAR"
    LOG_DIR="outputs_per_country/parquet/${ANALYSIS_YEAR}_supply_100%/logs"
    echo "[INFO] Running default scenario: 100%"
fi

# Create log directory
mkdir -p "$LOG_DIR"
echo "[INFO] Logs will be saved to: ${LOG_DIR}/"

# --- Conda bootstrap ---
export PATH=/soge-home/users/lina4376/miniconda3/bin:$PATH
source /soge-home/users/lina4376/miniconda3/etc/profile.d/conda.sh

conda activate p1_etl

echo "[INFO] Submitting 25 parallel siting analysis jobs..."
echo "[INFO] SLURM will automatically queue and manage job execution"
if [ -n "$RUN_ALL_SCENARIOS" ]; then
    echo "[INFO] Each job will run 5 scenarios (100%, 90%, 80%, 70%, 60%)"
fi
echo ""

# Submit all jobs
for i in {01..25}; do
    echo "[$(date +%H:%M:%S)] Submitting siting job $i..."
    sbatch --output="${LOG_DIR}/siting_${i}_%j.out" \
           --error="${LOG_DIR}/siting_${i}_%j.err" \
           $SBATCH_EXPORT parallel_scripts_siting/submit_parallel_siting_${i}.sh
    sleep 1
done

echo ""
echo "[INFO] All 25 siting jobs submitted!"
echo ""
echo "Monitor with:"
echo "  squeue -u \$USER"
echo "  watch -n 60 'squeue -u \$USER'"
echo ""
echo "Check completion:"
echo "  find outputs_per_country/parquet -name 'siting_*.parquet' | wc -l"
echo ""
echo "Resource allocation summary (siting analysis):"
echo "  Tier 1 (CHN, USA):              1 country/script  | Medium partition (48h) | 25G, 40 CPUs"
echo "  Tier 2 (IND, CAN, etc.):        2 countries/script | Medium partition (24h) | 25G, 40 CPUs"
echo "  Tier 3 (all others):           11 countries/script | Short partition (12h)  | 25G, 40 CPUs"
