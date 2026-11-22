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
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from apps.cases.communications import post_user_communication
from apps.cases.events import CaseEventType
from apps.cases.locks import case_has_lock, force_release_case_lock
from apps.cases.models import (
    ActorType,
    Case,
    CaseEvent,
    CaseProcedure,
    CaseStatus,
    DoctorDisposition,
    SchedulingUnit,
)
from apps.cases.procedure_catalog import PROCEDURE_PROFILES, get_procedure_profile

if TYPE_CHECKING:
    from apps.accounts.models import User

logger = logging.getLogger(__name__)

# Intercorrência pós-encerramento (change paridade-ats, slice 002, D3/D4):
# status compacto da issue ativa — sem espelhar os ~12 campos do ats-web.
POST_CLOSURE_ISSUE_STATUS_OPENED = "opened"

# Motivos legíveis de inelegibilidade (R1): um por ramo, sem PHI.
_POST_CLOSURE_INELIGIBLE_ACTIVE_ISSUE = "Já existe uma intercorrência ativa neste caso."
_POST_CLOSURE_INELIGIBLE_NOT_CLEANED = "Caso não está encerrado (CLEANED)."
_POST_CLOSURE_INELIGIBLE_DATA_DISPOSED = (
    "Os dados clínicos do caso foram dispostos (minimização ou purga) — reabertura indisponível."
)
_POST_CLOSURE_INELIGIBLE_UNIT_2 = (
    "Intercorrência pós-encerramento disponível apenas para agendamento na unidade 1."
)
_POST_CLOSURE_INELIGIBLE_NO_SCHEDULING = "Caso sem agendamento confirmado na unidade 1."

# Comunicação da abertura ao NIR (D4): motivo de texto livre interpolado no
# template — reuso do padrão do desmarcar (decisão do dono, D3); o caso
# retorna à fila para novo agendamento.
POST_CLOSURE_REOPEN_TEMPLATE = (
    "Intercorrência pós-encerramento — agendamento desmarcado ({reason}); "
    "caso retorna à fila para novo agendamento"
)

# Campos tocados pela abertura (D4): os 5 de agendamento corrente são limpos
# (mesmos do desmarcar em apps/scheduler; o histórico vive nos eventos) e o
# ``scheduling_reopen_reason`` + as colunas da issue são persistidos.
_POST_CLOSURE_REOPEN_FIELDS: tuple[str, ...] = (
    "scheduled_unit",
    "scheduled_datetime",
    "scheduled_location",
    "scheduled_by",
    "scheduled_decided_at",
    "scheduling_reopen_reason",
    "post_schedule_issue_status",
    "post_schedule_issue_reason",
    "post_schedule_issue_opened_by",
    "post_schedule_issue_opened_at",
)

# Vias de fechamento da issue registradas no evento de auditoria (D9).
_POST_CLOSURE_CLOSE_VIA_ACK = "acknowledge"
_POST_CLOSURE_CLOSE_VIA_ADMINISTRATIVE = "administrative_closure"

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


def _minimization_enabled() -> bool:
    """Modo de minimização ativo? (ADR-0010/D2 — lido no momento do serviço).

    A decisão de modo mora AQUI, nos entry points — nunca dentro de
    ``_clean_acknowledged_clinical_data``, que permanece incondicional
    (semântica de força total reusada pela purga retroativa do slice 003;
    se lesse o flag, a purga nasceria morta sob o default ``False``).
    """
    return bool(getattr(settings, "CASE_CLOSURE_MINIMIZATION_ENABLED", False))


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

    Escopo de equipe (visibilidade-por-equipe, slice 004): qualquer ``nir``
    com vínculo real — criador ou não — confirma; o OPERADOR é o ator das
    transições/eventos. Exige ``FINAL_REPLY_POSTED`` (``ValueError`` nomeado,
    antes de qualquer escrita) e re-check de papel ``nir`` DENTRO da
    transação (``PermissionError`` sem efeito — defesa em profundidade, mesmo
    padrão do ``_assert_nir_role`` dos serviços do gate). No MESMO ``atomic``
    com ``select_for_update``:
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
        if role != "nir":
            raise PermissionError("Confirmação de recebimento restrita ao papel nir.")
        if user is None or not user.roles.filter(name="nir").exists():
            raise PermissionError("Confirmação de recebimento restrita ao papel nir.")
        # D2/ADR-0010: retenção ativa pula a limpeza E a coleta/deleção de
        # arquivos; minimização ativa segue o caminho original byte-a-byte.
        # O estado transitório CLEANING percorre o fluxo sem efeito sobre
        # dados em retenção — aceito porque o conjunto de estados é contrato.
        minimize = _minimization_enabled()
        file_names: list[str] = []
        if minimize:
            file_names = [
                document.file.name
                for document in locked.documents.only("file")
                if document.file.name
            ]
            file_names.extend(
                attachment.file.name
                for attachment in locked.attachments.only("file")
                if attachment.file.name
            )
        locked.nir_acknowledge(user=user, role=role)
        locked.start_cleaning(user=user, role=role)
        if minimize:
            _clean_acknowledged_clinical_data(locked)
            # D2 (slice 002): o marcador de disposição é gravado no MESMO
            # atomic da minimização — a elegibilidade lê só esta coluna.
            locked.clinical_data_disposed_at = timezone.now()
            locked.save(update_fields=["clinical_data_disposed_at"])
        # R3/D9 (slice 002): QUALQUER chegada a CLEANED zera a issue ativa no
        # mesmo atomic, com evento de auditoria próprio quando havia issue.
        _close_post_closure_issue(
            case=locked, user=user, role=role, via=_POST_CLOSURE_CLOSE_VIA_ACK
        )
        locked.complete_cleaning(user=user, role=role)
        if minimize:
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


# ── Intercorrência pós-encerramento (change paridade-ats, slice 002, D2–D4) ──


def get_post_closure_issue_ineligibility_reason(case: Case) -> str:
    """Motivo legível da inelegibilidade à intercorrência — ``""`` se elegível (R1).

    Elegível: ``CLEANED`` + agendamento anterior confirmado na unidade 1 + dados
    clínicos retidos (coluna ``clinical_data_disposed_at`` NULA — D2, nunca
    proxy de campos) + sem issue ativa. A ordem dos ramos é contrato dos
    testes: issue ativa → não encerrado → dados dispostos → unidade 2 → sem
    agendamento confirmado.
    """
    if case.post_schedule_issue_status == POST_CLOSURE_ISSUE_STATUS_OPENED:
        return _POST_CLOSURE_INELIGIBLE_ACTIVE_ISSUE
    if case.status != CaseStatus.CLEANED:
        return _POST_CLOSURE_INELIGIBLE_NOT_CLEANED
    if case.clinical_data_disposed_at is not None:
        return _POST_CLOSURE_INELIGIBLE_DATA_DISPOSED
    if case.scheduled_unit == SchedulingUnit.UNIT_2:
        return _POST_CLOSURE_INELIGIBLE_UNIT_2
    if case.scheduled_unit != SchedulingUnit.UNIT_1:
        return _POST_CLOSURE_INELIGIBLE_NO_SCHEDULING
    return ""


def is_post_closure_issue_eligible(case: Case) -> bool:
    """O caso pode abrir intercorrência pós-encerramento? (R1, fail-closed)."""
    return get_post_closure_issue_ineligibility_reason(case) == ""


def _scheduling_snapshot(case: Case) -> dict[str, object]:
    """Snapshot administrativo do agendamento anterior (D4, sem PHI).

    Unidade/data-hora/local — os mesmos dados administrativos do precedente
    vigente do evento de reabertura do desmarcar; nada clínico vai ao payload.
    """
    scheduled_at = case.scheduled_datetime
    return {
        "scheduled_unit": case.scheduled_unit,
        "scheduled_datetime": scheduled_at.isoformat() if scheduled_at is not None else None,
        "scheduled_location": case.scheduled_location,
    }


def _close_post_closure_issue(*, case: Case, user: User, role: str, via: str) -> bool:
    """Zera a issue ativa no atomic corrente, com evento de auditoria (R3/D9).

    Chamada DENTRO do atomic de cada chegada a ``CLEANED`` (ciência do novo
    ciclo OU encerramento administrativo). Sem issue ativa é no-op total (sem
    save, sem evento). Com issue ativa: zera as 4 colunas e grava
    ``CASE_POST_CLOSURE_ISSUE_CLOSED`` com via/autor/papel. Devolve se havia
    issue ativa.
    """
    if case.post_schedule_issue_status != POST_CLOSURE_ISSUE_STATUS_OPENED:
        return False
    case.post_schedule_issue_status = ""
    case.post_schedule_issue_reason = ""
    case.post_schedule_issue_opened_by = None
    case.post_schedule_issue_opened_at = None
    case.save(
        update_fields=[
            "post_schedule_issue_status",
            "post_schedule_issue_reason",
            "post_schedule_issue_opened_by",
            "post_schedule_issue_opened_at",
        ]
    )
    CaseEvent.objects.create(
        case_id=case.case_id,
        event_type=CaseEventType.CASE_POST_CLOSURE_ISSUE_CLOSED,
        actor_type=ActorType.USER,
        actor=user,
        actor_role=role,
        payload={
            "via": via,
            "by": user.username,
            "role": role,
        },
    )
    return True


def open_post_closure_issue(case: Case, *, reason: str, user: User, role: str) -> Case:
    """Abre intercorrência pós-encerramento e reabre o caso ao agendador (R2).

    Motivo de texto livre obrigatório (reuso do padrão do desmarcar — decisão
    do dono, D3: sem catálogo, sem mensagem separada). Validações nomeadas
    ``ValueError`` ANTES de qualquer escrita (motivo vazio, caso inelegível).
    No MESMO atomic com ``select_for_update``: revalidação de elegibilidade
    sob lock (a corrida abertura×encerramento-administrativo resolve aqui — o
    perdedor falha sem escrita parcial) → transição ``reopen_post_closure``
    (``CLEANED → AWAITING_SCHEDULING``; o motivo entra no payload do evento
    ``CASE_STATUS_AWAITING_SCHEDULING``) → limpeza dos 5 campos de
    agendamento com snapshot prévio + persistência do motivo e da issue →
    evento ``CASE_POST_CLOSURE_ISSUE_OPENED`` (motivo + snapshot
    administrativo) → comunicação ao NIR com o motivo interpolado. As
    notificações (criador + fan-out dos agendadores, R4) disparam pelo signal
    do evento de transição e jamais bloqueiam a abertura. Quem chama traduz
    ``ValueError`` em mensagem + redirect, nunca 500.
    """
    stripped_reason = reason.strip()
    if not stripped_reason:
        raise ValueError("informe o motivo da intercorrência pós-encerramento")
    with transaction.atomic():
        locked = Case.objects.select_for_update().get(pk=case.pk)
        ineligibility = get_post_closure_issue_ineligibility_reason(locked)
        if ineligibility:
            raise ValueError(
                f"Caso não elegível para intercorrência pós-encerramento: {ineligibility}"
            )
        snapshot = _scheduling_snapshot(locked)
        now = timezone.now()

        locked.reopen_post_closure(reason=stripped_reason, user=user, role=role)

        locked.scheduled_unit = None
        locked.scheduled_datetime = None
        locked.scheduled_location = ""
        locked.scheduled_by = None
        locked.scheduled_decided_at = None
        locked.scheduling_reopen_reason = stripped_reason
        locked.post_schedule_issue_status = POST_CLOSURE_ISSUE_STATUS_OPENED
        locked.post_schedule_issue_reason = stripped_reason
        locked.post_schedule_issue_opened_by = user
        locked.post_schedule_issue_opened_at = now
        locked.save(update_fields=_POST_CLOSURE_REOPEN_FIELDS)

        CaseEvent.objects.create(
            case_id=locked.case_id,
            event_type=CaseEventType.CASE_POST_CLOSURE_ISSUE_OPENED,
            actor_type=ActorType.USER,
            actor=user,
            actor_role=role,
            payload={
                "reason": stripped_reason,
                "scheduling_snapshot": snapshot,
            },
        )
        body = POST_CLOSURE_REOPEN_TEMPLATE.format(reason=stripped_reason)
        post_user_communication(locked, user=user, role=role, body=body)
    locked.refresh_from_db()
    return locked


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
        # D2/ADR-0010: retenção ativa pula a limpeza E a coleta/deleção de
        # arquivos; minimização ativa segue o caminho original byte-a-byte.
        minimize = _minimization_enabled()
        file_names: list[str] = []
        if minimize:
            file_names = [
                document.file.name
                for document in locked.documents.only("file")
                if document.file.name
            ]
            file_names.extend(
                attachment.file.name
                for attachment in locked.attachments.only("file")
                if attachment.file.name
            )
            _clean_acknowledged_clinical_data(locked)
            # D2 (slice 002): marcador de disposição no MESMO atomic.
            locked.clinical_data_disposed_at = timezone.now()
            locked.save(update_fields=["clinical_data_disposed_at"])
        locked.administratively_close(
            user=user,
            role=active_role,
            reason_code=reason_code,
            reason_text=reason_text,
            lock_snapshot=lock_snapshot,
        )
        # R3/D9 (slice 002): QUALQUER chegada a CLEANED zera a issue ativa no
        # mesmo atomic, com evento de auditoria próprio registrando quem a
        # fechou. A corrida abertura×encerramento é resolvida pelo
        # ``select_for_update`` + revalidação do ``open_post_closure_issue``.
        _close_post_closure_issue(
            case=locked,
            user=user,
            role=active_role,
            via=_POST_CLOSURE_CLOSE_VIA_ADMINISTRATIVE,
        )
        if minimize:
            transaction.on_commit(lambda: _delete_files_best_effort(file_names))

    case.refresh_from_db()
    return case


# ── Purga retroativa dos dados retidos (change retencao-auditoria-casos-encerrados, D4) ──


@dataclass(frozen=True)
class RetainedDataCounts:
    """O que seria purgado de um caso — contagens puras, sem alterar nada.

    Fonte do ``--dry-run`` e do serviço: nomes dos campos clínicos/LLM ainda não
    zerados (subconjunto dos 7 de ``_CLEANED_EMPTY_VALUES``) + rows de
    documento/anexo + arquivos físicos coletáveis. Nunca carrega conteúdo
    clínico nem nomes de arquivo — só contagens.
    """

    cleaned_fields: tuple[str, ...]
    documents: int
    attachments: int
    files: int


@dataclass(frozen=True)
class PurgeResult:
    """Resultado da purga de um caso — contagens para o comando reportar.

    ``files_attempted/files_deleted/files_failed`` distinguem a purga completa
    da incompleta (``files_failed > 0`` = órfão com PHI a tratar). ``cleaned_fields``
    são os nomes dos 7 campos zerados, na ordem de ``_CLEANED_EMPTY_VALUES``.
    """

    documents_deleted: int
    attachments_deleted: int
    files_attempted: int
    files_deleted: int
    files_failed: int
    cleaned_fields: tuple[str, ...]


def _retained_field_names(case: Case) -> list[str]:
    """Nomes dos campos clínicos/LLM ainda não zerados (D4, sem PHI).

    Cada entrada de ``_CLEANED_EMPTY_VALUES`` tem valor vazio ``""`` (texto)
    ou ``{}`` (JSON) — ambos falsy; qualquer valor truthy é dado retido.
    """
    return [name for name in _CLEANED_EMPTY_VALUES if getattr(case, name)]


def _retained_file_names(case: Case) -> list[str]:
    """Nomes de storage das rows de documento/anexo (coleta pré-delete do closure).

    Mesma coleta dos entry points de encerramento: só nomes de storage
    (UUID, sem PHI) das rows com ``file.name`` preenchido.
    """
    file_names = [
        document.file.name for document in case.documents.only("file") if document.file.name
    ]
    file_names.extend(
        attachment.file.name for attachment in case.attachments.only("file") if attachment.file.name
    )
    return file_names


def retained_data_counts(case: Case) -> RetainedDataCounts:
    """Contagens do que seria purgado do caso, sem alterar nada (``--dry-run``)."""
    return RetainedDataCounts(
        cleaned_fields=tuple(_retained_field_names(case)),
        documents=case.documents.count(),
        attachments=case.attachments.count(),
        files=len(_retained_file_names(case)),
    )


def case_has_retained_data(case: Case) -> bool:
    """O caso ainda tem dados retidos? (≥1 campo clínico/LLM ou row de anexo/documento).

    Critério de seleção do comando e guarda de idempotência do serviço: caso
    sem nada retido (já minimizado) é pulado, sem evento.
    """
    if _retained_field_names(case):
        return True
    return case.documents.exists() or case.attachments.exists()


def purge_closed_case_data(case: Case) -> PurgeResult:
    """Reaplica a minimização a um caso ``CLEANED`` retido (D4, slice 003).

    Serviço puro do módulo (uma fonte de semântica, sem duplicação contra
    ``_CLEANED_EMPTY_VALUES``): no MESMO atomic por caso do closure
    (``select_for_update`` + coleta pré-delete + ``_clean_acknowledged_clinical_data``)
    — falha no meio não deixa meia-purga do banco. Caso fora de ``CLEANED`` é
    recusado com ``ValueError`` nomeado; caso sem dados retidos é no-op
    (idempotência: devolve zeros, sem evento).

    **Commit real obrigatório** (D4): o atomic é ``durable=True`` — chamada
    dentro de transação externa levanta ``RuntimeError`` nomeado, porque a
    deleção física só pode ocorrer após commit real, nunca sob savepoint.
    O comando roda em autocommit; testes usam ``TransactionTestCase``.

    **Trilha em duas fases** (D4, ator Sistema: ``actor=None`` → trilha exibe
    "Sistema", ``actor_role="system"`` como os workers):

    1. evento de purga DENTRO do atomic durável, junto com a limpeza — payload
       NOMES dos campos zerados + ``documents_deleted``/``attachments_deleted``
       + ``files_attempted`` (nomes já coletados pré-delete); prova a destruição
       do banco atomicamente com ela (crash entre commit e evento é impossível);
    2. após a deleção física síncrona (ordem banco → arquivos), evento
       complementar de TIPO PRÓPRIO (``CASE_RETAINED_DATA_PURGE_OUTCOME``)
       SOMENTE quando ``files_attempted > 0`` — payload ``files_deleted``/``files_failed``. Crash antes dele deixa a
       destruição provada com desfecho desconhecido (aceito e documentado na D4).
       O ``on_commit`` não serve aqui: impede conhecer ``files_deleted``/
       ``files_failed`` no retorno e no payload. ``OSError`` vira falha
       contada + log sem PHI (referência + contagem + tipo da exceção, sem
       caminho/mensagem/traceback), nunca propaga.

    Payloads NUNCA carregam conteúdo clínico nem nomes de arquivo.
    """
    with transaction.atomic(durable=True):
        locked = Case.objects.select_for_update().get(pk=case.pk)
        if locked.status != CaseStatus.CLEANED:
            raise ValueError(
                f"purga retroativa restrita a casos CLEANED — estado {locked.status!r}"
            )
        counts = retained_data_counts(locked)
        if not counts.cleaned_fields and counts.documents == 0 and counts.attachments == 0:
            return PurgeResult(
                documents_deleted=0,
                attachments_deleted=0,
                files_attempted=0,
                files_deleted=0,
                files_failed=0,
                cleaned_fields=(),
            )
        file_names = _retained_file_names(locked)
        _clean_acknowledged_clinical_data(locked)
        # D2 (slice 002): a purga dispõe dados — grava o marcador no MESMO
        # atomic da limpeza; a elegibilidade lê só esta coluna.
        locked.clinical_data_disposed_at = timezone.now()
        locked.save(update_fields=["clinical_data_disposed_at"])
        files_attempted = len(file_names)
        CaseEvent.objects.create(
            case_id=locked.case_id,
            event_type=CaseEventType.CASE_RETAINED_DATA_PURGED,
            actor_type=ActorType.SYSTEM,
            actor=None,
            actor_role="system",
            payload={
                "cleaned_fields": list(counts.cleaned_fields),
                "documents_deleted": counts.documents,
                "attachments_deleted": counts.attachments,
                "files_attempted": files_attempted,
            },
        )

    files_deleted = 0
    files_failed = 0
    reference = locked.display_reference
    for name in file_names:
        try:
            default_storage.delete(name)
        except OSError as file_error:
            files_failed += 1
            logger.warning(
                "falha ao remover arquivo físico da purga do caso %s (contagem %d/%d, tipo %s)",
                reference,
                files_failed,
                files_attempted,
                type(file_error).__name__,
            )
        else:
            files_deleted += 1

    if files_attempted > 0:
        CaseEvent.objects.create(
            case_id=locked.case_id,
            event_type=CaseEventType.CASE_RETAINED_DATA_PURGE_OUTCOME,
            actor_type=ActorType.SYSTEM,
            actor=None,
            actor_role="system",
            payload={
                "files_deleted": files_deleted,
                "files_failed": files_failed,
            },
        )
    return PurgeResult(
        documents_deleted=counts.documents,
        attachments_deleted=counts.attachments,
        files_attempted=files_attempted,
        files_deleted=files_deleted,
        files_failed=files_failed,
        cleaned_fields=counts.cleaned_fields,
    )
