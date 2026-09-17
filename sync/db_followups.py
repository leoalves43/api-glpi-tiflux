"""Postgres — tabela de auditoria de followups (várias linhas por chamado)."""

from sync.config import Config


def obter_chamados_para_varrer_followups(conn, config: Config) -> list[tuple[int, int]]:
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
    tabela_auditoria = config.tabela_auditoria
    tabela_followups = config.tabela_followups
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT t.id_glpi, t.numero_tiflux
            FROM {tabela_auditoria} t
            LEFT JOIN (
                SELECT id_glpi, MAX(atualizado_em) AS ultima_varredura
                FROM {tabela_followups}
                GROUP BY id_glpi
            ) f ON f.id_glpi = t.id_glpi
            WHERE t.status = 'sucesso'
            ORDER BY f.ultima_varredura ASC NULLS FIRST
            LIMIT %s
            """,
            (config.tamanho_pagina_followups,),
        )
        return cur.fetchall()


def obter_followups_glpi_ja_processados(conn, config: Config, id_glpi: int) -> set[int]:
    """
    IDs de ITILFollowup do GLPI já publicados com sucesso no Tiflux para este
    chamado. Só considera status='sucesso' (mesmo critério de
    obter_ids_ja_processados p/ chamados) — um followup que falhou fica de
    fora daqui de propósito, pra ser retentado na próxima execução.
    """
    tabela = config.tabela_followups
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT id_origem FROM {tabela} "
            f"WHERE direcao = 'glpi_para_tiflux' AND status = 'sucesso' AND id_glpi = %s",
            (id_glpi,),
        )
        return {row[0] for row in cur.fetchall()}


def obter_respostas_tiflux_ja_processadas_ou_proprias(conn, config: Config, numero_tiflux: int) -> set[int]:
    """
    IDs de resposta/comunicação interna do Tiflux a IGNORAR na varredura
    Tiflux -> GLPI: os que já processamos com sucesso nesse sentido (direcao=
    'tiflux_para_glpi', status='sucesso' — followups com erro ficam de fora
    de propósito, pra serem retentados) UNION os que a própria integração
    criou no sentido glpi_para_tiflux (id_destino, só existe em linhas de
    sucesso) — evita reimportar o próprio eco.
    """
    tabela = config.tabela_followups
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id_origem FROM {tabela}
            WHERE numero_tiflux = %s AND direcao = 'tiflux_para_glpi' AND status = 'sucesso'
            UNION
            SELECT id_destino FROM {tabela}
            WHERE numero_tiflux = %s AND direcao = 'glpi_para_tiflux' AND id_destino IS NOT NULL
            """,
            (numero_tiflux, numero_tiflux),
        )
        return {row[0] for row in cur.fetchall()}


def registrar_resultado_followup(
    conn, config: Config, id_glpi: int, numero_tiflux, direcao: str, tipo: str,
    id_origem: int, id_destino, status: str, mensagem: str,
) -> None:
    """Grava (ou atualiza, em caso de retry) o resultado da sincronização de um followup."""
    tabela = config.tabela_followups
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {tabela}
                (id_glpi, numero_tiflux, direcao, tipo, id_origem, id_destino, status, mensagem, tentativas, criado_em, atualizado_em)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, now(), now())
            ON CONFLICT (direcao, id_origem) DO UPDATE SET
                numero_tiflux = EXCLUDED.numero_tiflux,
                tipo          = EXCLUDED.tipo,
                id_destino    = EXCLUDED.id_destino,
                status        = EXCLUDED.status,
                mensagem      = EXCLUDED.mensagem,
                tentativas    = {tabela}.tentativas + 1,
                atualizado_em = now()
            """,
            (id_glpi, numero_tiflux, direcao, tipo, id_origem, id_destino, status, mensagem),
        )
    conn.commit()


def obter_ultima_acao_cascata_sucesso(conn, config: Config, id_glpi: int) -> str | None:
    """
    `tipo` ('encerramento', 'reabertura' ou 'reabertura_tiflux') da última
    ação de encerramento/reabertura em cascata BEM-SUCEDIDA registrada pra
    este chamado — linha única por chamado (direcao='tiflux_para_glpi',
    id_origem=-id_glpi, ver _mudar_status_em_cascata e
    _reabrir_tiflux_apos_recusa_glpi em sincronizacao_followups.py). None se
    nunca houve uma ação de cascata bem-sucedida, ou se a última tentativa
    registrada falhou (status='erro') — só o último estado confirmado conta,
    pra não confundir com uma tentativa que não mudou nada de fato.
    """
    tabela = config.tabela_followups
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT tipo FROM {tabela} "
            f"WHERE direcao = 'tiflux_para_glpi' AND id_origem = %s AND status = 'sucesso'",
            (-id_glpi,),
        )
        linha = cur.fetchone()
        return linha[0] if linha else None


def registrar_chamado_fechado_para_followups(conn, config: Config, id_glpi: int) -> None:
    """
    Marca (sem sincronizar nenhum followup) que este chamado foi conferido e
    está fechado no GLPI. Sem isso, um chamado fechado nunca ganharia uma
    linha na tabela de followups e ficaria pra sempre em primeiro lugar no
    rodízio de obter_chamados_para_varrer_followups (que ordena por última
    varredura), monopolizando o limite de chamados verificados por execução.
    """
    registrar_resultado_followup(
        conn, config, id_glpi, None, "verificacao_status", "status", -id_glpi, None,
        "fechado", "Chamado fechado no GLPI — fora do escopo da varredura de followups",
    )
