#!/usr/bin/env bash
# Google Cloud VM（Ubuntu）上で btc_paper_trader をセットアップする例。
# 使い方:
#   export DEPLOY_ROOT="$HOME/crypto_radar"   # リポジトリを clone したパス
#   bash btc_paper_trader/deploy/gcp/install.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
# install.sh は backend/btc_paper_trader/deploy/gcp/ にある → リポジトリルートは 4 つ上
DEPLOY_ROOT="${DEPLOY_ROOT:-$(cd "$SCRIPT_DIR/../../../.." && pwd)}"
BACKEND="$DEPLOY_ROOT/backend"
VENV="$BACKEND/venv"
PY="${VENV}/bin/python"
PIP="${VENV}/bin/pip"

echo "DEPLOY_ROOT=$DEPLOY_ROOT"

if [[ ! -d "$BACKEND/btc_paper_trader" ]]; then
  echo "error: $BACKEND/btc_paper_trader not found"
  exit 1
fi

sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip

python3 -m venv "$VENV"
"$PIP" install -U pip
"$PIP" install -r "$BACKEND/btc_paper_trader/requirements-btc.txt"

"$PY" -m btc_paper_trader fetch || true

UNIT_SRC="$BACKEND/btc_paper_trader/deploy/gcp/btc-paper-trader.service"
UNIT_DST="/etc/systemd/system/btc-paper-trader.service"
if [[ -f "$UNIT_SRC" ]] && command -v sudo >/dev/null; then
  # @DEPLOY_ROOT@ と @USER@ を置換
  sudo sed -e "s|@DEPLOY_ROOT@|$DEPLOY_ROOT|g" -e "s|@USER@|$USER|g" "$UNIT_SRC" | sudo tee "$UNIT_DST" >/dev/null
  echo "Installed $UNIT_DST"
  echo "  sudo systemctl daemon-reload"
  echo "  sudo systemctl enable --now btc-paper-trader"
else
  echo "Skipping systemd (no sudo or unit missing). Run paper manually:"
  echo "  cd $BACKEND && $PY -m btc_paper_trader paper"
fi
