"""Seed sintético da verificação (ui-parity-foundation, slice 002/D7).

Cria, de forma idempotente, os papéis fixos, os cinco usuários de papel
(``verify-nir``/``verify-doctor``/``verify-scheduler``/``verify-manager``/
``verify-admin`` — senha fictícia pública do harness, ver ``SKILL.md``) e
dois casos sintéticos em ``AWAITING_DOCTOR`` com procedimentos declarados,
para a fila médica ser legível no drive Playwright.

Uso (só pelo harness, sob ``config.settings.verify``):
    uv run python manage.py seed_verify --settings=config.settings.verify
"""

import os
import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand

from apps.accounts.models import Role
from apps.cases.models import Case, CaseProcedure

User = get_user_model()

VERIFY_PASSWORD = "hmd-verificacao-ficticia-2026"

# (username, primeiro nome, papel, é superusuário)
_VERIFY_USERS: tuple[tuple[str, str, str, bool], ...] = (
    ("verify-nir", "Verificação", "nir", False),
    ("verify-doctor", "Verificação", "doctor", False),
    ("verify-scheduler", "Verificação", "scheduler", False),
    ("verify-manager", "Verificação", "manager", False),
    ("verify-admin", "Verificação", "admin", True),
)

# Casos sintéticos: (nº de ocorrência, nome do paciente, idade, dias em tela)
_VERIFY_CASES: tuple[tuple[str, str, int, int], ...] = (
    ("VERIFY-001", "Paciente Sintético Um", 64, 3),
    ("VERIFY-002", "Paciente Sintético Dois", 71, 1),
)

# Tipos declarados válidos no catálogo (apps/cases/procedure_catalog.py).
_VERIFY_PROCEDURE_TYPES = ("art_perif", "cat_cardiaco")

_VERIFY_DB_NAME_RE = re.compile(r"hmd_verify_[a-z0-9_]+")
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _assert_verify_context() -> None:
    """Falha antes de qualquer acesso ORM fora da verificação (P0/P1).

    Este comando cria/reseta o superusuário sintético com a senha pública
    do harness; sob dev/prod ele seria uma credencial conhecida real. Só
    executa com settings ativas exatamente ``config.settings.verify`` E
    banco efetivo em loopback com nome ``hmd_verify_*`` (sem ``prod``).
    Chamado como primeira linha de ``handle`` — nenhum ORM antes dele.
    """
    settings_module: str | None = settings.SETTINGS_MODULE if settings.configured else None
    env_module = os.environ.get("DJANGO_SETTINGS_MODULE", "")
    if settings_module != "config.settings.verify" or (
        env_module and env_module != "config.settings.verify"
    ):
        raise ImproperlyConfigured(
            "seed_verify só executa sob config.settings.verify "
            f"(settings ativas={settings_module!r}, DJANGO_SETTINGS_MODULE={env_module!r})."
        )
    database = settings.DATABASES.get("default", {})
    host = str(database.get("HOST", ""))
    name = str(database.get("NAME", ""))
    if host not in _LOOPBACK_HOSTS or not _VERIFY_DB_NAME_RE.fullmatch(name) or "prod" in name:
        raise ImproperlyConfigured(
            "seed_verify recusa banco fora do padrão de verificação "
            f"(host={host!r}, banco={name!r})."
        )


class Command(BaseCommand):
    help = "Seed sintético da verificação: papéis, usuários de papel e casos"

    def handle(self, *args: object, **options: object) -> None:
        _assert_verify_context()
        for role_name in ("nir", "doctor", "scheduler", "manager", "admin"):
            Role.objects.get_or_create(name=role_name)

        users: dict[str, object] = {}
        for username, first_name, role_name, is_super in _VERIFY_USERS:
            user, _created = User.objects.get_or_create(
                username=username,
                defaults={
                    "first_name": first_name,
                    "last_name": f"Sintético {role_name}",
                    "is_active": True,
                    "account_status": "active",
                    "is_superuser": is_super,
                    "is_staff": is_super,
                },
            )
            user.first_name = first_name
            user.last_name = f"Sintético {role_name}"
            user.is_active = True
            user.account_status = "active"
            user.is_superuser = is_super
            user.is_staff = is_super
            user.set_password(VERIFY_PASSWORD)
            user.save()
            role = Role.objects.get(name=role_name)
            user.roles.set([role])
            users[username] = user

        creator = User.objects.get(username="verify-nir")
        for record_number, patient_name, patient_age, days_on_screen in _VERIFY_CASES:
            if Case.objects.filter(agency_record_number=record_number).exists():
                continue
            case = Case.objects.create(created_by=creator)
            for procedure_type in _VERIFY_PROCEDURE_TYPES:
                CaseProcedure.objects.create(
                    case=case,
                    procedure_type=procedure_type,
                    declared_by_nir=True,
                )
            case.start_pdf_extraction(user=None, role="system")
            case.complete_pdf_extraction(user=None, role="system")
            case.complete_anonymization(user=None, role="system")
            case.complete_llm_extraction(user=None, role="system")
            case.complete_llm_summarization(user=None, role="system")
            Case.objects.filter(pk=case.pk).update(
                agency_record_number=record_number,
                patient_name=patient_name,
                patient_age=patient_age,
                days_on_screen=days_on_screen,
            )
            self.stdout.write(f"  Created case {record_number}")
        self.stdout.write(self.style.SUCCESS("seed_verify concluído"))
