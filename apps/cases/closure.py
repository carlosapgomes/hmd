"""Serviços de fechamento do ciclo do caso (nir-result-closure, design D1).

Módulo de serviços do núcleo (``apps.cases`` — não do app de um papel): o
fechamento é ciclo de vida do caso, consumido pelas views do doctor (negativa
médica, slice 001), do intake (ciência do NIR, slice 002) e do painel
(encerramento administrativo, slice 001 do change painel-lista-encerramento).
Segue o padrão
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
from django.utils import timezone

from apps.cases.communications import post_user_communication
from apps.cases.locks import case_has_lock, force_release_case_lock
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


# ── Encerramento administrativo (change painel-lista-encerramento, slice 001) ──

# Motivo canônico da liberação FORÇADA do lock no encerramento administrativo
# (payload do ``CASE_LOCK_RELEASED`` de ``apps/cases/locks.py``).
FORCED_RELEASE_REASON = "administrative_closure"

# Catálogo FECHADO dos motivos do encerramento administrativo: código canônico →
# rótulo pt-BR. Código fora do catálogo é recusado pelo serviço — o conjunto é
# contrato (o select do painel, slice 003, deriva daqui).
ADMINISTRATIVE_CLOSURE_REASONS: dict[str, str] = {
    "processing_error": "Erro de processamento",
    "llm_failure": "Falha do LLM",
    "system_bug": "Bug do sistema",
    "stuck_lock": "Lock travado",
    "duplicate_reprocess": "Duplicado/reapresentação manual",
    "other": "Outro",
}

# Choices (código, rótulo) do catálogo para os formulários do painel.
_REASON_CHOICES: list[tuple[str, str]] = list(ADMINISTRATIVE_CLOSURE_REASONS.items())

# Papéis com o encerramento administrativo (defesa em profundidade: a rota do
# painel aplica ``role_required("manager", "admin")``).
ADMINISTRATIVE_CLOSURE_ROLES = frozenset({"manager", "admin"})

# Prefixo dos contextos de lock dos workers (``worker_pdf`` /
# ``worker_anonymization`` / ``worker_llm`` — tasks e orquestrador do pipeline):
# base da recusa fail-closed do encerramento.
WORKER_LOCK_CONTEXT_PREFIX = "worker_"


def _lock_snapshot(case: Case) -> dict[str, object]:
    """Snapshot dos campos de lock para o payload do encerramento (R1).

    Colhido ANTES do ``force_release_case_lock``, que zera os campos de lock na
    instância do caso. A régua de "havia lock" é a mesma de
    ``apps/cases/locks.py::_has_lock`` (portador, posse/lease ou contexto): sem
    nenhum desses campos o snapshot é vazio e nenhum release é gravado.
    """
    had_lock = case_has_lock(case)
    previous_until = case.locked_until
    return {
        "had_lock": had_lock,
        "previous_lock_context": case.lock_context,
        "previous_lock_until": previous_until.isoformat() if previous_until is not None else None,
    }


def administratively_close_case(
    *,
    case: Case,
    user: User,
    active_role: str,
    reason_code: str,
    reason_text: str,
) -> Case:
    """Encerra administrativamente o caso (R1–R3, design D2).

    Transição excepcional do supervisor do painel (manager/admin) para
    ``CLEANED`` de QUALQUER estado não-CLEANED, com motivo auditável. Validações
    nomeadas ``ValueError`` ANTES de qualquer escrita: catálogo, texto
    obrigatório (não vazio) e papel ativo. Dentro do ``atomic`` (linha relida sob
    ``select_for_update``): recusa de caso já ``CLEANED`` e recusa FAIL-CLOSED de
    lock de worker com lease VIVA (``locked_until`` no futuro + contexto
    ``worker_*`` — o worker em voo segue dono do caso) → e então, nesta
    ordem: snapshot do lock → ``force_release_case_lock`` (evento de release com
    o autor do encerramento; cópia dos campos limpos para a instância, para o
    ``save()`` full da transição não ressuscitar o lock) → coleta dos nomes dos
    arquivos ANTES do delete → minimização dos dados clínicos (MESMA do
    encerramento por ciência: rows de documento/anexo + 7 campos + arquivos
    físicos) → op pública ``Case.administratively_close`` (CASE_STATUS_CLEANED +
    CASE_ADMINISTRATIVELY_CLOSED) → ``transaction.on_commit`` da deleção física
    best-effort. Lock com lease EXPIRADA (worker travado) NÃO recusa — é o caso
    de uso do motivo ``stuck_lock``. Quem chama (a view do painel, slice 003)
    traduz ``ValueError`` em mensagem + re-render, nunca 500; o caso atualizado
    (CLEANED, sem lock) é devolvido.
    """
    if reason_code not in ADMINISTRATIVE_CLOSURE_REASONS:
        raise ValueError(f"motivo de encerramento administrativo fora do catálogo: {reason_code!r}")
    reason_text = reason_text.strip()
    if not reason_text:
        raise ValueError("texto do motivo do encerramento administrativo é obrigatório")
    if active_role not in ADMINISTRATIVE_CLOSURE_ROLES:
        raise ValueError(
            "encerramento administrativo restrito aos papéis manager/admin — "
            f"papel ativo {active_role!r}"
        )

    with transaction.atomic():
        locked = Case.objects.select_for_update().get(pk=case.pk)
        if locked.status == CaseStatus.CLEANED:
            raise ValueError("caso já encerrado — encerramento administrativo indisponível")
        now = timezone.now()
        if (
            locked.locked_until is not None
            and locked.locked_until > now
            and locked.lock_context.startswith(WORKER_LOCK_CONTEXT_PREFIX)
        ):
            raise ValueError(
                "caso em processamento; aguarde a task terminar ou trate o lock travado"
            )
        lock_snapshot = _lock_snapshot(locked)
        force_release_case_lock(locked, reason=FORCED_RELEASE_REASON, user=user, role=active_role)
        file_names = [
            document.file.name for document in locked.documents.only("file") if document.file.name
        ]
        file_names.extend(
            attachment.file.name
            for attachment in locked.attachments.only("file")
            if attachment.file.name
        )
        _clean_acknowledged_clinical_data(locked)
        locked.administratively_close(
            user=user,
            role=active_role,
            reason_code=reason_code,
            reason_text=reason_text,
            lock_snapshot=lock_snapshot,
        )
        transaction.on_commit(lambda: _delete_files_best_effort(file_names))

    case.refresh_from_db()
    return case
