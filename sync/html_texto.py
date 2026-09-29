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


def _decodificar_html_do_glpi(conteudo_html: str) -> str:
    """
    A API do GLPI devolve `content` com o HTML inteiro codificado em entidades
    ("&#60;p&#62;texto&#60;/p&#62;", visto ao vivo no #34522). Sem decodificar
    antes, o parser não enxerga nenhuma tag. HTML já cru (tem "<") passa direto.
    """
    if "<" in conteudo_html:
        return conteudo_html
    return html.unescape(conteudo_html)


def html_para_texto_plano(conteudo_html: str | None) -> str:
    """
    Extrai só o texto da descrição do GLPI, mantendo parágrafos/quebras de
    linha/listas legíveis — processamento_chamado.texto_para_html_tiflux() depois
    reconverte as quebras em <br> pro Tiflux.
    Ex.: html_para_texto_plano("&#60;p&#62;A&#60;/p&#62;&#60;p&#62;B&#60;/p&#62;") -> "A\\nB"
    """
    if not conteudo_html:
        return ""

    parser = _HTMLParaTexto()
    parser.feed(_decodificar_html_do_glpi(conteudo_html))
    texto = "".join(parser.partes)
    texto = html.unescape(texto)

    linhas = [linha.strip() for linha in texto.splitlines()]
    texto = "\n".join(linhas)
    while "\n\n\n" in texto:
        texto = texto.replace("\n\n\n", "\n\n")

    return texto.strip()
