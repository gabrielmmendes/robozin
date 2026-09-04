#!/usr/bin/env bash
#
# LINUX-ONLY. No Windows use chrome.ps1, que e o equivalente
# mantido deste script (google-chrome/pkill/setsid/curl nao existem la).
# Sobe um Chrome REAL (sem flags de automacao) com perfil persistente e a porta
# de depuracao aberta para o Playwright se conectar via CDP.
#
# Por que assim: quando o proprio Playwright lanca o navegador ele adiciona
# --enable-automation e correlatos, que Imperva/reCAPTCHA detectam. Conectando a
# um Chrome comum, o fingerprint e o de um navegador normal (navigator.webdriver
# fica false, sem barra de "controlado por software de teste").
#
# A lista de flags e deliberadamente curta: cada flag atipica e, ela propria, um
# sinal. So esta aqui o minimo para (a) abrir o CDP, (b) usar perfil proprio e
# (c) manter o idioma coerente com um usuario brasileiro.
set -euo pipefail

PROFILE="${CHROME_PROFILE:-${HOME}/equatorial-login/chrome-profile}"
PORT="${CDP_PORT:-9222}"

mkdir -p "$PROFILE"

# O sistema esta em en_US e o IP e de Goiania: sem isso, navigator.languages
# reporta en-US num acesso brasileiro - incoerencia que derruba o score do
# reCAPTCHA v3. --accept-lang ajusta tanto o header Accept-Language quanto
# navigator.languages; TZ mantem o relogio do JS em America/Sao_Paulo.
export LANGUAGE="pt_BR:pt"
export TZ="America/Sao_Paulo"

exec google-chrome \
  --remote-debugging-port="$PORT" \
  --user-data-dir="$PROFILE" \
  --lang=pt-BR \
  --accept-lang="pt-BR,pt,en-US,en" \
  --no-first-run \
  --no-default-browser-check \
  "https://goias.equatorialenergia.com.br/LoginGO.aspx"
