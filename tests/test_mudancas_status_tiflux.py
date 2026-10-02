import unittest
from datetime import datetime, timedelta, timezone

from sync.config import Config
from sync.mudancas_status_tiflux import obter_chamados_com_mudanca_de_status, selecionar_mudancas_de_status
from tests.fake_clients import FakeTifluxClient
from tests.fakes import FakeConnection

_CONFIG = Config(
    url_glpi="", app_token="", user_token="",
    url_tiflux="", token_tiflux="",
    db_host="", db_port="5432", db_name="", db_user="", db_password="",
    tabela_auditoria="x", tabela_followups="y",
)
_AGORA = datetime(2026, 10, 2, 19, 0, tzinfo=timezone.utc)


class TestSelecionarMudancasDeStatus(unittest.TestCase):
    def test_fechado_sem_encerramento_registrado_e_selecionado(self):
        tickets = [{"ticket_number": 10, "is_closed": True}]
        self.assertEqual(selecionar_mudancas_de_status(tickets, {10: (1, None)}), [(1, 10)])

    def test_fechado_ja_encerrado_em_cascata_fica_de_fora(self):
        tickets = [{"ticket_number": 10, "is_closed": True}]
        self.assertEqual(selecionar_mudancas_de_status(tickets, {10: (1, "encerramento")}), [])

    def test_fechado_de_novo_apos_reabertura_e_selecionado(self):
        tickets = [{"ticket_number": 10, "is_closed": True}]
        self.assertEqual(selecionar_mudancas_de_status(tickets, {10: (1, "reabertura")}), [(1, 10)])

    def test_reaberto_apos_encerramento_em_cascata_e_selecionado(self):
        # Tiflux #364212 / GLPI #34769: reaberto no Tiflux com o GLPI ainda Solucionado.
        tickets = [{"ticket_number": 364212, "is_closed": False}]
        self.assertEqual(selecionar_mudancas_de_status(tickets, {364212: (34769, "encerramento")}), [(34769, 364212)])

    def test_aberto_sem_encerramento_registrado_fica_de_fora(self):
        tickets = [{"ticket_number": 10, "is_closed": False}]
        self.assertEqual(selecionar_mudancas_de_status(tickets, {10: (1, None)}), [])

    def test_ticket_sem_chamado_sincronizado_fica_de_fora(self):
        # Tickets abertos direto no Tiflux (ex.: #361337) não têm chamado no GLPI.
        tickets = [{"ticket_number": 361337, "is_closed": True}]
        self.assertEqual(selecionar_mudancas_de_status(tickets, {}), [])


class TestObterChamadosComMudancaDeStatus(unittest.TestCase):
    def test_lista_desde_o_inicio_da_janela(self):
        tiflux = FakeTifluxClient()
        obter_chamados_com_mudanca_de_status(FakeConnection(), _CONFIG, tiflux, _AGORA)
        janela = timedelta(minutes=_CONFIG.janela_mudancas_status_tiflux_minutos)
        self.assertEqual(tiflux.inicios_listagem_atualizados, [_AGORA - janela])

    def test_sem_tickets_atualizados_nao_consulta_o_banco(self):
        conn = FakeConnection()
        self.assertEqual(obter_chamados_com_mudanca_de_status(conn, _CONFIG, FakeTifluxClient(), _AGORA), [])
        self.assertEqual(conn.execucoes, [])

    def test_cruza_tickets_com_auditoria(self):
        tiflux = FakeTifluxClient()
        tiflux.tickets_atualizados = [
            {"ticket_number": 10, "is_closed": True},
            {"ticket_number": 11, "is_closed": True},
        ]
        conn = FakeConnection(respostas=[[(10, 1, None), (11, 2, "encerramento")]])
        self.assertEqual(obter_chamados_com_mudanca_de_status(conn, _CONFIG, tiflux, _AGORA), [(1, 10)])
        _, params = conn.execucoes[0]
        self.assertEqual(params, ([10, 11],))


if __name__ == "__main__":
    unittest.main()
