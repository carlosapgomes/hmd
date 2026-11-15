"""Helpers de navegação compartilhados entre apps (slice PVP-001, R1/D5).

Espelho de ``apps/cases/navigation.py`` do ats-web (adaptado ao HMD): as
views de viewer dos 4 papéis validam o ``?next=`` com este helper em vez de
duplicar a lógica de segurança.
"""

from __future__ import annotations

from django.http import HttpRequest
from django.utils.http import url_has_allowed_host_and_scheme


def resolve_safe_next_url(
    request: HttpRequest, fallback_url: str, *, param_name: str = "next"
) -> str:
    """Retorna a URL ``next`` do querystring quando for segura, senão o fallback.

    Regras (D5):
    - Lê ``request.GET.get(param_name, "")``.
    - Aceita apenas URLs consideradas seguras por
      ``url_has_allowed_host_and_scheme``.
    - Usa ``allowed_hosts={request.get_host()}``.
    - Usa ``require_https=request.is_secure()``.
    - Retorna ``fallback_url`` quando ``next`` está ausente, vazio, externo,
      protocol-relative (``//evil``) ou inseguro.
    - Sempre retorna ``str``, nunca ``None``.
    """
    raw_next = request.GET.get(param_name, "")
    if raw_next and url_has_allowed_host_and_scheme(
        url=raw_next,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return raw_next
    return fallback_url
