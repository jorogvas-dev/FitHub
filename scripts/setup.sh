#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
command -v docker >/dev/null || { echo 'Install and start Docker first.'; exit 1; }
docker info >/dev/null
if [ ! -f .env ]; then
  command -v openssl >/dev/null || { echo 'openssl is required to generate secrets.'; exit 1; }
  umask 077
  password=$(openssl rand -hex 32)
  secret=$(openssl rand -hex 32)
  sed -e "s/POSTGRES_PASSWORD=GENERATE_WITH_SETUP_SCRIPT/POSTGRES_PASSWORD=$password/" -e "s/SECRET_KEY=GENERATE_WITH_SETUP_SCRIPT/SECRET_KEY=$secret/" .env.example > .env
fi
docker compose config --quiet
docker compose pull db chroma
docker compose build web nginx
docker compose up -d --wait --wait-timeout 180 db chroma
docker compose run --rm --no-deps -T web flask --app app db-upgrade
docker compose up -d --wait --wait-timeout 300
docker compose ps
echo 'Ready. Default: https://localhost:8443 (self-signed certificate); http://localhost:8010 redirects. Logs: http://localhost:8088. Check .env for custom ports.'
