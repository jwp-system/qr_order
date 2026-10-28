#!/usr/bin/env bash
# Oracle Cloud Ubuntu VM 최초 1회 설정. 저장소를 clone한 뒤 저장소 루트에서 실행:
#   bash deploy/setup.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

# 1. 패키지: PostgreSQL, nginx
sudo apt-get update
sudo apt-get install -y postgresql nginx curl

# 2. uv (Python 3.12는 uv가 .python-version을 보고 설치)
if ! command -v uv >/dev/null && [ ! -x "$HOME/.local/bin/uv" ]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

# 3. .env (없으면 예시에서 복사 후 중단 — 값을 채우고 다시 실행)
if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env
    echo ".env를 만들었습니다. 비밀번호/SECRET_KEY를 채운 뒤 다시 실행하세요."
    exit 1
fi
set -a; . ./.env; set +a

# 4. DB 사용자/데이터베이스 생성 (이미 있으면 건너뜀)
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='${POSTGRES_USER}'" | grep -q 1 \
    || sudo -u postgres psql -c "CREATE USER \"${POSTGRES_USER}\" WITH PASSWORD '${POSTGRES_PASSWORD}';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='${POSTGRES_DB}'" | grep -q 1 \
    || sudo -u postgres psql -c "CREATE DATABASE \"${POSTGRES_DB}\" OWNER \"${POSTGRES_USER}\";"

# 5. 의존성 설치 + 마이그레이션
uv sync --frozen --no-dev
uv run --no-dev alembic upgrade head

# 6. systemd 서비스 (경로/사용자를 현재 위치에 맞춰 설치)
sed -e "s#/home/ubuntu/qr_order#${APP_DIR}#g" -e "s#^User=ubuntu#User=$USER#" -e "s#^Group=ubuntu#Group=$USER#" \
    deploy/qr-order.service | sudo tee /etc/systemd/system/qr-order.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now qr-order

# 7. nginx
sudo cp deploy/nginx-qr-order.conf /etc/nginx/sites-available/qr-order
sudo ln -sf /etc/nginx/sites-available/qr-order /etc/nginx/sites-enabled/qr-order
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

# 8. OS 방화벽: Oracle Ubuntu 이미지는 iptables가 80/443을 막고 있다
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo apt-get install -y iptables-persistent
sudo netfilter-persistent save

echo "완료: curl http://127.0.0.1/healthz"
