"""Formulário da decisão médica por procedimento (doctor-queue-decision, slice 004, R1/D6).

``DoctorDecisionForm`` é dinâmico sobre os tipos **declarados** do caso
(``get_declared_procedure_types``): para cada tipo declarado nascem dois
campos — ``procedure_<type>__disposition`` (``approved|denied``; sem opção
"sem decisão") e ``procedure_<type>__reason`` (textarea, opcional na
aprovação). A validação é por procedimento e fail-closed (R1): todo
procedimento declarado exige disposição e a negativa exige motivo — o erro de
campo nomeia o procedimento pelo rótulo do catálogo. Aprovação sem motivo é
válida (o motivo livre é do médico, não exigência de domínio).

O formulário NÃO persiste nada: valida e expõe o mapa tipo → (disposição,
motivo) via ``decisions()`` para o serviço atômico do change 03
(``apps.cases.procedures.record_doctor_procedure_decisions``). Os campos de
cada procedimento são agrupados para renderização por
``procedure_fields()``.
"""

from __future__ import annotations

from typing import Any

from django import forms

from apps.cases.models import Case, DoctorDisposition
from apps.cases.procedure_catalog import PROCEDURE_PROFILES
from apps.cases.procedures import get_declared_procedure_types

# Perfil do catálogo por tipo (rótulo/subtipo das seções de decisão).
_LABEL_BY_TYPE: dict[str, str] = {
    profile.procedure_type: profile.label for profile in PROCEDURE_PROFILES
}
_SUBTYPE_BY_TYPE: dict[str, str] = {
    profile.procedure_type: profile.doctor_subtipo for profile in PROCEDURE_PROFILES
}

# Nomes dos campos dinâmicos por procedimento declarado (R1): a convenção usa
# ``__`` como separador entre o tipo e a fatia do formulário (o ats-web usa
# ``_`` — aqui deliberado pelo design D6, livre no HMD).
_DISPOSITION_SUFFIX = "__disposition"
_REASON_SUFFIX = "__reason"

# Disposições possíveis por procedimento (R1): sem opção "sem decisão".
_DISPOSITION_CHOICES: list[tuple[str, str]] = [
    ("", "— selecionar —"),
    (DoctorDisposition.APPROVED, "Aprovar"),
    (DoctorDisposition.DENIED, "Negar"),
]


class DoctorDecisionForm(forms.Form):
    """Decisão médica por procedimento declarado (validação por componente).

    Campos dinâmicos no ``__init__`` sobre ``get_declared_procedure_types``:
    a decisão é sempre **por procedimento** (o serviço do change 03 deriva o
    desfecho do caso: todos negados → ``DOCTOR_DENIED``; ≥1 aprovado →
    ``SCHEDULER_REQUESTED``). ``decisions()`` entrega o mapa validado para o
    serviço; ``procedure_fields()`` expõe os campos agrupados por procedimento
    para o template.
    """

    case: Case | None
    declared_types: tuple[str, ...]

    def __init__(self, *args: Any, case: Case | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.case = case
        declared_types = tuple(get_declared_procedure_types(case)) if case is not None else ()
        self.declared_types = declared_types
        for procedure_type in declared_types:
            self.fields[self._field_name(procedure_type, _DISPOSITION_SUFFIX)] = forms.ChoiceField(
                label="Decisão",
                choices=_DISPOSITION_CHOICES,
                required=False,
            )
            self.fields[self._field_name(procedure_type, _REASON_SUFFIX)] = forms.CharField(
                label="Motivo",
                widget=forms.Textarea(attrs={"rows": 2}),
                required=False,
            )

    @staticmethod
    def _field_name(procedure_type: str, suffix: str) -> str:
        """Nome canônico do campo dinâmico de um procedimento declarado (R1)."""
        return f"procedure_{procedure_type}{suffix}"

    def clean(self) -> dict[str, Any]:
        """Valida por procedimento declarado (fail-closed, R1).

        Todo procedimento exige disposição; negativa exige motivo — o erro é
        adicionado ao campo do procedimento, nomeando-o pelo rótulo do
        catálogo. Aprovação sem motivo passa.
        """
        cleaned = super().clean()
        if cleaned is None:
            cleaned = {}
        for procedure_type in self.declared_types:
            label = _LABEL_BY_TYPE.get(procedure_type, procedure_type)
            disposition = str(
                cleaned.get(self._field_name(procedure_type, _DISPOSITION_SUFFIX)) or ""
            )
            reason = str(
                cleaned.get(self._field_name(procedure_type, _REASON_SUFFIX)) or ""
            ).strip()
            if disposition == DoctorDisposition.DENIED:
                if not reason:
                    self.add_error(
                        self._field_name(procedure_type, _REASON_SUFFIX),
                        f"Informe o motivo da negativa de {label}.",
                    )
            elif disposition != DoctorDisposition.APPROVED:
                self.add_error(
                    self._field_name(procedure_type, _DISPOSITION_SUFFIX),
                    f"Defina a decisão para {label}.",
                )
        return cleaned

    def decisions(self) -> dict[str, tuple[str, str]]:
        """Mapa tipo → (disposição, motivo) validado para o serviço (R3/D6).

        Requer ``is_valid()`` (chamado pela view logo após a validação); o
        motivo sai normalizado (``strip``). A ordem de iteração é a declarada
        do caso — o serviço reordena pelo catálogo.
        """
        return {
            procedure_type: (
                str(self.cleaned_data[self._field_name(procedure_type, _DISPOSITION_SUFFIX)] or ""),
                str(
                    self.cleaned_data[self._field_name(procedure_type, _REASON_SUFFIX)] or ""
                ).strip(),
            )
            for procedure_type in self.declared_types
        }

    def procedure_fields(self) -> list[dict[str, object]]:
        """Campos ligados agrupados por procedimento declarado (template).

        Cada item carrega o rótulo/subtipo do catálogo e os dois BoundFields
        (decisão + motivo) para a renderização agrupada por procedimento.
        """
        return [
            {
                "label": _LABEL_BY_TYPE.get(procedure_type, procedure_type),
                "subtype": _SUBTYPE_BY_TYPE.get(procedure_type, ""),
                "disposition_field": self[self._field_name(procedure_type, _DISPOSITION_SUFFIX)],
                "reason_field": self[self._field_name(procedure_type, _REASON_SUFFIX)],
            }
            for procedure_type in self.declared_types
        ]
