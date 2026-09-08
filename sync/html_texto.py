"""Conversão de HTML (descrição de chamado do GLPI) para texto puro."""

import html
from html.parser import HTMLParser


class _HTMLParaTexto(HTMLParser):
    """Extrai texto puro de um HTML, preservando quebras de linha e listas."""

    _TAGS_QUEBRA_LINHA = {"p", "div", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "ol", "ul"}

    def __init__(self):
        super().__init__()
        self.partes: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self.partes.append("\n")
        elif tag == "li":
            self.partes.append("\n- ")

    def handle_endtag(self, tag):
        if tag in self._TAGS_QUEBRA_LINHA:
            self.partes.append("\n")

    def handle_data(self, data):
        self.partes.append(data)


def html_para_texto_plano(conteudo_html: str | None) -> str:
    """
    O campo de descrição do Tiflux é TEXTO PURO, não renderiza HTML — mandar as
    tags do GLPI direto faz elas aparecerem literalmente pro atendente. Essa
    função extrai só o texto, mantendo parágrafos/quebras de linha/listas legíveis.
    """
    if not conteudo_html:
        return ""

    parser = _HTMLParaTexto()
    parser.feed(conteudo_html)
    texto = "".join(parser.partes)
    texto = html.unescape(texto)

    linhas = [linha.strip() for linha in texto.splitlines()]
    texto = "\n".join(linhas)
    while "\n\n\n" in texto:
        texto = texto.replace("\n\n\n", "\n\n")

    return texto.strip()
