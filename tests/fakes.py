"""Fakes nomeados para substituir I/O externo (HTTP e Postgres) nos testes."""


class FakeResponse:
    """Substitui requests.Response."""

    def __init__(self, status_code: int = 200, json_data=None, text: str = "", content: bytes | None = None):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text or ""
        self.content = content if content is not None else self.text.encode()

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeRequests:
    """
    Substitui o módulo `requests`. Respostas são programadas por (método, trecho
    da URL) e consumidas em fila (FIFO) — permite programar sequências diferentes
    pra chamadas repetidas ao mesmo endpoint (ex.: retry de atribuição de técnico).
    Toda chamada é registrada em `chamadas` pra asserções de URL/payload.
    """

    def __init__(self):
        self.chamadas: list[tuple[str, str, dict]] = []
        self._respostas: dict[tuple[str, str], list[FakeResponse]] = {}

    def programar(self, metodo: str, url_contendo: str, resposta: FakeResponse) -> None:
        self._respostas.setdefault((metodo, url_contendo), []).append(resposta)

    def Session(self):
        # Produção usa requests.Session() por instância de cliente (reuso de
        # conexão). O fake não distingue sessão de módulo — devolve a si
        # mesmo, que já implementa get/post/put e registra tudo em `chamadas`.
        return self

    def close(self):
        pass

    def mount(self, *args, **kwargs):
        pass

    def get(self, url, **kwargs):
        return self._responder("GET", url, kwargs)

    def post(self, url, **kwargs):
        return self._responder("POST", url, kwargs)

    def put(self, url, **kwargs):
        return self._responder("PUT", url, kwargs)

    def _responder(self, metodo: str, url: str, kwargs: dict) -> FakeResponse:
        self.chamadas.append((metodo, url, kwargs))
        for (m, trecho), fila in self._respostas.items():
            if m == metodo and trecho in url and fila:
                return fila.pop(0)
        raise AssertionError(f"Nenhuma resposta programada para {metodo} {url}")


class FakeCursor:
    def __init__(self, conn: "FakeConnection"):
        self._conn = conn
        self._resultado: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params=None) -> None:
        self._conn.execucoes.append((sql, params))
        self._resultado = self._conn.proxima_resposta()

    def fetchall(self):
        return self._resultado

    def fetchone(self):
        return self._resultado[0] if self._resultado else None


class FakeConnection:
    """
    Substitui uma conexão psycopg2. `respostas` é a fila (FIFO) de resultados
    que cada `cursor().execute()` sucessivo deve devolver via fetchall/fetchone.
    `execucoes` registra (sql, params) de cada chamada pra asserções.
    """

    def __init__(self, respostas: list[list[tuple]] | None = None):
        self._respostas = list(respostas or [])
        self.execucoes: list[tuple[str, tuple]] = []
        self.commits = 0

    def proxima_resposta(self) -> list[tuple]:
        return self._respostas.pop(0) if self._respostas else []

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1
