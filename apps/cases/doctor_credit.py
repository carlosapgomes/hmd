"""Linha de crédito do médico nas respostas finais ao NIR (change registro-profissional, D2).

Helper único e puro (sem escrita, sem sessão) que compõe a última linha do
corpo das respostas finais (confirmações de agendamento e negativa médica) a
partir de ``case.doctor_decided_by`` — nunca do ``user`` que opera o post
(agendador nas confirmações, médico na negativa; a linha referencia o
DECISOR). Fonte única consumida por ``apps/scheduler/services.py`` e
``apps/cases/closure.py`` (dependência unidirecional scheduler/closure → cases):

- com registro: ``Médico responsável: {display_name} — {registro}``;
- sem registro: ``Médico responsável: {display_name}`` (degradação sem falha);
- decisor nulo: ``""`` (linha omitida; nunca bloqueia a postagem).

O registro formatado recebe ``.strip()`` defensivo — o número do conselho é
texto livre preenchido à mão no admin.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.cases.models import Case


def doctor_credit_line(case: Case) -> str:
    """Linha ``Médico responsável: ...`` do decisor do caso — ``""`` se nulo."""
    doctor = case.doctor_decided_by
    if doctor is None:
        return ""
    registration = doctor.professional_registration_display.strip()
    if registration:
        return f"Médico responsável: {doctor.display_name} — {registration}"
    return f"Médico responsável: {doctor.display_name}"
