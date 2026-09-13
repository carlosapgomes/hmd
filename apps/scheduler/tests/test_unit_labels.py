"""Rótulos de unidade configuráveis por ambiente (change unit-labels-env, slice 001).

Cobre R1–R4: as envs ``HMD_UNIT_1_LABEL``/``HMD_UNIT_2_LABEL`` com default
canônico (``_parse_unit_labels`` — a MESMA função de parsing usada pelo
``config/settings/base.py``; nenhum reload de módulo de settings nos testes), a
fonte única ``apps/cases.units`` e a propagação do rótulo vigente na resposta
final ao NIR da unidade 2 e nas choices do ``SchedulerConfirmForm``. Com os
defaults, o texto canônico do plano §4 (sem ponto final) e as choices atuais
ficam byte-a-byte iguais.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, cast

import pytest
from django.conf import settings
from django.test import Client, override_settings
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.cases.models import Case, MessageType
from apps.cases.units import unit_label, unit_labels
from apps.scheduler.forms import SchedulerConfirmForm
from apps.scheduler.services import confirm_case_scheduling
from config.settings.base import _parse_unit_labels

SCHEDULER_ROLE = "scheduler"
SYSTEM_ROLE = "system"

# Defaults canônicos de D1, o texto canônico do plano §4 (sem ponto final) e o
# rótulo configurado dos cenários de override.
CANONICAL_LABELS = {1: "Unidade 1", 2: "Unidade 2"}
CONFIGURED_LABELS = {1: "Unidade 1", 2: "Unidade Satélite"}
CANONICAL_UNIT_2_REPLY = (
    "Recusar o relatório — caso agendado na Unidade 2, que comunicará a Secretaria"
)

# Caminho (operação, kwargs) de NEW até ``SCHEDULER_REQUESTED`` — origem aceita
# pela confirmação do agendamento (mesmos passos do ``test_services.py``).
_STEPS_TO_SCHEDULER_REQUESTED: list[tuple[str, dict[str, object]]] = [
    ("start_pdf_extraction", {}),
    ("complete_pdf_extraction", {}),
    ("complete_anonymization", {}),
    ("complete_llm_extraction", {}),
    ("complete_llm_summarization", {}),
    ("record_doctor_decision", {"accepted": True}),
    ("request_scheduling", {}),
]


def _case_in_scheduler_requested(*, created_by: User) -> Case:
    """Caso novo dirigido pelas transições válidas até ``SCHEDULER_REQUESTED``."""
    case = Case.objects.create(created_by=created_by)
    for operation, extra in _STEPS_TO_SCHEDULER_REQUESTED:
        getattr(case, operation)(user=None, role=SYSTEM_ROLE, **extra)
    assert case.status == "SCHEDULER_REQUESTED"
    return case


def _future_datetime(**kwargs: int) -> datetime:
    """Data/hora aware futura (UTC) a partir de agora."""
    return timezone.now() + timedelta(**kwargs)


def _user_messages(case: Case) -> list[object]:
    """Mensagens manuais (user) da thread do caso, em ordem."""
    return list(case.communication_messages.filter(message_type=MessageType.USER))


@pytest.fixture
def scheduler_user() -> User:
    """Agendador ator dos serviços (papel ``scheduler``)."""
    role, _ = Role.objects.get_or_create(name=SCHEDULER_ROLE)
    user = User.objects.create_user(username="agendador-labels", password="senha-teste")
    user.roles.add(role)
    return user


# ── R1: envs com default canônico ───────────────────────────────────────────


def test_parse_unit_labels_defaults_when_envs_absent_or_empty() -> None:
    """R1: envs ausentes ou vazias (já com strip) → defaults canônicos."""
    assert _parse_unit_labels({}) == CANONICAL_LABELS
    assert (
        _parse_unit_labels({"HMD_UNIT_1_LABEL": "   ", "HMD_UNIT_2_LABEL": ""}) == CANONICAL_LABELS
    )


def test_parse_unit_labels_keeps_configured_values_stripped() -> None:
    """R1: envs configuradas viram rótulos com strip, sem outra validação."""
    assert _parse_unit_labels(
        {"HMD_UNIT_1_LABEL": "  Unidade A  ", "HMD_UNIT_2_LABEL": "Unidade Satélite"}
    ) == {1: "Unidade A", 2: "Unidade Satélite"}


def test_settings_unit_labels_resolved_from_env() -> None:
    """R1: em test.py ``UNIT_LABELS`` é PINADO nos canônicos (determinismo,
    imune a HMD_UNIT_*_LABEL no host); a resolução env→settings em si é
    coberta pelos testes puros de ``_parse_unit_labels`` acima."""
    assert settings.UNIT_LABELS == CANONICAL_LABELS


# ── R2: fonte única ``apps.cases.units`` ────────────────────────────────────


def test_unit_labels_read_settings_without_state() -> None:
    """R2: helper devolve os rótulos vigentes de ``settings.UNIT_LABELS``."""
    assert unit_labels() == dict(settings.UNIT_LABELS)
    assert unit_label(1) == settings.UNIT_LABELS[1]
    assert unit_label(2) == settings.UNIT_LABELS[2]


def test_unit_labels_follow_override() -> None:
    """R2: com ``UNIT_LABELS`` sobrescrito o helper lê o valor vigente."""
    with override_settings(UNIT_LABELS=CONFIGURED_LABELS):
        assert unit_labels() == CONFIGURED_LABELS
        assert unit_label(2) == "Unidade Satélite"


def test_unit_label_unknown_value_is_empty() -> None:
    """R2: unidade fora do mapa → vazio (fallback dos apresentadores atuais)."""
    assert unit_label(3) == ""


# ── R3: resposta final da unidade 2 interpola o rótulo ──────────────────────


@pytest.mark.django_db
def test_reply_unit_2_uses_configured_label(scheduler_user: User) -> None:
    """R3: com rótulo configurado, a resposta postada na unidade 2 o cita."""
    with override_settings(UNIT_LABELS=CONFIGURED_LABELS):
        case = _case_in_scheduler_requested(created_by=scheduler_user)
        confirm_case_scheduling(
            case,
            unit=2,
            scheduled_datetime=_future_datetime(hours=30),
            scheduled_location="Unidade 2 (internet)",
            user=scheduler_user,
            role=SCHEDULER_ROLE,
        )

    messages = _user_messages(case)
    assert len(messages) == 1
    assert cast(Any, messages[0]).body == (
        "Recusar o relatório — caso agendado na Unidade Satélite, que comunicará a Secretaria"
    )


@pytest.mark.django_db
def test_reply_unit_2_default_text_unchanged(scheduler_user: User) -> None:
    """R3: sem override (defaults) o texto canônico do plano §4 fica intacto."""
    case = _case_in_scheduler_requested(created_by=scheduler_user)
    confirm_case_scheduling(
        case,
        unit=2,
        scheduled_datetime=_future_datetime(hours=30),
        scheduled_location="Unidade 2 (internet)",
        user=scheduler_user,
        role=SCHEDULER_ROLE,
    )

    messages = _user_messages(case)
    assert len(messages) == 1
    body = cast(Any, messages[0]).body
    assert body == CANONICAL_UNIT_2_REPLY
    assert not body.endswith(".")


# ── R4: choices do formulário de confirmação ────────────────────────────────


def test_confirm_form_choices_use_configured_labels() -> None:
    """R4: com override, o campo ``unit`` renderiza as labels configuradas."""
    with override_settings(UNIT_LABELS=CONFIGURED_LABELS):
        form = SchedulerConfirmForm()
        choices = list(cast(Any, form.fields["unit"]).choices)
        rendered = str(form["unit"])
    assert choices == [(1, "Unidade 1"), (2, "Unidade Satélite")]
    assert "Unidade Satélite" in rendered


def test_confirm_form_default_choices_unchanged() -> None:
    """R4: sem override, as choices do campo ``unit`` seguem as atuais."""
    form = SchedulerConfirmForm()
    assert list(cast(Any, form.fields["unit"]).choices) == [(1, "Unidade 1"), (2, "Unidade 2")]


# ── Display: detail do agendador com labels configurados (belt-and-braces) ──


@pytest.mark.django_db
def test_scheduler_detail_uses_configured_labels(
    client: Client,
    nir_user: User,
    user_factory: Callable[..., User],
    scheduler_user: User,
) -> None:
    """Slice 002/belt-and-braces: bloco de agendamento E banner de
    intercorrência do detail do agendador exibem os labels configurados
    (apresentador e context processor, respectivamente)."""
    from django.urls import reverse

    case = _case_in_scheduler_requested(created_by=nir_user)
    confirm_case_scheduling(
        case,
        unit=2,
        scheduled_datetime=_future_datetime(days=2),
        scheduled_location="Sala de hemodinâmica",
        user=scheduler_user,
        role=SCHEDULER_ROLE,
    )
    client.force_login(scheduler_user)
    session = client.session
    session["active_role"] = SCHEDULER_ROLE
    session.save()

    with override_settings(UNIT_LABELS=CONFIGURED_LABELS):
        response = client.get(reverse("scheduler:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    # Bloco de agendamento + banner de intercorrência com o label CONFIGURADO.
    assert "Unidade Satélite" in body
    assert "confirmado na Unidade Satélite, que comunicará a Secretaria" in body
    # Thread (histórico append-only, design D3): a resposta postada ANTES do
    # override guarda o texto com os labels vigentes à época (canônicos) —
    # coexiste com o label configurado da exibição atual.
    assert "caso agendado na Unidade 2, que comunicará a Secretaria" in body
