"""Helper de URL segura para voltar (slice PVP-001, R1/D5).

Espelha ``apps/cases/navigation.py`` do ats-web (mesma semântica de
``url_has_allowed_host_and_scheme``): as views de viewer dos 4 papéis
reutilizam este helper em vez de duplicar a lógica de segurança.
"""

from __future__ import annotations

from django.http import HttpRequest
from django.test import RequestFactory

from apps.cases.navigation import resolve_safe_next_url

FALLBACK = "/detalhe-do-caso/"


def _request(path: str = "/", *, secure: bool = False) -> HttpRequest:
    """Request mínima de teste com host e esquema controlados."""
    factory = RequestFactory()
    request = factory.get(path, secure=secure)
    return request


def test_safe_same_host_next_accepted() -> None:
    """next do mesmo host é aceito."""
    request = _request("/?next=/casos/abc/")
    assert resolve_safe_next_url(request, FALLBACK) == "/casos/abc/"


def test_external_next_falls_back() -> None:
    """next externo cai no fallback."""
    request = _request("/?next=https://evil.example/")
    assert resolve_safe_next_url(request, FALLBACK) == FALLBACK


def test_protocol_relative_next_falls_back() -> None:
    """next protocol-relative (//evil) cai no fallback."""
    request = _request("/?next=//evil.example/phish")
    assert resolve_safe_next_url(request, FALLBACK) == FALLBACK


def test_missing_next_falls_back() -> None:
    """next ausente cai no fallback."""
    request = _request("/")
    assert resolve_safe_next_url(request, FALLBACK) == FALLBACK


def test_empty_next_falls_back() -> None:
    """next vazio cai no fallback."""
    request = _request("/?next=")
    assert resolve_safe_next_url(request, FALLBACK) == FALLBACK


def test_https_requires_https_when_secure() -> None:
    """Com request segura, http do mesmo host cai no fallback (https)."""
    request = _request("/?next=http://testserver/casos/abc/", secure=True)
    assert resolve_safe_next_url(request, FALLBACK) == FALLBACK
