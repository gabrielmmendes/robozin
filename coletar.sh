#!/usr/bin/env bash
#
# LINUX-ONLY. No Windows use coletar.ps1, que e o equivalente
# mantido deste script (google-chrome/pkill/setsid/curl nao existem la).
# Coleta autonoma da fatura mais recente.
#
# Mantem o Chrome de pe (ele cai em execucoes longas) e reexecuta a coleta ate
# conseguir. O backoff entre tentativas de login fica por conta do baixar_fatura.py;
# aqui so garantimos que o navegador exista e que a falha de um ciclo nao encerre
# o processo.
set -uo pipefail

cd "$(dirname "$0")"
source ./credenciais.env

: "${EQUATORIAL_PRAZO:=21600}"       # 6h por ciclo
: "${EQUATORIAL_ESPERA:=600}"        # 1a pausa: 10min (dobra a cada recusa)
: "${EQUATORIAL_ESPERA_MAX:=1800}"   # teto: 30min
: "${EQUATORIAL_DWELL:=20}"
: "${CICLOS:=4}"
export EQUATORIAL_PRAZO EQUATORIAL_ESPERA EQUATORIAL_ESPERA_MAX EQUATORIAL_DWELL

garantir_chrome() {
  if ! curl -sS --max-time 3 "http://127.0.0.1:${CDP_PORT:-9222}/json/version" >/dev/null 2>&1; then
    echo "[$(date +%H:%M:%S)] Chrome fora do ar; subindo"
    pkill -f "equatorial-login/[c]hrome-profile" 2>/dev/null
    sleep 3
    setsid nohup ./chrome.sh > saida/chrome.log 2>&1 < /dev/null &
    for _ in $(seq 1 30); do
      curl -sS --max-time 2 "http://127.0.0.1:${CDP_PORT:-9222}/json/version" >/dev/null 2>&1 && return 0
      sleep 2
    done
    echo "[$(date +%H:%M:%S)] Chrome nao subiu"; return 1
  fi
}

mkdir -p saida faturas
for ciclo in $(seq 1 "$CICLOS"); do
  echo "########## Ciclo $ciclo/$CICLOS - $(date +%d/%m\ %H:%M:%S) ##########"
  garantir_chrome || { sleep 60; continue; }
  if ./.venv/bin/python baixar_fatura.py; then
    echo "[$(date +%H:%M:%S)] Fatura baixada; encerrando"
    ls -la faturas/*.pdf 2>/dev/null
    exit 0
  fi
  echo "[$(date +%H:%M:%S)] Ciclo $ciclo sem sucesso"
done
echo "Todos os $CICLOS ciclos falharam"
exit 1
