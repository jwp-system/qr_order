#!/usr/bin/env bash
# 코드 업데이트 배포. 서버의 저장소 루트에서 실행:
#   bash deploy/deploy.sh
set -euo pipefail

cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"

git pull --ff-only origin main
uv sync --frozen --no-dev
uv run --no-dev alembic upgrade head
sudo systemctl restart qr-order

sleep 2
curl -fsS http://127.0.0.1:8000/healthz && echo
