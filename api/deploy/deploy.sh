#!/usr/bin/env bash
#
# Pull the newest code and roll the API stack forward, in place.
#
# Run by .github/workflows/deploy_api.yml over SSH, and safe to run by hand:
#
#   cd /opt/marketstore/DESKTOP_MarketStore-POS && ./api/deploy/deploy.sh
#
# `docker compose up -d --build` recreates only the containers whose image or
# config actually changed, so postgres and redis keep running and their volumes
# are never touched. There is deliberately no `down` anywhere in here.

set -euo pipefail

BRANCH="${DEPLOY_BRANCH:-main}"
COMPOSE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_DIR="$(cd "${COMPOSE_DIR}/.." && pwd)"
HEALTH_URL="${DEPLOY_HEALTH_URL:-http://127.0.0.1:8000/health}"
HEALTH_RETRIES="${DEPLOY_HEALTH_RETRIES:-30}"

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31m!!! %s\033[0m\n' "$*" >&2; exit 1; }

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  else
    docker-compose "$@"
  fi
}

cd "${REPO_DIR}"

[ -f "${COMPOSE_DIR}/.env" ] || fail "api/.env topilmadi: ${COMPOSE_DIR}/.env"

log "Kod yangilanmoqda (${BRANCH})"
PREVIOUS_SHA="$(git rev-parse HEAD)"
git fetch --quiet origin "${BRANCH}"
# Hard reset rather than pull: the server is a deployment target, not a place
# anyone edits, and a stray local change must never block a release.
git reset --hard --quiet "origin/${BRANCH}"
NEW_SHA="$(git rev-parse HEAD)"
echo "${PREVIOUS_SHA:0:7} -> ${NEW_SHA:0:7}"

if [ "${PREVIOUS_SHA}" = "${NEW_SHA}" ] && [ "${DEPLOY_FORCE:-0}" != "1" ]; then
  log "O'zgarish yo'q, qayta qurish shart emas"
  exit 0
fi

cd "${COMPOSE_DIR}"

# Put the previous commit back and rebuild from it, so what the server runs and
# what its checkout says are never out of step.
rollback() {
  local reason="$1"
  log "${reason} - ${PREVIOUS_SHA:0:7} ga qaytarilmoqda"
  compose logs --tail=80 api 2>/dev/null || true
  ( cd "${REPO_DIR}" && git reset --hard --quiet "${PREVIOUS_SHA}" )
  compose up -d --build --remove-orphans || true
  fail "Deploy muvaffaqiyatsiz, ${PREVIOUS_SHA:0:7} ga qaytarildi"
}

log "Konteynerlar qurilmoqda va yangilanmoqda"
# Alembic migratsiyasi api konteynerining CMD'ida ishlaydi.
# A failed build leaves the running containers untouched, so the shops are still
# served by the previous image while we put the checkout back to match.
compose up -d --build --remove-orphans || rollback "Qurish muvaffaqiyatsiz"

log "Sog'liq tekshiruvi"
for attempt in $(seq 1 "${HEALTH_RETRIES}"); do
  if curl --fail --silent --show-error --max-time 5 "${HEALTH_URL}" >/dev/null 2>&1; then
    log "API javob bermoqda (${attempt}-urinish)"
    compose ps
    log "Eski image'lar tozalanmoqda"
    docker image prune --force --filter "until=168h" >/dev/null 2>&1 || true
    log "Deploy tugadi: ${NEW_SHA:0:7}"
    exit 0
  fi
  sleep 2
done

rollback "API ${HEALTH_RETRIES} urinishdan keyin ham javob bermadi"
