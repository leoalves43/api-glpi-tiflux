"""
CLI de backup manual: força a sincronização de UM chamado específico,
independente do rodízio normal do cron — para o caso desse chamado ter sido
pulado na sondagem/varredura automática (ver docs/state/HANDOFF.md RISKS).

Consumida pela interface web (interface-web-api-glpi-tiflux/forcar_sincronizacao.php),
que lê a última linha de stdout como JSON: {"status", "numero_tiflux", "mensagem"}.

Uso: python -m sync.forcar_sincronizacao --id-glpi 33769

Nunca chama processar_chamado() se já existe um numero_tiflux associado ao
chamado (ver decidir_acao) — reprocessar duplicaria o ticket no Tiflux, mesmo
bug conhecido documentado em docs/ARCHITECTURE.md.
"""

import argparse
import json
import re
import sys

from sync import db_chamados
from sync.config import Config, log
from sync.glpi_client import GlpiClient
from sync.processamento_chamado import processar_chamado
from sync.sincronizacao_followups import (
    STATUS_GLPI_ABERTOS,
    sincronizar_followups_glpi_para_tiflux,
    sincronizar_followups_tiflux_para_glpi,
)
from sync.tiflux_client import TifluxClient

ACAO_CRIAR = "criar"
ACAO_RECUSAR_NUMERO_EXISTENTE = "recusar_numero_existente"
ACAO_FOLLOWUPS = "followups"

_CHAVE_LOCK = "forcar_sincronizacao"
_REGEX_TITULO_JA_SINCRONIZADO = re.compile(r"^#(\d+) - ")


def decidir_acao(estado: tuple[str, int | None] | None) -> str:
    """
    O que fazer, a partir do estado atual do chamado na auditoria
    (ver db_chamados.obter_estado_chamado):
      - Nenhuma linha, ou 'erro' sem numero_tiflux: nada foi criado no Tiflux
        ainda -> seguro chamar processar_chamado() (ACAO_CRIAR).
      - 'erro' COM numero_tiflux: falha ao atribuir técnico (bug conhecido)
        já criou um ticket no Tiflux; reprocessar duplicaria -> recusa
        (ACAO_RECUSAR_NUMERO_EXISTENTE), exige reconciliação manual.
      - 'sucesso': chamado já existe nos dois lados -> só followups
        (ACAO_FOLLOWUPS). main.py nunca grava status='ignorado' na auditoria,
        então esse valor não aparece aqui.
    """
    if estado is None:
        return ACAO_CRIAR
    status, numero_tiflux = estado
    if status == "sucesso":
        return ACAO_FOLLOWUPS
    if numero_tiflux is not None:
        return ACAO_RECUSAR_NUMERO_EXISTENTE
    return ACAO_CRIAR


def main() -> None:
    id_glpi = _ler_argumento_id_glpi()
    config = Config.carregar()

    try:
        conn = db_chamados.conectar_db(config)
    except Exception as e:
        _finalizar_com_erro(f"Não foi possível conectar ao Postgres: {e}")
        return

    if not _obter_lock(conn, id_glpi):
        _imprimir_resultado(
            "recusado", None,
            f"Já existe uma sincronização em andamento para o chamado #{id_glpi} "
            f"(outra execução segura o lock) — tente novamente em instantes.",
        )
        conn.close()
        return

    try:
        glpi = GlpiClient.autenticar(config)
    except Exception as e:
        _liberar_lock(conn, id_glpi)
        conn.close()
        _finalizar_com_erro(f"Não foi possível autenticar no GLPI: {e}")
        return

    tiflux = TifluxClient.conectar(config)
    try:
        _forcar(conn, config, glpi, tiflux, id_glpi)
    finally:
        glpi.encerrar_sessao()
        _liberar_lock(conn, id_glpi)
        conn.close()


def _ler_argumento_id_glpi() -> int:
    parser = argparse.ArgumentParser(description="Força a sincronização de um chamado específico.")
    parser.add_argument("--id-glpi", type=int, required=True)
    return parser.parse_args().id_glpi


def _obter_lock(conn, id_glpi: int) -> bool:
    """
    Lock consultivo de sessão do Postgres — evita que duas execuções
    simultâneas (duplo clique, duas abas) forcem o mesmo chamado ao mesmo
    tempo e dupliquem o ticket no Tiflux. NÃO protege contra a tarefa
    agendada `GLPI-Tiflux-Sync` (roda a cada 5 min, lock próprio dela não
    existe) processar o mesmo chamado em paralelo — só reduz a janela.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(hashtext(%s), %s)", (_CHAVE_LOCK, id_glpi))
        return cur.fetchone()[0]


def _liberar_lock(conn, id_glpi: int) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(hashtext(%s), %s)", (_CHAVE_LOCK, id_glpi))


def _forcar(conn, config: Config, glpi: GlpiClient, tiflux: TifluxClient, id_glpi: int) -> None:
    estado = db_chamados.obter_estado_chamado(conn, config, id_glpi)
    acao = decidir_acao(estado)

    if acao == ACAO_RECUSAR_NUMERO_EXISTENTE:
        _, numero_tiflux = estado
        _imprimir_resultado(
            "recusado", numero_tiflux,
            f"Chamado #{id_glpi} já tem o ticket Tiflux #{numero_tiflux} associado "
            f"(status 'erro' na auditoria, provável falha ao atribuir técnico) — "
            f"forçar recriaria e duplicaria o ticket no Tiflux. Reconcilie manualmente.",
        )
        return

    if acao == ACAO_FOLLOWUPS:
        _, numero_tiflux = estado
        _forcar_followups(conn, config, glpi, tiflux, id_glpi, numero_tiflux)
        return

    _forcar_criacao(conn, config, glpi, tiflux, id_glpi)


def _forcar_criacao(conn, config: Config, glpi: GlpiClient, tiflux: TifluxClient, id_glpi: int) -> None:
    numero_ja_existente = _numero_tiflux_no_titulo(glpi, id_glpi)
    if numero_ja_existente:
        _imprimir_resultado(
            "recusado", numero_ja_existente,
            f"Título do chamado #{id_glpi} no GLPI já está prefixado com #{numero_ja_existente} "
            f"— o ticket no Tiflux provavelmente já existe, mas sem linha correspondente na "
            f"auditoria. Reconcilie manualmente antes de forçar.",
        )
        return

    status, numero_tiflux, mensagem = processar_chamado(glpi, tiflux, config, id_glpi)
    log(f"[forçar #{id_glpi}] {status}: {mensagem}")

    if status == "ignorado":
        # Mesmo critério de main.py: 'ignorado' nunca é gravado na auditoria.
        _imprimir_resultado("ignorado", None, mensagem)
        return

    db_chamados.registrar_resultado(conn, config, id_glpi, numero_tiflux, status, mensagem)

    if status == "sucesso":
        _forcar_followups(conn, config, glpi, tiflux, id_glpi, numero_tiflux)
        return

    _imprimir_resultado(status, numero_tiflux, mensagem)


def _numero_tiflux_no_titulo(glpi: GlpiClient, id_glpi: int) -> str | None:
    ticket, _ = glpi.obter_ticket(id_glpi)
    if ticket is None:
        return None
    match = _REGEX_TITULO_JA_SINCRONIZADO.match(ticket.get("name") or "")
    return match.group(1) if match else None


def _forcar_followups(conn, config: Config, glpi: GlpiClient, tiflux: TifluxClient, id_glpi: int, numero_tiflux) -> None:
    ticket_glpi, status_code = glpi.obter_ticket(id_glpi)
    if ticket_glpi is None:
        _imprimir_resultado(
            "erro", numero_tiflux,
            f"Não foi possível conferir o chamado #{id_glpi} no GLPI (status {status_code}).",
        )
        return

    if ticket_glpi.get("status") not in STATUS_GLPI_ABERTOS:
        _imprimir_resultado(
            "fechado", numero_tiflux,
            f"Chamado #{id_glpi} está fechado no GLPI — nada a sincronizar "
            f"(forçar não reabre chamados fechados; use a varredura normal do cron pra isso).",
        )
        return

    sucesso_g2t, erro_g2t = sincronizar_followups_glpi_para_tiflux(conn, config, glpi, tiflux, id_glpi, numero_tiflux)
    sucesso_t2g, erro_t2g = sincronizar_followups_tiflux_para_glpi(
        conn, config, glpi, tiflux, id_glpi, numero_tiflux,
    )

    mensagem = (
        f"Followups do chamado #{id_glpi} (Tiflux #{numero_tiflux}) verificados — "
        f"GLPI->Tiflux: {sucesso_g2t} ok / {erro_g2t} erro | Tiflux->GLPI: {sucesso_t2g} ok / {erro_t2g} erro"
    )
    status = "sucesso" if (erro_g2t == 0 and erro_t2g == 0) else "erro"
    log(f"[forçar #{id_glpi}] {mensagem}")
    _imprimir_resultado(status, numero_tiflux, mensagem)


def _imprimir_resultado(status: str, numero_tiflux, mensagem: str) -> None:
    print(json.dumps({"status": status, "numero_tiflux": numero_tiflux, "mensagem": mensagem}, ensure_ascii=False))


def _finalizar_com_erro(mensagem: str) -> None:
    log(f"❌ {mensagem}")
    print(json.dumps({"status": "erro_fatal", "numero_tiflux": None, "mensagem": mensagem}, ensure_ascii=False))
    sys.exit(1)


if __name__ == "__main__":
    main()
