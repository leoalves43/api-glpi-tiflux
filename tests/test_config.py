import io
import unittest
from unittest.mock import patch

from sync.config import log


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


if __name__ == "__main__":
    unittest.main()
