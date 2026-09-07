"""Models de casos (change 03, slices 002–003, design D2/D4/D5/D7).

``Case`` é o núcleo enxuto: identificador UUID, FSM de 17 estados com
transições protegidas (``django-fsm-2``) e os campos de identidade/origem
(D7). ``CaseProcedure`` é a dimensão de procedimento por caso (D2) — 1–N rows
por caso, neutras, com unicidade (case, procedure_type). ``CaseEvent`` é a
trilha de auditoria append-only. Divergências deliberadas vs ats-web:
``actor_role`` (acréscimo HMD) e ``actor_type ∈ {user, system}``; gravação
direta — cada transição faz ``save()`` e cria o ``CaseEvent`` no mesmo
``transaction.atomic()``, sem o padrão pending-event + signal
``Case.post_save`` do ats-web (design D5).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django_fsm import RETURN_VALUE, FSMField, FSMModelMixin, transition

from apps.cases.events import case_status_event_type
from apps.cases.procedure_catalog import get_procedure_profile

if TYPE_CHECKING:
    from django.db.models.base import ModelBase

    from apps.accounts.models import User


class CaseStatus(models.TextChoices):
    """Os 17 estados do caso (contrato do design D4).

    O conjunto é contrato: mudanças futuras adicionam transições, nunca
    estados. Renomeados vs ats-web conforme D4 (o estado R2_POST_WIDGET do
    ats-web é deliberadamente extinto).
    """

    NEW = "NEW", "Novo"
    PDF_EXTRACTING = "PDF_EXTRACTING", "Extraindo PDF"
    ANONYMIZING = "ANONYMIZING", "Anonimizando"
    LLM_EXTRACTING = "LLM_EXTRACTING", "Extraindo via LLM"
    LLM_SUMMARIZING = "LLM_SUMMARIZING", "Sumarizando via LLM"
    AWAITING_DOCTOR = "AWAITING_DOCTOR", "Aguardando decisão médica"
    DOCTOR_DENIED = "DOCTOR_DENIED", "Negado pelo médico"
    DOCTOR_ACCEPTED = "DOCTOR_ACCEPTED", "Aceito pelo médico"
    SCHEDULER_REQUESTED = "SCHEDULER_REQUESTED", "Agendamento solicitado"
    AWAITING_SCHEDULING = "AWAITING_SCHEDULING", "Aguardando confirmação de agendamento"
    SCHEDULING_CONFIRMED = "SCHEDULING_CONFIRMED", "Agendamento confirmado"
    SCHEDULING_DENIED = "SCHEDULING_DENIED", "Agendamento negado"
    FAILED = "FAILED", "Falha de processamento"
    FINAL_REPLY_POSTED = "FINAL_REPLY_POSTED", "Resposta final publicada"
    AWAITING_NIR_ACK = "AWAITING_NIR_ACK", "Aguardando ciência do NIR"
    CLEANING = "CLEANING", "Limpando dados"
    CLEANED = "CLEANED", "Caso concluído"


class ActorType(models.TextChoices):
    """Autor do evento da trilha (design D5): usuário autenticado ou sistema."""

    USER = "user", "Usuário"
    SYSTEM = "system", "Sistema"


class DetectionStatus(models.TextChoices):
    """Projeção operacional da detecção da análise por procedimento (D2)."""

    PENDING = "pending", "Pendente"
    DETECTED = "detected", "Detectado"
    NOT_DETECTED = "not_detected", "Não detectado"


class DoctorDisposition(models.TextChoices):
    """Projeção operacional da decisão médica por procedimento (D2)."""

    PENDING = "pending", "Pendente"
    APPROVED = "approved", "Aprovado"
    DENIED = "denied", "Negado"


class Case(FSMModelMixin, models.Model):
    """Caso de regulação — entidade central (núcleo enxuto, design D7)."""

    case_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # FSM de 17 estados (design D4): atribuição direta é rejeitada
    # (``protected=True``); mudanças só pelas transições declaradas.
    status = FSMField(
        max_length=30,
        choices=CaseStatus.choices,
        default=CaseStatus.NEW,
        protected=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cases_created",
    )

    # Identidade/origem (gate 04 e prior-case 06).
    agency_record_number = models.CharField(max_length=20, blank=True)
    agency_record_extracted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Case {self.case_id} [{self.status}]"

    # ── Hooks FSM privados ─────────────────────────────────────────────────
    # Cada operação pública (tabela D4) tem um hook decorado com
    # ``@transition``: a lib valida o source e troca o estado. A operação
    # pública orquestra hook + save + evento no mesmo ``atomic`` (gravação
    # direta, R5) — o django-fsm troca o estado na memória após o corpo do
    # hook, então o save acontece no chamador da operação.

    @transition(field="status", source=CaseStatus.NEW, target=CaseStatus.PDF_EXTRACTING)
    def _fsm_start_pdf_extraction(self) -> None:
        """Hook FSM NEW → PDF_EXTRACTING."""

    @transition(field="status", source=CaseStatus.PDF_EXTRACTING, target=CaseStatus.ANONYMIZING)
    def _fsm_complete_pdf_extraction(self) -> None:
        """Hook FSM PDF_EXTRACTING → ANONYMIZING."""

    @transition(field="status", source=CaseStatus.ANONYMIZING, target=CaseStatus.ANONYMIZING)
    def _fsm_start_anonymization(self) -> None:
        """Hook FSM self ANONYMIZING (início do worker de anonimização)."""

    @transition(field="status", source=CaseStatus.ANONYMIZING, target=CaseStatus.LLM_EXTRACTING)
    def _fsm_complete_anonymization(self) -> None:
        """Hook FSM ANONYMIZING → LLM_EXTRACTING."""

    @transition(field="status", source=CaseStatus.LLM_EXTRACTING, target=CaseStatus.LLM_EXTRACTING)
    def _fsm_start_llm_extraction(self) -> None:
        """Hook FSM self LLM_EXTRACTING (início do worker de extração LLM)."""

    @transition(field="status", source=CaseStatus.LLM_EXTRACTING, target=CaseStatus.LLM_SUMMARIZING)
    def _fsm_complete_llm_extraction(self) -> None:
        """Hook FSM LLM_EXTRACTING → LLM_SUMMARIZING."""

    @transition(
        field="status", source=CaseStatus.LLM_SUMMARIZING, target=CaseStatus.LLM_SUMMARIZING
    )
    def _fsm_start_llm_summarization(self) -> None:
        """Hook FSM self LLM_SUMMARIZING (início do worker de sumarização LLM)."""

    @transition(
        field="status", source=CaseStatus.LLM_SUMMARIZING, target=CaseStatus.AWAITING_DOCTOR
    )
    def _fsm_complete_llm_summarization(self) -> None:
        """Hook FSM LLM_SUMMARIZING → AWAITING_DOCTOR."""

    @transition(
        field="status",
        source=[
            CaseStatus.PDF_EXTRACTING,
            CaseStatus.ANONYMIZING,
            CaseStatus.LLM_EXTRACTING,
            CaseStatus.LLM_SUMMARIZING,
        ],
        target=CaseStatus.FAILED,
    )
    def _fsm_fail_processing(self) -> None:
        """Hook FSM {processamento} → FAILED."""

    @transition(
        field="status",
        source=CaseStatus.AWAITING_DOCTOR,
        target=RETURN_VALUE(CaseStatus.DOCTOR_DENIED, CaseStatus.DOCTOR_ACCEPTED),
    )
    def _fsm_record_doctor_decision(self, accepted: bool) -> CaseStatus:
        """Hook FSM AWAITING_DOCTOR → DOCTOR_DENIED | DOCTOR_ACCEPTED."""
        return CaseStatus.DOCTOR_ACCEPTED if accepted else CaseStatus.DOCTOR_DENIED

    @transition(
        field="status", source=CaseStatus.DOCTOR_ACCEPTED, target=CaseStatus.SCHEDULER_REQUESTED
    )
    def _fsm_request_scheduling(self) -> None:
        """Hook FSM DOCTOR_ACCEPTED → SCHEDULER_REQUESTED."""

    @transition(
        field="status", source=CaseStatus.SCHEDULER_REQUESTED, target=CaseStatus.AWAITING_SCHEDULING
    )
    def _fsm_await_scheduling_confirmation(self) -> None:
        """Hook FSM SCHEDULER_REQUESTED → AWAITING_SCHEDULING."""

    @transition(
        field="status",
        source=CaseStatus.AWAITING_SCHEDULING,
        target=CaseStatus.SCHEDULING_CONFIRMED,
    )
    def _fsm_confirm_scheduling(self) -> None:
        """Hook FSM AWAITING_SCHEDULING → SCHEDULING_CONFIRMED."""

    @transition(
        field="status", source=CaseStatus.AWAITING_SCHEDULING, target=CaseStatus.SCHEDULING_DENIED
    )
    def _fsm_deny_scheduling(self) -> None:
        """Hook FSM AWAITING_SCHEDULING → SCHEDULING_DENIED."""

    @transition(
        field="status",
        source=[
            CaseStatus.DOCTOR_DENIED,
            CaseStatus.SCHEDULING_CONFIRMED,
            CaseStatus.SCHEDULING_DENIED,
        ],
        target=CaseStatus.FINAL_REPLY_POSTED,
    )
    def _fsm_post_final_reply(self) -> None:
        """Hook FSM {DOCTOR_DENIED, SCHEDULING_CONFIRMED, SCHEDULING_DENIED} → FINAL_REPLY_POSTED."""

    @transition(
        field="status", source=CaseStatus.FINAL_REPLY_POSTED, target=CaseStatus.AWAITING_NIR_ACK
    )
    def _fsm_nir_acknowledge(self) -> None:
        """Hook FSM FINAL_REPLY_POSTED → AWAITING_NIR_ACK."""

    @transition(field="status", source=CaseStatus.AWAITING_NIR_ACK, target=CaseStatus.CLEANING)
    def _fsm_start_cleaning(self) -> None:
        """Hook FSM AWAITING_NIR_ACK → CLEANING."""

    @transition(field="status", source=CaseStatus.CLEANING, target=CaseStatus.CLEANED)
    def _fsm_complete_cleaning(self) -> None:
        """Hook FSM CLEANING → CLEANED."""

    # ── Operações públicas (tabela D4) ─────────────────────────────────────
    # Cada transição recebe ``*, user=None, role=None`` (R5): ``role`` é o
    # papel ativo (views extraem da sessão; workers passam ``role="system"``).
    # A operação roda o hook FSM (TransitionNotAllowed em source inválido) e,
    # no mesmo ``transaction.atomic()``, persiste o caso e grava o evento
    # ``CASE_STATUS_<target>`` com payload ``{"source", "target", ...}``.

    def _run_transition(
        self,
        hook: Callable[[], None],
        *,
        user: User | None,
        role: str | None,
        extra_payload: dict[str, object] | None = None,
    ) -> None:
        source = str(self.status)
        with transaction.atomic():
            hook()
            self.save()
            payload: dict[str, object] = {"source": source, "target": str(self.status)}
            if extra_payload:
                payload.update(extra_payload)
            CaseEvent.objects.create(
                case_id=self.case_id,
                event_type=case_status_event_type(str(self.status)),
                actor_type=ActorType.USER if user is not None else ActorType.SYSTEM,
                actor=user,
                actor_role=role or "",
                payload=payload,
            )

    def start_pdf_extraction(self, *, user: User | None = None, role: str | None = None) -> None:
        """NEW → PDF_EXTRACTING (início do worker de extração de PDF)."""
        self._run_transition(self._fsm_start_pdf_extraction, user=user, role=role)

    def complete_pdf_extraction(self, *, user: User | None = None, role: str | None = None) -> None:
        """PDF_EXTRACTING → ANONYMIZING (fim do worker de PDF)."""
        self._run_transition(self._fsm_complete_pdf_extraction, user=user, role=role)

    def start_anonymization(self, *, user: User | None = None, role: str | None = None) -> None:
        """Self em ANONYMIZING: registra o início do worker de anonimização."""
        self._run_transition(self._fsm_start_anonymization, user=user, role=role)

    def complete_anonymization(self, *, user: User | None = None, role: str | None = None) -> None:
        """ANONYMIZING → LLM_EXTRACTING (fim do worker de anonimização)."""
        self._run_transition(self._fsm_complete_anonymization, user=user, role=role)

    def start_llm_extraction(self, *, user: User | None = None, role: str | None = None) -> None:
        """Self em LLM_EXTRACTING: registra o início do worker de extração LLM."""
        self._run_transition(self._fsm_start_llm_extraction, user=user, role=role)

    def complete_llm_extraction(self, *, user: User | None = None, role: str | None = None) -> None:
        """LLM_EXTRACTING → LLM_SUMMARIZING (fim da extração LLM)."""
        self._run_transition(self._fsm_complete_llm_extraction, user=user, role=role)

    def start_llm_summarization(self, *, user: User | None = None, role: str | None = None) -> None:
        """Self em LLM_SUMMARIZING: registra o início do worker de sumarização LLM."""
        self._run_transition(self._fsm_start_llm_summarization, user=user, role=role)

    def complete_llm_summarization(
        self, *, user: User | None = None, role: str | None = None
    ) -> None:
        """LLM_SUMMARIZING → AWAITING_DOCTOR (fim do pipeline de processamento)."""
        self._run_transition(self._fsm_complete_llm_summarization, user=user, role=role)

    def fail_processing(
        self,
        reason: str,
        *,
        user: User | None = None,
        role: str | None = None,
    ) -> None:
        """{PDF_EXTRACTING, ANONYMIZING, LLM_EXTRACTING, LLM_SUMMARIZING} → FAILED.

        O motivo da falha entra no payload do evento (R5).
        """
        self._run_transition(
            self._fsm_fail_processing,
            user=user,
            role=role,
            extra_payload={"reason": reason},
        )

    def record_doctor_decision(
        self,
        accepted: bool,
        *,
        user: User | None = None,
        role: str | None = None,
    ) -> None:
        """AWAITING_DOCTOR → DOCTOR_DENIED | DOCTOR_ACCEPTED (target pela decisão).

        Aceitação segue depois por ``request_scheduling`` — o encadeamento
        atômico é responsabilidade do serviço que registra a decisão
        (slice 003, R4), com os dois eventos no mesmo atomic.
        """
        self._run_transition(
            lambda: self._fsm_record_doctor_decision(accepted),
            user=user,
            role=role,
        )

    def request_scheduling(self, *, user: User | None = None, role: str | None = None) -> None:
        """DOCTOR_ACCEPTED → SCHEDULER_REQUESTED (caso aceito entra na fila de agendamento)."""
        self._run_transition(self._fsm_request_scheduling, user=user, role=role)

    def await_scheduling_confirmation(
        self, *, user: User | None = None, role: str | None = None
    ) -> None:
        """SCHEDULER_REQUESTED → AWAITING_SCHEDULING (pedido confirmado na fila)."""
        self._run_transition(self._fsm_await_scheduling_confirmation, user=user, role=role)

    def confirm_scheduling(self, *, user: User | None = None, role: str | None = None) -> None:
        """AWAITING_SCHEDULING → SCHEDULING_CONFIRMED (scheduler confirma)."""
        self._run_transition(self._fsm_confirm_scheduling, user=user, role=role)

    def deny_scheduling(self, *, user: User | None = None, role: str | None = None) -> None:
        """AWAITING_SCHEDULING → SCHEDULING_DENIED (scheduler nega)."""
        self._run_transition(self._fsm_deny_scheduling, user=user, role=role)

    def post_final_reply(self, *, user: User | None = None, role: str | None = None) -> None:
        """{DOCTOR_DENIED, SCHEDULING_CONFIRMED, SCHEDULING_DENIED} → FINAL_REPLY_POSTED."""
        self._run_transition(self._fsm_post_final_reply, user=user, role=role)

    def nir_acknowledge(self, *, user: User | None = None, role: str | None = None) -> None:
        """FINAL_REPLY_POSTED → AWAITING_NIR_ACK (ciência do NIR)."""
        self._run_transition(self._fsm_nir_acknowledge, user=user, role=role)

    def start_cleaning(self, *, user: User | None = None, role: str | None = None) -> None:
        """AWAITING_NIR_ACK → CLEANING (início da limpeza de dados)."""
        self._run_transition(self._fsm_start_cleaning, user=user, role=role)

    def complete_cleaning(self, *, user: User | None = None, role: str | None = None) -> None:
        """CLEANING → CLEANED (fim da limpeza de dados)."""
        self._run_transition(self._fsm_complete_cleaning, user=user, role=role)


def _validate_procedure_type_in_catalog(procedure_type: str) -> None:
    """Fail-fast (R1): tipo fora do catálogo é rejeitado nomeando o tipo.

    Validação única do campo ``procedure_type`` usada pela ``clean()`` e pelo
    ``save()``. ``bulk_create``/SQL cru seguem sendo o buraco documentado
    padrão do Django (métodos do model não rodam nesses caminhos) — fora de
    cobertura.
    """
    try:
        get_procedure_profile(procedure_type)
    except KeyError:
        raise ValidationError(
            {"procedure_type": f"procedimento fora do catálogo: {procedure_type!r}"}
        ) from None


class CaseProcedure(models.Model):
    """Dimensão de procedimento por caso, neutra (design D2 / padrão ats-web ADR-0004).

        O conjunto de procedimentos de um caso vive exclusivamente nestas rows
        (1–N por caso; no máximo uma row por (case, procedure_type)); declaração
        do NIR (``declared_by_nir``), detecção do pipeline (``detection_status``)
        e disposição médica (``doctor_disposition`` + ``doctor_reason`` +
        ``doctor_decided_at``) são fatos distintos por row. Rows não declaradas
        permanecem (a transformação é auditável); o contrato do conjunto é
    derivado exclusivamente das rows por ``apps/cases/procedures.py`` — as views
    nunca escrevem rows direto. ``procedure_type`` é validado contra o catálogo
    code-first (R1) na ``clean()`` e revalidado no ``save()`` (que também roda
    quando o ``objects.create()`` pula a ``full_clean()``) — tipo fora do
    catálogo é rejeitado nos dois caminhos.
    """

    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="procedures")
    procedure_type = models.CharField(max_length=20)
    declared_by_nir = models.BooleanField(default=False)
    detection_status = models.CharField(
        max_length=20,
        choices=DetectionStatus.choices,
        default=DetectionStatus.PENDING,
    )
    doctor_disposition = models.CharField(
        max_length=20,
        choices=DoctorDisposition.choices,
        default=DoctorDisposition.PENDING,
    )
    doctor_reason = models.TextField(blank=True)
    # Instante da decisão médica desta row (acréscimo HMD — o ats-web não tem).
    doctor_decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["case", "procedure_type"], name="uniq_case_procedure_type"
            ),
        ]

    def clean(self) -> None:
        super().clean()
        _validate_procedure_type_in_catalog(self.procedure_type)

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        """Persiste a row revalidando o tipo contra o catálogo (R1).

        ``objects.create()``/``get_or_create()`` não rodam a ``full_clean()``;
        o ``save()`` repete a validação do campo como defesa — tipo fora do
        catálogo nunca persiste por esse caminho. ``bulk_create``/SQL cru
        seguem sendo o buraco documentado padrão do Django (fora de cobertura).
        """
        _validate_procedure_type_in_catalog(self.procedure_type)
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

    def __str__(self) -> str:
        return f"CaseProcedure {self.case_id} [{self.procedure_type}]"


class CaseEvent(models.Model):
    """Trilha de auditoria append-only do caso (design D5).

    Fonte de verdade da história do caso: cada transição grava um evento com
    timestamp, ator (usuário ou sistema), papel ativo e payload enxuto. Sem
    métodos de alteração/remoção — as operações de negócio nunca editam a
    trilha.
    """

    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="events")
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    actor_type = models.CharField(max_length=10, choices=ActorType.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="case_events",
    )
    # Papel ativo do ator no momento (acréscimo HMD — o ats-web não tem).
    actor_role = models.CharField(max_length=30, blank=True)
    event_type = models.CharField(max_length=80, db_index=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["timestamp", "id"]

    def __str__(self) -> str:
        return f"CaseEvent {self.event_type} @ {self.timestamp}"
