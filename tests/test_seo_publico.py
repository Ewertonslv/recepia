"""Rotas públicas de SEO — robots.txt, sitemap.xml e canonical das landings.

Regressão: a rota do /sitemap.xml ficou aninhada dentro do corpo de
`tailwind_css()`, depois do `return`, então nunca era registrada e o endpoint
respondia 404 em produção. Nada cobria essas rotas, por isso passou.

Cobre também o host canônico: robots/sitemap/canonical já apontaram para
`app.recepia.app.br`, um subdomínio que não existe (NXDOMAIN) — canonical em
host morto faz o Google descartar as páginas que estão no ar.
"""
import re

HOST = "https://recepia.app.br"
HOST_MORTO = "app.recepia.app.br"


def test_robots_txt_servido(client):
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    assert "Sitemap:" in r.text


def test_sitemap_xml_servido(client):
    """A rota existe de fato — era o bug do 404."""
    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    assert "xml" in r.headers["content-type"]
    assert "<urlset" in r.text


def test_robots_e_sitemap_nao_apontam_host_morto(client):
    for rota in ("/robots.txt", "/sitemap.xml"):
        assert HOST_MORTO not in client.get(rota).text, f"{rota} aponta host inexistente"


def test_canonical_das_landings_usa_host_no_ar(client):
    for rota in ("/", "/termos", "/privacidade", "/cadastro"):
        r = client.get(rota)
        assert r.status_code == 200
        m = re.search(r'<link rel="canonical" href="([^"]+)"', r.text)
        assert m, f"{rota} sem tag canonical"
        assert m.group(1).startswith(HOST), f"{rota} com canonical em {m.group(1)}"
