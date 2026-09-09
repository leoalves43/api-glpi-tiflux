import contextlib
import io
import unittest

from sync.config import Config
from sync.sincronizacao_followups import (
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
        sucesso, erro = sincronizar_followups_tiflux_para_glpi(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        self.assertEqual((sucesso, erro), (1, 0))
        self.assertEqual(self.glpi.followups_criados[0]["is_private"], 0)

    def test_comunicacao_interna_vira_followup_privado_no_glpi(self):
        self.tiflux.comunicacoes = [{"id": 2, "text": "com"}]
        sucesso, erro = sincronizar_followups_tiflux_para_glpi(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        self.assertEqual((sucesso, erro), (1, 0))
        self.assertEqual(self.glpi.followups_criados[0]["is_private"], 1)

    def test_mesa_arrecadacao_atribui_followup_ao_leo_no_glpi(self):
        self.tiflux.respostas = [{"id": 1, "name": "resp"}]
        self.tiflux.mesa_do_ticket = 37964  # ARRECADAÇÃO
        sincronizar_followups_tiflux_para_glpi(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        self.assertEqual(self.glpi.followups_criados[0]["users_id"], _CONFIG.id_glpi_leo)

    def test_outra_mesa_atribui_followup_a_sania_no_glpi_mesmo_que_o_tecnico_seja_outro(self):
        self.tiflux.comunicacoes = [{"id": 2, "text": "com"}]
        self.tiflux.mesa_do_ticket = 37965  # FINANÇAS
        sincronizar_followups_tiflux_para_glpi(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        self.assertEqual(self.glpi.followups_criados[0]["users_id"], _CONFIG.id_glpi_sania)

    def test_mesa_atual_prevalece_mesmo_se_ticket_mudou_de_mesa_apos_criado(self):
        # Chamado criado originalmente em ARRECADAÇÃO, mas já foi movido pra
        # outra mesa no Tiflux — a autoria do followup deve seguir a mesa ATUAL.
        self.tiflux.respostas = [{"id": 1, "name": "resp"}]
        self.tiflux.mesa_do_ticket = 37966  # SUPRIMENTOS agora
        sincronizar_followups_tiflux_para_glpi(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        self.assertEqual(self.glpi.followups_criados[0]["users_id"], _CONFIG.id_glpi_sania)

    def test_mesa_desconhecida_cai_no_padrao_sania(self):
        self.tiflux.respostas = [{"id": 1, "name": "resp"}]
        self.tiflux.mesa_do_ticket = None
        sincronizar_followups_tiflux_para_glpi(self.conn, _CONFIG, self.glpi, self.tiflux, 1, "T-1")
        self.assertEqual(self.glpi.followups_criados[0]["users_id"], _CONFIG.id_glpi_sania)

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


class TestSincronizarFollowups(unittest.TestCase):
    def test_chamado_fechado_e_pulado_e_marcado(self):
        glpi = FakeGlpiClient()
        tiflux = FakeTifluxClient()
        glpi.tickets[1] = {"status": 5}  # fechado
        conn = FakeConnection(respostas=[[(1, "T-1")]])
        sincronizar_followups(conn, _CONFIG, glpi, tiflux)
        sql, params = conn.execucoes[-1]
        self.assertIn("verificacao_status", params)

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


if __name__ == "__main__":
    unittest.main()
