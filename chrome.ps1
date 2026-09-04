<#
Sobe um Chrome REAL (sem flags de automacao) com perfil persistente e a porta de
depuracao aberta, para o Playwright se conectar via CDP.

Por que assim: quando o proprio Playwright lanca o navegador ele adiciona
--enable-automation e correlatos, que Imperva/reCAPTCHA detectam. Conectando a um
Chrome comum, o fingerprint e o de um navegador normal (navigator.webdriver fica
false, sem barra de "controlado por software de teste").

A lista de flags e deliberadamente curta: cada flag atipica e, ela propria, um
sinal. So esta aqui o minimo para (a) abrir o CDP, (b) usar perfil proprio e
(c) manter o idioma coerente com um usuario brasileiro.

Diferenca para o chrome.sh (Linux): la era preciso exportar LANGUAGE e TZ porque
o sistema estava em en_US com IP de Goiania - incoerencia que derruba o score do
reCAPTCHA v3. Neste Windows o locale e o fuso ja sao pt-BR/America/Sao_Paulo,
entao nao ha nada a corrigir.
#>
[CmdletBinding()]
param(
    [string]$Perfil = $(if ($env:CHROME_PROFILE) { $env:CHROME_PROFILE }
                         else { Join-Path $PSScriptRoot 'chrome-profile' }),
    [int]$Port = $(if ($env:CDP_PORT) { [int]$env:CDP_PORT } else { 9222 })
)

$ErrorActionPreference = 'Stop'

$exe = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $exe) { throw "chrome.exe nao encontrado nos caminhos padrao." }
if (-not (Test-Path $Perfil)) { New-Item -ItemType Directory -Force $Perfil | Out-Null }

$flags = @(
    "--remote-debugging-port=$Port"
    "--user-data-dir=$Perfil"
    '--lang=pt-BR'
    '--accept-lang=pt-BR,pt,en-US,en'
    '--no-first-run'
    '--no-default-browser-check'
    'https://goias.equatorialenergia.com.br/LoginGO.aspx'
)

Write-Host "Subindo Chrome (perfil: $Perfil | CDP: $Port)"
Start-Process -FilePath $exe -ArgumentList $flags

# Espera o CDP responder antes de devolver o controle - quem chama (coletar.ps1)
# conta com isso para nao tentar conectar cedo demais.
for ($i = 0; $i -lt 30; $i++) {
    try {
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 `
            "http://127.0.0.1:$Port/json/version" | Out-Null
        Write-Host "CDP no ar em 127.0.0.1:$Port"
        exit 0
    } catch {
        Start-Sleep -Seconds 2
    }
}
throw "Chrome subiu mas o CDP nao respondeu na porta $Port."
