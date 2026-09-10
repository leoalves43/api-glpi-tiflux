"""Postgres — tabela de auditoria de chamados (um id_glpi por linha)."""

import psycopg2
import psycopg2.extensions

from sync.config import Config


def conectar_db(config: Config) -> psycopg2.extensions.connection:
    """
    Sem client_encoding explícito, o libpq no Windows herda o codepage do SO
    (cp1252) em vez de UTF8, e nem chega a mandar os bytes pro Postgres antes
    de estourar UnicodeEncodeError. Forçar UTF8 aqui resolve isso — mas o
    banco em si tem encoding WIN1252 (não dá pra mudar sem recriar o banco),
    então caracteres fora do WIN1252 (ex: '✪') ainda precisam ser saneados
    antes do INSERT; ver _sanear_win1252 em registrar_resultado.
    """
    conn = psycopg2.connect(
        host=config.db_host, port=config.db_port, dbname=config.db_name,
        user=config.db_user, password=config.db_password,
    )
    conn.set_client_encoding("UTF8")
    return conn


def _sanear_win1252(texto: str) -> str:
    """
    O banco de auditoria tem encoding WIN1252; texto vindo de fora (título do
    GLPI, corpo de erro da API do Tiflux) pode ter caracteres fora desse
    charset (ex: '✪') que o Postgres rejeita com UntranslatableCharacter.
    Substitui o que não for representável por '?' em vez de derrubar a
    sincronização inteira por causa de uma mensagem de auditoria.
    """
    return texto.encode("cp1252", errors="replace").decode("cp1252")


def obter_ids_ja_processados(conn, config: Config) -> set[int]:
    """
    IDs que já têm um resultado de sucesso gravado e não devem ser reprocessados.
    Chamados 'ignorado' (sem o grupo observador) não são gravados na auditoria,
    então não entram aqui — a sondagem vai re-conferir esses IDs a cada execução.
    """
    tabela = config.tabela_auditoria
    with conn.cursor() as cur:
        cur.execute(f"SELECT id_glpi FROM {tabela} WHERE status = 'sucesso'")
        return {row[0] for row in cur.fetchall()}


def obter_proximo_id_para_sondar(conn, config: Config) -> int:
    """
    De onde a sondagem deve continuar: o menor id_glpi entre os últimos
    QUANTIDADE_REGISTROS_PARA_RECUO chamados já confirmados (status 'sucesso'
    ou 'erro'), nunca abaixo de ID_MINIMO_GLPI. Recuar (em vez de continuar
    exatamente do maior_id + 1) reconfere um intervalo de IDs recentes que já
    foram sondados: um chamado pode devolver 404 na hora exata em que a
    sondagem passa por ele (ainda não commitado no GLPI, ou temporariamente
    na lixeira) e, sem esse recuo, nunca mais seria revisitado (chamado
    #33769 ficou órfão assim). Basear o recuo em confirmações reais (em vez
    de uma quantidade fixa de IDs) faz a janela se esticar sozinha quando há
    trechos longos de chamados 'ignorado' (fora do grupo observador) no meio
    — que não contam como confirmação — em vez de um número fixo que pode
    ficar pequeno demais. IDs já com resultado 'sucesso' são filtrados depois
    em obter_ids_ja_processados, então reconferir não os reprocessa.
    """
    tabela = config.tabela_auditoria
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT MIN(id_glpi) FROM (
                SELECT id_glpi FROM {tabela}
                WHERE status IN ('sucesso', 'erro')
                ORDER BY id_glpi DESC
                LIMIT %s
            ) ultimos_confirmados
            """,
            (config.quantidade_registros_para_recuo,),
        )
        menor_id_recente = cur.fetchone()[0]
    if menor_id_recente is None:
        return config.id_minimo_glpi
    return max(config.id_minimo_glpi, menor_id_recente)


def obter_ids_para_retry(conn, config: Config) -> set[int]:
    tabela = config.tabela_auditoria
    with conn.cursor() as cur:
        cur.execute(f"SELECT id_glpi FROM {tabela} WHERE status = 'erro'")
        return {row[0] for row in cur.fetchall()}


def registrar_resultado(conn, config: Config, id_glpi: int, numero_tiflux, status: str, mensagem: str) -> None:
    """Grava (ou atualiza, em caso de retry) o resultado da sincronização de um chamado."""
    tabela = config.tabela_auditoria
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {tabela} (id_glpi, numero_tiflux, status, mensagem, tentativas, criado_em, atualizado_em)
            VALUES (%s, %s, %s, %s, 1, now(), now())
            ON CONFLICT (id_glpi) DO UPDATE SET
                numero_tiflux = EXCLUDED.numero_tiflux,
                status        = EXCLUDED.status,
                mensagem      = EXCLUDED.mensagem,
                tentativas    = {tabela}.tentativas + 1,
                atualizado_em = now()
            """,
            (id_glpi, numero_tiflux, status, _sanear_win1252(mensagem)),
        )
    conn.commit()
