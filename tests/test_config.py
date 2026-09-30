import io
import os
import tempfile
import unittest
from unittest.mock import patch

from sync.config import Config, log


class TestLog(unittest.TestCase):
    def test_nao_quebra_quando_console_nao_suporta_emoji(self):
        """
        Regressão: log() crashava com UnicodeEncodeError em consoles cp1252
        (comum no Windows em pt-BR) ao imprimir mensagens com emoji — isso
        derrubava a sincronização inteira mesmo com o trabalho já feito.
        """
        saida_cp1252 = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
        with patch("sys.stdout", saida_cp1252):
            log("🔎 Sondagem de #1 até #2: 1 chamado(s) encontrado(s)")
        saida_cp1252.flush()

    def test_mensagem_sem_caracteres_especiais_passa_direto(self):
        buffer = io.StringIO()
        with patch("sys.stdout", buffer):
            log("Finalizado. Sucesso: 1 | Ignorado: 0 | Erro: 0")
        self.assertIn("Finalizado. Sucesso: 1", buffer.getvalue())


class TestConfigCarregar(unittest.TestCase):
    def setUp(self):
        arquivo = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False)
        arquivo.write("DB_HOST=localhost\nDB_NAME=banco\nDB_SCHEMA=esquema\n")
        arquivo.close()
        self.caminho_env = arquivo.name
        self.addCleanup(os.remove, self.caminho_env)

    def test_variavel_de_ambiente_sobrescreve_env(self):
        config = Config.carregar(self.caminho_env, ambiente={"DB_HOST": "host.docker.internal"})
        self.assertEqual(config.db_host, "host.docker.internal")
        self.assertEqual(config.db_name, "banco")

    def test_sem_variavel_de_ambiente_usa_env(self):
        config = Config.carregar(self.caminho_env, ambiente={})
        self.assertEqual(config.db_host, "localhost")
        self.assertEqual(config.tabela_auditoria, "esquema.api_glpi_tiflux")


if __name__ == "__main__":
    unittest.main()
