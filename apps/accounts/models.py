"""Modelos de conta e acesso do HMD (slice 003).

``Role`` (papéis fixos do sistema) e ``User(AbstractUser)`` customizado com
multi-role, status de conta e registro profissional opcional par-ou-nada.
``AUTH_USER_MODEL`` aponta para ``accounts.User`` (D8).
"""

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

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


class User(AbstractUser):
    """Usuário customizado com multi-role e status de conta.

    Divergência deliberada vs ats-web (D8): o registro profissional é
    opcional, mas ``clean()`` exige preenchimento par-ou-nada e associa a
    ``ValidationError`` ao campo pendente (melhor UX de formulário).
    """

    roles = models.ManyToManyField(Role, related_name="users", blank=True)
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
