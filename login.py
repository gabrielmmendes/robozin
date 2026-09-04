#!/usr/bin/env python3
"""
Login automatizado na Agencia Virtual da Equatorial Goias.
    https://goias.equatorialenergia.com.br/LoginGO.aspx

Camadas de defesa da pagina (todas verificadas em execucao):
  1. Imperva Incapsula        - WAF + fingerprint JS (cookies visid_incap_/nlbi_)
  2. reCAPTCHA Enterprise v3  - score invisivel, action="login", token expira ~2min
  3. Transmit Security (DRS)  - fingerprint de dispositivo -> hidden #tokenTransmit

Nenhum desses tokens e forjavel por HTTP puro: dependem de JS lendo o ambiente
real do navegador. Por isso dirigimos um Chrome de verdade via CDP (ver
chrome.sh) em vez de deixar o Playwright lancar o browser, o que acrescentaria
--enable-automation e afins.
"""

import os
import random
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

URL = "https://goias.equatorialenergia.com.br/LoginGO.aspx"
URL_HOME = "https://goias.equatorialenergia.com.br/"
AQUECER = os.environ.get("EQUATORIAL_AQUECER", "1") != "0"
CDP = f"http://127.0.0.1:{os.environ.get('CDP_PORT', '9222')}"
OUT = Path(__file__).parent / "saida"

UC = "".join(filter(str.isdigit, os.environ.get("EQUATORIAL_UC", "")))
DOC = "".join(filter(str.isdigit, os.environ.get("EQUATORIAL_DOC", "")))
NASC = os.environ.get("EQUATORIAL_NASC", "").strip()  # DD/MM/AAAA
# Pausa entre tentativas: intervalo curto e FIXO, sorteado nesta faixa.
# Nao e backoff. A recusa #001/#002 e independente entre tentativas (o proprio
# login manual humano tambem a recebe), entao esperar mais nao melhora a chance
# da proxima - so gasta a janela. Em 51 min, ~50 tentativas contra 7 do
# esquema exponencial anterior.
ESPERA_BASE = int(os.environ.get("EQUATORIAL_ESPERA", "20"))   # piso da pausa
ESPERA_MAX = int(os.environ.get("EQUATORIAL_ESPERA_MAX", "45"))  # teto da pausa
PRAZO = int(os.environ.get("EQUATORIAL_PRAZO", "21600"))       # desiste depois de 6h
DWELL = int(os.environ.get("EQUATORIAL_DWELL", "40"))  # permanencia antes de preencher
ROTACIONAR = os.environ.get("EQUATORIAL_ROTACIONAR", "0") == "1"
DOMINIO = "goias.equatorialenergia.com.br"

SEL_UC = "#WEBDOOR_headercorporativogo_txtUC"
SEL_DOC = "#WEBDOOR_headercorporativogo_txtDocumento"
SEL_ENTRAR = 'button.button[onclick*="ValidarCamposAreaLogada"]'
SEL_OK_REAL = "#WEBDOOR_headercorporativogo_okReal"
SEL_TK_RECAPTCHA = "#WEBDOOR_headercorporativogo_Recaptchav3_hfToken"
SEL_TK_TRANSMIT = "#tokenTransmit"
URL_FATURAS = ("https://goias.equatorialenergia.com.br/AgenciaGO/"
               "Servi%C3%A7os/comum/HistoricoFaturas.aspx")
SEL_DATA = "#WEBDOOR_headercorporativogo_txtData"
SEL_VALIDAR = "#WEBDOOR_headercorporativogo_btnValidar"
RECAPTCHA_KEY = "6LfWFzkoAAAAAAo7_vWKW7DIcigZYn0ec9ZV_43E"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def digitar(page, seletor, texto):
    """Digita tecla por tecla.

    Obrigatorio: os dois campos tem onpaste="return false" e aplicam mascara via
    onkeypress/onkeydown. page.fill() escreve direto no DOM sem disparar esses
    eventos, entao a mascara nao roda e a validacao do site falha.
    """
    campo = page.locator(seletor)
    campo.scroll_into_view_if_needed()
    caixa = campo.bounding_box()
    if caixa:  # move o mouse ate o campo em vez de teleportar o cursor
        page.mouse.move(
            caixa["x"] + caixa["width"] * random.uniform(0.2, 0.8),
            caixa["y"] + caixa["height"] * random.uniform(0.3, 0.7),
            steps=random.randint(8, 20),
        )
    campo.click()
    page.wait_for_timeout(random.randint(120, 350))
    for ch in texto:
        page.keyboard.type(ch, delay=random.randint(60, 170))
    page.wait_for_timeout(random.randint(200, 500))


def permanecer(page, segundos):
    """Fica na pagina interagindo, como um usuario que acabou de chegar.

    Nao e teatro: reCAPTCHA v3 e o DRS do Transmit pontuam tempo de permanencia
    e eventos de interacao. Submeter 7s depois do load, sem nenhum scroll ou
    movimento de mouse, e um padrao de bot ainda que o fingerprint do navegador
    seja perfeito.
    """
    fim = time.time() + segundos
    largura = page.viewport_size["width"] if page.viewport_size else 1280
    altura = page.viewport_size["height"] if page.viewport_size else 800
    while time.time() < fim:
        acao = random.random()
        if acao < 0.45:
            page.mouse.move(
                random.randint(60, largura - 60),
                random.randint(60, altura - 60),
                steps=random.randint(10, 30),
            )
        elif acao < 0.75:
            page.mouse.wheel(0, random.randint(-260, 320))
        page.wait_for_timeout(random.randint(700, 2200))


def aquecer(page):
    """Navega um pouco pelo site antes de ir ao login.

    Aterrissar direto em LoginGO.aspx, submeter e sumir e um padrao pobre de
    sinais: reCAPTCHA v3 pontua o historico de interacao *dentro da sessao*, e
    tanto o Imperva quanto o DRS do Transmit observam a sequencia de navegacao.
    Chegar pela home, ler um pouco e so entao ir ao login e o percurso que um
    cliente faz de verdade - e rende cookies e telemetria coerentes com ele.
    """
    try:
        log("Aquecendo a sessao pela home")
        page.goto(URL_HOME, wait_until="domcontentloaded", timeout=45_000)
        permanecer(page, random.randint(6, 12))

        # segue um link interno qualquer, como quem estava navegando
        internos = page.evaluate("""() => [...document.querySelectorAll('a')]
            .map(a => a.href)
            .filter(h => h && h.includes('equatorialenergia.com.br')
                      && !/Login|logout|sair/i.test(h))
            .slice(0, 40)""")
        if internos:
            destino = random.choice(internos)
            log(f"Visitando {destino[:70]}")
            page.goto(destino, wait_until="domcontentloaded", timeout=45_000)
            permanecer(page, random.randint(5, 10))
    except PWTimeout:
        log("Aquecimento incompleto (timeout); seguindo para o login")
    except Exception as e:
        log(f"Aquecimento falhou ({str(e)[:60]}); seguindo para o login")


def formatar_documento(digitos):
    if len(digitos) == 11:
        return f"{digitos[:3]}.{digitos[3:6]}.{digitos[6:9]}-{digitos[9:]}"
    if len(digitos) == 14:
        return f"{digitos[:2]}.{digitos[2:5]}.{digitos[5:8]}/{digitos[8:12]}-{digitos[12:]}"
    return digitos


def normalizar_documento(page, digitos):
    """Corrige o separador quando a mascara do site erra.

    mascaraMutuario dispara setTimeout(...,1) no onkeydown, ou seja, roda 1ms
    depois da tecla e as vezes antes do caractere entrar no campo. Digitando
    incrementalmente isso produz "123.456.789.01" - ponto onde deveria haver
    hifen -, e a versao de cpfCnpj ativa na pagina preserva separadores ja
    existentes, entao ela nunca se auto-corrige.
    """
    esperado = formatar_documento(digitos)
    if page.input_value(SEL_DOC) == esperado:
        return esperado, False
    page.evaluate(
        """([sel, val]) => {
            const el = document.querySelector(sel);
            el.value = val;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }""",
        [SEL_DOC, esperado],
    )
    return esperado, True


def esperar_transmit_pronto(page, timeout_s=20):
    """Espera o SDK do Transmit existir no escopo global.

    O site inicializa no evento load do proprio script:
        getElementById("ts-platform-script").addEventListener("load",
            () => tsPlatform.initialize({clientId: "..."}))
    initialize() e assincrona; clicar em Entrar antes dela terminar faz
    triggerActionEvent rejeitar - e ValidarCamposAreaLogada nao tem .catch(),
    entao o login morre em silencio, sem erro na tela e sem postback.

    Testar prontidao de verdade exigiria disparar um evento (o unico caminho que
    resolve e o proprio triggerActionEvent), o que sujaria a telemetria. Entao
    checamos so a presenca da API e deixamos submeter() cuidar do resto: ele tem
    o fallback com o .catch() que falta na pagina.
    """
    try:
        page.wait_for_function(
            "typeof window.tsPlatform?.drs?.triggerActionEvent === 'function'",
            timeout=timeout_s * 1000,
        )
        return True
    except PWTimeout:
        return False


def renovar_recaptcha(page):
    """Gera um token v3 novo imediatamente antes do submit.

    O token nasce no load da pagina e vale ~2 minutos. Com digitacao humanizada
    e a espera do SDK, o token original ja expirou na hora do submit - e o erro
    que volta e generico, dificil de diagnosticar.
    """
    return page.evaluate(
        """([key, sel]) => new Promise((res) => {
            if (typeof grecaptcha === 'undefined' || !grecaptcha.enterprise) {
                return res('grecaptcha ausente');
            }
            grecaptcha.enterprise.ready(() => {
                grecaptcha.enterprise.execute(key, { action: 'login' })
                    .then((t) => { document.querySelector(sel).value = t; res(`ok (${t.length} chars)`); })
                    .catch((e) => res('erro: ' + e));
            });
        })""",
        [RECAPTCHA_KEY, SEL_TK_RECAPTCHA],
    )


def submeter(page):
    """Clica em Entrar e garante que o postback saia.

    Caminho primario: o botao real, que executa ValidarCamposAreaLogada ->
    Transmit -> preenche #tokenTransmit -> setTimeout(2000) -> clica no okReal
    oculto. Deixar a cadeia nativa rodar e mais fiel do que montar o POST na mao.

    Fallback: se o token nao aparecer, refaz o que a pagina faria - com o .catch()
    que ela nao tem - e dispara o postback.
    """
    page.click(SEL_ENTRAR)
    try:
        page.wait_for_function(
            f"document.querySelector('{SEL_TK_TRANSMIT}')?.value?.length > 0",
            timeout=12_000,
        )
        log("Token Transmit preenchido pelo fluxo nativo")
        return "nativo"
    except PWTimeout:
        pass

    log("Fluxo nativo nao preencheu o token; aplicando fallback")
    r = page.evaluate(
        """([selTk, uc]) => new Promise((res) => {
            window.tsPlatform.drs.triggerActionEvent("logingo", { claimedUserId: uc })
                .then((resp) => {
                    document.querySelector(selTk).value = resp.actionToken;
                    res('ok');
                })
                .catch((e) => res('erro: ' + e));
            setTimeout(() => res('timeout'), 20000);
        })""",
        [SEL_TK_TRANSMIT, page.input_value(SEL_UC)],
    )
    if r != "ok":
        return f"falhou ({r})"
    page.wait_for_timeout(random.randint(1200, 2200))  # o site espera 2s aqui
    page.evaluate(f"document.querySelector('{SEL_OK_REAL}').click()")
    return "fallback"


def area_logada(page, timeout_s=20):
    """Confirma autenticacao pelo que so existe na area logada.

    Checar a URL nao serve: o site responde 200 na pagina de faturas mesmo
    deslogado, devolvendo o formulario de login no lugar do conteudo. E o menu
    da area logada demora a renderizar, entao insistimos por alguns segundos
    antes de concluir que nao estamos autenticados.
    """
    limite = time.time() + timeout_s
    while time.time() < limite:
        if page.locator(SEL_UC).count() == 0 and page.locator(SEL_DATA).count() == 0:
            texto = page.evaluate("document.body.innerText || ''")
            if "Sair" in texto and ("Meus dados" in texto or "Fornecimento" in texto):
                return True
        page.wait_for_timeout(1000)
    return False


def etapa2(page, dialogos):
    """Segunda etapa: data de nascimento do titular.

    Bem mais simples que a primeira - sem Transmit, sem mascara JS. So o campo
    de data e um postback direto. O reCAPTCHA e o mesmo hidden field, e como ja
    se passou mais de um minuto desde a etapa 1, o token precisa ser renovado.
    """
    if not NASC:
        log("EQUATORIAL_NASC nao definido - pare aqui e informe a data do titular")
        return False

    log("Preenchendo data de nascimento")
    digitar(page, SEL_DATA, NASC)
    valor = page.input_value(SEL_DATA)
    log(f"Data no campo: {valor}")

    log(f"reCAPTCHA v3 (etapa 2): {renovar_recaptcha(page)}")
    page.click(SEL_VALIDAR)

    try:
        page.wait_for_load_state("networkidle", timeout=45_000)
    except PWTimeout:
        pass
    page.wait_for_timeout(3000)
    return not dialogos


def conectar(p):
    """Conecta ao Chrome ja em execucao (chrome.sh) e devolve (ctx, page)."""
    try:
        browser = p.chromium.connect_over_cdp(CDP)
    except Exception as e:
        sys.exit(f"Nao consegui conectar em {CDP}: {e}\nRode ./chrome.sh primeiro.")
    ctx = browser.contexts[0]
    page = next((pg for pg in ctx.pages if "equatorial" in pg.url), None)
    if page is None:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
    return ctx, page


def escutar_dialogos(page):
    """O site reporta falha de login via alert(), e o Playwright descarta
    dialogos por padrao - sem este handler o erro fica invisivel e a execucao
    parece ter "simplesmente nao funcionado"."""
    dialogos = []

    def _dialogo(d):
        dialogos.append(d.message)
        log(f"ALERTA DO SITE: {d.message}")
        d.accept()

    page.on("dialog", _dialogo)
    return dialogos


def uma_tentativa_de_login(page, dialogos):
    """Executa o fluxo de login uma vez. True se terminou na area logada."""
    dialogos.clear()

    # Sempre recarrega: reaproveitar a aba deixa campos preenchidos de uma
    # execucao anterior, e o maxlength esconde a duplicacao silenciosamente.
    if AQUECER:
        aquecer(page)

    ir_para(page, URL)
    page.bring_to_front()
    page.wait_for_selector(SEL_UC, timeout=30_000)

    if not esperar_transmit_pronto(page):
        log("AVISO: SDK do Transmit nao apareceu; seguindo assim mesmo")

    permanecer(page, DWELL)
    digitar(page, SEL_UC, UC)
    digitar(page, SEL_DOC, DOC)
    page.keyboard.press("Tab")  # dispara o onblur do campo de documento
    page.wait_for_timeout(random.randint(400, 900))

    valor, corrigido = normalizar_documento(page, DOC)
    log(f"Documento: {valor}" + (" (separador corrigido)" if corrigido else ""))
    valor_uc = page.input_value(SEL_UC)
    if "".join(filter(str.isdigit, valor_uc)) != UC:
        sys.exit(f"UC inconsistente no campo: {valor_uc!r} (esperado {UC})")
    permanecer(page, random.randint(4, 9))  # pausa antes de clicar Entrar
    log(f"UC: {valor_uc} | reCAPTCHA: {renovar_recaptcha(page)}")
    log(f"Submetendo (via {submeter(page)})")

    # Etapa 1 aceita: o site troca o formulario pelo campo de data de
    # nascimento. Mas a sessao ja esta autenticada nesse ponto, entao da para ir
    # direto a area logada e pular a segunda etapa.
    try:
        page.wait_for_selector(SEL_DATA, timeout=25_000)
        log("Etapa 1 aceita (site ofereceu a data de nascimento)")
    except PWTimeout:
        if dialogos:
            log(f"Recusado pelo backend: {dialogos[-1]}")
            return False
        log("Campo de data nao apareceu, mas tambem nao houve alerta")

    ir_para(page, URL_FATURAS)
    if area_logada(page):
        return True

    log("Area logada recusada; tentando a segunda etapa (data de nascimento)")
    try:
        page.go_back(wait_until="domcontentloaded")
        page.wait_for_selector(SEL_DATA, timeout=20_000)
        etapa2(page, dialogos)
        ir_para(page, URL_FATURAS)
        return area_logada(page)
    except PWTimeout:
        log("Nao foi possivel retomar a etapa da data de nascimento")
        return False


def preparar_rede():
    """Poe a rota padrao no celular, se a rotacao estiver ligada.

    Devolve o estado a passar para liberar_rede(). Quem chama e responsavel por
    liberar num finally - deixar a maquina roteando tudo pelo 4G depois que o
    robo termina seria bem pior que nao rotacionar nada.
    """
    if not ROTACIONAR:
        return None
    import rede

    tether = rede.detectar_tether()
    log(f"Tethering: {tether['nome']} ({tether['descricao']})")
    estado = rede.assumir_rota(tether["ifIndex"])
    log(f"Saindo pelo IP {rede.ip_publico()}")
    return estado


def liberar_rede(estado):
    if estado is None:
        return
    import rede

    rede.restaurar_rota(estado)


def ir_para(page, url, tentativas=3):
    """page.goto() tolerante a troca de interface de rede.

    Depois de uma rotacao de IP o Chrome ainda tem sockets presos na interface
    antiga e o primeiro carregamento volta ERR_NETWORK_CHANGED / ERR_NETWORK_IO.
    Nao e falha do site: e so esperar o proximo carregamento.
    """
    for n in range(1, tentativas + 1):
        try:
            page.goto(url, wait_until="domcontentloaded")
            return True
        except Exception as e:
            msg = str(e)[:80]
            if n == tentativas:
                raise
            log(f"goto falhou ({msg}); retentando {n + 1}/{tentativas}")
            page.wait_for_timeout(4000)
    return False


def trocar_ip(page, ctx):
    """Rotaciona o IP do celular e reseta o rastro de sessao ligado ao antigo.

    Limpar os cookies do dominio e obrigatorio, nao higiene: visid_incap_/nlbi_/
    reese84 foram emitidos para o IP anterior, e reapresenta-los vindo de um IP
    novo e justamente a incoerencia que o Imperva procura. aquecer() reconstroi
    a sessao do zero na tentativa seguinte.
    """
    import rede  # so no Windows e so quando pedido; nao virar dependencia dura

    try:
        antes, depois = rede.rotacionar()
    except RuntimeError as e:
        log(f"Rotacao de IP falhou: {str(e)[:140]}")
        return False

    if ctx is not None:
        try:
            ctx.clear_cookies(domain=DOMINIO)
            log(f"Cookies de {DOMINIO} limpos")
        except TypeError:
            ctx.clear_cookies()  # Playwright antigo nao filtra por dominio
            log("Cookies limpos (sem filtro de dominio)")
        except Exception as e:
            log(f"AVISO: nao limpei os cookies: {str(e)[:80]}")
    return depois != antes


def garantir_login(page, dialogos, ctx=None, prazo_s=PRAZO):
    """Insiste ate entrar na area logada, com pausa curta entre tentativas.

    O backend recusa de forma intermitente com "#00N - Nao foi possivel realizar
    o login neste momento": mesmas credenciais e mesmo codigo ora passam, ora
    nao - e o login manual humano tambem apanha disso. Como a chance de sucesso
    e independente a cada tentativa, o que paga e tentar muitas vezes rapido,
    nao esperar mais entre elas.

    Com EQUATORIAL_ROTACIONAR=1, cada recusa tambem troca o IP do celular
    (ver rede.py). Expectativa modesta: sob CGNAT o IP novo tende a sair do
    mesmo pool/ASN que o site ja pontuou.
    """
    log("Verificando se a sessao anterior ainda vale")
    ir_para(page, URL_FATURAS)
    if area_logada(page, timeout_s=8):
        log("Sessao ainda valida - login desnecessario")
        return True

    limite = time.time() + prazo_s
    tentativa = 0
    while time.time() < limite:
        tentativa += 1
        log(f"--- Tentativa de login {tentativa} ---")
        try:
            if uma_tentativa_de_login(page, dialogos):
                titular = page.evaluate(
                    r"""() => {
                        const m = document.body.innerText.match(
                            /^\s*([A-ZÁÂÃÀÉÊÍÓÔÕÚÇ][A-ZÁÂÃÀÉÊÍÓÔÕÚÇ ]{6,60})\s*$/m);
                        return m ? m[1].trim() : null;
                    }"""
                )
                log(f"Login OK{f' - {titular}' if titular else ''} (tentativa {tentativa})")
                return True
        except PWTimeout as e:
            log(f"Timeout na tentativa {tentativa}: {str(e)[:90]}")

        if ROTACIONAR:
            trocar_ip(page, ctx)

        espera = random.randint(ESPERA_BASE, ESPERA_MAX)
        if time.time() + espera > limite:
            break
        log(f"Aguardando {espera}s antes da proxima tentativa")
        page.wait_for_timeout(espera * 1000)

    log(f"Prazo de {prazo_s}s esgotado sem conseguir logar")
    return False


def main():
    if not UC or not DOC:
        sys.exit(
            "Defina as credenciais antes de rodar:\n"
            "  export EQUATORIAL_UC=...    # unidade consumidora\n"
            "  export EQUATORIAL_DOC=...   # CPF/CNPJ"
        )
    OUT.mkdir(exist_ok=True)
    estado_rede = preparar_rede()
    try:
        with sync_playwright() as p:
            ctx, page = conectar(p)
            dialogos = escutar_dialogos(page)
            sucesso = garantir_login(page, dialogos, ctx=ctx)

            carimbo = time.strftime("%Y%m%d-%H%M%S")
            page.screenshot(path=str(OUT / f"login-{carimbo}.png"), full_page=True)
            log("RESULTADO: login OK" if sucesso else "RESULTADO: nao consegui logar")
            if sucesso:
                ctx.storage_state(path=str(OUT / "sessao.json"))
            return 0 if sucesso else 1
    finally:
        liberar_rede(estado_rede)


if __name__ == "__main__":
    sys.exit(main())
