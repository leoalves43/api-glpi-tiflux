"""Sincronização bidirecional de followups/respostas entre GLPI e Tiflux."""

import html
from datetime import datetime, timedelta

from sync import db_followups
from sync.config import Config, log
from sync.glpi_client import GlpiClient
from sync.regras_negocio import autor_e_solicitante, definir_autor_glpi
from sync.tiflux_client import TifluxClient

# Status de chamado no GLPI considerados "aberto" (Novo/Processando/Pendente)
STATUS_GLPI_ABERTOS = (1, 2, 3, 4)

# Status pra onde o chamado GLPI vai quando o ticket correspondente é
# fechado ou cancelado no Tiflux (encerramento em cascata)
STATUS_GLPI_SOLUCIONADO = 5

# Status pra onde o chamado GLPI volta quando um encerramento em cascata
# anterior é desfeito porque o ticket foi reaberto no Tiflux
STATUS_GLPI_REABERTO = 2  # Processando (atribuído)


def _prefixar_autor_tiflux(nome: str | None, timestamp_utc: str | None, conteudo: str) -> str:
    """
    Prefixa o conteúdo com o nome de quem respondeu de fato no Tiflux (em
    negrito) e a data/hora do assentamento — a autoria real no GLPI
    (users_id) é sempre Léo (ver definir_autor_glpi), não o técnico do
    Tiflux, então esse prefixo é a única forma de identificar quem
    respondeu de verdade dentro do texto.
    """
    cabecalho = f"<strong>{nome or 'Desconhecido'}</strong>"
    data_hora = _formatar_data_hora_brasilia(timestamp_utc)
    if data_hora:
        cabecalho += f" ({data_hora})"
    return f"{cabecalho}<br><br>{conteudo}"


def _formatar_data_hora_brasilia(timestamp_utc: str | None) -> str | None:
    """Converte um timestamp UTC do Tiflux (ex.: "2026-09-09T14:10:26Z") pra "dd/mm/aaaa hh:mm" em horário de Brasília (UTC-3)."""
    if not timestamp_utc:
        return None
    try:
        dt_utc = datetime.strptime(timestamp_utc, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return (dt_utc - timedelta(hours=3)).strftime("%d/%m/%Y %H:%M")


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

    totais = {"g2t_sucesso": 0, "g2t_erro": 0, "t2g_sucesso": 0, "t2g_erro": 0, "status_sucesso": 0, "status_erro": 0}
    for id_glpi, numero_tiflux in chamados:
        if numero_tiflux:
            _sincronizar_chamado_aberto(conn, config, glpi, tiflux, id_glpi, numero_tiflux, totais)

    log(f"Followups. GLPI->Tiflux: {totais['g2t_sucesso']} ok / {totais['g2t_erro']} erro | "
        f"Tiflux->GLPI: {totais['t2g_sucesso']} ok / {totais['t2g_erro']} erro | "
        f"Encerramento/reabertura em cascata: {totais['status_sucesso']} ok / {totais['status_erro']} erro")


def _sincronizar_chamado_aberto(conn, config, glpi, tiflux, id_glpi, numero_tiflux, totais) -> None:
    ticket_glpi, status_code = glpi.obter_ticket(id_glpi)
    if ticket_glpi is None:
        log(f"⚠️ Não foi possível conferir status do chamado #{id_glpi} no GLPI "
            f"(status {status_code}) — pulado nesta execução")
        return

    ticket_tiflux, _ = tiflux.obter_ticket(numero_tiflux)

    if ticket_glpi.get("status") not in STATUS_GLPI_ABERTOS:
        _tratar_chamado_fechado_no_glpi(conn, config, glpi, id_glpi, numero_tiflux, ticket_glpi, ticket_tiflux, totais)
        return

    if (
        ticket_tiflux
        and ticket_tiflux.get("is_closed")
        and db_followups.obter_ultima_acao_cascata_sucesso(conn, config, id_glpi) == "encerramento"
    ):
        # Chamado tinha sido encerrado em cascata (Tiflux fechado -> GLPI
        # Solucionado) e voltou a ficar aberto no GLPI (recusa da solução
        # pelo requerente, ou reabertura manual) sem que o Tiflux tenha sido
        # reaberto — essa integração não escuta esse evento no GLPI, então
        # reabre o Tiflux aqui pra equalizar. Sem isso, o followup da própria
        # recusa nunca seria publicado no Tiflux (422 "Cannot add answer in a
        # closed ticket") e o encerramento em cascata logo abaixo reabriria
        # o ciclo, forçando o GLPI de volta pra Solucionado.
        if _reabrir_tiflux_apos_recusa_glpi(conn, config, tiflux, id_glpi, numero_tiflux, totais):
            ticket_tiflux, _ = tiflux.obter_ticket(numero_tiflux)

    s, e = sincronizar_followups_glpi_para_tiflux(conn, config, glpi, tiflux, id_glpi, numero_tiflux)
    totais["g2t_sucesso"] += s
    totais["g2t_erro"] += e

    s, e = sincronizar_followups_tiflux_para_glpi(conn, config, glpi, tiflux, id_glpi, numero_tiflux)
    totais["t2g_sucesso"] += s
    totais["t2g_erro"] += e

    if ticket_tiflux and ticket_tiflux.get("is_closed"):
        _encerrar_em_cascata(conn, config, glpi, tiflux, id_glpi, numero_tiflux, totais)


def _tratar_chamado_fechado_no_glpi(conn, config, glpi, id_glpi, numero_tiflux, ticket_glpi, ticket_tiflux, totais) -> None:
    """
    Chamado já não está mais "aberto" no GLPI. Normalmente é porque um
    encerramento em cascata anterior já rodou (status Solucionado) — nesse
    caso, se o ticket foi REABERTO no Tiflux nesse meio tempo, desfaz o
    encerramento (volta pra Processando) pra followups voltarem a sincronizar
    na próxima execução. Fechamentos manuais no GLPI (ex.: status Fechado,
    feito por um técnico direto lá) não são mexidos — só reabrimos o que a
    própria integração fechou.
    """
    reabrir = (
        ticket_glpi.get("status") == STATUS_GLPI_SOLUCIONADO
        and ticket_tiflux is not None
        and not ticket_tiflux.get("is_closed")
    )
    if reabrir:
        _mudar_status_em_cascata(
            conn, config, glpi, id_glpi, numero_tiflux, STATUS_GLPI_REABERTO, "reabertura",
            f"Chamado #{id_glpi} reaberto no GLPI (status Processando) — reaberto no Tiflux #{numero_tiflux}",
            totais,
        )
        return

    db_followups.registrar_chamado_fechado_para_followups(conn, config, id_glpi)


_SEM_RESPOSTA_TIFLUX = "Chamado encerrado no Tiflux, sem resposta pública registrada."


def _encerrar_em_cascata(conn, config, glpi: GlpiClient, tiflux: TifluxClient, id_glpi, numero_tiflux, totais) -> None:
    """
    Essa instalação do GLPI recusa (com HTTP 200 mas message não-vazia, ver
    GlpiClient._atualizar_chamado) mudar o status pra Solucionado sem técnico
    atribuído E sem uma solução registrada — confirmado ao vivo. Garante os
    dois antes de tentar. Se qualquer um dos dois falhar aqui, a tentativa de
    status abaixo também vai falhar (GLPI recusa) e ser retentada na próxima
    execução, quando os dois são tentados de novo — idempotente via
    tecnico_atribuido()/solucao_registrada(), não duplica em retries.
    """
    if glpi.tecnico_atribuido(id_glpi) is None:
        glpi.atribuir_tecnico(id_glpi, definir_autor_glpi(config))

    if not glpi.solucao_registrada(id_glpi):
        conteudo_solucao = _ultima_resposta_publica_tiflux(tiflux, numero_tiflux, config)
        glpi.registrar_solucao(id_glpi, conteudo_solucao)

    _mudar_status_em_cascata(
        conn, config, glpi, id_glpi, numero_tiflux, STATUS_GLPI_SOLUCIONADO, "encerramento",
        f"Chamado #{id_glpi} encerrado no GLPI (status Solucionado) — fechado/cancelado no Tiflux #{numero_tiflux}",
        totais,
    )


def _ultima_resposta_publica_tiflux(tiflux: TifluxClient, numero_tiflux: str, config: Config) -> str:
    """A resposta pública (/answers) mais recente do ticket no Tiflux, por answer_time, vira o conteúdo da solução no GLPI (prefixada com autor+data, mesmo padrão dos followups)."""
    respostas = tiflux.listar_respostas(numero_tiflux, config.tamanho_pagina_respostas_tiflux, config.max_paginas_respostas_tiflux)
    if not respostas:
        return _SEM_RESPOSTA_TIFLUX
    mais_recente = max(respostas, key=lambda r: r.get("answer_time") or "")
    conteudo = html.unescape(mais_recente.get("name") or "") or _SEM_RESPOSTA_TIFLUX
    return _prefixar_autor_tiflux(mais_recente.get("author"), mais_recente.get("answer_time"), conteudo)


def _mudar_status_em_cascata(conn, config, glpi: GlpiClient, id_glpi, numero_tiflux, novo_status: int, tipo: str, mensagem_sucesso: str, totais) -> None:
    """
    Aplica uma mudança de status no GLPI espelhando o estado do ticket no
    Tiflux (encerramento ou reabertura). Sem bookkeeping de "já processado":
    se falhar, a condição que disparou a chamada (is_closed no Tiflux
    divergindo do status no GLPI) continua valendo e é retentada na próxima
    execução; se der certo, o status do GLPI muda e a condição deixa de valer.
    Uma única linha por chamado na tabela de auditoria (direcao=
    'tiflux_para_glpi', id_origem=-id_glpi) guarda a última ação de cascata,
    seja ela encerramento ou reabertura.
    """
    sucesso, erro = glpi.encerrar_chamado(id_glpi, novo_status)
    mensagem = mensagem_sucesso if sucesso else erro
    db_followups.registrar_resultado_followup(
        conn, config, id_glpi, numero_tiflux, "tiflux_para_glpi", tipo, -id_glpi, None,
        "sucesso" if sucesso else "erro", mensagem,
    )
    totais["status_sucesso" if sucesso else "status_erro"] += 1


def _reabrir_tiflux_apos_recusa_glpi(conn, config, tiflux: TifluxClient, id_glpi, numero_tiflux, totais) -> bool:
    """
    Reabre o ticket no Tiflux (ver TifluxClient.reabrir_ticket) e registra o
    resultado na mesma linha de auditoria de cascata do chamado (tipo
    'reabertura_tiflux'). O followup da recusa em si não é publicado aqui —
    volta a ser elegível pra sincronizar_followups_glpi_para_tiflux() assim
    que o Tiflux deixa de estar fechado (chamado pelo caller logo em
    seguida), mesmo caminho de qualquer followup pendente novo.
    Se essa tentativa falhar, o registro fica com status='erro' e
    obter_ultima_acao_cascata_sucesso() deixa de enxergar o 'encerramento'
    anterior como confirmado — a próxima execução cai no encerramento em
    cascata normal (linha ~93) em vez de retentar a reabertura, já que Tiflux
    ainda aparece fechado. Falha rara (erro de rede na chamada de reabrir);
    aceito como limitação conhecida, não uma máquina de estados completa.
    """
    motivo = f"Solução recusada pelo requerente no GLPI (chamado #{id_glpi})"
    sucesso, erro = tiflux.reabrir_ticket(numero_tiflux, motivo)
    mensagem = (
        f"Chamado #{id_glpi} reaberto no GLPI (provável recusa da solução) — "
        f"ticket Tiflux #{numero_tiflux} reaberto para equalizar"
        if sucesso else erro
    )
    db_followups.registrar_resultado_followup(
        conn, config, id_glpi, numero_tiflux, "tiflux_para_glpi", "reabertura_tiflux", -id_glpi, None,
        "sucesso" if sucesso else "erro", mensagem,
    )
    totais["status_sucesso" if sucesso else "status_erro"] += 1
    return sucesso


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
            conn, config, glpi, tiflux, id_chamado, numero_tiflux, followup, nome_requerente, id_requerente_ticket,
        )
        qtd_sucesso, qtd_erro = _acumular(sucesso, qtd_sucesso, qtd_erro)

    return qtd_sucesso, qtd_erro


def _followups_glpi_pendentes(conn, config: Config, glpi: GlpiClient, id_chamado: int) -> list[dict]:
    """
    Followups do GLPI ainda não publicados no Tiflux, excluindo os que a
    própria integração criou lá (Tiflux -> GLPI): esses sempre têm users_id
    Léo ou Sania (ver definir_autor_glpi), nunca de quem respondeu de fato no
    Tiflux — sem esse filtro, cada um viraria eco: publicado de volta no
    Tiflux como se fosse uma resposta nova, duplicando a mensagem original.
    Um followup escrito de verdade por Léo ou Sania direto no GLPI (não via
    esta integração) também é filtrado — mesma authoria, sem como distinguir.
    Followups privados não são sincronizados: só comunicação pública cruza
    pro Tiflux.
    """
    followups = glpi.obter_followups(id_chamado)
    if not followups:
        return []
    ja_processados = db_followups.obter_followups_glpi_ja_processados(conn, config, id_chamado)
    ids_proprios = {config.id_glpi_leo, config.id_glpi_sania}
    return [
        f for f in followups
        if f.get("id") not in ja_processados
        and f.get("users_id") not in ids_proprios
        and not f.get("is_private")
    ]


def _resolver_requerente(glpi: GlpiClient, id_chamado: int) -> tuple[str, int | None]:
    ticket, _ = glpi.obter_ticket(id_chamado)
    nome_requerente, _, id_requerente_ticket = glpi.obter_requerente(id_chamado, ticket or {})
    return nome_requerente, id_requerente_ticket


# Limite de arquivos por requisição de resposta no Tiflux (POST .../answers e
# .../client-answers) — ver openapi-spec-tiflux.json. Followup do GLPI com
# mais anexos que isso manda o excedente pro nível do chamado (ver
# _enviar_anexos_excedentes) em vez de descartar.
_MAX_ANEXOS_POR_RESPOSTA_TIFLUX = 10


def _publicar_followup_no_tiflux(
    conn, config, glpi: GlpiClient, tiflux: TifluxClient, id_chamado, numero_tiflux, followup,
    nome_requerente, id_requerente_ticket,
) -> bool:
    id_origem = followup.get("id")
    # Alguns followups do GLPI vêm com o conteúdo HTML-entity-encoded
    # (ex.: "&#60;p&#62;texto&#60;/p&#62;" em vez de "<p>texto</p>"),
    # dependendo de como foram criados; html.unescape() normaliza pros
    # dois casos (é um no-op se o conteúdo já vier como HTML literal).
    conteudo = html.unescape(followup.get("content") or "")
    anexos, avisos_anexos = glpi.obter_anexos_do_followup(id_origem, config.tamanho_maximo_anexo_mb)
    anexos_na_resposta = anexos[:_MAX_ANEXOS_POR_RESPOSTA_TIFLUX]
    anexos_excedentes = anexos[_MAX_ANEXOS_POR_RESPOSTA_TIFLUX:]

    tipo, resp = _enviar_followup_para_tiflux(
        tiflux, numero_tiflux, followup, conteudo, nome_requerente, id_requerente_ticket, anexos_na_resposta,
    )
    sucesso = _registrar_publicacao_no_tiflux(conn, config, id_chamado, numero_tiflux, tipo, id_origem, resp, avisos_anexos)
    if sucesso and anexos_excedentes:
        _enviar_anexos_excedentes(tiflux, numero_tiflux, id_origem, anexos_excedentes)
    return sucesso


def _enviar_anexos_excedentes(tiflux: TifluxClient, numero_tiflux, id_origem, anexos_excedentes: list) -> None:
    """Followup com mais de 10 anexos — o excedente vai pro chamado (nível ticket) no Tiflux, perdendo o vínculo visual com o followup, mas sem ser descartado."""
    _, falhados, motivos = tiflux.enviar_anexos(numero_tiflux, anexos_excedentes)
    if falhados:
        log(f"⚠️ Followup GLPI #{id_origem}: {falhados} anexo(s) excedente(s) (>10) falharam ao enviar pro "
            f"Tiflux #{numero_tiflux}: {'; '.join(motivos)}")


def _enviar_followup_para_tiflux(
    tiflux: TifluxClient, numero_tiflux, followup, conteudo, nome_requerente, id_requerente_ticket, anexos: list,
):
    id_autor = followup.get("users_id")

    if autor_e_solicitante(id_autor, id_requerente_ticket):
        return "publica", tiflux.publicar_resposta_cliente(numero_tiflux, conteudo, nome_requerente, anexos)
    return "publica", tiflux.publicar_resposta_agente(numero_tiflux, conteudo, anexos)


def _registrar_publicacao_no_tiflux(conn, config, id_chamado, numero_tiflux, tipo, id_origem, resp, avisos_anexos: list) -> bool:
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

    mensagem = f"Followup GLPI #{id_origem} publicado no Tiflux (id {id_destino})"
    if avisos_anexos:
        mensagem += f" | Avisos anexos: {'; '.join(avisos_anexos)}"
    db_followups.registrar_resultado_followup(
        conn, config, id_chamado, numero_tiflux, "glpi_para_tiflux", tipo, id_origem, id_destino, "sucesso", mensagem,
    )
    return True


# --- Tiflux -> GLPI -----------------------------------------------------

def sincronizar_followups_tiflux_para_glpi(
    conn, config: Config, glpi: GlpiClient, tiflux: TifluxClient, id_chamado: int, numero_tiflux: str,
) -> tuple[int, int]:
    """
    Busca respostas públicas (/answers) do ticket no Tiflux, filtra as que
    ainda não foram sincronizadas ou que a própria integração criou (eco), e
    cria um followup correspondente no GLPI pra cada uma. Comunicações
    internas (/internal_communications) não são sincronizadas: só comunicação
    pública cruza pro GLPI.

    A autoria do followup no GLPI é sempre Léo (ver definir_autor_glpi) — sem
    isso, o GLPI atribui tudo ao usuário autenticado da API, independente de
    quem respondeu de fato no Tiflux.

    Defesa contra eco: a tabela de auditoria (id já processado) mais o campo
    answer_origin/author.
    Retorna (qtd_sucesso, qtd_erro).
    """
    id_autor_glpi = definir_autor_glpi(config)

    ja_processados_ou_proprios = db_followups.obter_respostas_tiflux_ja_processadas_ou_proprias(conn, config, numero_tiflux)
    respostas = tiflux.listar_respostas(numero_tiflux, config.tamanho_pagina_respostas_tiflux, config.max_paginas_respostas_tiflux)

    return _publicar_respostas_publicas(conn, config, glpi, id_chamado, numero_tiflux, respostas, ja_processados_ou_proprios, id_autor_glpi)


def _publicar_respostas_publicas(conn, config, glpi: GlpiClient, id_chamado, numero_tiflux, respostas, ja_processados_ou_proprios, id_autor_glpi: int) -> tuple[int, int]:
    qtd_sucesso = qtd_erro = 0
    for resposta in respostas:
        if _deve_ignorar_resposta_publica(resposta, ja_processados_ou_proprios):
            continue
        id_origem = resposta.get("id")
        conteudo = html.unescape(resposta.get("name") or "")
        conteudo = _prefixar_autor_tiflux(resposta.get("author"), resposta.get("answer_time"), conteudo)
        id_criado, erro = glpi.criar_followup(id_chamado, conteudo, is_private=0, users_id=id_autor_glpi)
        if id_criado is not None:
            _restaurar_status_novo_glpi(glpi, id_chamado)
        sucesso = _registrar_followup_tiflux_para_glpi(
            conn, config, id_chamado, numero_tiflux, "publica", id_origem, id_criado, erro,
            mensagem_sucesso=f"Resposta Tiflux #{id_origem} publicada como followup no GLPI (id {id_criado})",
        )
        qtd_sucesso, qtd_erro = _acumular(sucesso, qtd_sucesso, qtd_erro)
    return qtd_sucesso, qtd_erro


def _deve_ignorar_resposta_publica(resposta: dict, ja_processados_ou_proprios: set[int]) -> bool:
    if resposta.get("id") in ja_processados_ou_proprios:
        return True
    return resposta.get("answer_origin") == "api" or str(resposta.get("author", "")).startswith("[API]")


def _acumular(sucesso: bool, qtd_sucesso: int, qtd_erro: int) -> tuple[int, int]:
    return (qtd_sucesso + 1, qtd_erro) if sucesso else (qtd_sucesso, qtd_erro + 1)


def _restaurar_status_novo_glpi(glpi: GlpiClient, id_glpi: int) -> None:
    """
    Criar o followup (acima) faz o GLPI mudar o status pra "Processando
    (atribuído)" automaticamente. Volta pra Novo; só loga se falhar — não
    afeta o registro do followup em si, que já foi criado com sucesso.
    """
    sucesso, erro = glpi.voltar_status_para_novo(id_glpi)
    if not sucesso:
        log(f"⚠️ Followup criado no chamado #{id_glpi}, mas falhou ao voltar status para Novo no GLPI: {erro}")


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
