#!/bin/bash --login
# ==============================================================================
# Run siting analysis for any country - useful for filling gaps or re-running
# Usage: ./submit_one_direct_siting.sh <ISO3> [--run-all-scenarios] [--supply-factor <value>]
#        ./submit_one_direct_siting.sh <ISO3> [--tier <1-3>] [options]
#
# Examples:
#   ./submit_one_direct_siting.sh KEN                      # Single country, 100% scenario
#   ./submit_one_direct_siting.sh KEN --run-all-scenarios  # Single country, all 5 scenarios
#   ./submit_one_direct_siting.sh KEN --supply-factor 0.9  # Single country, 90% scenario
#   ./submit_one_direct_siting.sh CHN                     # Dedicated Long node, 112 CPUs, all memory
# ==============================================================================

# --- Parse arguments ---
RUN_ALL_SCENARIOS=""
SUPPLY_FACTOR=""
SCENARIO_NAME=""
SBATCH_EXPORT=""
ISO3=""
TIER=""

while [ $# -gt 0 ]; do
    case $1 in
        --run-all-supply-factors|--run-all-scenarios)
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
        --scenario)
            if [ -z "$2" ] || [[ "$2" == --* ]]; then
                echo "Error: --scenario requires 100pct, 80pct, or 60pct"
                exit 1
            fi
            SCENARIO_NAME="$2"
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
    echo "  USA/CHN: dedicated Long node, 112 CPUs, all node memory (even with --tier)"
    echo "  T1: 25G, 40 CPUs, 12:00:00 (Short)  - IND"
    echo "  T2: 25G, 40 CPUs, 12:00:00 (Short)  - CAN, MEX, RUS, BRA, etc."
    echo "  T3: 25G, 40 CPUs, 12:00:00 (Short)  - All others (default)"
    exit 1
fi

# Convert to uppercase
ISO3=$(echo "$ISO3" | tr '[:lower:]' '[:upper:]')

# --- Auto-detect tier based on country if not specified ---
SITING_TIER_1="IND CHN USA"
SITING_TIER_2="ARG AUS SAU BRA CAN RUS IDN KAZ MEX"

if [ -z "$TIER" ]; then
    if [[ " $SITING_TIER_1 " =~ " $ISO3 " ]]; then
        TIER="1"
    elif [[ " $SITING_TIER_2 " =~ " $ISO3 " ]]; then
        TIER="2"
    else
        TIER="3"
    fi
    echo "[INFO] Auto-detected tier: T${TIER} for ${ISO3}"
fi

# --- Set SLURM resources based on tier ---
case $TIER in
    1)
        PARTITION="Short"
        TIME="12:00:00"
        MEM="25G"
        CPUS="40"
        ;;
    2)
        PARTITION="Short"
        TIME="12:00:00"
        MEM="25G"
        CPUS="40"
        ;;
    3|*)
        PARTITION="Short"
        TIME="12:00:00"
        MEM="25G"
        CPUS="40"
        ;;
esac

EXCLUSIVE_ARGS=()
case "$ISO3" in
    USA|CHN)
        PARTITION="Long"
        TIME="168:00:00"
        MEM="0"
        CPUS="112"
        EXCLUSIVE_ARGS=(--exclusive)
        ;;
esac

echo "[INFO] Resources: Partition=$PARTITION, Time=$TIME, Memory=$MEM, CPUs=$CPUS (0 = all node memory)"

# Resolve model year from config.py (override with ANALYSIS_YEAR env var if set)
ANALYSIS_YEAR="${ANALYSIS_YEAR:-$(python - <<'PY'
from config import ANALYSIS_YEAR
print(ANALYSIS_YEAR)
PY
)}"
echo "[INFO] Analysis year: ${ANALYSIS_YEAR}"

# --- Determine scenario flag and log directory ---
SITING_RUN=1

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

echo "[INFO] Country: ${ISO3}"
echo "[INFO] Scenario: ${SCENARIO_DESC}"
echo "[INFO] Logs: ${LOG_DIR}/"

# --- Create temporary SLURM script ---
TEMP_SCRIPT=$(mktemp /tmp/siting_${ISO3}_XXXXXX.sh)

cat > "$TEMP_SCRIPT" << 'HEREDOC_HEADER'
#!/bin/bash --login
#SBATCH --mail-type=END,FAIL

set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

HEREDOC_HEADER

cat >> "$TEMP_SCRIPT" << HEREDOC_BODY
echo "[INFO] Starting siting analysis for ${ISO3} (T${TIER}) at \$(date)"
echo "[INFO] Resources: ${PARTITION} partition, ${MEM} memory, ${CPUS} CPUs, ${TIME} time limit"

# --- directories ---
mkdir -p outputs_per_country/logs outputs_global

# --- Conda bootstrap ---
export PATH=/soge-home/users/lina4376/miniconda3/bin:\$PATH
source /soge-home/users/lina4376/miniconda3/etc/profile.d/conda.sh

conda --version
conda activate p1_etl || true

# Use the env's absolute python path to avoid activation issues in batch shells
PY=/soge-home/users/lina4376/miniconda3/envs/p1_etl/bin/python
echo "[INFO] Using Python: \$PY"
\$PY -c 'import sys; print(sys.executable)'

# Check scenario flags (passed via sbatch --export)
SCENARIO_FLAG=""
if [ -n "\${SUPPLY_FACTOR:-}" ]; then
    SCENARIO_FLAG="--supply-factor \$SUPPLY_FACTOR"
    echo "[INFO] Running single scenario: \${SUPPLY_FACTOR} (supply factor)"
elif [ "\${RUN_ALL_SCENARIOS:-}" = "1" ]; then
    SCENARIO_FLAG="--run-all-scenarios"
    echo "[INFO] Running ALL scenarios (100%, 90%, 80%, 70%, 60%)"
fi

# Process siting analysis (with retry for transient NFS issues)
echo "[INFO] Processing siting for ${ISO3} (T${TIER})..."
MAX_RETRIES=3
for ATTEMPT in \$(seq 1 \$MAX_RETRIES); do
    if \$PY process_country_siting.py ${ISO3} \$SCENARIO_FLAG --output-dir outputs_per_country; then
        echo "[SUCCESS] Siting for ${ISO3} completed at \$(date) (attempt \$ATTEMPT)"
        break
    else
        if [ "\$ATTEMPT" -lt "\$MAX_RETRIES" ]; then
            echo "[WARN] Siting for ${ISO3} failed on attempt \$ATTEMPT/\$MAX_RETRIES - retrying in 10s..."
            sleep 10
        else
            echo "[ERROR] Siting for ${ISO3} failed after \$MAX_RETRIES attempts at \$(date)"
            exit 1
        fi
    fi
done
HEREDOC_BODY

# --- Submit the job ---
echo "[INFO] Submitting siting analysis for ${ISO3}..."
sbatch --job-name="ds_${ISO3}_${ANALYSIS_YEAR}"        --partition="$PARTITION" \
       "${EXCLUSIVE_ARGS[@]}" \
       --exclude=ouce-cn62 \
       --time="$TIME" \
       --mem="$MEM" \
       --ntasks=1 \
       --nodes=1 \
       --cpus-per-task="$CPUS" \
       --output="${LOG_DIR}/siting_${ISO3}_%j.out" \
       --error="${LOG_DIR}/siting_${ISO3}_%j.err" \
       $SBATCH_EXPORT \
       "$TEMP_SCRIPT"

# Note: Temp script is NOT auto-deleted. SLURM copies it internally.
# The /tmp cleanup on compute nodes will handle it eventually.

echo ""
echo "Monitor with:"
echo "  squeue -u \$USER"
echo "  tail -f ${LOG_DIR}/siting_${ISO3}_*.out"
