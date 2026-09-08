"""Ponto de entrada da sincronização GLPI -> Tiflux. Ver docs/ARCHITECTURE.md."""

import sys

from sync import db_chamados
from sync.config import Config, log
from sync.glpi_client import GlpiClient
from sync.processamento_chamado import processar_chamado
from sync.sincronizacao_followups import sincronizar_followups
from sync.tiflux_client import TifluxClient


def main() -> None:
    log("Iniciando sincronização GLPI -> Tiflux")
    config = Config.carregar()

    try:
        conn = db_chamados.conectar_db(config)
    except Exception as e:
        log(f"❌ Não foi possível conectar ao Postgres: {e}")
        sys.exit(1)

    try:
        glpi = GlpiClient.autenticar(config)
    except Exception as e:
        log(f"❌ Não foi possível autenticar no GLPI: {e}")
        conn.close()
        sys.exit(1)

    tiflux = TifluxClient.conectar(config)

    try:
        _processar_chamados_pendentes(conn, config, glpi, tiflux)

        # Followups (GLPI <-> Tiflux) dos chamados já sincronizados — roda sempre,
        # mesmo sem chamados novos acima, e já pega chamados criados nesta mesma
        # execução em vez de esperar o próximo ciclo do cron.
        sincronizar_followups(conn, config, glpi, tiflux)
    finally:
        glpi.encerrar_sessao()
        conn.close()


def _processar_chamados_pendentes(conn, config: Config, glpi: GlpiClient, tiflux: TifluxClient) -> None:
    candidatos = _levantar_candidatos(conn, config, glpi)
    if not candidatos:
        log("Nenhum chamado novo ou pendente de retry.")
        return

    log(f"{len(candidatos)} chamado(s) para processar: {candidatos}")
    total_sucesso = total_ignorado = total_erro = 0

    for id_chamado in candidatos:
        status, numero_tiflux, mensagem = processar_chamado(glpi, tiflux, config, id_chamado)

        if status == "sucesso":
            total_sucesso += 1
            db_chamados.registrar_resultado(conn, config, id_chamado, numero_tiflux, status, mensagem)
            log(f"✅ Chamado #{id_chamado}: {mensagem}")
        elif status == "ignorado":
            total_ignorado += 1
            # Não interessa: nem grava na auditoria, nem loga por chamado —
            # só entra na contagem final abaixo.
        else:
            total_erro += 1
            db_chamados.registrar_resultado(conn, config, id_chamado, numero_tiflux, status, mensagem)
            log(f"❌ Chamado #{id_chamado}: {mensagem}")

    log(f"Finalizado. Sucesso: {total_sucesso} | Ignorado: {total_ignorado} | Erro: {total_erro}")


def _levantar_candidatos(conn, config: Config, glpi: GlpiClient) -> list[int]:
    ids_processados = db_chamados.obter_ids_ja_processados(conn, config)
    ids_retry = db_chamados.obter_ids_para_retry(conn, config)
    # Só reprocessa erros de chamados que ainda estão dentro da faixa válida
    # (se ID_MINIMO_GLPI mudar pra cima no futuro, erros antigos abaixo dele
    # não devem ficar sendo retentados pra sempre)
    ids_retry = {i for i in ids_retry if i >= config.id_minimo_glpi}

    id_inicial_sondagem = db_chamados.obter_proximo_id_para_sondar(conn, config)
    ids_glpi_encontrados = glpi.buscar_chamados_desde(
        id_inicial_sondagem, config.tamanho_pagina_busca, config.max_furos_seguidos
    )
    ids_novos = [i for i in ids_glpi_encontrados if i not in ids_processados]

    return sorted(set(ids_novos) | ids_retry)


if __name__ == "__main__":
    main()
