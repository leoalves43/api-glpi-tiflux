"""Processamento de um único chamado do GLPI: tradução e criação no Tiflux."""

import requests

from sync.config import Config, log
from sync.glpi_client import GlpiClient
from sync.html_texto import html_para_texto_plano
from sync.regras_negocio import (
    definir_autor_glpi,
    definir_prioridade,
    definir_tecnico,
    depara_categoria,
    texto_prioridade_glpi,
)
from sync.tiflux_client import TifluxClient

ResultadoChamado = tuple[str, str | None, str]


class _ChamadoNaoSincronizavel(Exception):
    """Interrompe o processamento de um chamado com o resultado final já decidido."""

    def __init__(self, status: str, mensagem: str, numero_tiflux: str | None = None):
        super().__init__(mensagem)
        self.status = status
        self.mensagem = mensagem
        self.numero_tiflux = numero_tiflux


def processar_chamado(glpi: GlpiClient, tiflux: TifluxClient, config: Config, id_chamado: int) -> ResultadoChamado:
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
        return _processar(glpi, tiflux, config, id_chamado)
    except _ChamadoNaoSincronizavel as e:
        return e.status, e.numero_tiflux, e.mensagem
    except requests.RequestException as e:
        return "erro", None, f"Erro de rede/conexão: {e}"
    except Exception as e:
        return "erro", None, f"Erro inesperado: {e}"


def _processar(glpi: GlpiClient, tiflux: TifluxClient, config: Config, id_chamado: int) -> ResultadoChamado:
    _validar_escopo(glpi, config, id_chamado)
    ticket = _buscar_ticket(glpi, id_chamado)
    mesa_tiflux = _resolver_mesa(ticket, tiflux)
    id_prioridade_tiflux = _resolver_prioridade(mesa_tiflux)

    nome_solicitante, email_solicitante, _ = glpi.obter_requerente(id_chamado, ticket)
    id_tecnico_tiflux, nome_tecnico_tiflux = definir_tecnico(mesa_tiflux, config)
    id_solicitante_tiflux, info_solicitante_tiflux = tiflux.obter_id_solicitante(nome_solicitante, email_solicitante)

    form_data = _montar_form_data(
        ticket, config, mesa_tiflux, id_prioridade_tiflux,
        id_solicitante_tiflux, nome_solicitante, email_solicitante, id_chamado,
    )
    ticket_number_tiflux = _criar_ticket(tiflux, form_data)
    if id_tecnico_tiflux is not None:
        _atribuir_tecnico(tiflux, ticket_number_tiflux, id_tecnico_tiflux, nome_tecnico_tiflux)
    aviso_titulo = _atualizar_titulo_glpi(glpi, id_chamado, ticket.get("name"), ticket_number_tiflux)
    aviso_tecnico_glpi = _atribuir_tecnico_glpi(glpi, id_chamado, mesa_tiflux, config, ticket_number_tiflux)
    resumo_anexos = _sincronizar_anexos(glpi, tiflux, config, id_chamado, ticket_number_tiflux)

    texto_tecnico = f"Técnico {nome_tecnico_tiflux}" if nome_tecnico_tiflux else "Sem técnico atribuído"
    msg = (f"Ticket #{ticket_number_tiflux} criado no Tiflux | Mesa {mesa_tiflux} | "
           f"Prioridade ID {id_prioridade_tiflux} | "
           f"{texto_tecnico} | Solicitante {info_solicitante_tiflux}"
           f"{resumo_anexos}{aviso_titulo}{aviso_tecnico_glpi}")
    return "sucesso", ticket_number_tiflux, msg


def _validar_escopo(glpi: GlpiClient, config: Config, id_chamado: int) -> None:
    esta_no_escopo, motivo = glpi.chamado_tem_grupo_observador(id_chamado, config.ids_grupo_observador)
    if not esta_no_escopo:
        raise _ChamadoNaoSincronizavel("ignorado", motivo)


def _buscar_ticket(glpi: GlpiClient, id_chamado: int) -> dict:
    ticket, status_code = glpi.obter_ticket(id_chamado)
    if ticket is None:
        raise _ChamadoNaoSincronizavel("erro", f"Chamado não encontrado no GLPI (status {status_code})")
    return ticket


def _resolver_mesa(ticket: dict, tiflux: TifluxClient) -> int:
    categoria_glpi = ticket.get("itilcategories_id")
    mesa_tiflux = depara_categoria(categoria_glpi)
    if mesa_tiflux is None:
        raise _ChamadoNaoSincronizavel(
            "erro",
            f"Categoria GLPI {categoria_glpi} não tem mesa correspondente em depara_categoria() "
            f"— chamado não sincronizado, requer revisão manual",
        )

    if not tiflux.validar_mesa_do_cliente(mesa_tiflux):
        raise _ChamadoNaoSincronizavel(
            "erro",
            f"Mesa {mesa_tiflux} não está vinculada ao cliente {tiflux.cliente_id} no Tiflux "
            f"(confira em Clientes > Mesas, ou ajuste depara_categoria() se o ID estiver errado)",
        )

    return mesa_tiflux


def _resolver_prioridade(mesa_tiflux: int) -> int:
    id_prioridade_tiflux = definir_prioridade(mesa_tiflux)
    if id_prioridade_tiflux is None:
        raise _ChamadoNaoSincronizavel(
            "erro",
            f"Mesa {mesa_tiflux} não tem prioridade configurada em PRIORIDADE_POR_MESA "
            f"(descubra o ID certo na API do Tiflux e preencha o dicionário)",
        )
    return id_prioridade_tiflux


def _criar_ticket(tiflux: TifluxClient, form_data: dict[str, str]) -> str:
    ticket_number_tiflux, erro = tiflux.criar_ticket(form_data)
    if erro:
        raise _ChamadoNaoSincronizavel("erro", erro)
    return ticket_number_tiflux


def _atribuir_tecnico(tiflux: TifluxClient, ticket_number_tiflux: str, id_tecnico_tiflux: int, nome_tecnico_tiflux: str) -> None:
    atribuiu, status_code, texto_resposta = tiflux.atribuir_tecnico(ticket_number_tiflux, id_tecnico_tiflux)
    if not atribuiu:
        msg = (f"Ticket #{ticket_number_tiflux} criado, mas falhou ao atribuir técnico "
               f"{nome_tecnico_tiflux} ({status_code}): {texto_resposta}")
        raise _ChamadoNaoSincronizavel("erro", msg, numero_tiflux=ticket_number_tiflux)


def _atualizar_titulo_glpi(glpi: GlpiClient, id_chamado: int, titulo_original: str, ticket_number_tiflux: str) -> str:
    """
    Prefixa o título do chamado no GLPI com o número do ticket no Tiflux, pra
    facilitar achar um a partir do outro. Falha aqui não derruba a
    sincronização (mesmo padrão dos anexos, não o de atribuir_tecnico): o
    ticket já foi criado no Tiflux, e marcar como erro reprocessaria do zero
    na próxima execução — criando um ticket DUPLICADO no Tiflux (bug conhecido,
    ver db_followups.obter_chamados_para_varrer_followups). Só loga e segue.
    Retorna um resumo (string vazia se deu certo) pra anexar na mensagem final.
    """
    novo_titulo = f"#{ticket_number_tiflux} - {titulo_original}"
    sucesso, erro = glpi.atualizar_titulo(id_chamado, novo_titulo)
    if sucesso:
        return ""
    log(f"⚠️ Ticket #{ticket_number_tiflux} criado, mas falhou ao prefixar o título no GLPI: {erro}")
    return " | Aviso: falha ao prefixar título no GLPI"


def _atribuir_tecnico_glpi(glpi: GlpiClient, id_chamado: int, mesa_tiflux: int, config: Config, ticket_number_tiflux: str) -> str:
    """
    Atribui um técnico responsável no GLPI (mesma regra de definir_autor_glpi:
    mesa ARRECADAÇÃO -> Léo, outras mesas -> Sania) — pré-requisito dessa
    instalação do GLPI pra aceitar status Solucionado/Fechado mais tarde (ver
    encerramento em cascata em sincronizacao_followups.py). Falha aqui não
    derruba a sincronização, mesmo motivo do título: reprocessar duplicaria
    o ticket no Tiflux.
    """
    id_tecnico_glpi = definir_autor_glpi(mesa_tiflux, config)
    sucesso, erro = glpi.atribuir_tecnico(id_chamado, id_tecnico_glpi)
    if sucesso:
        return ""
    log(f"⚠️ Ticket #{ticket_number_tiflux} criado, mas falhou ao atribuir técnico no GLPI: {erro}")
    return " | Aviso: falha ao atribuir técnico no GLPI"


def _montar_form_data(
    ticket: dict, config: Config, mesa_tiflux: int, id_prioridade_tiflux: int,
    id_solicitante_tiflux: int, nome_solicitante: str, email_solicitante: str | None, id_chamado: int,
) -> dict[str, str]:
    titulo_glpi = ticket.get("name")
    prioridade_glpi_texto = texto_prioridade_glpi(ticket.get("priority"))

    titulo_tiflux = f"{titulo_glpi} ({id_chamado})"
    cabecalho_personalizado = f"Este chamado tem a prioridade: {prioridade_glpi_texto}"
    info_solicitante_texto = f"Solicitante: {nome_solicitante} <{email_solicitante or 'Sem e-mail'}>"
    descricao_glpi_texto = html_para_texto_plano(ticket.get("content"))
    # Campo de descrição do Tiflux é texto puro (não HTML) — usa \n, não <br>
    descricao_tiflux = (f"{cabecalho_personalizado}\n\n{info_solicitante_texto}\n\n"
                         f"Descrição:\n{descricao_glpi_texto}")

    return {
        "title": titulo_tiflux,
        "description": descricao_tiflux,
        "client_id": str(config.cliente_tiflux_id),
        "desk_id": str(mesa_tiflux),
        "requestor_id": str(id_solicitante_tiflux),
        "priority_id": str(id_prioridade_tiflux),
        # Campo personalizado obrigatório "Módulo utilizado" -> sempre "Padrão".
        # "entities[][...]" (sem índice) é o formato que o Rack/Rails do Tiflux espera
        # pra montar um array de 1 hash; "entities[0][...]" gera um Hash {"0": {...}}
        # e a API rejeita com "did not contain a required property of 'entity_field_id'".
        "entities[][entity_field_id]": str(config.id_campo_modulo_utilizado_tiflux),
        "entities[][value]": str(config.id_opcao_modulo_utilizado_padrao_tiflux),
    }


def _sincronizar_anexos(glpi: GlpiClient, tiflux: TifluxClient, config: Config, id_chamado: int, ticket_number_tiflux: str) -> str:
    """
    Anexos (arquivos e imagens da descrição) — não falha o chamado se algo
    aqui der errado, o ticket já foi criado; só devolve um resumo pro log/auditoria.
    """
    anexos, avisos_anexos = glpi.obter_anexos(id_chamado, config.tamanho_maximo_anexo_mb)
    anexos_enviados, anexos_falhados, motivos_falha = tiflux.enviar_anexos(ticket_number_tiflux, anexos)

    if not anexos and not avisos_anexos:
        return ""

    resumo = f" | Anexos: {anexos_enviados} enviado(s)"
    if anexos_falhados:
        resumo += f", {anexos_falhados} falhou(aram) [{'; '.join(motivos_falha)}]"
    if avisos_anexos:
        resumo += f" | Avisos: {'; '.join(avisos_anexos)}"
    return resumo
