import unittest
from unittest.mock import patch

import requests

from sync.config import Config
from sync.processamento_chamado import processar_chamado
from tests.fake_clients import FakeGlpiClient, FakeTifluxClient

_CONFIG = Config(
    url_glpi="", app_token="", user_token="",
    url_tiflux="", token_tiflux="",
    db_host="", db_port="5432", db_name="", db_user="", db_password="",
    tabela_auditoria="x", tabela_followups="y",
)

_TICKET_ARRECADACAO = {"name": "Problema X", "content": "<p>desc</p>", "priority": 3, "itilcategories_id": 274}


class TestProcessarChamado(unittest.TestCase):
    def setUp(self):
        self.glpi = FakeGlpiClient()
        self.tiflux = FakeTifluxClient()

    def test_ignorado_quando_fora_do_grupo_observador(self):
        self.glpi.grupo_observador[1] = (False, "sem grupo")
        status, numero, msg = processar_chamado(self.glpi, self.tiflux, _CONFIG, 1)
        self.assertEqual((status, numero, msg), ("ignorado", None, "sem grupo"))

    def test_erro_quando_chamado_nao_existe_no_glpi(self):
        status, numero, msg = processar_chamado(self.glpi, self.tiflux, _CONFIG, 1)
        self.assertEqual(status, "erro")
        self.assertIsNone(numero)
        self.assertIn("não encontrado", msg)

    def test_erro_quando_categoria_sem_mesa_correspondente(self):
        self.glpi.tickets[1] = {**_TICKET_ARRECADACAO, "itilcategories_id": 999}
        status, numero, msg = processar_chamado(self.glpi, self.tiflux, _CONFIG, 1)
        self.assertEqual(status, "erro")
        self.assertIn("Categoria GLPI 999", msg)

    def test_erro_quando_mesa_nao_pertence_ao_cliente(self):
        self.glpi.tickets[1] = _TICKET_ARRECADACAO
        self.tiflux.mesas_validas = set()  # nenhuma mesa vinculada
        status, numero, msg = processar_chamado(self.glpi, self.tiflux, _CONFIG, 1)
        self.assertEqual(status, "erro")
        self.assertIn("não está vinculada ao cliente", msg)

    def test_erro_quando_mesa_sem_prioridade_configurada(self):
        self.glpi.tickets[1] = _TICKET_ARRECADACAO
        with patch("sync.processamento_chamado.definir_prioridade", return_value=None):
            status, numero, msg = processar_chamado(self.glpi, self.tiflux, _CONFIG, 1)
        self.assertEqual(status, "erro")
        self.assertIn("não tem prioridade configurada", msg)

    def test_erro_quando_criar_ticket_falha(self):
        self.glpi.tickets[1] = _TICKET_ARRECADACAO
        self.tiflux.resultado_criar_ticket = (None, "Falha ao criar ticket no Tiflux (500): boom")
        status, numero, msg = processar_chamado(self.glpi, self.tiflux, _CONFIG, 1)
        self.assertEqual((status, numero), ("erro", None))
        self.assertIn("500", msg)

    def test_erro_quando_atribuir_tecnico_falha_mantem_numero_tiflux(self):
        self.glpi.tickets[1] = _TICKET_ARRECADACAO
        self.tiflux.resultado_atribuir_tecnico = (False, 400, "sem permissao")
        status, numero, msg = processar_chamado(self.glpi, self.tiflux, _CONFIG, 1)
        self.assertEqual((status, numero), ("erro", "T-1"))
        self.assertIn("falhou ao atribuir técnico", msg)
        self.assertIn("400", msg)

    def test_sucesso_caminho_feliz_sem_anexos(self):
        self.glpi.tickets[1] = _TICKET_ARRECADACAO
        self.glpi.requerentes[1] = ("Fulano", "fulano@x.com", 9)
        status, numero, msg = processar_chamado(self.glpi, self.tiflux, _CONFIG, 1)
        self.assertEqual((status, numero), ("sucesso", "T-1"))
        self.assertIn("Ticket #T-1 criado no Tiflux", msg)
        self.assertIn("Léo Alves", msg)  # mesa ARRECADAÇÃO -> tecnico Leo
        self.assertNotIn("Anexos", msg)

    def test_sucesso_com_resumo_de_anexos(self):
        self.glpi.tickets[1] = _TICKET_ARRECADACAO
        self.glpi.anexos[1] = ([("a.txt", b"x", "text/plain")], [])
        self.tiflux.resultado_anexos = (1, 0, [])
        status, numero, msg = processar_chamado(self.glpi, self.tiflux, _CONFIG, 1)
        self.assertEqual(status, "sucesso")
        self.assertIn("Anexos: 1 enviado(s)", msg)

    def test_erro_de_rede_vira_status_erro(self):
        self.glpi.tickets[1] = _TICKET_ARRECADACAO
        with patch.object(self.glpi, "obter_requerente", side_effect=requests.ConnectionError("fora do ar")):
            status, numero, msg = processar_chamado(self.glpi, self.tiflux, _CONFIG, 1)
        self.assertEqual((status, numero), ("erro", None))
        self.assertIn("Erro de rede/conexão", msg)

    def test_erro_inesperado_vira_status_erro(self):
        self.glpi.tickets[1] = _TICKET_ARRECADACAO
        with patch.object(self.glpi, "obter_requerente", side_effect=ValueError("boom")):
            status, numero, msg = processar_chamado(self.glpi, self.tiflux, _CONFIG, 1)
        self.assertEqual((status, numero), ("erro", None))
        self.assertIn("Erro inesperado", msg)


if __name__ == "__main__":
    unittest.main()
