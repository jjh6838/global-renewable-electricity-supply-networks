#!/bin/bash --login
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
    echo "  $0 CHN --tier 1             # Use Tier 1 resources (95G memory)"
    echo "  $0 USA --tier 2             # Use Tier 2 resources (95G memory)"
    echo ""
    echo "Tier resources:"
    echo "  T1: 95G, 40 CPUs, 168:00:00 (Long)  - CHN"
    echo "  T2: 95G, 40 CPUs, 168:00:00 (Long)   - USA, IND, BRA, DEU, FRA, RUS"
    echo "  T3: 95G, 40 CPUs, 48:00:00 (Medium)  - CAN, MEX, RUS, AUS, etc."
    echo "  T4: 25G, 40 CPUs, 12:00:00 (Short)   - TUR, NGA, VEN, ETH, etc."
    echo "  T5: 25G, 40 CPUs, 12:00:00 (Short)   - All others (default)"
    exit 1
fi

# Convert to uppercase
ISO3=$(echo "$ISO3" | tr '[:lower:]' '[:upper:]')

# --- Auto-detect tier based on country if not specified ---
TIER_1="CHN"
TIER_2="USA FRA BRA DEU IND RUS"
TIER_3="AUS KAZ ZAF CAN MEX IDN ARG EGY IRN SAU"
TIER_4="PAK SWE COL GEO TCD DZA NGA SDN IRQ PER VEN NOR UKR GHA MMR JPN TUR ETH PHL MLI"

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
    echo "[INFO] Auto-detected tier: T${TIER} for ${ISO3}"
fi

# --- Set SLURM resources based on tier ---
case $TIER in
    1)
        PARTITION="Long"
        TIME="168:00:00"
        MEM="95G"
        CPUS="40"
        ;;
    2)
        PARTITION="Long"
        TIME="168:00:00"
        MEM="95G"
        CPUS="40"
        ;;
    3)
        PARTITION="Medium"
        TIME="48:00:00"
        MEM="95G"
        CPUS="40"
        ;;
    4)
        PARTITION="Short"
        TIME="12:00:00"
        MEM="25G"
        CPUS="40"
        ;;
    5|*)
        PARTITION="Short"
        TIME="12:00:00"
        MEM="25G"
        CPUS="40"
        ;;
esac

echo "[INFO] Resources: Partition=$PARTITION, Time=$TIME, Memory=$MEM, CPUs=$CPUS"

# Resolve model year from config.py (override with ANALYSIS_YEAR env var if set)
ANALYSIS_YEAR="${ANALYSIS_YEAR:-$(python - <<'PY'
from config import ANALYSIS_YEAR
print(ANALYSIS_YEAR)
PY
)}"

# --- Determine scenario flag and log directory ---
if [ -n "$SUPPLY_FACTOR" ]; then
    SCENARIO_PCT=$(echo "$SUPPLY_FACTOR * 100" | bc | cut -d. -f1)
    LOG_DIR="outputs_per_country/parquet/${ANALYSIS_YEAR}_supply_${SCENARIO_PCT}%/logs"
    SCENARIO_DESC="supply factor ${SCENARIO_PCT}%"
    SBATCH_EXPORT="--export=ALL,SUPPLY_FACTOR=$SUPPLY_FACTOR"
elif [ -n "$RUN_ALL_SCENARIOS" ]; then
    LOG_DIR="outputs_per_country/parquet/logs_run_all_scenarios"
    SCENARIO_DESC="ALL scenarios (100%, 90%, 80%, 70%, 60%)"
    SBATCH_EXPORT="--export=ALL,RUN_ALL_SCENARIOS=1"
else
    LOG_DIR="outputs_per_country/parquet/${ANALYSIS_YEAR}_supply_100%/logs"
    SCENARIO_DESC="100% (default)"
    SBATCH_EXPORT=""
fi

mkdir -p "$LOG_DIR"

echo "[INFO] Country: ${ISO3}"
echo "[INFO] Scenario: ${SCENARIO_DESC}"
echo "[INFO] Logs: ${LOG_DIR}/"

# --- Create temporary SLURM script ---
TEMP_SCRIPT=$(mktemp /tmp/submit_${ISO3}_XXXXXX.sh)

cat > "$TEMP_SCRIPT" << 'HEREDOC_HEADER'
#!/bin/bash --login
#SBATCH --mail-type=END,FAIL

set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

HEREDOC_HEADER

cat >> "$TEMP_SCRIPT" << HEREDOC_BODY
echo "[INFO] Starting ${ISO3} (T${TIER}) at \$(date)"
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

# Process country (with retry for transient NFS issues)
echo "[INFO] Processing ${ISO3} (T${TIER})..."
MAX_RETRIES=3
for ATTEMPT in \$(seq 1 \$MAX_RETRIES); do
    if \$PY process_country_supply.py ${ISO3} \$SCENARIO_FLAG --output-dir outputs_per_country; then
        echo "[SUCCESS] ${ISO3} completed at \$(date) (attempt \$ATTEMPT)"
        break
    else
        if [ "\$ATTEMPT" -lt "\$MAX_RETRIES" ]; then
            echo "[WARN] ${ISO3} failed on attempt \$ATTEMPT/\$MAX_RETRIES - retrying in 10s..."
            sleep 10
        else
            echo "[ERROR] ${ISO3} failed after \$MAX_RETRIES attempts at \$(date)"
            exit 1
        fi
    fi
done
HEREDOC_BODY

# --- Submit the job ---
echo "[INFO] Submitting ${ISO3}..."
sbatch --job-name="d_${ISO3}" \
       --partition="$PARTITION" \
       --exclude=ouce-cn62 \
       --time="$TIME" \
       --mem="$MEM" \
       --ntasks=1 \
       --nodes=1 \
       --cpus-per-task="$CPUS" \
       --output="${LOG_DIR}/${ISO3}_%j.out" \
       --error="${LOG_DIR}/${ISO3}_%j.err" \
       $SBATCH_EXPORT \
       "$TEMP_SCRIPT"

# Clean up temp script after a delay (give sbatch time to read it)
(sleep 5 && rm -f "$TEMP_SCRIPT") &

echo ""
echo "Monitor with:"
echo "  squeue -u \$USER"
echo "  tail -f ${LOG_DIR}/${ISO3}_*.out"
