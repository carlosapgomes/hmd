"""Context processors globais de ``apps.accounts``.

Expoem a todos os templates: o nome de exibição do app (``APP_DISPLAY_NAME``),
o contexto de papel ativo da sessão (slice 005, R3) — papel ativo, lista de
papéis do usuário e flag ``has_multiple_roles`` —, a contagem de notificações
não lidas (slice 002 do change 11, R2) e os rótulos de unidade configurados
(change unit-labels-env, D4).
"""

from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest

from apps.accounts.models import User
from apps.accounts.notifications import get_unread_notification_count
from apps.cases.models import Case, CaseStatus, SchedulingUnit
from apps.cases.units import unit_label


def app_display_name(request: HttpRequest) -> dict[str, str]:
    """Adiciona ``app_display_name`` ao contexto de todos os templates."""
    return {"app_display_name": settings.APP_DISPLAY_NAME}


def role_context(request: HttpRequest) -> dict[str, object]:
    """Expõe papel ativo, papéis do usuário e flag multi-role (R3).

    Usuário anônimo recebe valores vazios; o contexto nunca persiste nada no
    banco — apenas lê ``session["active_role"]`` e os papéis do usuário.
    """
    user = request.user
    if not isinstance(user, User) or not user.is_authenticated:
        return {"active_role": "", "user_roles": [], "has_multiple_roles": False}

    user_roles = list(user.roles.order_by("name").values_list("name", flat=True))
    return {
        "active_role": request.session.get("active_role", ""),
        "user_roles": user_roles,
        "has_multiple_roles": len(user_roles) > 1,
    }


def notification_unread_count(request: HttpRequest) -> dict[str, int]:
    """Expõe a contagem de notificações não lidas (badge global, D2).

    Usuário anônimo recebe 0 (nada de consulta sem destinatário); o valor é a
    fonte única consumida pelo sino de ``templates/base.html`` em toda página
    autenticada.
    """
    user = request.user
    if not isinstance(user, User) or not user.is_authenticated:
        return {"notification_unread_count": 0}
    return {"notification_unread_count": get_unread_notification_count(user)}


def queue_counters(request: HttpRequest) -> dict[str, int]:
    """Totais das abas das filas para os ``_nav.html`` (slice 004, R2).

    Sob os MESMOS guards das views — papel ativo ``doctor``/``admin`` para a
    fila médica (mais a matriz de subtipo via ``DoctorAccess``, predicado
    único da fila) e ``scheduler``/``admin`` para a fila do agendador — e
    apenas contagens leves por estado (``count()`` sobre ``status__in``),
    sem queries globais sem guard: fora desses papéis ativos (e para
    anônimo) o processor devolve ``{}`` sem tocar na tabela de casos.
    Os estados por aba vêm dos ``TAB_STATUSES`` das próprias views (import
    local, sem duplicar a regra e sem ciclo de import). As chaves são
    distintas por área (``doctor_*``/``scheduler_*``) porque o admin usa
    as duas filas; os plugs mapeiam para ``pending_count``/``decided_count``/
    ``processed_count`` dos partials.
    """
    user = request.user
    if not isinstance(user, User) or not user.is_authenticated:
        return {}
    active_role = request.session.get("active_role", "")
    counters: dict[str, int] = {}
    if active_role in ("doctor", "admin"):
        from apps.doctor.access import DoctorAccess
        from apps.doctor.views import TAB_STATUSES as DOCTOR_TAB_STATUSES

        access = DoctorAccess(user, active_role=active_role)

        def _doctor_count(statuses: tuple[CaseStatus, ...]) -> int:
            cases = Case.objects.filter(status__in=statuses)
            allowed_types = access.allowed_types
            if allowed_types is not None:
                cases = cases.filter(
                    procedures__declared_by_nir=True,
                    procedures__procedure_type__in=allowed_types,
                )
            return cases.values("case_id").distinct().count()

        counters["doctor_pending_count"] = _doctor_count(DOCTOR_TAB_STATUSES["aguardando"])
        counters["doctor_decided_count"] = _doctor_count(DOCTOR_TAB_STATUSES["decididos"])
    if active_role in ("scheduler", "admin"):
        from apps.scheduler.views import TAB_STATUSES as SCHEDULER_TAB_STATUSES

        counters["scheduler_pending_count"] = Case.objects.filter(
            status__in=SCHEDULER_TAB_STATUSES["aguardando"]
        ).count()
        counters["scheduler_processed_count"] = Case.objects.filter(
            status__in=SCHEDULER_TAB_STATUSES["processados"]
        ).count()
    return counters


def unit_labels(request: HttpRequest) -> dict[str, dict[str, str]]:
    """Expõe os rótulos de unidade configurados a todos os templates (D4).

    Fonte única ``apps.cases.units`` (lida por requisição — sem cache de
    módulo): os templates consomem ``unit_labels.unit_1``/``unit_labels.unit_2``,
    as mesmas chaves do contexto do painel.
    """
    return {
        "unit_labels": {
            "unit_1": unit_label(SchedulingUnit.UNIT_1),
            "unit_2": unit_label(SchedulingUnit.UNIT_2),
        }
    }
