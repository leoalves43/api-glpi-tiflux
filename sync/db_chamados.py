"""Postgres — tabela de auditoria de chamados (um id_glpi por linha)."""

import psycopg2
import psycopg2.extensions

from sync.config import Config


def conectar_db(config: Config) -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=config.db_host, port=config.db_port, dbname=config.db_name,
        user=config.db_user, password=config.db_password,
    )


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
    De onde a sondagem deve continuar: logo após o maior id_glpi já registrado
    na auditoria (sucesso, erro ou ignorado — qualquer um confirma que aquele
    ID já foi verificado), nunca abaixo de ID_MINIMO_GLPI.
    """
    tabela = config.tabela_auditoria
    with conn.cursor() as cur:
        cur.execute(f"SELECT MAX(id_glpi) FROM {tabela}")
        maior_id = cur.fetchone()[0]
    if maior_id is None:
        return config.id_minimo_glpi
    return max(config.id_minimo_glpi, maior_id + 1)


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
            (id_glpi, numero_tiflux, status, mensagem),
        )
    conn.commit()
