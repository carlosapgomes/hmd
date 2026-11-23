"""Seed sintético da verificação (ui-parity-foundation, slice 002/D7).

Cria, de forma idempotente, os papéis fixos, os cinco usuários de papel
(``verify-nir``/``verify-doctor``/``verify-scheduler``/``verify-manager``/
``verify-admin`` — senha fictícia pública do harness, ver ``SKILL.md``) e
dois casos sintéticos em ``AWAITING_DOCTOR`` com procedimentos declarados,
para a fila médica ser legível no drive Playwright.

Opção ``--procedure-review`` (confirm-nir-procedure-review, slice 002, D9):
antes de criar qualquer caso, chama o ``seed_prompts`` idempotente (templates
ativos sem sobrescrever conteúdo/versões) e cria, pela primitiva e pelo
pipeline reais com o adaptador ``config.llm_verify``, quatro fixtures de
retenção por divergência com números sintéticos estáveis — ``990101``
(manter), ``990102`` (trocar para não detectado), ``990103`` (detecção
vazia) e ``990104`` (falha tipada da LLM2 após confirmação). Idempotente na
mesma run (não duplica nem reseta confirmação/decisão/progresso/prompts);
caso existente com o identificador mas incompatível gera erro explícito.

Opção ``--reapresentacao-s2`` (change reapresentacao-casos-filas, slice 002):
par 880101 (mesmo nome + mesmo nº, ``angio_art_perif`` — concordância exata
no ``config.llm_verify``) criado pela primitiva e pelo pipeline reais: o
original é envelhecido 6 dias após concluir e o reenvio é processado pelo
orquestrador real, aplicando a substituição médica pelo serviço S2 (sem
toque direto no vínculo). Idempotente: cadeia aplicada é preservada.

Uso (só pelo harness, sob ``config.settings.verify``):
    uv run python manage.py seed_verify --settings=config.settings.verify
    uv run python manage.py seed_verify --procedure-review --settings=config.settings.verify
"""

import argparse
import os
import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Role
from apps.cases.events import CaseEventType
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

# Fixtures da revisão de procedimento (D9): (nº sintético estável, nome do
# paciente, idade, dias em tela, tipo declarado). A confirmação e os
# resultados pós-confirmação NÃO são semeados — nascem somente do POST real
# do navegador (manter 990101, trocar 990102→angio_art_perif com o tipo
# indetectado, manter 990103 vazio, trocar 990104→art_cerebral com falha
# tipada da LLM2).
_PROCEDURE_REVIEW_FIXTURES: tuple[tuple[str, str, int, int, str], ...] = (
    ("990101", "Maria Sintetica Alfa", 64, 3, "art_perif"),
    ("990102", "Maria Sintetica Beta", 65, 5, "flebografia"),
    ("990103", "Maria Sintetica Gama", 66, 2, "nefrostomia"),
    ("990104", "Maria Sintetica Delta", 67, 7, "dren_biliar"),
)

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


def _review_report_lines(
    record_number: str, patient_name: str, patient_age: int, days_on_screen: int
) -> list[str]:
    """Linhas do relatório SESAB sintético da fixture (texto extraível).

    Passa no gate (cabeçalho + sinais institucionais + 6 seções, >500 chars)
    com entidades SEMPRE cobertas pela anonimização (sem \"Unid. Origem\" /
    \"Data Adm. Unid.\" / nomes de unidade — parciais vazariam no guard de
    tokens da LLM2). O nº aparece só no ``Código:`` (varrido do texto limpo
    por ser o nº do caso, sem afetar a extração).
    """
    lines = [
        "RELATÓRIO DE OCORRÊNCIAS",
        "Central Estadual de Regulação",
        "Secretaria da Saúde do Estado",
        "Governo do Estado da Bahia",
        f"Código: {record_number}",
        "Abertura: 01/02/2025",
        "Motivo da Solicitação: avaliação hemodinâmica do caso sintético",
        "Complemento da Solicitação: encaminhado para avaliação de verificação",
        "Resumo Clínico: paciente adulto em investigação para procedimento hemodinâmico",
        f"{patient_name} - Idade: {patient_age}a. - Sexo Masculino - Raça/Cor Parda",
        "Paciente:",
        f"Dias em tela: {days_on_screen}",
    ]
    while len("\n".join(lines)) < 560:
        lines.append("Linha de preenchimento do relatório sintético de verificação.")
    return lines


class Command(BaseCommand):
    help = "Seed sintético da verificação: papéis, usuários de papel e casos"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--procedure-review",
            action="store_true",
            default=False,
            help=(
                "Semeia as fixtures da revisão de procedimento (990101–990104) "
                "pela primitiva/pipeline reais após o seed_prompts."
            ),
        )
        parser.add_argument(
            "--reapresentacao-s2",
            action="store_true",
            default=False,
            help=(
                "Semeia o par de reapresentação médica (880101) pela "
                "primitiva/pipeline reais após o seed_prompts."
            ),
        )

    def handle(self, *args: object, **options: object) -> None:
        _assert_verify_context()
        self._ensure_roles_and_users()
        if options.get("procedure_review"):
            self._seed_procedure_review()
            return
        if options.get("reapresentacao_s2"):
            self._seed_reappearance_s2()
            return
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

    def _ensure_roles_and_users(self) -> None:
        """Papéis fixos + cinco usuários de papel (idempotente, D7)."""
        for role_name in ("nir", "doctor", "scheduler", "manager", "admin"):
            Role.objects.get_or_create(name=role_name)

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

    def _seed_procedure_review(self) -> None:
        """Fixtures 990101–990104 pela primitiva/pipeline reais (D9).

        Chama o ``seed_prompts`` idempotente ANTES de criar qualquer caso
        (templates ativos sem sobrescrever conteúdo/versões — sem presumir
        que migrate ou o seed padrão os criaram). Idempotente: caso existente
        compatível (mesmo nº, declarado original ou confirmação real
        posterior) é preservado sem reset; nº duplicado ou caso sem marca
        com declarado divergente e sem confirmação é conflito explícito.
        """
        from apps.intake.services import create_case_with_documents

        call_command("seed_prompts")
        import pymupdf
        from django.core.files.uploadedfile import SimpleUploadedFile

        creator = User.objects.get(username="verify-nir")
        for (
            record_number,
            patient_name,
            patient_age,
            days_on_screen,
            declared,
        ) in _PROCEDURE_REVIEW_FIXTURES:
            existing = list(Case.objects.filter(agency_record_number=record_number))
            if existing:
                if len(existing) == 1 and self._fixture_compatible(existing[0], declared):
                    self.stdout.write(f"  Kept case {record_number}")
                    continue
                raise CommandError(
                    f"seed_verify --procedure-review: conflito de fixture — "
                    f"já existe caso incompatível com o nº {record_number} "
                    f"({len(existing)} registro(s)); nada foi sobrescrito."
                )
            document = pymupdf.open()  # type: ignore[no-untyped-call]
            try:
                document.new_page().insert_text(
                    (72, 72),
                    "\n".join(
                        _review_report_lines(
                            record_number, patient_name, patient_age, days_on_screen
                        )
                    ),
                )
                pdf_bytes = bytes(document.tobytes())  # type: ignore[no-untyped-call]
            finally:
                document.close()  # type: ignore[no-untyped-call]
            case = create_case_with_documents(
                user=creator,
                role="nir",
                file=SimpleUploadedFile(
                    f"relatorio-{record_number}.pdf",
                    pdf_bytes,
                    content_type="application/pdf",
                ),
                procedure_type=declared,
            )
            case.refresh_from_db()
            if (
                not case.manual_review_required
                or case.manual_review_reason != "procedure_divergence"
            ):
                raise CommandError(
                    f"seed_verify --procedure-review: fixture {record_number} não "
                    f"reteve por divergência (status={case.status}); setup abortado."
                )
            self.stdout.write(f"  Created case {record_number}")
        self.stdout.write(self.style.SUCCESS("seed_verify --procedure-review concluído"))

    def _seed_reappearance_s2(self) -> None:
        """Par de reapresentação médica 880101 (slice 002 — setup, não prova).

        Original e reenvio (mesmo nome + mesmo nº, ``angio_art_perif`` com
        concordância exata no ``config.llm_verify``) criados pela primitiva e
        pelo pipeline REAIS: o original é envelhecido 6 dias após concluir e
        o reenvio aplica a substituição pelo serviço S2 dentro do atomic
        final do orquestrador — sem tocar no vínculo, na espera ou no status
        por aqui. Idempotente: cadeia aplicada é preservada; estado parcial
        incompatível aborta com erro explícito.
        """
        from datetime import timedelta

        from django.utils import timezone

        from apps.cases.models import CaseStatus
        from apps.cases.reappearance import current_representative

        call_command("seed_prompts")
        import pymupdf
        from django.core.files.uploadedfile import SimpleUploadedFile

        from apps.intake.services import create_case_with_documents

        number = "880101"
        name = "Paciente Sintetico Reapresentado"
        declared = "angio_art_perif"
        creator = User.objects.get(username="verify-nir")
        existing = list(Case.objects.filter(agency_record_number=number).order_by("created_at"))
        for case in existing:
            if case.status == CaseStatus.CLEANED:
                representative = current_representative(case)
                if (
                    representative.pk != case.pk
                    and representative.status == CaseStatus.AWAITING_DOCTOR
                    and representative.agency_record_number == number
                ):
                    self.stdout.write("  Kept reappearance chain 880101")
                    self.stdout.write(
                        self.style.SUCCESS("seed_verify --reapresentacao-s2 concluído")
                    )
                    return
        if existing and any(case.status != CaseStatus.AWAITING_DOCTOR for case in existing):
            raise CommandError(
                "seed_verify --reapresentacao-s2: casos 880101 em estado "
                "incompatível sem cadeia aplicada; nada foi sobrescrito."
            )
        original = existing[0] if existing else None
        if original is None:
            document = pymupdf.open()  # type: ignore[no-untyped-call]
            try:
                document.new_page().insert_text(
                    (72, 72),
                    "\n".join(_review_report_lines(number, name, 60, 3)),
                )
                pdf_bytes = bytes(document.tobytes())  # type: ignore[no-untyped-call]
            finally:
                document.close()  # type: ignore[no-untyped-call]
            original = create_case_with_documents(
                user=creator,
                role="nir",
                file=SimpleUploadedFile(
                    f"relatorio-{number}-original.pdf",
                    pdf_bytes,
                    content_type="application/pdf",
                ),
                procedure_type=declared,
            )
            original.refresh_from_db()
            if original.status != CaseStatus.AWAITING_DOCTOR:
                raise CommandError(
                    "seed_verify --reapresentacao-s2: original 880101 não "
                    f"concluiu (status={original.status}); setup abortado."
                )
            Case.objects.filter(pk=original.pk).update(
                created_at=timezone.now() - timedelta(days=6)
            )
            original.refresh_from_db()
            self.stdout.write("  Created original 880101")
        if not Case.objects.filter(agency_record_number=number).exclude(pk=original.pk).exists():
            document = pymupdf.open()  # type: ignore[no-untyped-call]
            try:
                document.new_page().insert_text(
                    (72, 72),
                    "\n".join(_review_report_lines(number, name, 60, 4)),
                )
                pdf_bytes = bytes(document.tobytes())  # type: ignore[no-untyped-call]
            finally:
                document.close()  # type: ignore[no-untyped-call]
            incoming = create_case_with_documents(
                user=creator,
                role="nir",
                file=SimpleUploadedFile(
                    f"relatorio-{number}-reenvio.pdf",
                    pdf_bytes,
                    content_type="application/pdf",
                ),
                procedure_type=declared,
            )
            incoming.refresh_from_db()
            if incoming.status != CaseStatus.AWAITING_DOCTOR:
                raise CommandError(
                    "seed_verify --reapresentacao-s2: reenvio 880101 não "
                    f"concluiu (status={incoming.status}); setup abortado."
                )
            self.stdout.write("  Created incoming 880101")
        self.stdout.write(self.style.SUCCESS("seed_verify --reapresentacao-s2 concluído"))

    @staticmethod
    def _fixture_compatible(case: Case, declared: str) -> bool:
        """Caso existente pode ser preservado (nosso, inclusive confirmado)."""
        from apps.cases.procedure_review import has_procedure_review_marker
        from apps.cases.procedures import get_declared_procedure_types

        if not has_procedure_review_marker(case):
            return False
        if list(get_declared_procedure_types(case)) == [declared]:
            return True
        return Case.objects.filter(
            pk=case.pk,
            events__event_type=CaseEventType.CASE_PROCEDURE_REVIEW_CONFIRMED,
        ).exists()
