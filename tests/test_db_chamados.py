import unittest

from sync import db_chamados
from sync.config import Config
from tests.fakes import FakeConnection

_CONFIG = Config(
    url_glpi="", app_token="", user_token="",
    url_tiflux="", token_tiflux="",
    db_host="", db_port="5432", db_name="", db_user="", db_password="",
    tabela_auditoria="siap.api_glpi_tiflux", tabela_followups="siap.api_glpi_tiflux_followups",
    id_minimo_glpi=33637,
    janela_releitura_sondagem=50,
)


class TestObterIdsJaProcessados(unittest.TestCase):
    def test_retorna_conjunto_de_ids(self):
        conn = FakeConnection(respostas=[[(1,), (2,), (2,)]])
        self.assertEqual(db_chamados.obter_ids_ja_processados(conn, _CONFIG), {1, 2})


class TestObterProximoIdParaSondar(unittest.TestCase):
    def test_sem_linhas_usa_id_minimo(self):
        conn = FakeConnection(respostas=[[(None,)]])
        self.assertEqual(db_chamados.obter_proximo_id_para_sondar(conn, _CONFIG), 33637)

    def test_maior_id_abaixo_do_minimo_ainda_usa_minimo(self):
        conn = FakeConnection(respostas=[[(100,)]])
        self.assertEqual(db_chamados.obter_proximo_id_para_sondar(conn, _CONFIG), 33637)

    def test_recua_a_janela_de_releitura_a_partir_do_maior_id_registrado(self):
        conn = FakeConnection(respostas=[[(40000,)]])
        self.assertEqual(db_chamados.obter_proximo_id_para_sondar(conn, _CONFIG), 40001 - 50)

    def test_recuo_nunca_fica_abaixo_do_id_minimo(self):
        conn = FakeConnection(respostas=[[(33660,)]])
        self.assertEqual(db_chamados.obter_proximo_id_para_sondar(conn, _CONFIG), 33637)


class TestObterIdsParaRetry(unittest.TestCase):
    def test_retorna_conjunto_de_ids_com_erro(self):
        conn = FakeConnection(respostas=[[(5,), (9,)]])
        self.assertEqual(db_chamados.obter_ids_para_retry(conn, _CONFIG), {5, 9})


class TestRegistrarResultado(unittest.TestCase):
    def test_grava_e_comita(self):
        conn = FakeConnection()
        db_chamados.registrar_resultado(conn, _CONFIG, 123, "T1", "sucesso", "ok")
        self.assertEqual(len(conn.execucoes), 1)
        _, params = conn.execucoes[0]
        self.assertEqual(params, (123, "T1", "sucesso", "ok"))
        self.assertEqual(conn.commits, 1)

    def test_sanea_caractere_fora_do_win1252_antes_de_gravar(self):
        """
        Banco de auditoria tem encoding WIN1252; caracteres como '✪' (vindos de
        título do GLPI ou corpo de erro da API do Tiflux) derrubavam o INSERT
        com UntranslatableCharacter e paravam a sincronização inteira.
        """
        conn = FakeConnection()
        db_chamados.registrar_resultado(conn, _CONFIG, 123, "T1", "erro", "Cliente SP-CARAGUATATUBA-PM ✪ falhou")
        _, params = conn.execucoes[0]
        self.assertEqual(params, (123, "T1", "erro", "Cliente SP-CARAGUATATUBA-PM ? falhou"))


if __name__ == "__main__":
    unittest.main()
