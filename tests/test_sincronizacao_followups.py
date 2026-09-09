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

    def test_followup_privado_vai_para_comunicacao_interna(self):
        self.glpi.followups[1] = [{"id": 10, "content": "oi", "is_private": 1, "users_id": 5}]
        self.glpi.tickets[1] = {}
        sucesso, erro = sincronizar_followups_glpi_para_tiflux(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        self.assertEqual((sucesso, erro), (1, 0))
        self.assertEqual(self.tiflux.publicacoes[0][0], "interna")

    def test_followup_publico_do_requerente_vai_para_client_answer(self):
        self.glpi.followups[1] = [{"id": 10, "content": "oi", "is_private": 0, "users_id": 5}]
        self.glpi.tickets[1] = {}
        self.glpi.requerentes[1] = ("Fulano", "f@x.com", 5)
        sucesso, erro = sincronizar_followups_glpi_para_tiflux(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        self.assertEqual((sucesso, erro), (1, 0))
        self.assertEqual(self.tiflux.publicacoes[0][0], "cliente")

    def test_followup_publico_de_outro_autor_vai_para_answer_de_agente(self):
        self.glpi.followups[1] = [{"id": 10, "content": "oi", "is_private": 0, "users_id": 99}]
        self.glpi.tickets[1] = {}
        self.glpi.requerentes[1] = ("Fulano", "f@x.com", 5)
        sucesso, erro = sincronizar_followups_glpi_para_tiflux(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        self.assertEqual((sucesso, erro), (1, 0))
        self.assertEqual(self.tiflux.publicacoes[0][0], "agente")

    def test_falha_http_ao_publicar_conta_como_erro(self):
        self.glpi.followups[1] = [{"id": 10, "content": "oi", "is_private": 1, "users_id": 5}]
        self.glpi.tickets[1] = {}
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
        self.glpi.followups[1] = [{"id": 11, "content": "oi", "is_private": 1, "users_id": _CONFIG.id_glpi_sania}]
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
        self.glpi.followups[1] = [{"id": 10, "content": "oi", "is_private": 1, "users_id": 5}]
        sucesso, erro = sincronizar_followups_glpi_para_tiflux(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        self.assertEqual((sucesso, erro), (0, 0))
        self.assertEqual(self.tiflux.publicacoes, [])


class TestSincronizarFollowupsTifluxParaGlpi(unittest.TestCase):
    def setUp(self):
        self.glpi = FakeGlpiClient()
        self.tiflux = FakeTifluxClient()
        self.conn = FakeConnection(respostas=[[]])

    def test_resposta_publica_vira_followup_publico_no_glpi(self):
        self.tiflux.respostas = [{"id": 1, "name": "resp"}]
        sucesso, erro = sincronizar_followups_tiflux_para_glpi(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1", {})
        self.assertEqual((sucesso, erro), (1, 0))
        self.assertEqual(self.glpi.followups_criados[0]["is_private"], 0)

    def test_resposta_publica_e_prefixada_com_autor_e_data_em_negrito(self):
        self.tiflux.respostas = [{"id": 1, "name": "resp", "author": "José Augusto", "answer_time": "2026-09-09T14:10:26Z"}]
        sincronizar_followups_tiflux_para_glpi(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1", {})
        conteudo = self.glpi.followups_criados[0]["conteudo"]
        self.assertEqual(conteudo, "<strong>José Augusto</strong> (09/09/2026 11:10)<br><br>resp")
        # autoria real no GLPI (users_id) continua seguindo a mesa, não o autor exibido no texto
        self.assertEqual(self.glpi.followups_criados[0]["users_id"], _CONFIG.id_glpi_sania)

    def test_comunicacao_interna_vira_followup_privado_no_glpi(self):
        self.tiflux.comunicacoes = [{"id": 2, "text": "com"}]
        sucesso, erro = sincronizar_followups_tiflux_para_glpi(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1", {})
        self.assertEqual((sucesso, erro), (1, 0))
        self.assertEqual(self.glpi.followups_criados[0]["is_private"], 1)

    def test_comunicacao_interna_e_prefixada_com_autor_e_data_em_negrito(self):
        self.tiflux.comunicacoes = [{"id": 2, "text": "com", "user": {"name": "Sania Almeida"}, "created_at": "2026-09-09T14:10:26Z"}]
        sincronizar_followups_tiflux_para_glpi(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1", {})
        conteudo = self.glpi.followups_criados[0]["conteudo"]
        self.assertEqual(conteudo, "<strong>Sania Almeida</strong> (09/09/2026 11:10)<br><br>com")

    def test_sem_autor_ou_data_usa_desconhecido_e_omite_parenteses(self):
        self.tiflux.respostas = [{"id": 1, "name": "resp"}]
        sincronizar_followups_tiflux_para_glpi(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1", {})
        conteudo = self.glpi.followups_criados[0]["conteudo"]
        self.assertEqual(conteudo, "<strong>Desconhecido</strong><br><br>resp")

    def test_mesa_arrecadacao_atribui_followup_ao_leo_no_glpi(self):
        self.tiflux.respostas = [{"id": 1, "name": "resp"}]
        ticket_tiflux = {"desk": {"id": 37964}}  # ARRECADAÇÃO
        sincronizar_followups_tiflux_para_glpi(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1", ticket_tiflux)
        self.assertEqual(self.glpi.followups_criados[0]["users_id"], _CONFIG.id_glpi_leo)

    def test_outra_mesa_atribui_followup_a_sania_no_glpi_mesmo_que_o_tecnico_seja_outro(self):
        self.tiflux.comunicacoes = [{"id": 2, "text": "com"}]
        ticket_tiflux = {"desk": {"id": 37965}}  # FINANÇAS
        sincronizar_followups_tiflux_para_glpi(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1", ticket_tiflux)
        self.assertEqual(self.glpi.followups_criados[0]["users_id"], _CONFIG.id_glpi_sania)

    def test_mesa_atual_prevalece_mesmo_se_ticket_mudou_de_mesa_apos_criado(self):
        # Chamado criado originalmente em ARRECADAÇÃO, mas já foi movido pra
        # outra mesa no Tiflux — a autoria do followup deve seguir a mesa ATUAL.
        self.tiflux.respostas = [{"id": 1, "name": "resp"}]
        ticket_tiflux = {"desk": {"id": 37966}}  # SUPRIMENTOS agora
        sincronizar_followups_tiflux_para_glpi(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1", ticket_tiflux)
        self.assertEqual(self.glpi.followups_criados[0]["users_id"], _CONFIG.id_glpi_sania)

    def test_mesa_desconhecida_cai_no_padrao_sania(self):
        self.tiflux.respostas = [{"id": 1, "name": "resp"}]
        sincronizar_followups_tiflux_para_glpi(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1", None)
        self.assertEqual(self.glpi.followups_criados[0]["users_id"], _CONFIG.id_glpi_sania)

    def test_resposta_de_origem_api_e_ignorada_eco(self):
        self.tiflux.respostas = [{"id": 1, "name": "eco", "answer_origin": "api"}]
        sucesso, erro = sincronizar_followups_tiflux_para_glpi(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1", {})
        self.assertEqual((sucesso, erro), (0, 0))
        self.assertEqual(self.glpi.followups_criados, [])

    def test_falha_ao_criar_followup_no_glpi_conta_como_erro(self):
        self.tiflux.respostas = [{"id": 1, "name": "resp"}]
        self.glpi.erro_ao_criar_followup = "Falha ao criar followup no GLPI (500): boom"
        sucesso, erro = sincronizar_followups_tiflux_para_glpi(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1", {})
        self.assertEqual((sucesso, erro), (0, 1))


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


if __name__ == "__main__":
    unittest.main()
