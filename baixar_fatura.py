#!/usr/bin/env python3
"""
Baixa a fatura mais recente da Agencia Virtual da Equatorial Goias.

Fluxo: garante login (login.py) -> Historico de Faturas -> consulta ano a ano,
do mais recente para tras -> baixa a fatura do topo.

Uso (Windows):
    .\\chrome.ps1
    .\\coletar.ps1
Uso (direto):
    python baixar_fatura.py
"""

import os
import random
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from login import (
    ESPERA_BASE, ESPERA_MAX, PRAZO, URL_FATURAS,
    area_logada, conectar, escutar_dialogos, garantir_login, ir_para,
    liberar_rede, log, preparar_rede,
)

FATURAS = Path(__file__).parent / "faturas"
ANOS_ATRAS = int(os.environ.get("EQUATORIAL_ANOS_ATRAS", "3"))
SEL_CONSULTAR = ("#CONTENT_btEnviar, input[value*='Consultar' i], "
                 "button:has-text('Consultar')")


def dump_estrutura(page, rotulo):
    """Registra selects, botoes e links da pagina.

    O portal e ASP.NET WebForms: ids sao gerados e mudam de pagina para pagina,
    entao vale guardar o mapa de cada estado para ajustar os seletores.
    """
    dados = page.evaluate("""() => ({
        selects: [...document.querySelectorAll('select')].map(s => ({
            id: s.id, name: s.name, visivel: s.offsetParent !== null,
            valor: s.value,
            opcoes: [...s.options].slice(0, 25).map(o => ({v: o.value, t: o.text.trim()}))
        })),
        botoes: [...document.querySelectorAll('button, input[type=submit], input[type=button], a.button')]
            .filter(b => b.offsetParent !== null)
            .map(b => ({txt: (b.innerText || b.value || '').trim().slice(0, 40),
                        id: b.id, name: b.name,
                        onclick: (b.getAttribute('onclick') || '').slice(0, 120)})),
        tabelas: [...document.querySelectorAll('table')].map(t => ({
            id: t.id, linhas: t.rows.length,
            cabecalho: [...(t.rows[0] ? t.rows[0].cells : [])].map(c => c.innerText.trim()).slice(0, 10),
            amostra: [...t.rows].slice(1, 4).map(r =>
                [...r.cells].map(c => c.innerText.trim().slice(0, 30)))
        })),
        links: [...document.querySelectorAll('a')]
            .filter(a => a.offsetParent !== null)
            .map(a => ({txt: (a.innerText || '').trim().slice(0, 40),
                        href: (a.getAttribute('href') || '').slice(0, 120),
                        onclick: (a.getAttribute('onclick') || '').slice(0, 140)}))
            .filter(a => /fatura|segunda|pdf|boleto|download|visualiz|imprim|\\d{2}\\/\\d{4}/i
                         .test(a.txt + a.href + a.onclick)).slice(0, 25)
    })""")
    destino = FATURAS / f"estrutura-{rotulo}.json"
    destino.write_text(__import__("json").dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Estrutura ({rotulo}) salva em {destino.name}")
    return dados


def tem_resultados(page):
    """True se a consulta trouxe tabela com linhas de dados.

    A pagina de consulta nao tem nenhuma <table> no estado inicial (confirmado
    em estrutura-historico.json: "tabelas": []), entao qualquer tabela com mais
    de uma linha e resultado de verdade, nao layout.
    """
    return page.locator("table tr").count() > 1


def dispensar_modal(page, timeout_s=10):
    """Fecha o aviso de "nao existe faturas" e devolve a mensagem, ou None.

    Um ano sem faturas nao devolve tabela vazia: devolve um modal. Ele nao tem
    id nem name - estrutura-resultado.json registrou so os botoes anonimos "x" e
    "OK" -, entao o unico jeito de alcanca-lo e por texto e por role. Sem
    dispensa-lo, ele fica por cima e bloqueia o proximo clique em Consultar.
    """
    fim = time.time() + timeout_s
    while time.time() < fim:
        texto = page.evaluate("document.body.innerText || ''")
        achado = re.search(r"N[aã]o existe Faturas[^\n]*", texto)
        if achado:
            msg = achado.group(0).strip()
            try:
                ok = page.get_by_role("button", name=re.compile(r"^\s*OK\s*$", re.I)).first
                if ok.count():
                    ok.click()
                    page.wait_for_timeout(1000)
            except Exception as e:
                log(f"(nao consegui clicar no OK do modal: {str(e)[:60]})")
            return msg
        if tem_resultados(page):
            return None
        page.wait_for_timeout(500)
    return None


def consultar(page):
    """Consulta o historico ano a ano, do mais recente para tras.

    A pagina lista apenas as faturas JA PAGAS da UC, entao o ano corrente pode
    estar vazio - foi exatamente o que travou a coleta antes: o codigo fixava
    max(anos) e desistia quando 2026 nao trouxe nada. Descer para 2025, 2024...
    e o que faz a consulta render resultado.

    Devolve o ano que trouxe linhas, ou None.
    """
    page.wait_for_selector("select", timeout=30_000)
    est = dump_estrutura(page, "historico")

    # O select do ano e o que tem anos como opcoes (o outro select e a UC).
    alvo = None
    for s in est["selects"]:
        if not s["visivel"]:
            continue
        anos = [o["t"] for o in s["opcoes"] if re.fullmatch(r"(19|20)\d{2}", o["t"])]
        if anos and (alvo is None or len(anos) > len(alvo[1])):
            alvo = (s, anos)
    if alvo is None:
        log("Nao encontrei o select de ano")
        return None

    sel_ano, anos = alvo
    seletor = f"#{sel_ano['id']}" if sel_ano["id"] else f"select[name='{sel_ano['name']}']"
    candidatos = sorted(anos, reverse=True)[:ANOS_ATRAS]
    log(f"Anos a tentar: {', '.join(candidatos)}")

    for ano in candidatos:
        # Consultar e um postback: a pagina volta inteira e o select pode ter
        # perdido a selecao, entao reescolhemos o ano a cada volta.
        page.wait_for_selector(seletor, timeout=20_000)
        page.select_option(seletor, label=ano)
        page.wait_for_timeout(random.randint(600, 1400))

        botao = page.locator(SEL_CONSULTAR).first
        if botao.count() == 0:
            log("Botao Consultar nao encontrado")
            return None
        log(f"Consultando {ano}")
        botao.click()
        try:
            page.wait_for_load_state("networkidle", timeout=45_000)
        except PWTimeout:
            pass

        vazio = dispensar_modal(page)
        dump_estrutura(page, f"resultado-{ano}")
        if vazio:
            log(f"Ano {ano}: {vazio}")
            continue
        if tem_resultados(page):
            log(f"Ano {ano}: tabela com resultados")
            return ano
        log(f"Ano {ano}: nem modal nem tabela; tentando o ano anterior")

    log(f"Nenhum dos {len(candidatos)} anos mais recentes retornou faturas")
    return None


def salvar_pdf(page, ctx, acionar, nome):
    """Dispara `acionar` e salva o PDF, tentando os tres caminhos possiveis.

    O portal ora entrega o PDF como download, ora como navegacao para um
    ashx/aspx que responde application/pdf, ora abre em aba nova. Um handler de
    response cobre os casos em que o evento de download nao dispara.
    """
    FATURAS.mkdir(exist_ok=True)
    capturado = {}

    def on_resp(r):
        try:
            ct = (r.headers or {}).get("content-type", "")
            if "pdf" in ct.lower() and not capturado:
                capturado["body"] = r.body()
                capturado["url"] = r.url
        except Exception:
            pass

    ctx.on("response", on_resp)
    # sem isto o Chrome pode bloquear o download quando dirigido via CDP
    try:
        cdp = ctx.new_cdp_session(page)
        cdp.send("Browser.setDownloadBehavior",
                 {"behavior": "allow", "downloadPath": str(FATURAS)})
    except Exception as e:
        log(f"(setDownloadBehavior indisponivel: {str(e)[:60]})")

    destino = FATURAS / nome
    try:
        with page.expect_download(timeout=30_000) as info:
            acionar()
        info.value.save_as(str(destino))
        log(f"Baixado via evento de download: {destino.name}")
        return destino
    except PWTimeout:
        pass
    finally:
        ctx.remove_listener("response", on_resp)

    page.wait_for_timeout(4000)
    if capturado.get("body"):
        destino.write_bytes(capturado["body"])
        log(f"Baixado via resposta PDF ({capturado['url'][:70]}): {destino.name}")
        return destino

    # ultima chance: alguma aba nova com o PDF
    for pg in ctx.pages:
        if ".pdf" in pg.url.lower() or "pdf" in pg.url.lower():
            corpo = ctx.request.get(pg.url).body()
            if corpo[:4] == b"%PDF":
                destino.write_bytes(corpo)
                log(f"Baixado da aba {pg.url[:70]}: {destino.name}")
                return destino
    return None


def baixar_mais_recente(page, ctx, ano):
    """Identifica a fatura do topo da lista e baixa."""
    est = dump_estrutura(page, f"download-{ano}")

    tabela = max((t for t in est["tabelas"] if t["linhas"] > 1),
                 key=lambda t: t["linhas"], default=None)
    if tabela:
        log(f"Tabela com {tabela['linhas']} linhas | cabecalho: {tabela['cabecalho']}")
    else:
        log("Nenhuma tabela com resultados")

    # A fatura mais recente e a primeira linha; procuramos nela o acionador.
    gatilho = page.locator(
        "table tr:not(:first-child) a[href*='.pdf'], "
        "table tr:not(:first-child) a[onclick], "
        "table tr:not(:first-child) input[type=image], "
        "table tr:not(:first-child) a"
    ).first
    if gatilho.count() == 0:
        log(f"Nao achei link de download na tabela - veja estrutura-download-{ano}.json")
        return None

    rotulo = (gatilho.inner_text() or "fatura").strip() or "fatura"
    log(f"Fatura mais recente: {rotulo!r}")
    nome = f"fatura-{re.sub(r'[^0-9A-Za-z]+', '-', rotulo).strip('-') or time.strftime('%Y%m%d')}.pdf"
    return salvar_pdf(page, ctx, lambda: gatilho.click(), nome)


def coletar():
    with sync_playwright() as p:
        ctx, page = conectar(p)
        dialogos = escutar_dialogos(page)

        if not garantir_login(page, dialogos, ctx=ctx, prazo_s=PRAZO):
            log("Nao consegui logar dentro do prazo")
            return 1

        # A consulta tambem pode falhar por instabilidade; insiste com a mesma
        # politica de espera curta usada no login.
        limite = time.time() + PRAZO
        rodada = 0
        while time.time() < limite:
            rodada += 1
            log(f"=== Consulta, rodada {rodada} ===")
            if not area_logada(page, timeout_s=8):
                log("Sessao caiu; refazendo login")
                if not garantir_login(page, dialogos, ctx=ctx,
                                      prazo_s=int(limite - time.time())):
                    return 1
            ir_para(page, URL_FATURAS)
            page.wait_for_timeout(2500)

            try:
                ano = consultar(page)
                if ano:
                    caminho = baixar_mais_recente(page, ctx, ano)
                    if caminho and caminho.exists() and caminho.stat().st_size > 1000:
                        log(f"PRONTO: {caminho} ({caminho.stat().st_size} bytes)")
                        return 0
                    log("Consulta feita, mas o download nao veio")
            except PWTimeout as e:
                log(f"Timeout na rodada {rodada}: {str(e)[:90]}")

            page.screenshot(path=str(FATURAS / f"rodada-{rodada}.png"), full_page=True)
            espera = random.randint(ESPERA_BASE, ESPERA_MAX)
            if time.time() + espera > limite:
                break
            log(f"Aguardando {espera}s antes de tentar de novo")
            page.wait_for_timeout(espera * 1000)

        log("Prazo esgotado sem baixar a fatura")
        return 1


def main():
    FATURAS.mkdir(exist_ok=True)
    # A rota so muda com EQUATORIAL_ROTACIONAR=1; o finally e o que garante que
    # a maquina nao fique navegando pelo 4G depois que o robo sai.
    estado_rede = preparar_rede()
    try:
        return coletar()
    finally:
        liberar_rede(estado_rede)


if __name__ == "__main__":
    sys.exit(main())
