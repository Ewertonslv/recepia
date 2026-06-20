"""Testes do hardening de CSRF no Google OAuth (parâmetro `state`).

O OAuth fica desabilitado por padrão (sem env vars). Aqui habilitamos via
monkeypatch dos flags de módulo para exercitar o fluxo de state.
"""
import api.auth as auth_mod


def _habilitar_oauth(monkeypatch):
    monkeypatch.setattr(auth_mod, "_GOOGLE_ENABLED", True)
    monkeypatch.setattr(auth_mod, "_G_CLIENT_ID", "fake-client-id")
    monkeypatch.setattr(auth_mod, "_G_CLIENT_SECRET", "fake-secret")
    monkeypatch.setattr(auth_mod, "_G_REDIRECT_URI", "https://www.recepia.app.br/auth/google/callback")


def test_login_seta_cookie_e_state(client, monkeypatch):
    _habilitar_oauth(monkeypatch)
    r = client.get("/auth/google", follow_redirects=False)
    assert r.status_code in (302, 307)
    # state vai no redirect pro Google e também no cookie httponly.
    assert "state=" in r.headers["location"]
    assert "g_oauth_state" in r.headers.get("set-cookie", "")


def test_callback_rejeita_state_invalido(client, monkeypatch):
    _habilitar_oauth(monkeypatch)
    # Sem cookie correspondente → mismatch → rejeita ANTES de trocar o code.
    r = client.get(
        "/auth/google/callback?code=abc&state=forjado",
        follow_redirects=False,
    )
    assert r.status_code in (302, 307)
    assert "google_error=invalid_state" in r.headers["location"]


def test_callback_sem_state_rejeita(client, monkeypatch):
    _habilitar_oauth(monkeypatch)
    r = client.get("/auth/google/callback?code=abc", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "google_error=invalid_state" in r.headers["location"]
