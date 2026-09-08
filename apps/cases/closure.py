"""Serviços de fechamento do ciclo do caso (nir-result-closure, design D1).

Módulo de serviços do núcleo (``apps.cases`` — não do app de um papel): o
fechamento é ciclo de vida do caso, consumido pelas views do doctor (negativa
médica, slice 001) e do intake (ciência do NIR, slice 002). Segue o padrão
transacional dos services existentes (``transaction.atomic()`` +
``select_for_update``, validações nomeadas ``ValueError`` **antes** de
qualquer escrita e transições via ops públicas da FSM) e o padrão de resposta
final ao NIR do agendamento (apps/scheduler): constante do módulo interpolada
posta como user message autoral na thread — nunca notice system.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import transaction

from apps.cases.communications import post_user_communication
from apps.cases.models import Case, CaseProcedure, CaseStatus, DoctorDisposition
from apps.cases.procedure_catalog import PROCEDURE_PROFILES, get_procedure_profile

if TYPE_CHECKING:
    from apps.accounts.models import User

# Resposta final da negativa médica ao NIR (D1, plano §6): o NIR é o
# destinatário e vê dados REAIS — o template é interpolado com um item por
# procedimento negado (label legível do catálogo + motivo real da row), na
# ordem canônica do catálogo.
DENIAL_REPLY_TEMPLATE = (
    "Resposta final da avaliação médica — procedimentos negados:\n{denied_items}"
)

# Ordem canônica dos itens da resposta: índice do tipo no registro do catálogo.
_CATALOG_ORDER: dict[str, int] = {
    profile.procedure_type: index for index, profile in enumerate(PROCEDURE_PROFILES)
}


def _denial_items(rows: list[CaseProcedure]) -> str:
    """Items do corpo da resposta: um por row negada, na ordem do catálogo."""
    ordered = sorted(rows, key=lambda row: _CATALOG_ORDER[row.procedure_type])
    return "\n".join(
        f"- {get_procedure_profile(row.procedure_type).label}: {row.doctor_reason}"
        for row in ordered
    )


def post_doctor_denial_reply(case: Case, *, user: User, role: str) -> None:
    """Publica a resposta final da negativa médica (R1/R2, slice 001).

    Exige o caso em ``DOCTOR_DENIED`` (negativa total da decisão do slice 003)
    e ao menos uma row negada (``doctor_disposition=denied``) para compor a
    resposta — erros ``ValueError`` nomeados sem qualquer efeito. No MESMO
    ``atomic`` com ``select_for_update``: transição pública
    ``DOCTOR_DENIED → FINAL_REPLY_POSTED`` (``post_final_reply``, ator =
    médico/papel recebidos) e o post da resposta ao NIR na thread
    (``post_user_communication``, autor = médico/papel ``doctor``) com
    ``DENIAL_REPLY_TEMPLATE`` interpolado com o label legível e o motivo real
    de cada procedimento negado. Falha no meio do atomic desfaz transição,
    evento e comunicação juntos — quem chama (a view ``doctor:case_decide``)
    traduz ``ValueError`` em mensagem + redirect, nunca 500.
    """
    with transaction.atomic():
        locked = Case.objects.select_for_update().get(pk=case.pk)
        if locked.status != CaseStatus.DOCTOR_DENIED:
            raise ValueError(
                f"resposta final de negação indisponível no estado {locked.status!r} — "
                "esperado DOCTOR_DENIED"
            )
        denied_rows = list(
            CaseProcedure.objects.filter(case=locked, doctor_disposition=DoctorDisposition.DENIED)
        )
        if not denied_rows:
            raise ValueError(
                "resposta final de negação sem procedimentos negados — "
                "rows inconsistentes com o estado DOCTOR_DENIED"
            )
        locked.post_final_reply(user=user, role=role)
        body = DENIAL_REPLY_TEMPLATE.format(denied_items=_denial_items(denied_rows))
        post_user_communication(locked, user=user, role=role, body=body)
