import contextlib
import io
import unittest
from unittest.mock import patch

from sync.glpi_client import GlpiClient
from tests.fakes import FakeRequests, FakeResponse

URL_BASE = "https://glpi.example/apirest.php"

# log() imprime emojis; o console do Windows nesse ambiente usa cp1252 e não
# consegue codificá-los — redireciona pra um buffer em memória só nos testes
# que exercitam código com log(), sem tocar o comportamento de produção.
_sem_console = lambda: contextlib.redirect_stdout(io.StringIO())


def _client(fake: FakeRequests) -> GlpiClient:
    return GlpiClient(URL_BASE, "app-token", {"App-Token": "app-token", "Session-Token": "sess"})


class TestAutenticar(unittest.TestCase):
    def test_monta_headers_com_session_token(self):
        fake = FakeRequests()
        fake.programar("GET", "/initSession", FakeResponse(200, {"session_token": "abc123"}))
        with patch("sync.glpi_client.requests", fake):
            from sync.config import Config
            config = Config(
                url_glpi=URL_BASE, app_token="app-token", user_token="user-token",
                url_tiflux="", token_tiflux="",
                db_host="", db_port="5432", db_name="", db_user="", db_password="",
                tabela_auditoria="x", tabela_followups="y",
            )
            client = GlpiClient.autenticar(config)
        self.assertEqual(client._headers["Session-Token"], "abc123")


class TestBuscarChamadosDesde(unittest.TestCase):
    def test_para_apos_max_furos_seguidos(self):
        fake = FakeRequests()
        for _ in range(5):
            fake.programar("GET", "/Ticket/", FakeResponse(404))
        with patch("sync.glpi_client.requests", fake), _sem_console():
            client = _client(fake)
            encontrados = client.buscar_chamados_desde(100, limite_por_execucao=10, max_furos_seguidos=5)
        self.assertEqual(encontrados, [])

    def test_para_ao_atingir_o_limite(self):
        fake = FakeRequests()
        for _ in range(3):
            fake.programar("GET", "/Ticket/", FakeResponse(200, {"id": 1}))
        with patch("sync.glpi_client.requests", fake), _sem_console():
            client = _client(fake)
            encontrados = client.buscar_chamados_desde(100, limite_por_execucao=3, max_furos_seguidos=50)
        self.assertEqual(encontrados, [100, 101, 102])


class TestObterTicket(unittest.TestCase):
    def test_encontrado_retorna_dados_e_status(self):
        fake = FakeRequests()
        fake.programar("GET", "/Ticket/42", FakeResponse(200, {"id": 42}))
        with patch("sync.glpi_client.requests", fake):
            ticket, status = _client(fake).obter_ticket(42)
        self.assertEqual((ticket, status), ({"id": 42}, 200))

    def test_nao_encontrado_retorna_none_e_status(self):
        fake = FakeRequests()
        fake.programar("GET", "/Ticket/42", FakeResponse(404, text="not found"))
        with patch("sync.glpi_client.requests", fake):
            ticket, status = _client(fake).obter_ticket(42)
        self.assertEqual((ticket, status), (None, 404))


class TestCriarFollowup(unittest.TestCase):
    def test_sucesso_retorna_id(self):
        fake = FakeRequests()
        fake.programar("POST", "/ITILFollowup", FakeResponse(201, {"id": 9}))
        with patch("sync.glpi_client.requests", fake):
            id_criado, erro = _client(fake).criar_followup(1, "<p>oi</p>")
        self.assertEqual((id_criado, erro), (9, None))

    def test_falha_http_retorna_erro(self):
        fake = FakeRequests()
        fake.programar("POST", "/ITILFollowup", FakeResponse(400, text="bad request"))
        with patch("sync.glpi_client.requests", fake):
            id_criado, erro = _client(fake).criar_followup(1, "<p>oi</p>")
        self.assertIsNone(id_criado)
        self.assertIn("400", erro)

    def test_users_id_e_enviado_no_payload_quando_informado(self):
        fake = FakeRequests()
        fake.programar("POST", "/ITILFollowup", FakeResponse(201, {"id": 9}))
        with patch("sync.glpi_client.requests", fake):
            _client(fake).criar_followup(1, "<p>oi</p>", users_id=4988)
        _, _, kwargs = fake.chamadas[-1]
        self.assertEqual(kwargs["json"]["input"]["users_id"], 4988)

    def test_users_id_ausente_do_payload_quando_nao_informado(self):
        fake = FakeRequests()
        fake.programar("POST", "/ITILFollowup", FakeResponse(201, {"id": 9}))
        with patch("sync.glpi_client.requests", fake):
            _client(fake).criar_followup(1, "<p>oi</p>")
        _, _, kwargs = fake.chamadas[-1]
        self.assertNotIn("users_id", kwargs["json"]["input"])

    def test_sem_id_na_resposta_retorna_erro(self):
        fake = FakeRequests()
        fake.programar("POST", "/ITILFollowup", FakeResponse(201, {}))
        with patch("sync.glpi_client.requests", fake):
            id_criado, erro = _client(fake).criar_followup(1, "<p>oi</p>")
        self.assertIsNone(id_criado)
        self.assertIsNotNone(erro)


class TestEncerrarChamado(unittest.TestCase):
    def test_sucesso_retorna_true(self):
        fake = FakeRequests()
        fake.programar("PUT", "/Ticket/1", FakeResponse(200, {}))
        with patch("sync.glpi_client.requests", fake):
            sucesso, erro = _client(fake).encerrar_chamado(1, 5)
        self.assertEqual((sucesso, erro), (True, None))
        _, _, kwargs = fake.chamadas[-1]
        self.assertEqual(kwargs["json"]["input"]["status"], 5)

    def test_falha_http_retorna_erro(self):
        fake = FakeRequests()
        fake.programar("PUT", "/Ticket/1", FakeResponse(400, text="bad request"))
        with patch("sync.glpi_client.requests", fake):
            sucesso, erro = _client(fake).encerrar_chamado(1, 5)
        self.assertFalse(sucesso)
        self.assertIn("400", erro)


class TestAtualizarTitulo(unittest.TestCase):
    def test_sucesso_retorna_true(self):
        fake = FakeRequests()
        fake.programar("PUT", "/Ticket/1", FakeResponse(200, {}))
        with patch("sync.glpi_client.requests", fake):
            sucesso, erro = _client(fake).atualizar_titulo(1, "#361458 - Solicito acesso")
        self.assertEqual((sucesso, erro), (True, None))
        _, _, kwargs = fake.chamadas[-1]
        self.assertEqual(kwargs["json"]["input"]["name"], "#361458 - Solicito acesso")

    def test_falha_http_retorna_erro(self):
        fake = FakeRequests()
        fake.programar("PUT", "/Ticket/1", FakeResponse(400, text="bad request"))
        with patch("sync.glpi_client.requests", fake):
            sucesso, erro = _client(fake).atualizar_titulo(1, "novo titulo")
        self.assertFalse(sucesso)
        self.assertIn("400", erro)


class TestChamadoTemGrupoObservador(unittest.TestCase):
    def test_encontrado_como_observador(self):
        fake = FakeRequests()
        fake.programar("GET", "/Group_Ticket", FakeResponse(200, [{"type": 3, "groups_id": 22}]))
        with patch("sync.glpi_client.requests", fake):
            ok, motivo = _client(fake).chamado_tem_grupo_observador(1, 22)
        self.assertTrue(ok)
        self.assertIsNone(motivo)

    def test_grupo_presente_mas_nao_como_observador(self):
        fake = FakeRequests()
        fake.programar("GET", "/Group_Ticket", FakeResponse(200, [{"type": 1, "groups_id": 22}]))
        with patch("sync.glpi_client.requests", fake):
            ok, motivo = _client(fake).chamado_tem_grupo_observador(1, 22)
        self.assertFalse(ok)
        self.assertIn("22", motivo)


class TestObterRequerente(unittest.TestCase):
    def test_resolve_por_ticket_user_type_1(self):
        fake = FakeRequests()
        fake.programar("GET", "/Ticket_User", FakeResponse(200, [{"type": 1, "users_id": 7}]))
        fake.programar("GET", "/User/7", FakeResponse(200, {"firstname": "Ana", "realname": "Silva", "email": "ana@x.com"}))
        with patch("sync.glpi_client.requests", fake):
            nome, email, id_req = _client(fake).obter_requerente(1, {})
        self.assertEqual((nome, email, id_req), ("Ana Silva", "ana@x.com", 7))

    def test_sem_vinculo_usa_fallback_do_ticket(self):
        fake = FakeRequests()
        fake.programar("GET", "/Ticket_User", FakeResponse(200, []))
        fake.programar("GET", "/User/9", FakeResponse(200, {"firstname": "", "realname": "", "name": "login9", "email": None}))
        fake.programar("GET", "/UserEmail", FakeResponse(200, [{"email": "alt@x.com"}]))
        with patch("sync.glpi_client.requests", fake):
            nome, email, id_req = _client(fake).obter_requerente(1, {"users_id_recipient": 9})
        self.assertEqual((nome, email, id_req), ("Login: login9", "alt@x.com", 9))

    def test_sem_nenhum_requerente_retorna_desconhecido(self):
        fake = FakeRequests()
        fake.programar("GET", "/Ticket_User", FakeResponse(200, []))
        with patch("sync.glpi_client.requests", fake):
            nome, email, id_req = _client(fake).obter_requerente(1, {})
        self.assertEqual((nome, email, id_req), ("Desconhecido", None, None))


class TestObterAnexos(unittest.TestCase):
    def test_anexo_dentro_do_limite_e_incluido(self):
        fake = FakeRequests()
        fake.programar("GET", "/Document_Item", FakeResponse(200, [{"documents_id": 5}]))
        fake.programar("GET", "/Document/5", FakeResponse(200, {"filename": "a.txt", "mime": "text/plain"}))
        fake.programar("GET", "/Document/5", FakeResponse(200, json_data=None, text="conteudo"))
        with patch("sync.glpi_client.requests", fake):
            anexos, avisos = _client(fake).obter_anexos(1, tamanho_maximo_mb=25)
        self.assertEqual(len(anexos), 1)
        self.assertEqual(anexos[0][0], "a.txt")
        self.assertEqual(avisos, [])

    def test_sem_vinculos_retorna_vazio(self):
        fake = FakeRequests()
        fake.programar("GET", "/Document_Item", FakeResponse(200, []))
        with patch("sync.glpi_client.requests", fake):
            anexos, avisos = _client(fake).obter_anexos(1, tamanho_maximo_mb=25)
        self.assertEqual((anexos, avisos), ([], []))


if __name__ == "__main__":
    unittest.main()
