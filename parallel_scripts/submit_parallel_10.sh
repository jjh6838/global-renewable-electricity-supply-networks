#!/bin/bash --login
#SBATCH --job-name=p10_t3
#SBATCH --partition=Medium
#SBATCH --time=48:00:00
#SBATCH --mem=95G
#SBATCH --ntasks=1
#SBATCH --nodes=1
#SBATCH --cpus-per-task=40
#SBATCH --output=outputs_per_country/logs/parallel_10_%j.out
#SBATCH --error=outputs_per_country/logs/parallel_10_%j.err
#SBATCH --mail-type=END,FAIL

set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

echo "[INFO] Starting parallel script 10/40 (T3) at $(date)"
echo "[INFO] Processing 1 countries in this batch: CAN"
echo "[INFO] Tier: T3 | Memory: 95G | CPUs: 40 | Time: 48:00:00"

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
# Use ${VAR:-} syntax to avoid 'unbound variable' errors with set -u
SCENARIO_FLAG=""
if [ -n "${SUPPLY_FACTOR:-}" ]; then
    SCENARIO_FLAG="--supply-factor $SUPPLY_FACTOR"
    echo "[INFO] Running single scenario: ${SUPPLY_FACTOR} (supply factor)"
elif [ "${RUN_ALL_SCENARIOS:-}" = "1" ]; then
    SCENARIO_FLAG="--run-all-scenarios"
    echo "[INFO] Running ALL scenarios (100%, 90%, 80%, 70%, 60%)"
fi

# Process countries in this batch

echo "[INFO] Processing CAN (T3)..."
MAX_RETRIES=3
for ATTEMPT in $(seq 1 $MAX_RETRIES); do
    if $PY process_country_supply.py CAN $SCENARIO_FLAG --output-dir outputs_per_country; then
        echo "[SUCCESS] CAN completed (attempt $ATTEMPT)"
        break
    else
        if [ "$ATTEMPT" -lt "$MAX_RETRIES" ]; then
            echo "[WARN] CAN failed on attempt $ATTEMPT/$MAX_RETRIES - retrying in 10s..."
            sleep 10
        else
            echo "[ERROR] CAN failed after $MAX_RETRIES attempts"
        fi
    fi
done

echo "[INFO] Batch 10/40 (T3) completed at $(date)"
