"""Endpoints públicos de saúde do runtime de produção (slice 001, R3/design D2).

``/healthz`` é liveness — o processo web está de pé? Responde sem autenticação,
sem sessão e sem banco (nenhuma dependência). ``/readyz`` é readiness — valida o
banco default com um ``SELECT 1`` (nenhum dado de negócio é lido) e devolve 503
quando o banco não responde, para o healthcheck do compose decidir.

As duas rotas são globais (sem namespace), públicas e isentas do
``IntranetGuardMiddleware`` (``EXEMPT_PATHS`` em ``apps.accounts.middleware``):
um probe de infraestrutura nunca pode receber 403.
"""

from __future__ import annotations

from django.core.cache import cache
from django.db import connection
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_safe


@require_GET
def healthz_view(request: HttpRequest) -> JsonResponse:
    """Liveness: 200 ``{"status": "ok"}`` — sem login, sem banco, sem dados."""
    return JsonResponse({"status": "ok"})


@require_safe
def readyz_view(request: HttpRequest) -> JsonResponse:
    """Readiness: 200 ``{"status": "ready"}`` com o banco respondendo E o
    cache compartilhado utilizável (tabela ``hmd_cache`` presente — o
    rate-limit de login depende dela); senão 503."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        # P2 review F2: toca o cache uma vez — se a tabela hmd_cache não
        # existir (createcachetable do migrator não rodou), o login quebraria
        # com ProgrammingError; o readyz deve reportar não-pronto antes disso.
        cache.get("__readyz__")
    except Exception:
        # Qualquer falha do probe (conexão recusada, timeout, credencial,
        # tabela de cache ausente) vira 503 com status fixo: o healthcheck
        # nunca recebe 500 nem detalhe interno do banco.
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ready"})
