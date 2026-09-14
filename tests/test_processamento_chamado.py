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
_TICKET_FINANCAS = {"name": "Problema Y", "content": "<p>desc</p>", "priority": 3, "itilcategories_id": 279}


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

    def test_sucesso_prefixa_titulo_no_glpi_com_numero_do_tiflux(self):
        self.glpi.tickets[1] = _TICKET_ARRECADACAO
        processar_chamado(self.glpi, self.tiflux, _CONFIG, 1)
        self.assertEqual(self.glpi.titulos_atualizados, [(1, "#T-1 - Problema X")])

    def test_sucesso_preenche_modulo_utilizado_com_padrao(self):
        self.glpi.tickets[1] = _TICKET_ARRECADACAO
        processar_chamado(self.glpi, self.tiflux, _CONFIG, 1)
        form_data = self.tiflux.tickets_criados[0]
        self.assertEqual(form_data["entities[][entity_field_id]"], str(_CONFIG.id_campo_modulo_utilizado_tiflux))
        self.assertEqual(form_data["entities[][value]"], str(_CONFIG.id_opcao_modulo_utilizado_padrao_tiflux))

    def test_sucesso_volta_status_para_novo_no_glpi_apos_atribuir_tecnico(self):
        """
        Atribuir técnico (Ticket_User) faz o GLPI mudar o status pra
        "Processando (atribuído)" automaticamente; a integração deve
        restaurar pra Novo depois.
        """
        self.glpi.tickets[1] = _TICKET_ARRECADACAO
        processar_chamado(self.glpi, self.tiflux, _CONFIG, 1)
        self.assertEqual(self.glpi.status_restaurados_para_novo, [1])

    def test_falha_ao_voltar_status_para_novo_nao_derruba_sincronizacao(self):
        self.glpi.tickets[1] = _TICKET_ARRECADACAO
        self.glpi.resultado_voltar_status_para_novo = (False, "boom")
        status, numero, msg = processar_chamado(self.glpi, self.tiflux, _CONFIG, 1)
        self.assertEqual((status, numero), ("sucesso", "T-1"))
        self.assertIn("falha ao voltar status para Novo", msg)

    def test_sucesso_atribui_tecnico_leo_no_glpi_para_mesa_arrecadacao(self):
        self.glpi.tickets[1] = _TICKET_ARRECADACAO
        processar_chamado(self.glpi, self.tiflux, _CONFIG, 1)
        self.assertEqual(self.glpi.tecnicos_atribuidos_glpi, [(1, _CONFIG.id_glpi_leo)])

    def test_sucesso_atribui_tecnico_sania_no_glpi_para_outras_mesas(self):
        self.glpi.tickets[1] = _TICKET_FINANCAS
        processar_chamado(self.glpi, self.tiflux, _CONFIG, 1)
        self.assertEqual(self.glpi.tecnicos_atribuidos_glpi, [(1, _CONFIG.id_glpi_sania)])

    def test_falha_ao_atribuir_tecnico_no_glpi_nao_derruba_sincronizacao(self):
        self.glpi.tickets[1] = _TICKET_ARRECADACAO
        self.glpi.resultado_atribuir_tecnico_glpi = (False, "Falha ao atribuir técnico no GLPI ao chamado #1 (500): boom")
        status, numero, msg = processar_chamado(self.glpi, self.tiflux, _CONFIG, 1)
        self.assertEqual((status, numero), ("sucesso", "T-1"))
        self.assertIn("Aviso: falha ao atribuir técnico no GLPI", msg)

    def test_falha_ao_atualizar_titulo_nao_derruba_sincronizacao(self):
        # Ao contrário de atribuir_tecnico: já criou o ticket no Tiflux, marcar
        # como erro duplicaria no reprocessamento — só avisa e segue (like anexos).
        self.glpi.tickets[1] = _TICKET_ARRECADACAO
        self.glpi.resultado_atualizar_titulo = (False, "Falha ao atualizar título do chamado #1 no GLPI (500): boom")
        status, numero, msg = processar_chamado(self.glpi, self.tiflux, _CONFIG, 1)
        self.assertEqual((status, numero), ("sucesso", "T-1"))
        self.assertIn("Aviso: falha ao prefixar título", msg)

    def test_sucesso_mesa_diferente_de_arrecadacao_fica_sem_tecnico(self):
        self.glpi.tickets[1] = _TICKET_FINANCAS
        status, numero, msg = processar_chamado(self.glpi, self.tiflux, _CONFIG, 1)
        self.assertEqual((status, numero), ("sucesso", "T-1"))
        self.assertIn("Sem técnico atribuído", msg)
        self.assertNotIn("Léo Alves", msg)
        self.assertNotIn("Sânia", msg)

    def test_nao_chama_atribuir_tecnico_quando_mesa_nao_e_arrecadacao(self):
        self.glpi.tickets[1] = _TICKET_FINANCAS
        with patch.object(self.tiflux, "atribuir_tecnico") as atribuir_tecnico:
            processar_chamado(self.glpi, self.tiflux, _CONFIG, 1)
        atribuir_tecnico.assert_not_called()

    def test_sucesso_com_resumo_de_anexos(self):
        self.glpi.tickets[1] = _TICKET_ARRECADACAO
        self.glpi.anexos[1] = ([("a.txt", b"x", "text/plain")], [])
        self.tiflux.resultado_anexos = (1, 0, [])
        status, numero, msg = processar_chamado(self.glpi, self.tiflux, _CONFIG, 1)
        self.assertEqual(status, "sucesso")
        self.assertIn("Anexos: 1 enviado(s)", msg)

    def test_ticket_existente_no_tiflux_e_vinculado_sem_criar_duplicata(self):
        self.glpi.tickets[1] = _TICKET_ARRECADACAO
        self.tiflux.resultado_buscar_ticket_existente = ("361837", None)
        status, numero, msg = processar_chamado(self.glpi, self.tiflux, _CONFIG, 1)
        self.assertEqual((status, numero), ("sucesso", "361837"))
        self.assertIn("já existia no Tiflux", msg)
        self.assertEqual(self.tiflux.tickets_criados, [])

    def test_ticket_existente_no_tiflux_ainda_prefixa_titulo_no_glpi(self):
        self.glpi.tickets[1] = _TICKET_ARRECADACAO
        self.tiflux.resultado_buscar_ticket_existente = ("361837", None)
        processar_chamado(self.glpi, self.tiflux, _CONFIG, 1)
        self.assertEqual(self.glpi.titulos_atualizados, [(1, "#361837 - Problema X")])

    def test_erro_ao_buscar_ticket_existente_nao_cria_ticket(self):
        self.glpi.tickets[1] = _TICKET_ARRECADACAO
        self.tiflux.resultado_buscar_ticket_existente = (None, "2 tickets no Tiflux têm o chamado GLPI #1 no título")
        status, numero, msg = processar_chamado(self.glpi, self.tiflux, _CONFIG, 1)
        self.assertEqual((status, numero), ("erro", None))
        self.assertIn("2 tickets", msg)
        self.assertEqual(self.tiflux.tickets_criados, [])

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
