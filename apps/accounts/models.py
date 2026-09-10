"""Modelos de conta e acesso do HMD (slice 003).

``Role`` (papéis fixos do sistema), ``DoctorSpecialty`` (subtipos de médico,
change doctor-queue-decision), ``User(AbstractUser)`` customizado com
multi-role, status de conta e registro profissional opcional par-ou-nada e
``UserNotification`` (notificações in-app por marcos do caso, change
dashboard-notifications-pwa). ``AUTH_USER_MODEL`` aponta para
``accounts.User`` (D8).
"""

import uuid
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

# Validação de formato do UPN (D3): ``usuario@dominio`` com sufixo livre — o
# ambiente é uma floresta multi-domínio e o sufixo do UPN não é restrito a um
# domínio fixo (ex.: o usuário pode vir de outro domínio em trust).
upn_validator = RegexValidator(
    regex=r"^[^@\s]+@[^@\s]+$",
    message="Informe o UPN completo no formato usuario@dominio (ex.: cpf@dominio).",
)


class ProfessionalCouncil(models.TextChoices):
    """Conselhos profissionais disponíveis."""

    COREN = "COREN", "COREN"
    CRM = "CRM", "CRM"


class Role(models.Model):
    """Papel do usuário no sistema. Um usuário pode ter múltiplos papéis."""

    name = models.CharField(max_length=20, unique=True)

    class Meta:
        verbose_name = "Papel"
        verbose_name_plural = "Papéis"

    def __str__(self) -> str:
        return self.name


class DoctorSpecialty(models.Model):
    """Subtipo de médico do catálogo (angio/neuro/cardio/radio).

    Conjunto vazio no usuário = generalista (vê qualquer subtipo). O seed
    vive na data migration ``0003`` com os nomes congelados; a coerência com
    ``apps.cases.procedure_catalog.VALID_DOCTOR_SUBTYPES`` é garantida por
    teste de invariante (nunca por import de código vivo na migration).
    """

    name = models.CharField(max_length=20, unique=True)

    class Meta:
        verbose_name = "Subtipo de médico"
        verbose_name_plural = "Subtipos de médico"

    def __str__(self) -> str:
        return self.name


class User(AbstractUser):
    """Usuário customizado com multi-role e status de conta.

    Divergência deliberada vs ats-web (D8): o registro profissional é
    opcional, mas ``clean()`` exige preenchimento par-ou-nada e associa a
    ``ValidationError`` ao campo pendente (melhor UX de formulário).
    """

    roles = models.ManyToManyField(Role, related_name="users", blank=True)
    # Subtipos de médico do usuário (doctor-queue-decision D1): conjunto vazio
    # = generalista. Atribuição manual no Django admin (sem UI self-service).
    specialties = models.ManyToManyField(DoctorSpecialty, related_name="users", blank=True)
    account_status = models.CharField(
        max_length=10,
        choices=[
            ("active", "Active"),
            ("blocked", "Blocked"),
            ("removed", "Removed"),
        ],
        default="active",
    )

    professional_council = models.CharField(
        "Conselho profissional",
        max_length=10,
        choices=ProfessionalCouncil.choices,
        blank=True,
    )
    professional_council_number = models.CharField(
        "Número do conselho profissional",
        max_length=30,
        blank=True,
    )

    # Origem AD da identidade (D3, slice 001 R1): UPN completo
    # ``cpf@dominio``, único e opcional. Usuários com ``ad_upn`` têm a
    # credencial no Active Directory — a senha local é inutilizada no
    # provisionamento administrativo (R3).
    ad_upn = models.CharField(
        "UPN do Active Directory",
        max_length=150,
        unique=True,
        null=True,
        blank=True,
        validators=[upn_validator],
        help_text=(
            "UPN completo no formato usuario@dominio (ex.: cpf@dominio). O "
            "sufixo é livre — floresta multi-domínio."
        ),
    )

    def clean(self) -> None:
        super().clean()
        has_council = bool(self.professional_council)
        has_number = bool(self.professional_council_number)
        if has_council and not has_number:
            raise ValidationError(
                {
                    "professional_council_number": (
                        "Preencha o número do conselho profissional ou deixe o "
                        "campo de conselho vazio."
                    )
                }
            )
        if has_number and not has_council:
            raise ValidationError(
                {
                    "professional_council": (
                        "Selecione o conselho profissional ou apague o número informado."
                    )
                }
            )

    @property
    def display_name(self) -> str:
        """Nome preferencial: full name ou fallback para username."""
        return self.get_full_name() or self.username

    @property
    def professional_registration_display(self) -> str:
        """Registro profissional formatado: ex.: 'CRM 12345' ou '' se vazio."""
        if self.professional_council and self.professional_council_number:
            return f"{self.professional_council} {self.professional_council_number}"
        return ""


class NotificationType(models.TextChoices):
    """Tipo da notificação in-app — os 3 marcos do ciclo (change 11, D1)."""

    FINAL_REPLY_POSTED = "final_reply_posted", "Resposta final publicada"
    SCHEDULER_REQUESTED = "scheduler_requested", "Caso pronto para agendamento"
    SCHEDULING_REOPENED = "scheduling_reopened", "Caso reaberto por intercorrência"


class UserNotificationQuerySet(models.QuerySet["UserNotification"]):
    """QuerySet de ``UserNotification`` com predicados de visibilidade nomeados."""

    def visible_for_list(self, *, now: datetime | None = None) -> "UserNotificationQuerySet":
        """Filtra a janela de visibilidade da lista (D2).

        Devolve não lidas (``read_at IS NULL``) OU lidas dentro da janela de
        retenção (``read_at >= cutoff``). Esconde somente as lidas antes do
        corte — o ``exclude`` NUNCA remove não lidas porque ``read_at <
        cutoff`` avalia NULL para elas. Nada é apagado: a leitura antiga só sai
        do resultado.
        """
        cutoff = (now or timezone.now()) - timedelta(
            hours=getattr(settings, "NOTIFICATION_READ_RETENTION_HOURS", 48)
        )
        return self.exclude(read_at__lt=cutoff)


class UserNotificationManager(models.Manager["UserNotification"]):
    """Manager padrão de ``UserNotification`` expondo ``visible_for_list``.

    A instância usada em ``objects`` é derivada com ``from_queryset`` para
    copiar os métodos públicos de ``UserNotificationQuerySet``; as assinaturas
    ficam declaradas aqui para o type checker, que não enxerga métodos
    copiados dinamicamente pelo django-stubs.
    """

    def get_queryset(self) -> "UserNotificationQuerySet":
        return UserNotificationQuerySet(self.model, using=self._db)

    def visible_for_list(self, *, now: datetime | None = None) -> "UserNotificationQuerySet":
        return self.get_queryset().visible_for_list(now=now)


class UserNotification(models.Model):
    """Notificação in-app de um marco do caso para um usuário (D1).

    Derivada do EVENTO da trilha (``CaseEvent``), nunca da mensagem ``system``
    (D6): o par (``recipient``, ``event``) é único — idempotência estrutural
    para signal re-disparado. O conteúdo é sempre texto fixo + o caso
    referenciado (zero PHI). A row nasce na mesma transação do evento: se o
    caso sofrer rollback, a notificação some junto (desejável).
    """

    # Manager construído com from_queryset: preserva a API de Manager
    # (filter/create) e expõe o QuerySet customizado no runtime.
    objects = UserNotificationManager.from_queryset(UserNotificationQuerySet)()

    notification_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    case = models.ForeignKey(
        "cases.Case",
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    # Reserva (nullable): eventos futuros podem não ter row de trilha própria.
    event = models.ForeignKey(
        "cases.CaseEvent",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    notification_type = models.CharField(max_length=30, choices=NotificationType.choices)
    title = models.CharField(max_length=160)
    body_preview = models.CharField(max_length=240)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "read_at", "created_at"]),
            models.Index(fields=["case", "created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["recipient", "event"], name="uniq_notif_recipient_event"
            )
        ]

    def __str__(self) -> str:
        return f"UserNotification {self.notification_type} → {self.recipient_id}"
