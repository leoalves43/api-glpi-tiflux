"""Sincronização bidirecional de followups/respostas entre GLPI e Tiflux."""

import html

from sync import db_followups
from sync.config import Config, log
from sync.glpi_client import GlpiClient
from sync.regras_negocio import autor_e_solicitante
from sync.tiflux_client import TifluxClient

# Status de chamado no GLPI considerados "aberto" (Novo/Processando/Pendente)
STATUS_GLPI_ABERTOS = (1, 2, 3, 4)


def sincronizar_followups(conn, config: Config, glpi: GlpiClient, tiflux: TifluxClient) -> None:
    """
    Percorre uma leva de chamados já sincronizados (obter_chamados_para_varrer_followups),
    ignora os que já estão fechados no GLPI (fora do escopo desta varredura,
    mas marcados via registrar_chamado_fechado_para_followups pra não travar o
    rodízio), e sincroniza followups nos dois sentidos pros demais.
    """
    chamados = db_followups.obter_chamados_para_varrer_followups(conn, config)
    if not chamados:
        return

    totais = {"g2t_sucesso": 0, "g2t_erro": 0, "t2g_sucesso": 0, "t2g_erro": 0}
    for id_glpi, numero_tiflux in chamados:
        if numero_tiflux:
            _sincronizar_chamado_aberto(conn, config, glpi, tiflux, id_glpi, numero_tiflux, totais)

    log(f"Followups. GLPI->Tiflux: {totais['g2t_sucesso']} ok / {totais['g2t_erro']} erro | "
        f"Tiflux->GLPI: {totais['t2g_sucesso']} ok / {totais['t2g_erro']} erro")


def _sincronizar_chamado_aberto(conn, config, glpi, tiflux, id_glpi, numero_tiflux, totais) -> None:
    ticket, status_code = glpi.obter_ticket(id_glpi)
    if ticket is None:
        log(f"⚠️ Não foi possível conferir status do chamado #{id_glpi} no GLPI "
            f"(status {status_code}) — pulado nesta execução")
        return

    if ticket.get("status") not in STATUS_GLPI_ABERTOS:
        db_followups.registrar_chamado_fechado_para_followups(conn, config, id_glpi)
        return

    s, e = sincronizar_followups_glpi_para_tiflux(conn, config, glpi, tiflux, id_glpi, numero_tiflux)
    totais["g2t_sucesso"] += s
    totais["g2t_erro"] += e

    s, e = sincronizar_followups_tiflux_para_glpi(conn, config, glpi, tiflux, id_glpi, numero_tiflux)
    totais["t2g_sucesso"] += s
    totais["t2g_erro"] += e


# --- GLPI -> Tiflux -----------------------------------------------------

def sincronizar_followups_glpi_para_tiflux(
    conn, config: Config, glpi: GlpiClient, tiflux: TifluxClient, id_chamado: int, numero_tiflux: str,
) -> tuple[int, int]:
    """
    Busca followups do chamado no GLPI, filtra os que ainda não foram
    publicados no Tiflux, e cria cada um lá. Retorna (qtd_sucesso, qtd_erro).
    """
    pendentes = _followups_glpi_pendentes(conn, config, glpi, id_chamado)
    if not pendentes:
        return 0, 0

    nome_requerente, id_requerente_ticket = _resolver_requerente(glpi, id_chamado)

    qtd_sucesso = qtd_erro = 0
    for followup in pendentes:
        sucesso = _publicar_followup_no_tiflux(
            conn, config, tiflux, id_chamado, numero_tiflux, followup, nome_requerente, id_requerente_ticket,
        )
        qtd_sucesso, qtd_erro = _acumular(sucesso, qtd_sucesso, qtd_erro)

    return qtd_sucesso, qtd_erro


def _followups_glpi_pendentes(conn, config: Config, glpi: GlpiClient, id_chamado: int) -> list[dict]:
    followups = glpi.obter_followups(id_chamado)
    if not followups:
        return []
    ja_processados = db_followups.obter_followups_glpi_ja_processados(conn, config, id_chamado)
    return [f for f in followups if f.get("id") not in ja_processados]


def _resolver_requerente(glpi: GlpiClient, id_chamado: int) -> tuple[str, int | None]:
    ticket, _ = glpi.obter_ticket(id_chamado)
    nome_requerente, _, id_requerente_ticket = glpi.obter_requerente(id_chamado, ticket or {})
    return nome_requerente, id_requerente_ticket


def _publicar_followup_no_tiflux(
    conn, config, tiflux: TifluxClient, id_chamado, numero_tiflux, followup, nome_requerente, id_requerente_ticket,
) -> bool:
    id_origem = followup.get("id")
    # Alguns followups do GLPI vêm com o conteúdo HTML-entity-encoded
    # (ex.: "&#60;p&#62;texto&#60;/p&#62;" em vez de "<p>texto</p>"),
    # dependendo de como foram criados; html.unescape() normaliza pros
    # dois casos (é um no-op se o conteúdo já vier como HTML literal).
    conteudo = html.unescape(followup.get("content") or "")
    tipo, resp = _enviar_followup_para_tiflux(tiflux, numero_tiflux, followup, conteudo, nome_requerente, id_requerente_ticket)
    return _registrar_publicacao_no_tiflux(conn, config, id_chamado, numero_tiflux, tipo, id_origem, resp)


def _enviar_followup_para_tiflux(tiflux: TifluxClient, numero_tiflux, followup, conteudo, nome_requerente, id_requerente_ticket):
    is_private = bool(followup.get("is_private"))
    id_autor = followup.get("users_id")

    if is_private:
        return "interna", tiflux.publicar_comunicacao_interna(numero_tiflux, conteudo)
    if autor_e_solicitante(id_autor, id_requerente_ticket):
        return "publica", tiflux.publicar_resposta_cliente(numero_tiflux, conteudo, nome_requerente)
    return "publica", tiflux.publicar_resposta_agente(numero_tiflux, conteudo)


def _registrar_publicacao_no_tiflux(conn, config, id_chamado, numero_tiflux, tipo, id_origem, resp) -> bool:
    if resp.status_code not in (200, 201):
        db_followups.registrar_resultado_followup(
            conn, config, id_chamado, numero_tiflux, "glpi_para_tiflux", tipo, id_origem, None,
            "erro", f"Falha ao publicar followup no Tiflux ({resp.status_code}): {resp.text}",
        )
        return False

    id_destino = resp.json().get("id")
    if not id_destino:
        db_followups.registrar_resultado_followup(
            conn, config, id_chamado, numero_tiflux, "glpi_para_tiflux", tipo, id_origem, None,
            "erro", "Followup publicado no Tiflux, mas não foi possível identificar o id na resposta",
        )
        return False

    db_followups.registrar_resultado_followup(
        conn, config, id_chamado, numero_tiflux, "glpi_para_tiflux", tipo, id_origem, id_destino,
        "sucesso", f"Followup GLPI #{id_origem} publicado no Tiflux (id {id_destino})",
    )
    return True


# --- Tiflux -> GLPI -----------------------------------------------------

def sincronizar_followups_tiflux_para_glpi(
    conn, config: Config, glpi: GlpiClient, tiflux: TifluxClient, id_chamado: int, numero_tiflux: str,
) -> tuple[int, int]:
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
    ja_processados_ou_proprios = db_followups.obter_respostas_tiflux_ja_processadas_ou_proprias(conn, config, numero_tiflux)
    respostas = tiflux.listar_respostas(numero_tiflux, config.tamanho_pagina_respostas_tiflux, config.max_paginas_respostas_tiflux)
    comunicacoes = tiflux.listar_comunicacoes_internas(numero_tiflux, config.tamanho_pagina_respostas_tiflux, config.max_paginas_respostas_tiflux)

    s1, e1 = _publicar_respostas_publicas(conn, config, glpi, id_chamado, numero_tiflux, respostas, ja_processados_ou_proprios)
    s2, e2 = _publicar_comunicacoes_internas(conn, config, glpi, id_chamado, numero_tiflux, comunicacoes, ja_processados_ou_proprios)
    return s1 + s2, e1 + e2


def _publicar_respostas_publicas(conn, config, glpi: GlpiClient, id_chamado, numero_tiflux, respostas, ja_processados_ou_proprios) -> tuple[int, int]:
    qtd_sucesso = qtd_erro = 0
    for resposta in respostas:
        if _deve_ignorar_resposta_publica(resposta, ja_processados_ou_proprios):
            continue
        id_origem = resposta.get("id")
        conteudo = html.unescape(resposta.get("name") or "")
        id_criado, erro = glpi.criar_followup(id_chamado, conteudo, is_private=0)
        sucesso = _registrar_followup_tiflux_para_glpi(
            conn, config, id_chamado, numero_tiflux, "publica", id_origem, id_criado, erro,
            mensagem_sucesso=f"Resposta Tiflux #{id_origem} publicada como followup no GLPI (id {id_criado})",
        )
        qtd_sucesso, qtd_erro = _acumular(sucesso, qtd_sucesso, qtd_erro)
    return qtd_sucesso, qtd_erro


def _publicar_comunicacoes_internas(conn, config, glpi: GlpiClient, id_chamado, numero_tiflux, comunicacoes, ja_processados_ou_proprios) -> tuple[int, int]:
    qtd_sucesso = qtd_erro = 0
    for comunicacao in comunicacoes:
        id_origem = comunicacao.get("id")
        if id_origem in ja_processados_ou_proprios:
            continue
        conteudo = html.unescape(comunicacao.get("text") or "")
        id_criado, erro = glpi.criar_followup(id_chamado, conteudo, is_private=1)
        sucesso = _registrar_followup_tiflux_para_glpi(
            conn, config, id_chamado, numero_tiflux, "interna", id_origem, id_criado, erro,
            mensagem_sucesso=f"Comunicação interna Tiflux #{id_origem} publicada como followup privado no GLPI (id {id_criado})",
        )
        qtd_sucesso, qtd_erro = _acumular(sucesso, qtd_sucesso, qtd_erro)
    return qtd_sucesso, qtd_erro


def _deve_ignorar_resposta_publica(resposta: dict, ja_processados_ou_proprios: set[int]) -> bool:
    if resposta.get("id") in ja_processados_ou_proprios:
        return True
    return resposta.get("answer_origin") == "api" or str(resposta.get("author", "")).startswith("[API]")


def _acumular(sucesso: bool, qtd_sucesso: int, qtd_erro: int) -> tuple[int, int]:
    return (qtd_sucesso + 1, qtd_erro) if sucesso else (qtd_sucesso, qtd_erro + 1)


def _registrar_followup_tiflux_para_glpi(
    conn, config, id_chamado, numero_tiflux, tipo, id_origem, id_criado, erro, mensagem_sucesso: str,
) -> bool:
    if erro:
        db_followups.registrar_resultado_followup(
            conn, config, id_chamado, numero_tiflux, "tiflux_para_glpi", tipo, id_origem, None, "erro", erro,
        )
        return False

    db_followups.registrar_resultado_followup(
        conn, config, id_chamado, numero_tiflux, "tiflux_para_glpi", tipo, id_origem, id_criado,
        "sucesso", mensagem_sucesso,
    )
    return True
