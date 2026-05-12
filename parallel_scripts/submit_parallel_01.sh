#!/bin/bash --login
#SBATCH --job-name=p01_t1
#SBATCH --partition=Long
#SBATCH --time=168:00:00
#SBATCH --mem=95G
#SBATCH --ntasks=1
#SBATCH --nodes=1
#SBATCH --cpus-per-task=40
#SBATCH --output=outputs_per_country/logs/parallel_01_%j.out
#SBATCH --error=outputs_per_country/logs/parallel_01_%j.err
#SBATCH --mail-type=END,FAIL

set -euo pipefail
cd "$SLURM_SUBMIT_DIR"

echo "[INFO] Starting parallel script 1/40 (T1) at $(date)"
echo "[INFO] Processing 1 countries in this batch: CHN"
echo "[INFO] Tier: T1 | Memory: 95G | CPUs: 40 | Time: 168:00:00"

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

echo "[INFO] Processing CHN (T1)..."
MAX_RETRIES=3
for ATTEMPT in $(seq 1 $MAX_RETRIES); do
    if $PY process_country_supply.py CHN $SCENARIO_FLAG --output-dir outputs_per_country; then
        echo "[SUCCESS] CHN completed (attempt $ATTEMPT)"
        break
    else
        if [ "$ATTEMPT" -lt "$MAX_RETRIES" ]; then
            echo "[WARN] CHN failed on attempt $ATTEMPT/$MAX_RETRIES - retrying in 10s..."
            sleep 10
        else
            echo "[ERROR] CHN failed after $MAX_RETRIES attempts"
        fi
    fi
done

echo "[INFO] Batch 1/40 (T1) completed at $(date)"
