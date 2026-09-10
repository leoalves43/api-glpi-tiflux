import contextlib
import io
import json
import unittest
from typing import Callable

from sync import forcar_sincronizacao
from sync.config import Config
from tests.fake_clients import FakeGlpiClient, FakeTifluxClient
from tests.fakes import FakeConnection

_CONFIG = Config(
    url_glpi="", app_token="", user_token="",
    url_tiflux="", token_tiflux="",
    db_host="", db_port="5432", db_name="", db_user="", db_password="",
    tabela_auditoria="siap.api_glpi_tiflux", tabela_followups="siap.api_glpi_tiflux_followups",
)

_TICKET_ARRECADACAO_ABERTO = {
    "name": "Problema X", "content": "<p>desc</p>", "priority": 3, "itilcategories_id": 274, "status": 1,
}


def _capturar_ultima_linha_json(func: Callable[[], None]) -> dict:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        func()
    linhas = [l for l in buffer.getvalue().splitlines() if l.strip()]
    return json.loads(linhas[-1])


class TestDecidirAcao(unittest.TestCase):
    def test_nenhuma_linha_cria(self):
        self.assertEqual(forcar_sincronizacao.decidir_acao(None), forcar_sincronizacao.ACAO_CRIAR)

    def test_erro_sem_numero_tiflux_cria(self):
        self.assertEqual(forcar_sincronizacao.decidir_acao(("erro", None)), forcar_sincronizacao.ACAO_CRIAR)

    def test_erro_com_numero_tiflux_recusa(self):
        self.assertEqual(
            forcar_sincronizacao.decidir_acao(("erro", 361535)),
            forcar_sincronizacao.ACAO_RECUSAR_NUMERO_EXISTENTE,
        )

    def test_sucesso_vai_para_followups(self):
        self.assertEqual(forcar_sincronizacao.decidir_acao(("sucesso", 361535)), forcar_sincronizacao.ACAO_FOLLOWUPS)


class TestObterLock(unittest.TestCase):
    def test_lock_concedido(self):
        conn = FakeConnection(respostas=[[(True,)]])
        self.assertTrue(forcar_sincronizacao._obter_lock(conn, 1))

    def test_lock_negado(self):
        conn = FakeConnection(respostas=[[(False,)]])
        self.assertFalse(forcar_sincronizacao._obter_lock(conn, 1))


class TestNumeroTifluxNoTitulo(unittest.TestCase):
    def test_extrai_numero_quando_titulo_prefixado(self):
        glpi = FakeGlpiClient()
        glpi.tickets[1] = {"name": "#361535 - Problema"}
        self.assertEqual(forcar_sincronizacao._numero_tiflux_no_titulo(glpi, 1), "361535")

    def test_none_quando_sem_prefixo(self):
        glpi = FakeGlpiClient()
        glpi.tickets[1] = {"name": "Problema sem prefixo"}
        self.assertIsNone(forcar_sincronizacao._numero_tiflux_no_titulo(glpi, 1))

    def test_none_quando_chamado_nao_existe_no_glpi(self):
        glpi = FakeGlpiClient()
        self.assertIsNone(forcar_sincronizacao._numero_tiflux_no_titulo(glpi, 1))


class TestForcarCriacao(unittest.TestCase):
    def setUp(self):
        self.glpi = FakeGlpiClient()
        self.tiflux = FakeTifluxClient()

    def test_recusa_quando_titulo_ja_prefixado_sem_tocar_no_banco(self):
        self.glpi.tickets[1] = {"name": "#361535 - Problema"}
        conn = FakeConnection()
        resultado = _capturar_ultima_linha_json(
            lambda: forcar_sincronizacao._forcar_criacao(conn, _CONFIG, self.glpi, self.tiflux, 1)
        )
        self.assertEqual(resultado["status"], "recusado")
        self.assertEqual(resultado["numero_tiflux"], "361535")
        self.assertEqual(len(conn.execucoes), 0)

    def test_ignorado_nao_grava_na_auditoria(self):
        self.glpi.grupo_observador[1] = (False, "sem grupo observador")
        conn = FakeConnection()
        resultado = _capturar_ultima_linha_json(
            lambda: forcar_sincronizacao._forcar_criacao(conn, _CONFIG, self.glpi, self.tiflux, 1)
        )
        self.assertEqual(resultado["status"], "ignorado")
        self.assertEqual(len(conn.execucoes), 0)

    def test_erro_quando_chamado_nao_existe_grava_na_auditoria(self):
        conn = FakeConnection()
        resultado = _capturar_ultima_linha_json(
            lambda: forcar_sincronizacao._forcar_criacao(conn, _CONFIG, self.glpi, self.tiflux, 1)
        )
        self.assertEqual(resultado["status"], "erro")
        self.assertEqual(len(conn.execucoes), 1)

    def test_sucesso_cria_e_encaminha_para_followups(self):
        self.glpi.tickets[1] = _TICKET_ARRECADACAO_ABERTO
        self.glpi.requerentes[1] = ("Fulano", "fulano@x.com", 9)
        conn = FakeConnection(respostas=[[], []])  # insert do chamado, depois select de followups t2g
        resultado = _capturar_ultima_linha_json(
            lambda: forcar_sincronizacao._forcar_criacao(conn, _CONFIG, self.glpi, self.tiflux, 1)
        )
        self.assertEqual(resultado["status"], "sucesso")
        self.assertIn("verificados", resultado["mensagem"])


class TestForcarDispatch(unittest.TestCase):
    def setUp(self):
        self.glpi = FakeGlpiClient()
        self.tiflux = FakeTifluxClient()

    def test_recusa_quando_ja_existe_numero_tiflux_com_erro(self):
        conn = FakeConnection(respostas=[[("erro", 361535)]])
        resultado = _capturar_ultima_linha_json(
            lambda: forcar_sincronizacao._forcar(conn, _CONFIG, self.glpi, self.tiflux, 1)
        )
        self.assertEqual(resultado, {
            "status": "recusado", "numero_tiflux": 361535,
            "mensagem": resultado["mensagem"],
        })
        self.assertIn("duplicaria", resultado["mensagem"])

    def test_encaminha_para_followups_quando_ja_sincronizado(self):
        self.glpi.tickets[1] = {"status": 1}
        conn = FakeConnection(respostas=[[("sucesso", 361535)], []])
        resultado = _capturar_ultima_linha_json(
            lambda: forcar_sincronizacao._forcar(conn, _CONFIG, self.glpi, self.tiflux, 1)
        )
        self.assertEqual(resultado["status"], "sucesso")
        self.assertIn("verificados", resultado["mensagem"])

    def test_fechado_no_glpi_nao_sincroniza_followups(self):
        self.glpi.tickets[1] = {"status": 6}
        conn = FakeConnection(respostas=[[("sucesso", 361535)]])
        resultado = _capturar_ultima_linha_json(
            lambda: forcar_sincronizacao._forcar(conn, _CONFIG, self.glpi, self.tiflux, 1)
        )
        self.assertEqual(resultado["status"], "fechado")


if __name__ == "__main__":
    unittest.main()
