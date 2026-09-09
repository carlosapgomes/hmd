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

import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING

from django.core.files.storage import default_storage
from django.db import transaction

from apps.cases.communications import post_user_communication
from apps.cases.models import Case, CaseProcedure, CaseStatus, DoctorDisposition
from apps.cases.procedure_catalog import PROCEDURE_PROFILES, get_procedure_profile

if TYPE_CHECKING:
    from apps.accounts.models import User

logger = logging.getLogger(__name__)

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


# Campos zerados pela limpeza do ack (design D2 — minimização): textos e
# artefatos LLM/pseudônimos são PHI derivada (ou token→valor do perímetro) e
# saem do caso; ``anonymization_report`` fica (métrica de auditoria, não-PHI).
# Cada entrada é o valor "vazio" do tipo do campo (``""`` texto / ``{}`` JSON).
_CLEANED_EMPTY_VALUES: dict[str, object] = {
    "extracted_text": "",
    "anonymized_text": "",
    "pseudonym_map": {},
    "structured_data": {},
    "summary_text": "",
    "suggested_action": {},
    "policy_result": {},
}


def _delete_files_best_effort(file_names: Iterable[str]) -> None:
    """Remove os arquivos físicos best-effort (D2), com log em falha.

    Roda APÓS o commit (``transaction.on_commit``): o rollback do banco não
    reverte o filesystem, então a deleção física nunca pode preceder o commit
    das deleções de row. Um orfão eventual em falha é resíduo aceito e
    registrado no log — nunca propaga a exceção ao chamador.
    """
    for name in file_names:
        try:
            default_storage.delete(name)
        except OSError:
            logger.warning(
                "falha ao remover arquivo físico da limpeza do caso: %s", name, exc_info=True
            )


def _clean_acknowledged_clinical_data(case: Case) -> None:
    """Minimização do D2 no caso sob lock (D2): rows de documento/anexo e campos.

    Deleta as rows ``CaseDocument`` E ``CaseAttachment`` (os nomes dos arquivos
    já foram coletados ANTES pelo chamador) e zera os 7 campos clínicos/LLM
    (``extracted_text``/``anonymized_text``/``pseudonym_map``/``structured_data``/
    ``summary_text``/``suggested_action``/``policy_result``). Preserva
    identificação (``patient_*``/``agency_record_number``),
    ``anonymization_report``, rows ``CaseProcedure``/``CaseEvent``,
    comunicações e ``scheduled_*``. O ``save`` com ``update_fields`` é
    obrigatório para não vazar a zeragem para fora do conjunto do D2 e para
    manter a instância sob lock coerente com o banco (as transições FSM
    seguintes salvam o objeto inteiro).
    """
    case.documents.all().delete()
    case.attachments.all().delete()
    for field_name, empty_value in _CLEANED_EMPTY_VALUES.items():
        setattr(case, field_name, empty_value)
    case.save(update_fields=[*_CLEANED_EMPTY_VALUES])


def acknowledge_case_receipt(case: Case, *, user: User, role: str) -> None:
    """Confirma o recebimento da resposta final e limpa o caso (R1–R3, D1/D2).

    Exige ``FINAL_REPLY_POSTED`` e que ``user`` seja o CRIADOR do caso (escopo
    por criador do design D1) — erros ``ValueError`` nomeados e distintos,
    antes de qualquer escrita. No MESMO ``atomic`` com ``select_for_update``:
    coleta os nomes dos arquivos das rows ``CaseDocument`` e ``CaseAttachment``
    ANTES do delete → encadeia as transições públicas ``nir_acknowledge`` →
    ``start_cleaning`` → a limpeza (D2: rows de documento + anexos + campos
    clínicos/LLM) → ``complete_cleaning`` — 3 eventos ``CASE_STATUS_*``
    (``AWAITING_NIR_ACK``, ``CLEANING`` e ``CLEANED``, transitórios no mesmo
    atomic). Os arquivos físicos (documentos E anexos) são removidos
    best-effort apenas APÓS o commit (``transaction.on_commit``; log em
    falha); arquivos de outros casos nunca são tocados. Quem chama (a view do
    intake, slice 003) traduz os erros em mensagem + redirect, nunca 500.
    """
    with transaction.atomic():
        locked = Case.objects.select_for_update().get(pk=case.pk)
        if locked.status != CaseStatus.FINAL_REPLY_POSTED:
            raise ValueError(
                f"ciência do NIR indisponível no estado {locked.status!r} — "
                "esperado FINAL_REPLY_POSTED"
            )
        if locked.created_by_id != user.pk:
            raise ValueError(
                "confirmação de recebimento restrita ao criador do caso — caso de outro usuário"
            )
        file_names = [
            document.file.name for document in locked.documents.only("file") if document.file.name
        ]
        file_names.extend(
            attachment.file.name
            for attachment in locked.attachments.only("file")
            if attachment.file.name
        )
        locked.nir_acknowledge(user=user, role=role)
        locked.start_cleaning(user=user, role=role)
        _clean_acknowledged_clinical_data(locked)
        locked.complete_cleaning(user=user, role=role)
        transaction.on_commit(lambda: _delete_files_best_effort(file_names))


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
