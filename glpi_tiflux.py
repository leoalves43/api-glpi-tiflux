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

Tabela de auditoria: ver criar_tabela_auditoria.sql
"""

import sys
import urllib.parse
from datetime import datetime

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

# Regra de negócio: só processar chamados a partir deste número
ID_MINIMO_GLPI = 33637

# Quantos chamados buscar por execução do cron (aumente se ficar tickets p/ trás)
TAMANHO_PAGINA_BUSCA = 200

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
    """IDs que já têm um resultado final (sucesso ou ignorado) e não devem ser reprocessados."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT id_glpi FROM {TABELA_AUDITORIA} WHERE status IN ('sucesso', 'ignorado')")
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


# =============================================================================
# 4. TRADUÇÃO GLPI -> TIFLUX (regras de negócio)
# =============================================================================

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

    return nome_solicitante, email_solicitante


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

        nome_solicitante_glpi, email_solicitante_glpi = obter_requerente_glpi(headers_glpi, id_chamado, ticket)

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
        descricao_tiflux = f"{cabecalho_personalizado}<br><br>{info_solicitante_texto}<br><br>Descrição:<br>{descricao_glpi}"

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

        msg = (f"Ticket #{ticket_number_tiflux} criado no Tiflux | Mesa {mesa_tiflux} | "
               f"Prioridade ID {id_prioridade_tiflux} | "
               f"Técnico {nome_tecnico_tiflux} | Solicitante {info_solicitante_tiflux}")
        return "sucesso", ticket_number_tiflux, msg

    except requests.RequestException as e:
        return "erro", None, f"Erro de rede/conexão: {e}"
    except Exception as e:
        return "erro", None, f"Erro inesperado: {e}"


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
            log("Nenhum chamado novo ou pendente de retry. Nada a fazer.")
            return

        log(f"{len(candidatos)} chamado(s) para processar: {candidatos}")

        total_sucesso = 0
        total_ignorado = 0
        total_erro = 0
        for id_chamado in candidatos:
            status, numero_tiflux, mensagem = processar_chamado(headers_glpi, id_chamado)
            registrar_resultado(conn, id_chamado, numero_tiflux, status, mensagem)

            if status == "sucesso":
                total_sucesso += 1
                log(f"✅ Chamado #{id_chamado}: {mensagem}")
            elif status == "ignorado":
                total_ignorado += 1
                log(f"⏭️  Chamado #{id_chamado} ignorado: {mensagem}")
            else:
                total_erro += 1
                log(f"❌ Chamado #{id_chamado}: {mensagem}")

        log(f"Finalizado. Sucesso: {total_sucesso} | Ignorado: {total_ignorado} | Erro: {total_erro}")

    finally:
        encerrar_sessao_glpi(headers_glpi)
        conn.close()


if __name__ == "__main__":
    main()
