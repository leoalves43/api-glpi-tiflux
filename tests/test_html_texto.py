import unittest

from sync.html_texto import html_para_texto_plano


class TestHtmlParaTextoPlano(unittest.TestCase):
    def test_vazio_ou_none_retorna_string_vazia(self):
        self.assertEqual(html_para_texto_plano(None), "")
        self.assertEqual(html_para_texto_plano(""), "")

    def test_paragrafos_viram_linhas_separadas(self):
        resultado = html_para_texto_plano("<p>Primeiro</p><p>Segundo</p>")
        self.assertEqual(resultado, "Primeiro\nSegundo")

    def test_br_vira_quebra_de_linha_simples(self):
        resultado = html_para_texto_plano("Linha um<br>Linha dois")
        self.assertEqual(resultado, "Linha um\nLinha dois")

    def test_lista_preserva_itens_com_marcador(self):
        resultado = html_para_texto_plano("<ul><li>Item A</li><li>Item B</li></ul>")
        self.assertEqual(resultado, "- Item A\n- Item B")

    def test_colapsa_tres_ou_mais_quebras_em_duas(self):
        resultado = html_para_texto_plano("<p>A</p><p></p><p></p><p>B</p>")
        self.assertEqual(resultado, "A\n\nB")

    def test_desfaz_html_entities(self):
        resultado = html_para_texto_plano("<p>&lt;tag&gt; &amp; texto</p>")
        self.assertEqual(resultado, "<tag> & texto")


if __name__ == "__main__":
    unittest.main()
