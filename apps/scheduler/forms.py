"""Formulários das ações do agendador (scheduler-multi-unit, slice 003, R4).

Três formulários por caso, sem persisência própria — validam e entregam os
dados normalizados para os serviços transacionais de ``apps/scheduler/
services.py`` (o atomic + transições FSM + comunicação ao NIR ficam no
serviço, nunca na view/form):

- ``SchedulerConfirmForm``: confirmar agendamento — unidade de destino (1|2),
  data + horário futuros e local. A validação espelha o serviço
  (``_validate_scheduled_unit``/``_validate_scheduled_datetime``): unidade
  fora de {1, 2} e data/hora no passado são rejeitadas com erro de campo antes
  de qualquer escrita; ``aware_scheduled_datetime()`` expõe o datetime aware
  (fuso local da aplicação) que o serviço recebe.
- ``SchedulerDenyForm``: negar agendamento — motivo obrigatório (após
  ``strip``), espelhando o ``deny_case_scheduling``.
- ``SchedulerReopenForm``: desmarcar por intercorrência (unidade 1) — motivo
  obrigatório, espelhando o ``reopen_scheduling_after_incident``.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from django import forms
from django.utils import timezone

from apps.cases.models import SchedulingUnit
from apps.cases.units import unit_label

# Mensagens de validação espelhando as do serviço (R4 — o form falha antes do
# serviço, com o mesmo critério de domínio).
UNIT_INVALID_MESSAGE = "Selecione uma unidade de destino válida (1 ou 2)."
FUTURE_DATETIME_MESSAGE = "Informe uma data/hora futura para o agendamento."
DENY_REASON_REQUIRED_MESSAGE = "Informe o motivo da negação do agendamento."
REOPEN_REASON_REQUIRED_MESSAGE = "Informe o motivo da intercorrência."


def _unit_choices() -> list[tuple[int, str]]:
    """Unidades de destino aceitas (R4/plano §4) com os rótulos vigentes.

    Callable (nunca resolvido no import): o Django avalia as choices na
    montagem do campo, então os rótulos vêm da fonte única a cada renderização
    — ``unit ∈ {1, 2}`` segue tipado em int para o serviço receber normalizado.
    """
    return [
        (SchedulingUnit.UNIT_1, unit_label(SchedulingUnit.UNIT_1)),
        (SchedulingUnit.UNIT_2, unit_label(SchedulingUnit.UNIT_2)),
    ]


def _required_text(reason: str, message: str) -> str:
    """Motivo normalizado (strip) e obrigatório — erro nomeado quando vazio."""
    stripped = reason.strip()
    if not stripped:
        raise forms.ValidationError(message)
    return stripped


class SchedulerConfirmForm(forms.Form):
    """Confirmação do agendamento: unidade + data/hora futura + local (R4)."""

    unit = forms.TypedChoiceField(
        coerce=int,
        choices=_unit_choices,
        label="Unidade de destino",
    )
    scheduled_date = forms.DateField(
        label="Data do agendamento",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    scheduled_time = forms.TimeField(
        label="Horário do agendamento",
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    scheduled_location = forms.CharField(
        max_length=200,
        label="Local",
        widget=forms.TextInput(attrs={"placeholder": "Ex.: Bloco B — Sala 2"}),
    )

    def clean_unit(self) -> int:
        """Unidade fora de {1, 2} → erro (mesmo critério do serviço)."""
        unit = int(self.cleaned_data["unit"])
        if unit not in (SchedulingUnit.UNIT_1, SchedulingUnit.UNIT_2):
            raise forms.ValidationError(UNIT_INVALID_MESSAGE)
        return unit

    def clean(self) -> dict[str, Any]:
        """Data/hora no passado → erro (espelho do ``confirm_case_scheduling``)."""
        cleaned = super().clean()
        if cleaned is None:
            cleaned = {}
        aware = self.aware_scheduled_datetime(cleaned)
        if aware is not None and aware < timezone.now():
            self.add_error("scheduled_time", FUTURE_DATETIME_MESSAGE)
        return cleaned

    def aware_scheduled_datetime(self, cleaned: dict[str, Any] | None = None) -> datetime | None:
        """Datetime aware (fuso local da aplicação) combinando data + horário.

        O serviço exige datetime aware e rejeita valor no passado; o form
        entrega o valor já no fuso local para a mesma regra rodar no atomic.
        """
        data = cleaned if cleaned is not None else self.cleaned_data
        date_value = data.get("scheduled_date")
        time_value = data.get("scheduled_time")
        if not isinstance(date_value, date) or not isinstance(time_value, time):
            return None
        return timezone.make_aware(datetime.combine(date_value, time_value))


class SchedulerDenyForm(forms.Form):
    """Negação do agendamento com motivo obrigatório (R4)."""

    reason = forms.CharField(
        label="Motivo da negação",
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def clean_reason(self) -> str:
        return _required_text(self.cleaned_data["reason"], DENY_REASON_REQUIRED_MESSAGE)


class SchedulerReopenForm(forms.Form):
    """Desmarcar por intercorrência (unidade 1 apenas) com motivo obrigatório (R4)."""

    reason = forms.CharField(
        label="Motivo da intercorrência",
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def clean_reason(self) -> str:
        return _required_text(self.cleaned_data["reason"], REOPEN_REASON_REQUIRED_MESSAGE)
