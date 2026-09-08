"""
glpi_tiflux.py
Sincroniza chamados do GLPI (>= ID_MINIMO_GLPI) para o Tiflux.

Feito para rodar via cron. Cada execução:
  1. Autentica no GLPI e no Tiflux.
  2. Busca no GLPI chamados com id >= ID_MINIMO_GLPI que ainda não foram
     sincronizados com sucesso (consultando a tabela de auditoria no Postgres).
  3. Reprocessa também os chamados que ficaram com status 'erro' em execuções
     anteriores (retry automático).
  4. Para cada chamado: monta o pacote, cria no Tiflux, atribui o técnico,
     e grava o resultado (sucesso ou erro) na tabela de auditoria.
  5. Sincroniza followups (GLPI <-> Tiflux) de uma leva de chamados já
     sincronizados: publica followups novos do GLPI como resposta/comunicação
     interna no Tiflux, e respostas/comunicações internas novas do Tiflux como
     followup no GLPI. Só varre chamados ainda abertos no GLPI.

Tabelas de auditoria: ver criar_tabela_auditoria.sql
"""

import html
import sys
import urllib.parse
from datetime import datetime
from html.parser import HTMLParser

import psycopg2
import psycopg2.extras
import requests

# =============================================================================
# 1. CONFIGURAÇÃO
# =============================================================================

def carregar_credenciais(caminho="credenciais.txt"):
    credenciais = {}
    with open(caminho, "r") as arquivo:
        for linha in arquivo:
            if "=" in linha:
                chave, valor = linha.split("=", 1)
                credenciais[chave.strip()] = valor.strip()
    return credenciais


CRED = carregar_credenciais()

URL_BASE = CRED.get("URL_GLPI")
APP_TOKEN = CRED.get("APP_TOKEN")
USER_TOKEN = CRED.get("USER_TOKEN")

URL_TIFLUX = CRED.get("URL_TIFLUX")
TOKEN_TIFLUX = CRED.get("TOKEN_TIFLUX")

DB_HOST = CRED.get("DB_HOST")
DB_PORT = CRED.get("DB_PORT", "5432")
DB_NAME = CRED.get("DB_NAME")
DB_USER = CRED.get("DB_USER")
DB_PASSWORD = CRED.get("DB_PASSWORD")
DB_SCHEMA = CRED.get("DB_SCHEMA", "public")
DB_TABLE = CRED.get("DB_TABLE", "api_glpi_tiflux")
TABELA_AUDITORIA = f"{DB_SCHEMA}.{DB_TABLE}"

DB_TABLE_FOLLOWUPS = CRED.get("DB_TABLE_FOLLOWUPS", "api_glpi_tiflux_followups")
TABELA_FOLLOWUPS = f"{DB_SCHEMA}.{DB_TABLE_FOLLOWUPS}"

# Regra de negócio: só processar chamados a partir deste número
ID_MINIMO_GLPI = 33637

# Quantos chamados buscar por execução do cron (aumente se ficar tickets p/ trás)
TAMANHO_PAGINA_BUSCA = 200

# Quantos chamados já sincronizados varrer por execução em busca de followups novos
TAMANHO_PAGINA_FOLLOWUPS = 50

# Paginação ao listar respostas/comunicações internas de um ticket no Tiflux
TAMANHO_PAGINA_RESPOSTAS_TIFLUX = 100
MAX_PAGINAS_RESPOSTAS_TIFLUX = 20  # limite de segurança

CLIENTE_TIFLUX_ID = 762707
ID_SOLICITANTE_PADRAO = 3758056  # Ju STII
# Prioridade não é mais fixa: é resolvida dinamicamente por mesa em obter_prioridade_tiflux()

# Só sincroniza chamados que tenham esse grupo como OBSERVADOR no GLPI
ID_GRUPO_OBSERVADOR = 22  # Embras Atendimentos

ID_TECNICO_LEO = 117180
ID_TECNICO_SANIA = 1019979

headers_tiflux_json = {
    "accept": "application/json",
    "Content-Type": "application/json",
    "Authorization": f"Bearer {TOKEN_TIFLUX}",
}
headers_tiflux_form = {
    "accept": "application/json",
    "Authorization": f"Bearer {TOKEN_TIFLUX}",
}
# Para requisições GET sem corpo. IMPORTANTE: nunca mandar Content-Type: application/json
# num GET sem body — o Rails (usado pelo Tiflux) faz o "param wrapping" do corpo (mesmo vazio)
# dentro de uma chave com o nome singular do recurso (ex: "requestor"), e como a action de
# busca (index) não espera esse parâmetro, a API responde 400 "unpermitted parameter".
headers_tiflux_get = {
    "accept": "application/json",
    "Authorization": f"Bearer {TOKEN_TIFLUX}",
}


def log(msg):
    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{agora}] {msg}")


# =============================================================================
# 2. POSTGRES — TABELA DE AUDITORIA
# =============================================================================

def conectar_db():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD,
    )


def obter_ids_ja_processados(conn):
    """
    IDs que já têm um resultado de sucesso gravado e não devem ser reprocessados.
    Chamados 'ignorado' (sem o grupo observador) não são gravados na auditoria,
    então não entram aqui — a sondagem vai re-conferir esses IDs a cada execução.
    """
    with conn.cursor() as cur:
        cur.execute(f"SELECT id_glpi FROM {TABELA_AUDITORIA} WHERE status = 'sucesso'")
        return {row[0] for row in cur.fetchall()}


def obter_proximo_id_para_sondar(conn):
    """
    De onde a sondagem deve continuar: logo após o maior id_glpi já registrado
    na auditoria (sucesso, erro ou ignorado — qualquer um confirma que aquele
    ID já foi verificado), nunca abaixo de ID_MINIMO_GLPI.
    """
    with conn.cursor() as cur:
        cur.execute(f"SELECT MAX(id_glpi) FROM {TABELA_AUDITORIA}")
        maior_id = cur.fetchone()[0]
    if maior_id is None:
        return ID_MINIMO_GLPI
    return max(ID_MINIMO_GLPI, maior_id + 1)


def obter_ids_para_retry(conn):
    with conn.cursor() as cur:
        cur.execute(f"SELECT id_glpi FROM {TABELA_AUDITORIA} WHERE status = 'erro'")
        return {row[0] for row in cur.fetchall()}


def registrar_resultado(conn, id_glpi, numero_tiflux, status, mensagem):
    """Grava (ou atualiza, em caso de retry) o resultado da sincronização de um chamado."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {TABELA_AUDITORIA} (id_glpi, numero_tiflux, status, mensagem, tentativas, criado_em, atualizado_em)
            VALUES (%s, %s, %s, %s, 1, now(), now())
            ON CONFLICT (id_glpi) DO UPDATE SET
                numero_tiflux = EXCLUDED.numero_tiflux,
                status        = EXCLUDED.status,
                mensagem      = EXCLUDED.mensagem,
                tentativas    = {TABELA_AUDITORIA}.tentativas + 1,
                atualizado_em = now()
            """,
            (id_glpi, numero_tiflux, status, mensagem),
        )
    conn.commit()


def obter_chamados_para_varrer_followups(conn, limite=TAMANHO_PAGINA_FOLLOWUPS):
    """
    Retorna até `limite` pares (id_glpi, numero_tiflux) de chamados já
    sincronizados com sucesso, para varrer em busca de followups novos.
    Só considera chamados com status='sucesso' na tabela de auditoria de
    chamados — linhas 'erro' podem ter numero_tiflux inconsistente (ver bug
    conhecido de duplicação no reprocessamento de falha de atribuição de
    técnico, em processar_chamado()).
    Prioriza os chamados menos recentemente varridos (LEFT JOIN pelo timestamp
    mais recente na tabela de followups), pra fazer um rodízio justo entre
    execuções do cron.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT t.id_glpi, t.numero_tiflux
            FROM {TABELA_AUDITORIA} t
            LEFT JOIN (
                SELECT id_glpi, MAX(atualizado_em) AS ultima_varredura
                FROM {TABELA_FOLLOWUPS}
                GROUP BY id_glpi
            ) f ON f.id_glpi = t.id_glpi
            WHERE t.status = 'sucesso'
            ORDER BY f.ultima_varredura ASC NULLS FIRST
            LIMIT %s
            """,
            (limite,),
        )
        return cur.fetchall()


def obter_followups_glpi_ja_processados(conn, id_glpi):
    """
    IDs de ITILFollowup do GLPI já publicados com sucesso no Tiflux para este
    chamado. Só considera status='sucesso' (mesmo critério de
    obter_ids_ja_processados p/ chamados) — um followup que falhou fica de
    fora daqui de propósito, pra ser retentado na próxima execução.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT id_origem FROM {TABELA_FOLLOWUPS} "
            f"WHERE direcao = 'glpi_para_tiflux' AND status = 'sucesso' AND id_glpi = %s",
            (id_glpi,),
        )
        return {row[0] for row in cur.fetchall()}


def obter_respostas_tiflux_ja_processadas_ou_proprias(conn, numero_tiflux):
    """
    IDs de resposta/comunicação interna do Tiflux a IGNORAR na varredura
    Tiflux -> GLPI: os que já processamos com sucesso nesse sentido (direcao=
    'tiflux_para_glpi', status='sucesso' — followups com erro ficam de fora
    de propósito, pra serem retentados) UNION os que a própria integração
    criou no sentido glpi_para_tiflux (id_destino, só existe em linhas de
    sucesso) — evita reimportar o próprio eco.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id_origem FROM {TABELA_FOLLOWUPS}
            WHERE numero_tiflux = %s AND direcao = 'tiflux_para_glpi' AND status = 'sucesso'
            UNION
            SELECT id_destino FROM {TABELA_FOLLOWUPS}
            WHERE numero_tiflux = %s AND direcao = 'glpi_para_tiflux' AND id_destino IS NOT NULL
            """,
            (numero_tiflux, numero_tiflux),
        )
        return {row[0] for row in cur.fetchall()}


def registrar_resultado_followup(conn, id_glpi, numero_tiflux, direcao, tipo, id_origem, id_destino, status, mensagem):
    """Grava (ou atualiza, em caso de retry) o resultado da sincronização de um followup."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {TABELA_FOLLOWUPS}
                (id_glpi, numero_tiflux, direcao, tipo, id_origem, id_destino, status, mensagem, tentativas, criado_em, atualizado_em)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, now(), now())
            ON CONFLICT (direcao, id_origem) DO UPDATE SET
                numero_tiflux = EXCLUDED.numero_tiflux,
                id_destino    = EXCLUDED.id_destino,
                status        = EXCLUDED.status,
                mensagem      = EXCLUDED.mensagem,
                tentativas    = {TABELA_FOLLOWUPS}.tentativas + 1,
                atualizado_em = now()
            """,
            (id_glpi, numero_tiflux, direcao, tipo, id_origem, id_destino, status, mensagem),
        )
    conn.commit()


def registrar_chamado_fechado_para_followups(conn, id_glpi):
    """
    Marca (sem sincronizar nenhum followup) que este chamado foi conferido e
    está fechado no GLPI. Sem isso, um chamado fechado nunca ganharia uma
    linha na tabela de followups e ficaria pra sempre em primeiro lugar no
    rodízio de obter_chamados_para_varrer_followups (que ordena por última
    varredura), monopolizando o limite de chamados verificados por execução.
    """
    registrar_resultado_followup(
        conn, id_glpi, None, "verificacao_status", "status", -id_glpi, None,
        "fechado", "Chamado fechado no GLPI — fora do escopo da varredura de followups",
    )


# =============================================================================
# 3. GLPI — AUTENTICAÇÃO E BUSCA DE CHAMADOS
# =============================================================================

def autenticar_glpi():
    resposta = requests.get(
        f"{URL_BASE}/initSession",
        headers={"App-Token": APP_TOKEN, "Authorization": f"user_token {USER_TOKEN}"},
    )
    resposta.raise_for_status()
    session_token = resposta.json().get("session_token")
    return {"App-Token": APP_TOKEN, "Session-Token": session_token}


def encerrar_sessao_glpi(headers_glpi):
    try:
        requests.get(f"{URL_BASE}/killSession", headers=headers_glpi)
    except requests.RequestException:
        pass


# Máximo de IDs "furados" (404) seguidos antes de considerar que chegamos no
# fim dos chamados criados até agora e parar de sondar nessa execução.
MAX_FUROS_SEGUIDOS = 50


def buscar_chamados_desde(headers_glpi, id_inicial, limite_por_execucao=TAMANHO_PAGINA_BUSCA):
    """
    Sonda sequencialmente cada ID a partir de id_inicial via GET /Ticket/{id}
    (o mesmo endpoint, comprovadamente confiável, que já usamos pra buscar os
    dados de cada chamado). Evitamos o endpoint /search/Ticket porque nessa
    instalação ele se mostrou inconsistente (campo/boundary/entidade geraram
    resultados que não batiam com chamados confirmados via GET direto).

    Para depois de MAX_FUROS_SEGUIDOS IDs seguidos sem chamado (assume que
    chegou no fim dos criados até agora), ou ao atingir limite_por_execucao
    chamados encontrados.
    """
    encontrados = []
    id_atual = id_inicial
    furos_seguidos = 0

    while furos_seguidos < MAX_FUROS_SEGUIDOS and len(encontrados) < limite_por_execucao:
        resp = requests.get(f"{URL_BASE}/Ticket/{id_atual}", headers=headers_glpi)
        if resp.status_code in (200, 206):
            encontrados.append(id_atual)
            furos_seguidos = 0
        elif resp.status_code == 404:
            furos_seguidos += 1
        else:
            log(f"⚠️ Status inesperado ({resp.status_code}) ao sondar chamado #{id_atual}: {resp.text}")
            furos_seguidos += 1
        id_atual += 1

    if furos_seguidos >= MAX_FUROS_SEGUIDOS:
        log(f"🔎 Sondagem parou após {MAX_FUROS_SEGUIDOS} IDs seguidos sem chamado "
            f"(parou em #{id_atual - 1}). Se isso for prematuro, aumente MAX_FUROS_SEGUIDOS.")

    log(f"🔎 Sondagem de #{id_inicial} até #{id_atual - 1}: {len(encontrados)} chamado(s) encontrado(s)")
    return encontrados


def obter_followups_glpi(headers_glpi, id_chamado):
    """
    GET /Ticket/{id}/ITILFollowup — lista os followups (públicos e privados)
    do chamado. Retorna lista de dicts crus do GLPI, ou [] em caso de erro
    (loga aviso, não levanta).
    """
    resp = requests.get(f"{URL_BASE}/Ticket/{id_chamado}/ITILFollowup", headers=headers_glpi)
    if resp.status_code not in (200, 206):
        log(f"⚠️ Falha ao buscar followups do chamado #{id_chamado} no GLPI (status {resp.status_code}): {resp.text}")
        return []
    dados = resp.json()
    return dados if isinstance(dados, list) else []


def criar_followup_glpi(headers_glpi, id_chamado, conteudo_html, is_private=0):
    """
    POST /ITILFollowup, usando o "input wrapper" padrão do GLPI pra criação de
    itens. Retorna (id_criado, erro_ou_None).
    """
    payload = {
        "input": {
            "itemtype": "Ticket",
            "items_id": id_chamado,
            "content": conteudo_html,
            "is_private": is_private,
        }
    }
    resp = requests.post(f"{URL_BASE}/ITILFollowup", json=payload, headers=headers_glpi)
    if resp.status_code not in (200, 201):
        return None, f"Falha ao criar followup no GLPI ({resp.status_code}): {resp.text}"

    dados = resp.json()
    id_criado = dados.get("id") if isinstance(dados, dict) else None
    if not id_criado:
        return None, "Followup criado no GLPI, mas não foi possível identificar o id na resposta"

    return id_criado, None


# =============================================================================
# 4. TRADUÇÃO GLPI -> TIFLUX (regras de negócio)
# =============================================================================

class _HTMLParaTexto(HTMLParser):
    """Extrai texto puro de um HTML, preservando quebras de linha e listas."""

    _TAGS_QUEBRA_LINHA = {"p", "div", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "ol", "ul"}

    def __init__(self):
        super().__init__()
        self.partes = []

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self.partes.append("\n")
        elif tag == "li":
            self.partes.append("\n- ")

    def handle_endtag(self, tag):
        if tag in self._TAGS_QUEBRA_LINHA:
            self.partes.append("\n")

    def handle_data(self, data):
        self.partes.append(data)


def html_para_texto_plano(conteudo_html):
    """
    O campo de descrição do Tiflux é TEXTO PURO, não renderiza HTML — mandar as
    tags do GLPI direto faz elas aparecerem literalmente pro atendente. Essa
    função extrai só o texto, mantendo parágrafos/quebras de linha/listas legíveis.
    """
    if not conteudo_html:
        return ""

    parser = _HTMLParaTexto()
    parser.feed(conteudo_html)
    texto = "".join(parser.partes)
    texto = html.unescape(texto)

    linhas = [linha.strip() for linha in texto.splitlines()]
    texto = "\n".join(linhas)
    while "\n\n\n" in texto:
        texto = texto.replace("\n\n\n", "\n\n")

    return texto.strip()


def depara_categoria(cat_id):
    """
    Retorna o ID da mesa no Tiflux correspondente à categoria do GLPI, ou None
    se a categoria não tiver correspondência conhecida — nesse caso o chamado
    NÃO é sincronizado automaticamente, fica registrado como erro pra revisão manual.
    """
    if cat_id in range(267, 272):
        return 37963  # ADMINISTRATIVO/RH
    if cat_id in range(272, 277):
        return 37964  # ARRECADAÇÃO
    if cat_id in range(277, 282):
        return 37965  # FINANÇAS
    if cat_id in range(282, 287):
        return 37966  # SUPRIMENTOS
    return None


def definir_tecnico(id_mesa):
    if id_mesa == 37964:  # ARRECADAÇÃO
        return ID_TECNICO_LEO, "Léo Alves"
    return ID_TECNICO_SANIA, "Sânia Almeida"


# De/para fixo de prioridade por mesa. Prioridade no Tiflux é cadastrada POR MESA,
# então o mesmo ID não serve pra todas.
PRIORIDADE_POR_MESA = {
    37963: 120547,  # ADMINISTRATIVO/RH  -> Solicitar um Atendimento
    37964: 120549,  # ARRECADAÇÃO        -> Solicitar um Atendimento
    37965: 120551,  # FINANÇAS           -> Solicitar um Atendimento
    37966: 121197,  # SUPRIMENTOS        -> Solicitar um Atendimento
}


def definir_prioridade(id_mesa):
    """Retorna o ID de prioridade fixo configurado para a mesa, ou None se não configurado."""
    return PRIORIDADE_POR_MESA.get(id_mesa)


# Mesas (desks) confirmadas como vinculadas ao cliente no Tiflux — carregado uma vez
# por execução via _validar_mesa_do_cliente(). Evita bater na API pra cada chamado.
_mesas_validas_do_cliente = None


def _validar_mesa_do_cliente(id_mesa):
    """
    Confere se a mesa pertence ao CLIENTE_TIFLUX_ID antes de tentar criar o ticket.
    A API do Tiflux só acusa isso com um erro genérico na criação do ticket
    ("This ticket's client do not belongs to the selected desk"); aqui a gente
    detecta antes e devolve uma mensagem que já indica a causa.
    """
    global _mesas_validas_do_cliente
    if _mesas_validas_do_cliente is None:
        resp = requests.get(f"{URL_TIFLUX}/clients/{CLIENTE_TIFLUX_ID}/desks", headers=headers_tiflux_get)
        if resp.status_code != 200:
            log(f"⚠️ Não foi possível validar as mesas do cliente ({resp.status_code}): {resp.text}")
            _mesas_validas_do_cliente = set()  # não bloqueia, deixa a API acusar na criação
        else:
            _mesas_validas_do_cliente = {m.get("id") for m in resp.json()}

    if not _mesas_validas_do_cliente:
        return True  # não conseguimos validar, segue e deixa a criação do ticket decidir

    return id_mesa in _mesas_validas_do_cliente


def cadastrar_solicitante_tiflux(nome, email):
    url_criar = f"{URL_TIFLUX}/clients/{CLIENTE_TIFLUX_ID}/requestors"
    payload = {
        "name": nome if nome != "Desconhecido" else "Solicitante Sem Nome",
        "email": email,
        "can_open_ticket": True,
    }
    resp = requests.post(url_criar, json=payload, headers=headers_tiflux_json)
    if resp.status_code in (200, 201):
        dados = resp.json()
        return dados.get("id"), f"{dados.get('name')} (Cadastrado Automaticamente via API)"

    log(f"⚠️ Falha ao cadastrar solicitante ({resp.status_code}): {resp.text}")
    return ID_SOLICITANTE_PADRAO, "Ju STII (Padrão - Falha ao Auto-Cadastrar no TiFlux)"


def obter_id_solicitante_tiflux(nome_glpi, email_glpi):
    if not email_glpi:
        return ID_SOLICITANTE_PADRAO, "Ju STII (Padrão - Sem E-mail no GLPI)"

    email_limpo = email_glpi.strip().lower()
    email_encoded = urllib.parse.quote(email_limpo)
    url_busca = f"{URL_TIFLUX}/clients/{CLIENTE_TIFLUX_ID}/requestors?email={email_encoded}"
    resposta = requests.get(url_busca, headers=headers_tiflux_get)

    if resposta.status_code == 200:
        dados = resposta.json()
        if isinstance(dados, list):
            for item in dados:
                if str(item.get("email", "")).strip().lower() == email_limpo:
                    return item.get("id"), f"{item.get('name')} (Existente no TiFlux - ID: {item.get('id')})"
    elif resposta.status_code != 404:
        log(f"⚠️ Busca de solicitante retornou status inesperado ({resposta.status_code}): {resposta.text}")

    return cadastrar_solicitante_tiflux(nome_glpi, email_glpi)


def obter_requerente_glpi(headers_glpi, id_chamado, ticket):
    nome_solicitante = "Desconhecido"
    email_solicitante = None
    id_requerente = None

    resp_vinculos = requests.get(f"{URL_BASE}/Ticket/{id_chamado}/Ticket_User", headers=headers_glpi)
    if resp_vinculos.status_code in (200, 206):
        for v in resp_vinculos.json():
            if v.get("type") == 1:
                id_requerente = v.get("users_id")
                break

    if not id_requerente:
        id_requerente = ticket.get("users_id_recipient") or ticket.get("users_id_lastupdater")

    if id_requerente:
        resp_usuario = requests.get(f"{URL_BASE}/User/{id_requerente}", headers=headers_glpi)
        if resp_usuario.status_code in (200, 206):
            dados_usuario = resp_usuario.json()
            p_nome = dados_usuario.get("firstname", "")
            s_nome = dados_usuario.get("realname", "")
            login = dados_usuario.get("name", "")
            nome_solicitante = f"{p_nome} {s_nome}".strip() if (p_nome or s_nome) else f"Login: {login}"
            email_solicitante = dados_usuario.get("email")

        if not email_solicitante:
            resp_email = requests.get(f"{URL_BASE}/User/{id_requerente}/UserEmail", headers=headers_glpi)
            if resp_email.status_code in (200, 206):
                lista_emails = resp_email.json()
                if isinstance(lista_emails, list) and lista_emails:
                    email_solicitante = lista_emails[0].get("email")

    return nome_solicitante, email_solicitante, id_requerente


def autor_e_solicitante(id_autor_followup, id_requerente_ticket):
    """Compara o autor de um followup do GLPI ao requerente do chamado (já resolvido por obter_requerente_glpi)."""
    return id_requerente_ticket is not None and id_autor_followup == id_requerente_ticket


def chamado_tem_grupo_observador(headers_glpi, id_chamado):
    """
    Confere se o grupo ID_GRUPO_OBSERVADOR está vinculado ao chamado como
    OBSERVADOR (type=3 em Group_Ticket, conforme GLPI: 1=Requerente, 2=Atribuído, 3=Observador).
    Retorna (bool, motivo_se_nao_encontrado_ou_erro).
    """
    resp = requests.get(f"{URL_BASE}/Ticket/{id_chamado}/Group_Ticket", headers=headers_glpi)
    if resp.status_code not in (200, 206):
        return False, f"Falha ao consultar grupos do chamado no GLPI (status {resp.status_code})"

    for vinculo in resp.json():
        if vinculo.get("type") == 3 and vinculo.get("groups_id") == ID_GRUPO_OBSERVADOR:
            return True, None

    return False, f"Chamado não tem o grupo observador ID {ID_GRUPO_OBSERVADOR}"


# Tamanho máximo de anexo aceito pelo Tiflux
TAMANHO_MAXIMO_ANEXO_MB = 25


def obter_anexos_glpi(headers_glpi, id_chamado):
    """
    Busca os documentos vinculados ao chamado no GLPI (anexos e imagens
    inseridas na descrição) e baixa o conteúdo binário de cada um.
    Retorna (lista_de_anexos, avisos) onde cada anexo é (nome, conteudo_bytes, mime)
    e avisos é uma lista de strings com o que não pôde ser baixado/enviado.
    """
    anexos = []
    avisos = []

    resp = requests.get(f"{URL_BASE}/Ticket/{id_chamado}/Document_Item", headers=headers_glpi)
    if resp.status_code not in (200, 206):
        avisos.append(f"Falha ao listar anexos do chamado no GLPI (status {resp.status_code})")
        return anexos, avisos

    vinculos = resp.json()
    if not isinstance(vinculos, list) or not vinculos:
        return anexos, avisos

    for vinculo in vinculos:
        doc_id = vinculo.get("documents_id")
        if not doc_id:
            continue

        resp_doc = requests.get(f"{URL_BASE}/Document/{doc_id}", headers=headers_glpi)
        if resp_doc.status_code not in (200, 206):
            avisos.append(f"Documento {doc_id}: falha ao obter metadados (status {resp_doc.status_code})")
            continue
        meta = resp_doc.json()
        nome_arquivo = meta.get("filename") or meta.get("name") or f"arquivo_{doc_id}"
        mime = meta.get("mime") or "application/octet-stream"

        resp_bin = requests.get(
            f"{URL_BASE}/Document/{doc_id}", headers=headers_glpi, params={"alt": "media"}
        )
        if resp_bin.status_code not in (200, 206):
            avisos.append(f"'{nome_arquivo}': falha ao baixar conteúdo (status {resp_bin.status_code})")
            continue

        tamanho_mb = len(resp_bin.content) / (1024 * 1024)
        if tamanho_mb > TAMANHO_MAXIMO_ANEXO_MB:
            avisos.append(f"'{nome_arquivo}' tem {tamanho_mb:.1f}MB, acima do limite de "
                           f"{TAMANHO_MAXIMO_ANEXO_MB}MB do Tiflux — não enviado")
            continue

        anexos.append((nome_arquivo, resp_bin.content, mime))

    return anexos, avisos


def enviar_anexos_tiflux(ticket_number_tiflux, anexos):
    """
    Envia os anexos pro ticket no Tiflux, em lotes de até 10 por requisição
    (limite da API). Retorna (qtd_enviados, qtd_falhados, motivos_das_falhas).
    """
    if not anexos:
        return 0, 0, []

    enviados = 0
    falhados = 0
    motivos = []

    for i in range(0, len(anexos), 10):
        lote = anexos[i:i + 10]
        arquivos_form = [("files[]", (nome, conteudo, mime)) for nome, conteudo, mime in lote]
        # headers_tiflux_get só tem Accept + Authorization — sem Content-Type,
        # pra deixar o requests montar o multipart/form-data com o boundary certo.
        resp = requests.post(
            f"{URL_TIFLUX}/tickets/{ticket_number_tiflux}/files",
            files=arquivos_form,
            headers=headers_tiflux_get,
        )
        if resp.status_code in (200, 201):
            enviados += len(lote)
        else:
            falhados += len(lote)
            motivos.append(f"Lote {i // 10 + 1} ({resp.status_code}): {resp.text}")

    return enviados, falhados, motivos


# =============================================================================
# 5. PROCESSAMENTO DE UM CHAMADO
# =============================================================================

def processar_chamado(headers_glpi, id_chamado):
    """
    Processa um único chamado do GLPI: busca dados, traduz, cria no Tiflux
    e atribui o técnico.
    Retorna (status, numero_tiflux, mensagem):
      - status='sucesso'  -> sincronizado normalmente
      - status='ignorado' -> fora do escopo (ex: sem o grupo observador exigido);
        NÃO é reprocessado nas próximas execuções
      - status='erro'     -> falha real (API, rede, dado inconsistente);
        É reprocessado automaticamente nas próximas execuções
    """
    try:
        esta_no_escopo, motivo = chamado_tem_grupo_observador(headers_glpi, id_chamado)
        if not esta_no_escopo:
            return "ignorado", None, motivo

        resp_ticket = requests.get(f"{URL_BASE}/Ticket/{id_chamado}", headers=headers_glpi)
        if resp_ticket.status_code not in (200, 206):
            return "erro", None, f"Chamado não encontrado no GLPI (status {resp_ticket.status_code})"

        ticket = resp_ticket.json()
        titulo_glpi = ticket.get("name")
        descricao_glpi = ticket.get("content")
        prioridade_glpi = ticket.get("priority")
        categoria_glpi = ticket.get("itilcategories_id")

        nome_solicitante_glpi, email_solicitante_glpi, _ = obter_requerente_glpi(headers_glpi, id_chamado, ticket)

        mesa_tiflux = depara_categoria(categoria_glpi)

        if mesa_tiflux is None:
            return ("erro", None,
                    f"Categoria GLPI {categoria_glpi} não tem mesa correspondente em depara_categoria() "
                    f"— chamado não sincronizado, requer revisão manual")

        if not _validar_mesa_do_cliente(mesa_tiflux):
            return ("erro", None,
                    f"Mesa {mesa_tiflux} não está vinculada ao cliente {CLIENTE_TIFLUX_ID} no Tiflux "
                    f"(confira em Clientes > Mesas, ou ajuste depara_categoria() se o ID estiver errado)")

        id_tecnico_tiflux, nome_tecnico_tiflux = definir_tecnico(mesa_tiflux)

        id_solicitante_tiflux, info_solicitante_tiflux = obter_id_solicitante_tiflux(
            nome_solicitante_glpi, email_solicitante_glpi
        )

        mapa_prioridades = {1: "Baixa", 2: "Média", 3: "Normal", 4: "Alta", 5: "Urgente"}
        prioridade_glpi_texto = mapa_prioridades.get(prioridade_glpi, "Normal")

        id_prioridade_tiflux = definir_prioridade(mesa_tiflux)
        if id_prioridade_tiflux is None:
            return ("erro", None,
                    f"Mesa {mesa_tiflux} não tem prioridade configurada em PRIORIDADE_POR_MESA "
                    f"(rode listar_config_tiflux.py pra descobrir o ID certo e preencha o dicionário)")

        titulo_tiflux = f"{titulo_glpi} ({id_chamado})"
        cabecalho_personalizado = f"Este chamado tem a prioridade: {prioridade_glpi_texto}"
        info_solicitante_texto = f"Solicitante: {nome_solicitante_glpi} <{email_solicitante_glpi or 'Sem e-mail'}>"
        descricao_glpi_texto = html_para_texto_plano(descricao_glpi)
        # Campo de descrição do Tiflux é texto puro (não HTML) — usa \n, não <br>
        descricao_tiflux = (f"{cabecalho_personalizado}\n\n{info_solicitante_texto}\n\n"
                             f"Descrição:\n{descricao_glpi_texto}")

        form_data = {
            "title": titulo_tiflux,
            "description": descricao_tiflux,
            "client_id": str(CLIENTE_TIFLUX_ID),
            "desk_id": str(mesa_tiflux),
            "requestor_id": str(id_solicitante_tiflux),
            "priority_id": str(id_prioridade_tiflux),
        }

        resp_criacao = requests.post(f"{URL_TIFLUX}/tickets", data=form_data, headers=headers_tiflux_form)
        if resp_criacao.status_code not in (200, 201):
            return "erro", None, f"Falha ao criar ticket no Tiflux ({resp_criacao.status_code}): {resp_criacao.text}"

        dados_retorno = resp_criacao.json()
        ticket_number_tiflux = None
        if isinstance(dados_retorno, dict) and "ticket" in dados_retorno:
            ticket_number_tiflux = dados_retorno["ticket"].get("ticket_number")

        if not ticket_number_tiflux:
            return "erro", None, "Ticket criado, mas não foi possível identificar o ticket_number na resposta"

        # Atribuição do técnico
        url_alterar_responsavel = f"{URL_TIFLUX}/tickets/{ticket_number_tiflux}/change_responsible"
        payload_resp = {"responsible_id": id_tecnico_tiflux}
        resp_update = requests.post(url_alterar_responsavel, json=payload_resp, headers=headers_tiflux_json)
        if resp_update.status_code not in (200, 201, 204):
            url_put = f"{URL_TIFLUX}/tickets/{ticket_number_tiflux}"
            payload_put = {"ticket": {"responsible_id": id_tecnico_tiflux}}
            resp_update = requests.put(url_put, json=payload_put, headers=headers_tiflux_json)

        if resp_update.status_code not in (200, 201, 204):
            msg = (f"Ticket #{ticket_number_tiflux} criado, mas falhou ao atribuir técnico "
                   f"{nome_tecnico_tiflux} ({resp_update.status_code}): {resp_update.text}")
            return "erro", ticket_number_tiflux, msg

        # Anexos (arquivos e imagens da descrição) — não falha o chamado se algo
        # aqui der errado, o ticket já foi criado; só registra no log/auditoria.
        anexos, avisos_anexos = obter_anexos_glpi(headers_glpi, id_chamado)
        anexos_enviados, anexos_falhados, motivos_falha = enviar_anexos_tiflux(ticket_number_tiflux, anexos)

        resumo_anexos = ""
        if anexos or avisos_anexos:
            resumo_anexos = f" | Anexos: {anexos_enviados} enviado(s)"
            if anexos_falhados:
                resumo_anexos += f", {anexos_falhados} falhou(aram) [{'; '.join(motivos_falha)}]"
            if avisos_anexos:
                resumo_anexos += f" | Avisos: {'; '.join(avisos_anexos)}"

        msg = (f"Ticket #{ticket_number_tiflux} criado no Tiflux | Mesa {mesa_tiflux} | "
               f"Prioridade ID {id_prioridade_tiflux} | "
               f"Técnico {nome_tecnico_tiflux} | Solicitante {info_solicitante_tiflux}"
               f"{resumo_anexos}")
        return "sucesso", ticket_number_tiflux, msg

    except requests.RequestException as e:
        return "erro", None, f"Erro de rede/conexão: {e}"
    except Exception as e:
        return "erro", None, f"Erro inesperado: {e}"


# =============================================================================
# 5B. SINCRONIZAÇÃO DE FOLLOWUPS (GLPI <-> TIFLUX)
# =============================================================================

def sincronizar_followups_glpi_para_tiflux(conn, headers_glpi, id_chamado, numero_tiflux):
    """
    Busca followups do chamado no GLPI, filtra os que ainda não foram
    publicados no Tiflux, e cria cada um lá:
      - is_private=1                    -> POST /internal_communications
      - público, autor é o requerente   -> POST /client-answers
      - público, autor é outra pessoa   -> POST /answers (como agente)
    Conteúdo (HTML) é passado direto — confirmado que o Tiflux aceita/renderiza
    HTML nesses campos, ao contrário da descrição do ticket (ver html_para_texto_plano).
    Retorna (qtd_sucesso, qtd_erro).
    """
    followups = obter_followups_glpi(headers_glpi, id_chamado)
    if not followups:
        return 0, 0

    ja_processados = obter_followups_glpi_ja_processados(conn, id_chamado)
    pendentes = [f for f in followups if f.get("id") not in ja_processados]
    if not pendentes:
        return 0, 0

    resp_ticket = requests.get(f"{URL_BASE}/Ticket/{id_chamado}", headers=headers_glpi)
    ticket = resp_ticket.json() if resp_ticket.status_code in (200, 206) else {}
    nome_requerente, _, id_requerente_ticket = obter_requerente_glpi(headers_glpi, id_chamado, ticket)

    qtd_sucesso = 0
    qtd_erro = 0

    for followup in pendentes:
        id_origem = followup.get("id")
        # Alguns followups do GLPI vêm com o conteúdo HTML-entity-encoded
        # (ex.: "&#60;p&#62;texto&#60;/p&#62;" em vez de "<p>texto</p>"),
        # dependendo de como foram criados; html.unescape() normaliza pros
        # dois casos (é um no-op se o conteúdo já vier como HTML literal).
        conteudo = html.unescape(followup.get("content") or "")
        is_private = bool(followup.get("is_private"))
        id_autor = followup.get("users_id")

        # IMPORTANTE: esses três endpoints exigem multipart/form-data de verdade
        # (confirmado ao vivo — /internal_communications rejeita com 415 se o
        # body vier como application/x-www-form-urlencoded). requests só monta
        # multipart de fato com o parâmetro files=; o truque (None, valor) força
        # isso sem precisar de um arquivo real, igual ao já usado em enviar_anexos_tiflux.
        if is_private:
            tipo = "interna"
            resp = requests.post(
                f"{URL_TIFLUX}/tickets/{numero_tiflux}/internal_communications",
                files={"text": (None, conteudo)},
                headers=headers_tiflux_get,
            )
        elif autor_e_solicitante(id_autor, id_requerente_ticket):
            tipo = "publica"
            resp = requests.post(
                f"{URL_TIFLUX}/tickets/{numero_tiflux}/client-answers",
                files={"name": (None, conteudo), "author_name": (None, nome_requerente)},
                headers=headers_tiflux_get,
            )
        else:
            tipo = "publica"
            resp = requests.post(
                f"{URL_TIFLUX}/tickets/{numero_tiflux}/answers",
                files={"name": (None, conteudo)},
                headers=headers_tiflux_get,
            )

        if resp.status_code not in (200, 201):
            qtd_erro += 1
            registrar_resultado_followup(
                conn, id_chamado, numero_tiflux, "glpi_para_tiflux", tipo, id_origem, None,
                "erro", f"Falha ao publicar followup no Tiflux ({resp.status_code}): {resp.text}",
            )
            continue

        id_destino = resp.json().get("id")
        if not id_destino:
            qtd_erro += 1
            registrar_resultado_followup(
                conn, id_chamado, numero_tiflux, "glpi_para_tiflux", tipo, id_origem, None,
                "erro", "Followup publicado no Tiflux, mas não foi possível identificar o id na resposta",
            )
            continue

        qtd_sucesso += 1
        registrar_resultado_followup(
            conn, id_chamado, numero_tiflux, "glpi_para_tiflux", tipo, id_origem, id_destino,
            "sucesso", f"Followup GLPI #{id_origem} publicado no Tiflux (id {id_destino})",
        )

    return qtd_sucesso, qtd_erro


def _listar_respostas_tiflux(endpoint, numero_tiflux):
    """
    Pagina um endpoint de resposta/comunicação do Tiflux (offset = número da
    página, não deslocamento de linha — ver doc da API) e retorna todos os itens.
    """
    itens = []
    pagina = 1
    while pagina <= MAX_PAGINAS_RESPOSTAS_TIFLUX:
        resp = requests.get(
            f"{URL_TIFLUX}/tickets/{numero_tiflux}/{endpoint}",
            params={"offset": pagina, "limit": TAMANHO_PAGINA_RESPOSTAS_TIFLUX},
            headers=headers_tiflux_get,
        )
        if resp.status_code != 200:
            log(f"⚠️ Falha ao listar {endpoint} do ticket Tiflux #{numero_tiflux} (status {resp.status_code}): {resp.text}")
            break

        pagina_itens = resp.json()
        if not isinstance(pagina_itens, list) or not pagina_itens:
            break

        itens.extend(pagina_itens)
        if len(pagina_itens) < TAMANHO_PAGINA_RESPOSTAS_TIFLUX:
            break
        pagina += 1

    return itens


def sincronizar_followups_tiflux_para_glpi(conn, headers_glpi, id_chamado, numero_tiflux):
    """
    Busca respostas públicas (/answers) e comunicações internas
    (/internal_communications) do ticket no Tiflux, filtra as que ainda não
    foram sincronizadas ou que a própria integração criou (eco), e cria um
    followup correspondente no GLPI pra cada uma.

    Defesa contra eco: a tabela de auditoria (id já processado ou já criado por
    nós) é o mecanismo primário, único disponível pra /internal_communications.
    Pra /answers, o campo answer_origin/author (só existe nesse endpoint) serve
    de defesa adicional.
    Retorna (qtd_sucesso, qtd_erro).
    """
    ja_processados_ou_proprios = obter_respostas_tiflux_ja_processadas_ou_proprias(conn, numero_tiflux)

    respostas = _listar_respostas_tiflux("answers", numero_tiflux)
    comunicacoes = _listar_respostas_tiflux("internal_communications", numero_tiflux)

    qtd_sucesso = 0
    qtd_erro = 0

    for resposta in respostas:
        id_origem = resposta.get("id")
        if id_origem in ja_processados_ou_proprios:
            continue
        if resposta.get("answer_origin") == "api" or str(resposta.get("author", "")).startswith("[API]"):
            continue

        conteudo = html.unescape(resposta.get("name") or "")
        id_criado, erro = criar_followup_glpi(headers_glpi, id_chamado, conteudo, is_private=0)
        if erro:
            qtd_erro += 1
            registrar_resultado_followup(
                conn, id_chamado, numero_tiflux, "tiflux_para_glpi", "publica", id_origem, None, "erro", erro,
            )
        else:
            qtd_sucesso += 1
            registrar_resultado_followup(
                conn, id_chamado, numero_tiflux, "tiflux_para_glpi", "publica", id_origem, id_criado,
                "sucesso", f"Resposta Tiflux #{id_origem} publicada como followup no GLPI (id {id_criado})",
            )

    for comunicacao in comunicacoes:
        id_origem = comunicacao.get("id")
        if id_origem in ja_processados_ou_proprios:
            continue

        conteudo = html.unescape(comunicacao.get("text") or "")
        id_criado, erro = criar_followup_glpi(headers_glpi, id_chamado, conteudo, is_private=1)
        if erro:
            qtd_erro += 1
            registrar_resultado_followup(
                conn, id_chamado, numero_tiflux, "tiflux_para_glpi", "interna", id_origem, None, "erro", erro,
            )
        else:
            qtd_sucesso += 1
            registrar_resultado_followup(
                conn, id_chamado, numero_tiflux, "tiflux_para_glpi", "interna", id_origem, id_criado,
                "sucesso", f"Comunicação interna Tiflux #{id_origem} publicada como followup privado no GLPI (id {id_criado})",
            )

    return qtd_sucesso, qtd_erro


def sincronizar_followups(conn, headers_glpi):
    """
    Percorre uma leva de chamados já sincronizados (obter_chamados_para_varrer_followups),
    ignora os que já estão fechados no GLPI (fora do escopo desta varredura,
    mas marcados via registrar_chamado_fechado_para_followups pra não travar o
    rodízio), e sincroniza followups nos dois sentidos pros demais.
    """
    chamados = obter_chamados_para_varrer_followups(conn)
    if not chamados:
        return

    total_g2t_sucesso = total_g2t_erro = 0
    total_t2g_sucesso = total_t2g_erro = 0

    for id_glpi, numero_tiflux in chamados:
        if not numero_tiflux:
            continue

        resp_ticket = requests.get(f"{URL_BASE}/Ticket/{id_glpi}", headers=headers_glpi)
        if resp_ticket.status_code not in (200, 206):
            log(f"⚠️ Não foi possível conferir status do chamado #{id_glpi} no GLPI "
                f"(status {resp_ticket.status_code}) — pulado nesta execução")
            continue

        status_glpi = resp_ticket.json().get("status")
        if status_glpi not in (1, 2, 3, 4):  # não está mais "aberto" (Novo/Processando/Pendente)
            registrar_chamado_fechado_para_followups(conn, id_glpi)
            continue

        s, e = sincronizar_followups_glpi_para_tiflux(conn, headers_glpi, id_glpi, numero_tiflux)
        total_g2t_sucesso += s
        total_g2t_erro += e

        s, e = sincronizar_followups_tiflux_para_glpi(conn, headers_glpi, id_glpi, numero_tiflux)
        total_t2g_sucesso += s
        total_t2g_erro += e

    log(f"Followups. GLPI->Tiflux: {total_g2t_sucesso} ok / {total_g2t_erro} erro | "
        f"Tiflux->GLPI: {total_t2g_sucesso} ok / {total_t2g_erro} erro")


# =============================================================================
# 6. MAIN
# =============================================================================

def main():
    log("Iniciando sincronização GLPI -> Tiflux")

    try:
        conn = conectar_db()
    except Exception as e:
        log(f"❌ Não foi possível conectar ao Postgres: {e}")
        sys.exit(1)

    try:
        headers_glpi = autenticar_glpi()
    except Exception as e:
        log(f"❌ Não foi possível autenticar no GLPI: {e}")
        conn.close()
        sys.exit(1)

    try:
        ids_processados = obter_ids_ja_processados(conn)
        ids_retry = obter_ids_para_retry(conn)
        # Só reprocessa erros de chamados que ainda estão dentro da faixa válida
        # (se ID_MINIMO_GLPI mudar pra cima no futuro, erros antigos abaixo dele
        # não devem ficar sendo retentados pra sempre)
        ids_retry = {i for i in ids_retry if i >= ID_MINIMO_GLPI}

        id_inicial_sondagem = obter_proximo_id_para_sondar(conn)
        ids_glpi_encontrados = buscar_chamados_desde(headers_glpi, id_inicial_sondagem)
        ids_novos = [i for i in ids_glpi_encontrados if i not in ids_processados]

        candidatos = sorted(set(ids_novos) | ids_retry)

        if not candidatos:
            log("Nenhum chamado novo ou pendente de retry.")
        else:
            log(f"{len(candidatos)} chamado(s) para processar: {candidatos}")

            total_sucesso = 0
            total_ignorado = 0
            total_erro = 0
            for id_chamado in candidatos:
                status, numero_tiflux, mensagem = processar_chamado(headers_glpi, id_chamado)

                if status == "sucesso":
                    total_sucesso += 1
                    registrar_resultado(conn, id_chamado, numero_tiflux, status, mensagem)
                    log(f"✅ Chamado #{id_chamado}: {mensagem}")
                elif status == "ignorado":
                    total_ignorado += 1
                    # Não interessa: nem grava na auditoria, nem loga por chamado —
                    # só entra na contagem final abaixo.
                else:
                    total_erro += 1
                    registrar_resultado(conn, id_chamado, numero_tiflux, status, mensagem)
                    log(f"❌ Chamado #{id_chamado}: {mensagem}")

            log(f"Finalizado. Sucesso: {total_sucesso} | Ignorado: {total_ignorado} | Erro: {total_erro}")

        # Followups (GLPI <-> Tiflux) dos chamados já sincronizados — roda sempre,
        # mesmo sem chamados novos acima, e já pega chamados criados nesta mesma
        # execução em vez de esperar o próximo ciclo do cron.
        sincronizar_followups(conn, headers_glpi)

    finally:
        encerrar_sessao_glpi(headers_glpi)
        conn.close()


if __name__ == "__main__":
    main()
