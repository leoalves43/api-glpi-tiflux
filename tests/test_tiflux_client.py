import contextlib
import io
import unittest
from unittest.mock import patch

from sync.tiflux_client import TifluxClient
from tests.fakes import FakeRequests, FakeResponse

URL_BASE = "https://api.tiflux.com/api/v2"
_sem_console = lambda: contextlib.redirect_stdout(io.StringIO())


def _client() -> TifluxClient:
    return TifluxClient(URL_BASE, "token", cliente_id=762707, id_solicitante_padrao=3758056)


class TestValidarMesaDoCliente(unittest.TestCase):
    def test_mesa_presente_na_lista_do_cliente(self):
        fake = FakeRequests()
        fake.programar("GET", "/desks", FakeResponse(200, [{"id": 37963}, {"id": 37964}]))
        with patch("sync.tiflux_client.requests", fake):
            client = _client()
            self.assertTrue(client.validar_mesa_do_cliente(37963))
            self.assertFalse(client.validar_mesa_do_cliente(99999))
            # segunda chamada não deve bater na API de novo (cache de instância)
            self.assertEqual(len(fake.chamadas), 1)

    def test_falha_ao_listar_mesas_nao_bloqueia(self):
        fake = FakeRequests()
        fake.programar("GET", "/desks", FakeResponse(500, text="erro"))
        with patch("sync.tiflux_client.requests", fake), _sem_console():
            self.assertTrue(_client().validar_mesa_do_cliente(37963))


class TestObterIdSolicitante(unittest.TestCase):
    def test_sem_email_usa_padrao(self):
        client = _client()
        id_solicitante, info = client.obter_id_solicitante("Fulano", None)
        self.assertEqual(id_solicitante, 3758056)
        self.assertIn("Sem E-mail", info)

    def test_encontrado_por_email(self):
        fake = FakeRequests()
        fake.programar("GET", "/requestors", FakeResponse(200, [{"id": 5, "email": "a@x.com", "name": "A"}]))
        with patch("sync.tiflux_client.requests", fake):
            id_solicitante, info = _client().obter_id_solicitante("A", "a@x.com")
        self.assertEqual(id_solicitante, 5)
        self.assertIn("Existente", info)

    def test_nao_encontrado_cadastra(self):
        fake = FakeRequests()
        fake.programar("GET", "/requestors", FakeResponse(404))
        fake.programar("POST", "/requestors", FakeResponse(201, {"id": 8, "name": "Novo"}))
        with patch("sync.tiflux_client.requests", fake):
            id_solicitante, info = _client().obter_id_solicitante("Novo", "novo@x.com")
        self.assertEqual(id_solicitante, 8)
        self.assertIn("Cadastrado Automaticamente", info)

    def test_falha_ao_cadastrar_usa_padrao(self):
        fake = FakeRequests()
        fake.programar("GET", "/requestors", FakeResponse(404))
        fake.programar("POST", "/requestors", FakeResponse(500, text="erro"))
        with patch("sync.tiflux_client.requests", fake), _sem_console():
            id_solicitante, info = _client().obter_id_solicitante("Novo", "novo@x.com")
        self.assertEqual(id_solicitante, 3758056)
        self.assertIn("Falha ao Auto-Cadastrar", info)


class TestCriarTicket(unittest.TestCase):
    def test_sucesso_retorna_ticket_number(self):
        fake = FakeRequests()
        fake.programar("POST", "/tickets", FakeResponse(201, {"ticket": {"ticket_number": "T-1"}}))
        with patch("sync.tiflux_client.requests", fake):
            numero, erro = _client().criar_ticket({"title": "x"})
        self.assertEqual((numero, erro), ("T-1", None))

    def test_falha_http_retorna_erro(self):
        fake = FakeRequests()
        fake.programar("POST", "/tickets", FakeResponse(422, text="invalido"))
        with patch("sync.tiflux_client.requests", fake):
            numero, erro = _client().criar_ticket({"title": "x"})
        self.assertIsNone(numero)
        self.assertIn("422", erro)

    def test_sem_ticket_number_na_resposta_retorna_erro(self):
        fake = FakeRequests()
        fake.programar("POST", "/tickets", FakeResponse(201, {"ticket": {}}))
        with patch("sync.tiflux_client.requests", fake):
            numero, erro = _client().criar_ticket({"title": "x"})
        self.assertIsNone(numero)
        self.assertIsNotNone(erro)


class TestAtribuirTecnico(unittest.TestCase):
    def test_sucesso_no_change_responsible(self):
        fake = FakeRequests()
        fake.programar("POST", "/change_responsible", FakeResponse(200))
        with patch("sync.tiflux_client.requests", fake):
            ok, status, _texto = _client().atribuir_tecnico("T-1", 117180)
        self.assertTrue(ok)
        self.assertEqual(status, 200)

    def test_fallback_para_put_quando_post_falha(self):
        fake = FakeRequests()
        fake.programar("POST", "/change_responsible", FakeResponse(400, text="falhou"))
        fake.programar("PUT", "/tickets/T-1", FakeResponse(200))
        with patch("sync.tiflux_client.requests", fake):
            ok, status, _texto = _client().atribuir_tecnico("T-1", 117180)
        self.assertTrue(ok)
        self.assertEqual(status, 200)

    def test_ambos_falham_retorna_status_e_texto_da_ultima_tentativa(self):
        fake = FakeRequests()
        fake.programar("POST", "/change_responsible", FakeResponse(400, text="falhou post"))
        fake.programar("PUT", "/tickets/T-1", FakeResponse(500, text="falhou put"))
        with patch("sync.tiflux_client.requests", fake):
            ok, status, texto = _client().atribuir_tecnico("T-1", 117180)
        self.assertFalse(ok)
        self.assertEqual((status, texto), (500, "falhou put"))


class TestEnviarAnexos(unittest.TestCase):
    def test_sem_anexos_nao_chama_api(self):
        fake = FakeRequests()
        with patch("sync.tiflux_client.requests", fake):
            enviados, falhados, motivos = _client().enviar_anexos("T-1", [])
        self.assertEqual((enviados, falhados, motivos), (0, 0, []))
        self.assertEqual(fake.chamadas, [])

    def test_envia_em_lotes_de_ate_dez(self):
        fake = FakeRequests()
        fake.programar("POST", "/files", FakeResponse(200))
        fake.programar("POST", "/files", FakeResponse(200))
        anexos = [(f"a{i}.txt", b"x", "text/plain") for i in range(15)]
        with patch("sync.tiflux_client.requests", fake):
            enviados, falhados, motivos = _client().enviar_anexos("T-1", anexos)
        self.assertEqual((enviados, falhados, motivos), (15, 0, []))
        self.assertEqual(len(fake.chamadas), 2)

    def test_lote_com_falha_e_contabilizado(self):
        fake = FakeRequests()
        fake.programar("POST", "/files", FakeResponse(500, text="erro"))
        anexos = [("a.txt", b"x", "text/plain")]
        with patch("sync.tiflux_client.requests", fake):
            enviados, falhados, motivos = _client().enviar_anexos("T-1", anexos)
        self.assertEqual((enviados, falhados), (0, 1))
        self.assertEqual(len(motivos), 1)


class TestListarPaginado(unittest.TestCase):
    def test_para_quando_pagina_vem_menor_que_o_tamanho(self):
        fake = FakeRequests()
        fake.programar("GET", "/answers", FakeResponse(200, [{"id": 1}, {"id": 2}]))
        with patch("sync.tiflux_client.requests", fake):
            itens = _client().listar_respostas("T-1", tamanho_pagina=5, max_paginas=20)
        self.assertEqual(itens, [{"id": 1}, {"id": 2}])
        self.assertEqual(len(fake.chamadas), 1)

    def test_pagina_ate_lista_vazia(self):
        fake = FakeRequests()
        fake.programar("GET", "/answers", FakeResponse(200, [{"id": i} for i in range(5)]))
        fake.programar("GET", "/answers", FakeResponse(200, []))
        with patch("sync.tiflux_client.requests", fake):
            itens = _client().listar_respostas("T-1", tamanho_pagina=5, max_paginas=20)
        self.assertEqual(len(itens), 5)
        self.assertEqual(len(fake.chamadas), 2)

    def test_falha_http_interrompe_e_retorna_o_que_ja_tinha(self):
        fake = FakeRequests()
        fake.programar("GET", "/internal_communications", FakeResponse(200, [{"id": 1}]))
        fake.programar("GET", "/internal_communications", FakeResponse(500, text="erro"))
        with patch("sync.tiflux_client.requests", fake), _sem_console():
            itens = _client().listar_comunicacoes_internas("T-1", tamanho_pagina=1, max_paginas=20)
        self.assertEqual(itens, [{"id": 1}])


if __name__ == "__main__":
    unittest.main()
