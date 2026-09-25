import contextlib
import io
import unittest

from sync.config import Config
from sync.sincronizacao_followups import (
    _formatar_data_hora_brasilia,
    _prefixar_autor_tiflux,
    sincronizar_followups,
    sincronizar_followups_glpi_para_tiflux,
    sincronizar_followups_tiflux_para_glpi,
)
from tests.fake_clients import FakeGlpiClient, FakeTifluxClient, _FakeHttpResponse
from tests.fakes import FakeConnection

_sem_console = lambda: contextlib.redirect_stdout(io.StringIO())

_CONFIG = Config(
    url_glpi="", app_token="", user_token="",
    url_tiflux="", token_tiflux="",
    db_host="", db_port="5432", db_name="", db_user="", db_password="",
    tabela_auditoria="x", tabela_followups="y",
)


class TestFormatarDataHoraBrasilia(unittest.TestCase):
    def test_converte_utc_para_brasilia(self):
        self.assertEqual(_formatar_data_hora_brasilia("2026-09-09T14:10:26Z"), "09/09/2026 11:10")

    def test_none_retorna_none(self):
        self.assertIsNone(_formatar_data_hora_brasilia(None))

    def test_formato_invalido_retorna_none(self):
        self.assertIsNone(_formatar_data_hora_brasilia("não é uma data"))


class TestPrefixarAutorTiflux(unittest.TestCase):
    def test_com_nome_e_data(self):
        resultado = _prefixar_autor_tiflux("José Augusto", "2026-09-09T14:10:26Z", "conteúdo")
        self.assertEqual(resultado, "<strong>José Augusto</strong> (09/09/2026 11:10)<br><br>conteúdo")

    def test_sem_nome_usa_desconhecido(self):
        resultado = _prefixar_autor_tiflux(None, "2026-09-09T14:10:26Z", "conteúdo")
        self.assertTrue(resultado.startswith("<strong>Desconhecido</strong>"))

    def test_sem_data_omite_parenteses(self):
        resultado = _prefixar_autor_tiflux("José Augusto", None, "conteúdo")
        self.assertEqual(resultado, "<strong>José Augusto</strong><br><br>conteúdo")


class TestSincronizarFollowupsGlpiParaTiflux(unittest.TestCase):
    def setUp(self):
        self.glpi = FakeGlpiClient()
        self.tiflux = FakeTifluxClient()
        self.conn = FakeConnection(respostas=[[]])  # nenhum followup já processado

    def test_sem_followups_no_glpi_nao_faz_nada(self):
        sucesso, erro = sincronizar_followups_glpi_para_tiflux(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        self.assertEqual((sucesso, erro), (0, 0))

    def test_followup_privado_nao_e_sincronizado(self):
        self.glpi.followups[1] = [{"id": 10, "content": "oi", "is_private": 1, "users_id": 5}]
        self.glpi.tickets[1] = {}
        sucesso, erro = sincronizar_followups_glpi_para_tiflux(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        self.assertEqual((sucesso, erro), (0, 0))
        self.assertEqual(self.tiflux.publicacoes, [])

    def test_followup_publico_do_requerente_vai_com_nome_dele(self):
        self.glpi.followups[1] = [{"id": 10, "content": "oi", "is_private": 0, "users_id": 5}]
        self.glpi.nomes_usuarios[5] = "Fulano"
        sucesso, erro = sincronizar_followups_glpi_para_tiflux(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        self.assertEqual((sucesso, erro), (1, 0))
        self.assertEqual(self.tiflux.publicacoes[0][0], "cliente")
        self.assertEqual(self.tiflux.publicacoes[0][3], "Fulano")

    def test_followup_publico_de_quem_nao_e_requerente_vai_com_nome_do_autor(self):
        # GLPI #34522: followup do Marcio (não requerente) chegava no Tiflux
        # como resposta de agente, assinada "API Embras" (dono do token).
        self.glpi.followups[1] = [{"id": 10, "content": "oi", "is_private": 0, "users_id": 780}]
        self.glpi.nomes_usuarios[780] = "Marcio Silva"
        sucesso, erro = sincronizar_followups_glpi_para_tiflux(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        self.assertEqual((sucesso, erro), (1, 0))
        self.assertEqual(self.tiflux.publicacoes[0][0], "cliente")
        self.assertEqual(self.tiflux.publicacoes[0][3], "Marcio Silva")

    def test_falha_http_ao_publicar_conta_como_erro(self):
        self.glpi.followups[1] = [{"id": 10, "content": "oi", "is_private": 0, "users_id": 5}]
        self.glpi.tickets[1] = {}
        self.glpi.requerentes[1] = ("Fulano", "f@x.com", 5)
        self.tiflux.resposta_publicacao = _FakeHttpResponse(500, None)
        sucesso, erro = sincronizar_followups_glpi_para_tiflux(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        self.assertEqual((sucesso, erro), (0, 1))

    def test_followup_criado_pela_integracao_em_nome_de_leo_nao_e_reenviado_ao_tiflux(self):
        # Eco: um followup Tiflux->GLPI criado com users_id=Léo (mesa
        # ARRECADAÇÃO) não pode voltar pro Tiflux como se fosse resposta nova.
        self.glpi.followups[1] = [{"id": 10, "content": "oi", "is_private": 0, "users_id": _CONFIG.id_glpi_leo}]
        self.glpi.tickets[1] = {}
        sucesso, erro = sincronizar_followups_glpi_para_tiflux(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        self.assertEqual((sucesso, erro), (0, 0))
        self.assertEqual(self.tiflux.publicacoes, [])

    def test_followup_criado_pela_integracao_em_nome_de_sania_nao_e_reenviado_ao_tiflux(self):
        self.glpi.followups[1] = [{"id": 11, "content": "oi", "is_private": 0, "users_id": _CONFIG.id_glpi_sania}]
        self.glpi.tickets[1] = {}
        sucesso, erro = sincronizar_followups_glpi_para_tiflux(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        self.assertEqual((sucesso, erro), (0, 0))
        self.assertEqual(self.tiflux.publicacoes, [])

    def test_followup_de_outro_autor_e_enviado_normalmente_mesmo_com_outros_ja_filtrados(self):
        self.glpi.followups[1] = [
            {"id": 10, "content": "eco", "is_private": 0, "users_id": _CONFIG.id_glpi_leo},
            {"id": 12, "content": "resposta real", "is_private": 0, "users_id": 42},
        ]
        self.glpi.tickets[1] = {}
        self.glpi.requerentes[1] = ("Fulano", "f@x.com", 5)
        sucesso, erro = sincronizar_followups_glpi_para_tiflux(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        self.assertEqual((sucesso, erro), (1, 0))
        self.assertEqual(len(self.tiflux.publicacoes), 1)

    def test_ja_processado_e_ignorado(self):
        self.conn = FakeConnection(respostas=[[(10,)]])
        self.glpi.followups[1] = [{"id": 10, "content": "oi", "is_private": 0, "users_id": 5}]
        sucesso, erro = sincronizar_followups_glpi_para_tiflux(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        self.assertEqual((sucesso, erro), (0, 0))
        self.assertEqual(self.tiflux.publicacoes, [])

    def test_anexo_do_followup_vai_junto_na_resposta_nao_como_anexo_do_chamado(self):
        # Regressão: chamado GLPI #34234 / Tiflux #362601 — anexo mandado num
        # followup do requerente nunca aparecia em /Ticket/{id}/Document_Item
        # (só em /ITILFollowup/{id}/Document_Item), então era descartado.
        self.glpi.followups[1] = [{"id": 10, "content": "oi", "is_private": 0, "users_id": 5}]
        self.glpi.tickets[1] = {}
        self.glpi.requerentes[1] = ("Fulano", "f@x.com", 5)
        anexo = ("planilha.xlsx", b"conteudo", "application/vnd.ms-excel")
        self.glpi.anexos_followup[10] = ([anexo], [])
        sucesso, erro = sincronizar_followups_glpi_para_tiflux(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        self.assertEqual((sucesso, erro), (1, 0))
        self.assertEqual(self.tiflux.publicacoes[0][4], [anexo])
        self.assertEqual(self.tiflux.resultado_anexos, (0, 0, []))  # nada foi pro nível do chamado

    def test_mais_de_dez_anexos_manda_excedente_pro_nivel_do_chamado(self):
        self.glpi.followups[1] = [{"id": 10, "content": "oi", "is_private": 0, "users_id": 5}]
        self.glpi.tickets[1] = {}
        self.glpi.requerentes[1] = ("Fulano", "f@x.com", 5)
        anexos = [(f"a{i}.txt", b"x", "text/plain") for i in range(12)]
        self.glpi.anexos_followup[10] = (anexos, [])
        self.tiflux.resultado_anexos = (2, 0, [])
        sucesso, erro = sincronizar_followups_glpi_para_tiflux(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        self.assertEqual((sucesso, erro), (1, 0))
        self.assertEqual(len(self.tiflux.publicacoes[0][4]), 10)


class TestSincronizarFollowupsTifluxParaGlpi(unittest.TestCase):
    def setUp(self):
        self.glpi = FakeGlpiClient()
        self.tiflux = FakeTifluxClient()
        self.conn = FakeConnection(respostas=[[]])

    def test_resposta_publica_vira_followup_publico_no_glpi(self):
        self.tiflux.respostas = [{"id": 1, "name": "resp"}]
        sucesso, erro = sincronizar_followups_tiflux_para_glpi(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        self.assertEqual((sucesso, erro), (1, 0))
        self.assertEqual(self.glpi.followups_criados[0]["is_private"], 0)

    def test_resposta_publica_e_prefixada_com_autor_e_data_em_negrito(self):
        self.tiflux.respostas = [{"id": 1, "name": "resp", "author": "José Augusto", "answer_time": "2026-09-09T14:10:26Z"}]
        sincronizar_followups_tiflux_para_glpi(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        conteudo = self.glpi.followups_criados[0]["conteudo"]
        self.assertEqual(conteudo, "<strong>José Augusto</strong> (09/09/2026 11:10)<br><br>resp")
        # autoria real no GLPI (users_id) é sempre Léo, não o autor exibido no texto
        self.assertEqual(self.glpi.followups_criados[0]["users_id"], _CONFIG.id_glpi_leo)

    def test_comunicacao_interna_no_tiflux_nao_e_sincronizada(self):
        self.tiflux.comunicacoes = [{"id": 2, "text": "com"}]
        sucesso, erro = sincronizar_followups_tiflux_para_glpi(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        self.assertEqual((sucesso, erro), (0, 0))
        self.assertEqual(self.glpi.followups_criados, [])

    def test_sem_autor_ou_data_usa_desconhecido_e_omite_parenteses(self):
        self.tiflux.respostas = [{"id": 1, "name": "resp"}]
        sincronizar_followups_tiflux_para_glpi(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        conteudo = self.glpi.followups_criados[0]["conteudo"]
        self.assertEqual(conteudo, "<strong>Desconhecido</strong><br><br>resp")

    def test_followup_e_sempre_atribuido_ao_leo_no_glpi_independente_da_mesa(self):
        self.tiflux.respostas = [{"id": 1, "name": "resp"}]
        sincronizar_followups_tiflux_para_glpi(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        self.assertEqual(self.glpi.followups_criados[0]["users_id"], _CONFIG.id_glpi_leo)

    def test_resposta_de_origem_api_e_ignorada_eco(self):
        self.tiflux.respostas = [{"id": 1, "name": "eco", "answer_origin": "api"}]
        sucesso, erro = sincronizar_followups_tiflux_para_glpi(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        self.assertEqual((sucesso, erro), (0, 0))
        self.assertEqual(self.glpi.followups_criados, [])

    def test_falha_ao_criar_followup_no_glpi_conta_como_erro(self):
        self.tiflux.respostas = [{"id": 1, "name": "resp"}]
        self.glpi.erro_ao_criar_followup = "Falha ao criar followup no GLPI (500): boom"
        sucesso, erro = sincronizar_followups_tiflux_para_glpi(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        self.assertEqual((sucesso, erro), (0, 1))

    def test_followup_criado_com_sucesso_volta_status_para_novo_no_glpi(self):
        """Criar o followup faz o GLPI mudar o status pra "Processando (atribuído)" automaticamente; deve voltar pra Novo."""
        self.tiflux.respostas = [{"id": 1, "name": "resp"}]
        sincronizar_followups_tiflux_para_glpi(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        self.assertEqual(self.glpi.status_restaurados_para_novo, [1])

    def test_falha_ao_criar_followup_nao_tenta_voltar_status(self):
        self.tiflux.respostas = [{"id": 1, "name": "resp"}]
        self.glpi.erro_ao_criar_followup = "Falha ao criar followup no GLPI (500): boom"
        sincronizar_followups_tiflux_para_glpi(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        self.assertEqual(self.glpi.status_restaurados_para_novo, [])


class TestSincronizarFollowups(unittest.TestCase):
    def test_chamado_fechado_manualmente_no_glpi_e_pulado_e_marcado(self):
        # status 6 (Fechado) não é o status que a cascata usa (5, Solucionado)
        # — não é reaberto automaticamente, só marcado como fora do escopo.
        glpi = FakeGlpiClient()
        tiflux = FakeTifluxClient()
        glpi.tickets[1] = {"status": 6}
        conn = FakeConnection(respostas=[[(1, "T-1")]])
        sincronizar_followups(conn, _CONFIG, glpi, tiflux)
        sql, params = conn.execucoes[-1]
        self.assertIn("verificacao_status", params)
        self.assertEqual(glpi.chamados_encerrados, [])

    def test_chamado_aberto_sem_followup_novo_e_marcado_como_varrido(self):
        # Regressão GLPI #34522: sem essa marca o rodízio (ORDER BY última
        # varredura) nunca mais voltava a um chamado recém-sincronizado.
        glpi = FakeGlpiClient()
        tiflux = FakeTifluxClient()
        glpi.tickets[1] = {"status": 1}
        tiflux.ticket_tiflux = {"is_closed": False, "desk": {"id": 37964}}
        conn = FakeConnection(respostas=[[(1, "T-1")], [], []])
        sincronizar_followups(conn, _CONFIG, glpi, tiflux)
        _, params = conn.execucoes[-1]
        self.assertEqual(params[2:7], ("verificacao_status", "status", -1, None, "aberto"))

    def test_chamado_sem_numero_tiflux_e_ignorado(self):
        glpi = FakeGlpiClient()
        tiflux = FakeTifluxClient()
        conn = FakeConnection(respostas=[[(1, None)]])
        sincronizar_followups(conn, _CONFIG, glpi, tiflux)
        # única execução é a própria SELECT de candidatos — nada mais rodou
        self.assertEqual(len(conn.execucoes), 1)

    def test_falha_ao_conferir_status_pula_sem_quebrar(self):
        glpi = FakeGlpiClient()
        tiflux = FakeTifluxClient()
        conn = FakeConnection(respostas=[[(1, "T-1")]])
        with _sem_console():
            sincronizar_followups(conn, _CONFIG, glpi, tiflux)
        # única execução é a própria SELECT de candidatos — nada mais rodou
        self.assertEqual(len(conn.execucoes), 1)


class TestEncerramentoEmCascata(unittest.TestCase):
    def setUp(self):
        self.glpi = FakeGlpiClient()
        self.tiflux = FakeTifluxClient()
        self.glpi.tickets[1] = {"status": 1}  # aberto no GLPI

    def test_ticket_fechado_no_tiflux_encerra_no_glpi_como_solucionado(self):
        self.tiflux.ticket_tiflux = {"is_closed": True, "desk": {"id": 37964}}
        conn = FakeConnection(respostas=[[(1, "T-1")], [], []])
        sincronizar_followups(conn, _CONFIG, self.glpi, self.tiflux)
        self.assertEqual(self.glpi.chamados_encerrados, [(1, 5)])

    def test_ticket_aberto_no_tiflux_nao_encerra_no_glpi(self):
        self.tiflux.ticket_tiflux = {"is_closed": False, "desk": {"id": 37964}}
        conn = FakeConnection(respostas=[[(1, "T-1")], [], []])
        sincronizar_followups(conn, _CONFIG, self.glpi, self.tiflux)
        self.assertEqual(self.glpi.chamados_encerrados, [])

    def test_ticket_cancelado_no_tiflux_tambem_encerra_no_glpi(self):
        # Tiflux não distingue close/cancel em is_closed — ambos disparam o encerramento
        self.tiflux.ticket_tiflux = {"is_closed": True, "desk": {"id": 37965}}
        conn = FakeConnection(respostas=[[(1, "T-1")], [], []])
        sincronizar_followups(conn, _CONFIG, self.glpi, self.tiflux)
        self.assertEqual(self.glpi.chamados_encerrados, [(1, 5)])

    def test_falha_ao_encerrar_e_registrada_como_erro_mas_nao_quebra_a_execucao(self):
        self.glpi.resultado_encerrar_chamado = (False, "Falha ao encerrar chamado #1 no GLPI (500): boom")
        self.tiflux.ticket_tiflux = {"is_closed": True, "desk": {"id": 37964}}
        conn = FakeConnection(respostas=[[(1, "T-1")], [], []])
        sincronizar_followups(conn, _CONFIG, self.glpi, self.tiflux)
        sql, params = conn.execucoes[-1]
        self.assertIn("erro", params)
        self.assertIn("encerramento", params)

    def test_followups_pendentes_sao_sincronizados_antes_do_encerramento(self):
        # Ordem exigida pelo usuário: sincroniza o que falta e só depois encerra.
        self.tiflux.respostas = [{"id": 1, "name": "resp"}]
        self.tiflux.ticket_tiflux = {"is_closed": True, "desk": {"id": 37964}}
        conn = FakeConnection(respostas=[[(1, "T-1")], [], []])
        sincronizar_followups(conn, _CONFIG, self.glpi, self.tiflux)
        self.assertEqual(len(self.glpi.followups_criados), 1)
        self.assertEqual(self.glpi.chamados_encerrados, [(1, 5)])


class TestPreparacaoEncerramentoCascata(unittest.TestCase):
    def setUp(self):
        self.glpi = FakeGlpiClient()
        self.tiflux = FakeTifluxClient()
        self.glpi.tickets[1] = {"status": 1}
        self.tiflux.ticket_tiflux = {"is_closed": True, "desk": {"id": 37964}}  # ARRECADAÇÃO

    def test_atribui_tecnico_no_glpi_quando_ainda_nao_tem(self):
        conn = FakeConnection(respostas=[[(1, "T-1")], [], []])
        sincronizar_followups(conn, _CONFIG, self.glpi, self.tiflux)
        self.assertEqual(self.glpi.tecnicos_atribuidos_glpi, [(1, _CONFIG.id_glpi_leo)])

    def test_nao_atribui_tecnico_de_novo_se_ja_tem(self):
        self.glpi.tecnico_ja_atribuido[1] = _CONFIG.id_glpi_leo
        conn = FakeConnection(respostas=[[(1, "T-1")], [], []])
        sincronizar_followups(conn, _CONFIG, self.glpi, self.tiflux)
        self.assertEqual(self.glpi.tecnicos_atribuidos_glpi, [])

    def test_registra_ultima_resposta_publica_como_solucao_prefixada_com_autor_e_data(self):
        self.tiflux.respostas = [
            {"id": 1, "name": "primeira resposta", "answer_time": "2026-09-01T10:00:00Z", "author": "Fulano"},
            {"id": 2, "name": "resposta mais recente", "answer_time": "2026-09-05T10:00:00Z", "author": "José Augusto"},
        ]
        conn = FakeConnection(respostas=[[(1, "T-1")], [], []])
        sincronizar_followups(conn, _CONFIG, self.glpi, self.tiflux)
        conteudo = self.glpi.solucoes_registradas[0][1]
        self.assertIn("<strong>José Augusto</strong> (05/09/2026 07:00)", conteudo)
        self.assertIn("resposta mais recente", conteudo)

    def test_sem_resposta_publica_usa_texto_padrao(self):
        conn = FakeConnection(respostas=[[(1, "T-1")], [], []])
        sincronizar_followups(conn, _CONFIG, self.glpi, self.tiflux)
        self.assertEqual(self.glpi.solucoes_registradas, [(1, "Chamado encerrado no Tiflux, sem resposta pública registrada.")])

    def test_sem_resposta_publica_mas_agrupado_usa_texto_de_agrupamento_com_ticket_pai(self):
        # Ticket agrupado (is_grouped) não tem resposta própria — a resposta de
        # verdade está no ticket pai (ticket_reference). Ver chamado GLPI #34294
        # / ticket Tiflux #362749, agrupado ao #362748.
        self.tiflux.ticket_tiflux = {
            "is_closed": True, "desk": {"id": 37964},
            "is_grouped": True, "ticket_reference": {"ticket_number": 362748},
        }
        conn = FakeConnection(respostas=[[(1, "T-1")], [], []])
        sincronizar_followups(conn, _CONFIG, self.glpi, self.tiflux)
        self.assertEqual(self.glpi.solucoes_registradas, [(1, "Chamado encerrado no Tiflux por agrupamento ao ticket #362748.")])

    def test_agrupado_sem_referencia_de_ticket_pai_usa_texto_generico_de_agrupamento(self):
        self.tiflux.ticket_tiflux = {
            "is_closed": True, "desk": {"id": 37964}, "is_grouped": True, "ticket_reference": {},
        }
        conn = FakeConnection(respostas=[[(1, "T-1")], [], []])
        sincronizar_followups(conn, _CONFIG, self.glpi, self.tiflux)
        self.assertEqual(self.glpi.solucoes_registradas, [(1, "Chamado encerrado no Tiflux por agrupamento a outro ticket.")])

    def test_nao_registra_solucao_de_novo_se_ja_tem(self):
        self.glpi.ja_tem_solucao[1] = True
        conn = FakeConnection(respostas=[[(1, "T-1")], [], []])
        sincronizar_followups(conn, _CONFIG, self.glpi, self.tiflux)
        self.assertEqual(self.glpi.solucoes_registradas, [])


class TestReaberturaEmCascata(unittest.TestCase):
    def setUp(self):
        self.glpi = FakeGlpiClient()
        self.tiflux = FakeTifluxClient()
        self.glpi.tickets[1] = {"status": 5}  # Solucionado — presumivelmente por cascata anterior

    def test_reaberto_no_tiflux_reabre_no_glpi_como_processando(self):
        self.tiflux.ticket_tiflux = {"is_closed": False, "desk": {"id": 37964}}
        conn = FakeConnection(respostas=[[(1, "T-1")]])
        sincronizar_followups(conn, _CONFIG, self.glpi, self.tiflux)
        self.assertEqual(self.glpi.chamados_encerrados, [(1, 2)])
        sql, params = conn.execucoes[-1]
        self.assertIn("reabertura", params)
        self.assertIn("sucesso", params)

    def test_continua_fechado_no_tiflux_nao_reabre_no_glpi(self):
        self.tiflux.ticket_tiflux = {"is_closed": True, "desk": {"id": 37964}}
        conn = FakeConnection(respostas=[[(1, "T-1")]])
        sincronizar_followups(conn, _CONFIG, self.glpi, self.tiflux)
        self.assertEqual(self.glpi.chamados_encerrados, [])
        sql, params = conn.execucoes[-1]
        self.assertIn("verificacao_status", params)

    def test_falha_ao_consultar_tiflux_nao_reabre_no_glpi(self):
        self.tiflux.ticket_tiflux = None  # falha ao consultar o ticket no Tiflux
        conn = FakeConnection(respostas=[[(1, "T-1")]])
        sincronizar_followups(conn, _CONFIG, self.glpi, self.tiflux)
        self.assertEqual(self.glpi.chamados_encerrados, [])

    def test_status_glpi_fechado_manualmente_nunca_e_reaberto(self):
        # status 6 (Fechado) não é o status que a cascata usa — mesmo com o
        # ticket aberto de novo no Tiflux, não mexe (foi encerrado por um
        # técnico direto no GLPI, fora do escopo desta integração).
        self.glpi.tickets[1] = {"status": 6}
        self.tiflux.ticket_tiflux = {"is_closed": False, "desk": {"id": 37964}}
        conn = FakeConnection(respostas=[[(1, "T-1")]])
        sincronizar_followups(conn, _CONFIG, self.glpi, self.tiflux)
        self.assertEqual(self.glpi.chamados_encerrados, [])


class TestReaberturaTifluxAposRecusaGlpi(unittest.TestCase):
    def setUp(self):
        self.glpi = FakeGlpiClient()
        self.tiflux = FakeTifluxClient()
        self.glpi.tickets[1] = {"status": 1}  # aberto no GLPI de novo
        self.tiflux.ticket_tiflux = {"is_closed": True, "desk": {"id": 37964}}

    def test_recusa_apos_encerramento_em_cascata_reabre_o_tiflux(self):
        conn = FakeConnection(respostas=[[(1, "T-1")], [("encerramento",)], [], []])
        sincronizar_followups(conn, _CONFIG, self.glpi, self.tiflux)
        self.assertEqual(len(self.tiflux.tickets_reabertos), 1)
        ticket_reaberto, motivo = self.tiflux.tickets_reabertos[0]
        self.assertEqual(ticket_reaberto, "T-1")
        self.assertIn("1", motivo)  # id_glpi referenciado no motivo

    def test_apos_reabrir_o_tiflux_nao_encerra_o_glpi_de_novo(self):
        conn = FakeConnection(respostas=[[(1, "T-1")], [("encerramento",)], [], []])
        sincronizar_followups(conn, _CONFIG, self.glpi, self.tiflux)
        self.assertEqual(self.glpi.chamados_encerrados, [])

    def test_reabertura_e_registrada_na_auditoria(self):
        conn = FakeConnection(respostas=[[(1, "T-1")], [("encerramento",)], [], []])
        sincronizar_followups(conn, _CONFIG, self.glpi, self.tiflux)
        self.assertTrue(any(
            "reabertura_tiflux" in params and "sucesso" in params for _, params in conn.execucoes
        ))

    def test_followup_da_recusa_e_publicado_no_tiflux_apos_reabrir(self):
        # A recusa em si chega como um followup novo no GLPI; só é publicável
        # no Tiflux depois que o ticket reabre — sem tratamento especial, é o
        # mesmo caminho de qualquer followup pendente.
        self.glpi.followups[1] = [{"id": 10, "content": "recusado, favor verificar", "is_private": 0, "users_id": 42}]
        self.glpi.requerentes[1] = ("Fulano", "f@x.com", 42)
        conn = FakeConnection(respostas=[[(1, "T-1")], [("encerramento",)], [], []])
        sincronizar_followups(conn, _CONFIG, self.glpi, self.tiflux)
        self.assertEqual(len(self.tiflux.publicacoes), 1)

    def test_sem_historico_de_encerramento_em_cascata_nao_reabre_o_tiflux(self):
        # Tiflux fechado por um humano direto, chamado nunca foi encerrado em
        # cascata por esta integração — deve seguir o caminho normal
        # (encerrar o GLPI em cascata), não reabrir o Tiflux.
        conn = FakeConnection(respostas=[[(1, "T-1")], [], [], []])
        sincronizar_followups(conn, _CONFIG, self.glpi, self.tiflux)
        self.assertEqual(self.tiflux.tickets_reabertos, [])
        self.assertEqual(self.glpi.chamados_encerrados, [(1, 5)])

    def test_ultima_acao_de_cascata_foi_reabertura_nao_reabre_de_novo(self):
        # Já reaberto numa execução anterior (tipo='reabertura_tiflux') —
        # não deve tentar de novo.
        conn = FakeConnection(respostas=[[(1, "T-1")], [("reabertura_tiflux",)], [], []])
        sincronizar_followups(conn, _CONFIG, self.glpi, self.tiflux)
        self.assertEqual(self.tiflux.tickets_reabertos, [])

    def test_falha_ao_reabrir_e_registrada_como_erro(self):
        self.tiflux.resultado_reabrir_ticket = (False, "Falha ao reabrir ticket no Tiflux (500): boom")
        conn = FakeConnection(respostas=[[(1, "T-1")], [("encerramento",)], [], []])
        sincronizar_followups(conn, _CONFIG, self.glpi, self.tiflux)
        self.assertTrue(any(
            "reabertura_tiflux" in params and "erro" in params for _, params in conn.execucoes
        ))


if __name__ == "__main__":
    unittest.main()
