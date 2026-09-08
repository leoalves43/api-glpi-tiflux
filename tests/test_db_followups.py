import unittest

from sync import db_followups
from sync.config import Config
from tests.fakes import FakeConnection

_CONFIG = Config(
    url_glpi="", app_token="", user_token="",
    url_tiflux="", token_tiflux="",
    db_host="", db_port="5432", db_name="", db_user="", db_password="",
    tabela_auditoria="siap.api_glpi_tiflux", tabela_followups="siap.api_glpi_tiflux_followups",
)


class TestObterChamadosParaVarrerFollowups(unittest.TestCase):
    def test_retorna_pares_id_glpi_numero_tiflux(self):
        conn = FakeConnection(respostas=[[(1, "T1"), (2, "T2")]])
        self.assertEqual(
            db_followups.obter_chamados_para_varrer_followups(conn, _CONFIG),
            [(1, "T1"), (2, "T2")],
        )

    def test_usa_limite_configurado(self):
        conn = FakeConnection(respostas=[[]])
        db_followups.obter_chamados_para_varrer_followups(conn, _CONFIG)
        _, params = conn.execucoes[0]
        self.assertEqual(params, (_CONFIG.tamanho_pagina_followups,))


class TestObterFollowupsGlpiJaProcessados(unittest.TestCase):
    def test_retorna_conjunto_de_ids_origem(self):
        conn = FakeConnection(respostas=[[(10,), (11,)]])
        self.assertEqual(db_followups.obter_followups_glpi_ja_processados(conn, _CONFIG, 99), {10, 11})


class TestObterRespostasTifluxJaProcessadasOuProprias(unittest.TestCase):
    def test_uniao_dos_dois_conjuntos(self):
        conn = FakeConnection(respostas=[[(1,), (2,), (3,)]])
        resultado = db_followups.obter_respostas_tiflux_ja_processadas_ou_proprias(conn, _CONFIG, "T1")
        self.assertEqual(resultado, {1, 2, 3})


class TestRegistrarResultadoFollowup(unittest.TestCase):
    def test_grava_e_comita(self):
        conn = FakeConnection()
        db_followups.registrar_resultado_followup(
            conn, _CONFIG, 1, "T1", "glpi_para_tiflux", "publica", 55, 77, "sucesso", "ok",
        )
        _, params = conn.execucoes[0]
        self.assertEqual(params, (1, "T1", "glpi_para_tiflux", "publica", 55, 77, "sucesso", "ok"))
        self.assertEqual(conn.commits, 1)


class TestRegistrarChamadoFechadoParaFollowups(unittest.TestCase):
    def test_usa_id_negativo_como_sentinela(self):
        conn = FakeConnection()
        db_followups.registrar_chamado_fechado_para_followups(conn, _CONFIG, 42)
        _, params = conn.execucoes[0]
        id_glpi, numero_tiflux, direcao, tipo, id_origem, id_destino, status, _mensagem = params
        self.assertEqual((id_glpi, numero_tiflux, direcao, tipo, id_origem, id_destino, status),
                          (42, None, "verificacao_status", "status", -42, None, "fechado"))


if __name__ == "__main__":
    unittest.main()
