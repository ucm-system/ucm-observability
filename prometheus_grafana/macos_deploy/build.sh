#!/bin/bash
# ========================================================
# UCM Observability Deploy Tool - macOS build script
# Usage: bash build.sh
# Output: ./dist/UCMObsDeploy.app (double-click to run)
# ========================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

echo "============================================="
echo " UCM Observability - building macOS .app ..."
echo "============================================="

# 1. Install PyInstaller
python3 -m pip install pyinstaller --quiet

# 2. Verify Python and PyInstaller
python3 --version
python3 -m PyInstaller --version >/dev/null 2>&1 || {
    echo "[ERROR] PyInstaller not found. Run: pip3 install pyinstaller"
    exit 1
}

# 3. Build from project root (PyInstaller resolves data files relative to CWD)
cd "$PROJ_ROOT"

python3 -m PyInstaller --noconfirm --onefile --windowed --name UCMObsDeploy \
    --add-data "prometheus_grafana/prometheus.yml:." \
    --add-data "prometheus_grafana/docker-compose.yaml:." \
    --add-data "prometheus_grafana/grafana-datasource.yml:." \
    --add-data "prometheus_grafana/grafana-dashboard-provider.yml:." \
    --add-data "prometheus_grafana/src/deploy_client_core.py:." \
    --add-data "prometheus_grafana/dashboards:dashboards" \
    "prometheus_grafana/macos_deploy/deploy_client.py"

echo "============================================="
echo " Build OK: ${PROJ_ROOT}/dist/UCMObsDeploy.app"
echo "============================================="

# 4. Cleanup intermediate files
rm -rf "${PROJ_ROOT}/build"
rm -f "${PROJ_ROOT}/UCMObsDeploy.spec"
