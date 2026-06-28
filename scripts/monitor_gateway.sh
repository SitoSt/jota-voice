#!/usr/bin/env bash
# monitor_gateway.sh — Monitoriza jota-gateway en tiempo real desde el Mac.
#
# Uso: bash scripts/monitor_gateway.sh
#
# Requiere GATEWAY_SSH_HOST en .env.local (usuario@ip-del-servidor).
# Conecta al servidor por SSH y sigue los logs de jota_gateway,
# filtrando y coloreando por severidad.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$REPO_DIR/.env.local"

if [ -f "$ENV_FILE" ]; then
    set -a; . "$ENV_FILE"; set +a
fi

if [ -z "${GATEWAY_SSH_HOST:-}" ]; then
    echo "Error: GATEWAY_SSH_HOST no definido." >&2
    echo "Añade GATEWAY_SSH_HOST=usuario@ip en .env.local" >&2
    exit 1
fi

CONTAINER="jota_gateway"

RED='\033[0;31m'
YELLOW='\033[0;33m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
RESET='\033[0m'

echo -e "${CYAN}=== Monitor jota-gateway (${CONTAINER} en ${GATEWAY_SSH_HOST}) ===${RESET}"
echo -e "${CYAN}Conectando a ${GATEWAY_SSH_HOST}...${RESET}"
echo ""

ssh -o ConnectTimeout=10 "${GATEWAY_SSH_HOST}" "docker logs ${CONTAINER} -f --tail=0 2>&1" | \
while IFS= read -r line; do
    if echo "${line}" | grep -q "ERROR\|Exception\|Traceback\|error"; then
        echo -e "${RED}${line}${RESET}"
    elif echo "${line}" | grep -q "WARNING\|Warning"; then
        echo -e "${YELLOW}${line}${RESET}"
    elif echo "${line}" | grep -q "transcription\|llm_start\|tts_start\|session_start\|done\|Handshake"; then
        echo -e "${GREEN}${line}${RESET}"
    else
        echo "${line}"
    fi
done
