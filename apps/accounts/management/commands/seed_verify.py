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
        parser.add_argument(
            "--reapresentacao-s4",
            action="store_true",
            default=False,
            help=(
                "Semeia as fixtures de reapresentação no agendamento "
                "(880401, 880402, 880403) pelos serviços reais."
            ),
        )
        parser.add_argument(
            "--reapresentacao-s5",
            action="store_true",
            default=False,
            help=(
                "Semeia as fixtures de conferência NIR de reapresentação "
                "(880501–880508) pelos serviços reais."
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
        if options.get("reapresentacao_s4"):
            self._seed_reappearance_s4()
            return
        if options.get("reapresentacao_s5"):
            self._seed_reappearance_s5()
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
    def _make_pdf_bytes(lines: list[str]) -> bytes:
        import pymupdf

        document = pymupdf.open()  # type: ignore[no-untyped-call]
        try:
            document.new_page().insert_text((72, 72), "\n".join(lines))
            return bytes(document.tobytes())  # type: ignore[no-untyped-call]
        finally:
            document.close()  # type: ignore[no-untyped-call]

    @staticmethod
    def _make_png_bytes() -> bytes:
        import pymupdf

        pix = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 64, 64), False)  # type: ignore[no-untyped-call]
        pix.clear_with(210)  # type: ignore[no-untyped-call]
        return bytes(pix.tobytes("png"))  # type: ignore[no-untyped-call]

    def _seed_reappearance_s4(self) -> None:
        """Fixtures S4 (880401, 880402, 880403) pelos serviços reais (setup, não prova).

        - ``880401``: substituição médica (espera herdada 6 dias > created_at)
          → aprovação médica → ``SCHEDULER_REQUESTED`` + 2 incomings distintos
          ``scheduling_repeat`` aplicados (com PDF de relatório, anexo PDF e
          anexo PNG válidos).
        - ``880402``: aprovado → ``scheduling_repeat`` aplicado → confirmado
          pelo agendador → ciência do NIR em retenção (``CLEANED`` retido).
        - ``880403``: aprovado → ``scheduling_repeat`` aplicado → confirmado
          pelo agendador → ciência do NIR → purgado via ``purge_closed_case_data``
          (``CLEANED`` minimizado/purgado).
        """
        import json
        from datetime import timedelta

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings
        from django.utils import timezone

        from apps.cases.closure import acknowledge_case_receipt, purge_closed_case_data
        from apps.cases.models import (
            CaseReappearance,
            CaseStatus,
            ReappearanceState,
            ResolutionKind,
        )
        from apps.cases.procedures import record_doctor_procedure_decisions
        from apps.cases.reappearance import resolve_scheduling_repeat
        from apps.intake.services import create_case_with_documents
        from apps.scheduler.services import confirm_case_scheduling

        call_command("seed_prompts")
        creator = User.objects.get(username="verify-nir")
        doctor = User.objects.get(username="verify-doctor")
        scheduler = User.objects.get(username="verify-scheduler")
        declared = "angio_art_perif"

        def _ensure_scheduling_repeat_applied(incoming: Case, canonical: Case) -> None:
            incoming.refresh_from_db()
            if incoming.status != CaseStatus.CLEANED:
                resolve_scheduling_repeat(incoming.pk, user=creator, role="system")
                incoming.refresh_from_db()
            applied = CaseReappearance.objects.filter(
                incoming_case=incoming,
                canonical_case=canonical,
                resolution_kind=ResolutionKind.SCHEDULING_REPEAT,
                state=ReappearanceState.APPLIED,
            ).exists()
            if not applied:
                raise CommandError(
                    f"seed_verify --reapresentacao-s4: incoming {incoming.case_id} "
                    f"não aplicou scheduling_repeat em {canonical.case_id} "
                    f"(status={incoming.status}); setup abortado."
                )

        # ── 1) 880401: pendente de agendamento com espera herdada + 2 reapresentações ──
        num_401 = "880401"
        name_401 = "Paciente Agendamento Pendente"
        existing_401 = list(
            Case.objects.filter(agency_record_number=num_401).order_by("created_at")
        )
        canon_401 = next(
            (c for c in existing_401 if c.status == CaseStatus.SCHEDULER_REQUESTED),
            None,
        )
        if canon_401 is None:
            if existing_401:
                raise CommandError(
                    f"seed_verify --reapresentacao-s4: {num_401} em estado incompatível."
                )
            orig_401 = create_case_with_documents(
                user=creator,
                role="nir",
                file=SimpleUploadedFile(
                    f"relatorio-{num_401}-original.pdf",
                    self._make_pdf_bytes(_review_report_lines(num_401, name_401, 62, 3)),
                    content_type="application/pdf",
                ),
                procedure_type=declared,
            )
            orig_401.refresh_from_db()
            if orig_401.status != CaseStatus.AWAITING_DOCTOR:
                raise CommandError(
                    f"seed_verify --reapresentacao-s4: original {num_401} "
                    f"não atingiu AWAITING_DOCTOR (status={orig_401.status})."
                )
            Case.objects.filter(pk=orig_401.pk).update(
                created_at=timezone.now() - timedelta(days=6)
            )
            canon_401 = create_case_with_documents(
                user=creator,
                role="nir",
                file=SimpleUploadedFile(
                    f"relatorio-{num_401}-substituto.pdf",
                    self._make_pdf_bytes(_review_report_lines(num_401, name_401, 62, 4)),
                    content_type="application/pdf",
                ),
                procedure_type=declared,
            )
            canon_401.refresh_from_db()
            if (
                canon_401.status != CaseStatus.AWAITING_DOCTOR
                or canon_401.reappearance_wait_started_at is None
                or canon_401.reappearance_wait_started_at
                >= canon_401.created_at - timedelta(days=4)
            ):
                raise CommandError(
                    f"seed_verify --reapresentacao-s4: substituição médica {num_401} "
                    f"não herdou espera (status={canon_401.status}, "
                    f"wait={canon_401.reappearance_wait_started_at})."
                )
            record_doctor_procedure_decisions(
                canon_401,
                {declared: ("approved", "")},
                user=doctor,
                role="doctor",
            )
            canon_401.refresh_from_db()
            if canon_401.status != CaseStatus.SCHEDULER_REQUESTED:
                raise CommandError(
                    f"seed_verify --reapresentacao-s4: {num_401} não atingiu "
                    f"SCHEDULER_REQUESTED após aprovação (status={canon_401.status})."
                )
            inc_401_1 = create_case_with_documents(
                user=creator,
                role="nir",
                file=SimpleUploadedFile(
                    f"relatorio-{num_401}-reenvio-1.pdf",
                    self._make_pdf_bytes(_review_report_lines(num_401, name_401, 62, 5)),
                    content_type="application/pdf",
                ),
                procedure_type=declared,
                attachments=[
                    SimpleUploadedFile(
                        f"anexo-{num_401}-1.pdf",
                        self._make_pdf_bytes(
                            [f"Anexo PDF {num_401}", "Documento complementar valido para PDF.js."]
                        ),
                        content_type="application/pdf",
                    ),
                    SimpleUploadedFile(
                        f"imagem-{num_401}-1.png",
                        self._make_png_bytes(),
                        content_type="image/png",
                    ),
                ],
            )
            _ensure_scheduling_repeat_applied(inc_401_1, canon_401)
            inc_401_2 = create_case_with_documents(
                user=creator,
                role="nir",
                file=SimpleUploadedFile(
                    f"relatorio-{num_401}-reenvio-2.pdf",
                    self._make_pdf_bytes(_review_report_lines(num_401, name_401, 62, 6)),
                    content_type="application/pdf",
                ),
                procedure_type=declared,
            )
            _ensure_scheduling_repeat_applied(inc_401_2, canon_401)
            self.stdout.write(f"  Created S4 pending fixture {num_401} ({canon_401.case_id})")
        else:
            self.stdout.write(f"  Kept S4 pending fixture {num_401} ({canon_401.case_id})")

        # ── 2) 880402: CLEANED retido histórico ──
        num_402 = "880402"
        name_402 = "Paciente Historico Retido"
        existing_402 = list(
            Case.objects.filter(agency_record_number=num_402).order_by("created_at")
        )
        canon_402 = next(
            (
                c
                for c in existing_402
                if c.status == CaseStatus.CLEANED
                and c.scheduling_decided_at is not None
                and c.clinical_data_disposed_at is None
            ),
            None,
        )
        if canon_402 is None:
            if existing_402:
                raise CommandError(
                    f"seed_verify --reapresentacao-s4: {num_402} em estado incompatível."
                )
            canon_402 = create_case_with_documents(
                user=creator,
                role="nir",
                file=SimpleUploadedFile(
                    f"relatorio-{num_402}-original.pdf",
                    self._make_pdf_bytes(_review_report_lines(num_402, name_402, 58, 2)),
                    content_type="application/pdf",
                ),
                procedure_type=declared,
            )
            record_doctor_procedure_decisions(
                canon_402,
                {declared: ("approved", "")},
                user=doctor,
                role="doctor",
            )
            inc_402 = create_case_with_documents(
                user=creator,
                role="nir",
                file=SimpleUploadedFile(
                    f"relatorio-{num_402}-reenvio.pdf",
                    self._make_pdf_bytes(_review_report_lines(num_402, name_402, 58, 3)),
                    content_type="application/pdf",
                ),
                procedure_type=declared,
            )
            _ensure_scheduling_repeat_applied(inc_402, canon_402)
            canon_402.refresh_from_db()
            confirm_case_scheduling(
                canon_402,
                unit=1,
                scheduled_datetime=timezone.now() + timedelta(days=2),
                scheduled_location="Sala Hemodinamica 1",
                user=scheduler,
                role="scheduler",
            )
            canon_402.refresh_from_db()
            with override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=False):
                acknowledge_case_receipt(canon_402, user=creator, role="nir")
            canon_402.refresh_from_db()
            if (
                canon_402.status != CaseStatus.CLEANED
                or canon_402.clinical_data_disposed_at is not None
            ):
                raise CommandError(
                    f"seed_verify --reapresentacao-s4: {num_402} não ficou CLEANED retido."
                )
            self.stdout.write(f"  Created S4 retained fixture {num_402} ({canon_402.case_id})")
        else:
            self.stdout.write(f"  Kept S4 retained fixture {num_402} ({canon_402.case_id})")

        # ── 3) 880403: CLEANED minimizado/purgado histórico ──
        num_403 = "880403"
        name_403 = "Paciente Historico Purgado"
        existing_403 = list(
            Case.objects.filter(agency_record_number=num_403).order_by("created_at")
        )
        canon_403 = next(
            (
                c
                for c in existing_403
                if c.status == CaseStatus.CLEANED
                and c.scheduling_decided_at is not None
                and c.clinical_data_disposed_at is not None
            ),
            None,
        )
        if canon_403 is None:
            if existing_403:
                raise CommandError(
                    f"seed_verify --reapresentacao-s4: {num_403} em estado incompatível."
                )
            canon_403 = create_case_with_documents(
                user=creator,
                role="nir",
                file=SimpleUploadedFile(
                    f"relatorio-{num_403}-original.pdf",
                    self._make_pdf_bytes(_review_report_lines(num_403, name_403, 70, 4)),
                    content_type="application/pdf",
                ),
                procedure_type=declared,
            )
            record_doctor_procedure_decisions(
                canon_403,
                {declared: ("approved", "")},
                user=doctor,
                role="doctor",
            )
            inc_403 = create_case_with_documents(
                user=creator,
                role="nir",
                file=SimpleUploadedFile(
                    f"relatorio-{num_403}-reenvio.pdf",
                    self._make_pdf_bytes(_review_report_lines(num_403, name_403, 70, 5)),
                    content_type="application/pdf",
                ),
                procedure_type=declared,
            )
            _ensure_scheduling_repeat_applied(inc_403, canon_403)
            canon_403.refresh_from_db()
            confirm_case_scheduling(
                canon_403,
                unit=1,
                scheduled_datetime=timezone.now() + timedelta(days=3),
                scheduled_location="Sala Hemodinamica 2",
                user=scheduler,
                role="scheduler",
            )
            canon_403.refresh_from_db()
            acknowledge_case_receipt(canon_403, user=creator, role="nir")
            canon_403.refresh_from_db()
            if canon_403.clinical_data_disposed_at is None:
                purge_closed_case_data(canon_403)
                canon_403.refresh_from_db()
            if (
                canon_403.status != CaseStatus.CLEANED
                or canon_403.clinical_data_disposed_at is None
            ):
                raise CommandError(
                    f"seed_verify --reapresentacao-s4: {num_403} não ficou CLEANED purgado."
                )
            self.stdout.write(f"  Created S4 purged fixture {num_403} ({canon_403.case_id})")
        else:
            self.stdout.write(f"  Kept S4 purged fixture {num_403} ({canon_403.case_id})")

        payload = {
            "880401": str(canon_401.case_id),
            "880402": str(canon_402.case_id),
            "880403": str(canon_403.case_id),
        }
        self.stdout.write(f"S4_SEED_JSON {json.dumps(payload)}")
        self.stdout.write(self.style.SUCCESS("seed_verify --reapresentacao-s4 concluído"))

    def _seed_reappearance_s5(self) -> None:
        """Fixtures S5 (880501–880508) pelos serviços reais (setup, não prova).

        Cria usuário criador sintético ``verify-nir-criador`` (NIR, ativo,
        senha inutilizável) para que ``verify-nir`` atue como outro membro da
        equipe NIR (não-criador). Semeia 4 pares fracos por ``origin_unit``
        (mesmo ``patient_name`` + ``procedure_type`` + ``origin_unit``,
        ``agency_record_number`` distintos e CNS vazio — nunca auto-resolvem):
        - Par 1 (silêncio + independente): 880501 + 880502 (AWAITING_DOCTOR).
        - Par 2 (substituição médica): 880503 (5 dias atrás) + 880504 (hoje).
        - Par 3 (repetição de agendamento): 880505 (SCHEDULER_REQUESTED) + 880506.
        - Par 4 (negativa histórica tardia): 880507 (FINAL_REPLY_POSTED,
          decidido 3 dias atrás) + 880508 (AWAITING_DOCTOR com CASE_LLM2_COMPLETED).
        """
        import json
        from datetime import timedelta

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.utils import timezone

        from apps.cases.closure import post_doctor_denial_reply
        from apps.cases.models import ActorType, CaseEvent, CaseStatus
        from apps.cases.procedures import record_doctor_procedure_decisions
        from apps.cases.reappearance import list_nir_reappearance_candidates
        from apps.intake.services import create_case_with_documents

        call_command("seed_prompts")
        nir_role = Role.objects.get(name="nir")
        creator, _ = User.objects.get_or_create(
            username="verify-nir-criador",
            defaults={
                "first_name": "Verificação Criador",
                "last_name": "Sintético nir",
                "is_active": True,
                "account_status": "active",
                "is_superuser": False,
                "is_staff": False,
            },
        )
        creator.first_name = "Verificação Criador"
        creator.last_name = "Sintético nir"
        creator.is_active = True
        creator.account_status = "active"
        creator.is_superuser = False
        creator.is_staff = False
        creator.set_unusable_password()
        creator.save()
        creator.roles.set([nir_role])

        doctor = User.objects.get(username="verify-doctor")
        declared = "angio_art_perif"
        s5_numbers = (
            "880501",
            "880502",
            "880503",
            "880504",
            "880505",
            "880506",
            "880507",
            "880508",
        )
        existing_cases = {
            c.agency_record_number: c
            for c in Case.objects.filter(agency_record_number__in=s5_numbers)
        }
        if existing_cases:
            if len(existing_cases) != len(s5_numbers) or Case.objects.filter(
                agency_record_number__in=s5_numbers
            ).count() != len(s5_numbers):
                raise CommandError(
                    "seed_verify --reapresentacao-s5: estado parcial incompatível para 880501–880508."
                )
            cases_by_num = existing_cases
        else:

            def _create_s5_case(
                record_number: str,
                patient_name: str,
                patient_age: int,
                days_on_screen: int,
                origin_unit: str,
                suffix: str,
            ) -> Case:
                created = create_case_with_documents(
                    user=creator,
                    role="nir",
                    file=SimpleUploadedFile(
                        f"relatorio-{record_number}-{suffix}.pdf",
                        self._make_pdf_bytes(
                            _review_report_lines(
                                record_number, patient_name, patient_age, days_on_screen
                            )
                        ),
                        content_type="application/pdf",
                    ),
                    procedure_type=declared,
                )
                created.refresh_from_db()
                if created.status != CaseStatus.AWAITING_DOCTOR:
                    raise CommandError(
                        f"seed_verify --reapresentacao-s5: {record_number} não atingiu "
                        f"AWAITING_DOCTOR (status={created.status})."
                    )
                Case.objects.filter(pk=created.pk).update(origin_unit=origin_unit)
                created.refresh_from_db()
                return created

            # Par 1: Silêncio + Independente
            c880501 = _create_s5_case(
                "880501",
                "Paciente Conferencia Indep",
                61,
                2,
                "Unidade Origem S5 Alfa",
                "anterior",
            )
            c880502 = _create_s5_case(
                "880502",
                "Paciente Conferencia Indep",
                61,
                1,
                "Unidade Origem S5 Alfa",
                "atual",
            )

            # Par 2: Substituição Médica (880503 criado há 5 dias, 880504 hoje)
            c880503 = _create_s5_case(
                "880503",
                "Paciente Conferencia Medico",
                62,
                5,
                "Unidade Origem S5 Beta",
                "anterior",
            )
            Case.objects.filter(pk=c880503.pk).update(created_at=timezone.now() - timedelta(days=5))
            c880503.refresh_from_db()
            c880504 = _create_s5_case(
                "880504",
                "Paciente Conferencia Medico",
                62,
                1,
                "Unidade Origem S5 Beta",
                "atual",
            )

            # Par 3: Repetição de Agendamento (880505 aprovado -> SCHEDULER_REQUESTED)
            c880505 = _create_s5_case(
                "880505",
                "Paciente Conferencia Agenda",
                63,
                4,
                "Unidade Origem S5 Gama",
                "anterior",
            )
            record_doctor_procedure_decisions(
                c880505,
                {declared: ("approved", "")},
                user=doctor,
                role="doctor",
            )
            c880505.refresh_from_db()
            if c880505.status != CaseStatus.SCHEDULER_REQUESTED:
                raise CommandError(
                    f"seed_verify --reapresentacao-s5: 880505 não atingiu "
                    f"SCHEDULER_REQUESTED (status={c880505.status})."
                )
            c880506 = _create_s5_case(
                "880506",
                "Paciente Conferencia Agenda",
                63,
                1,
                "Unidade Origem S5 Gama",
                "atual",
            )

            # Par 4: Negativa Histórica Tardia (880507 negado -> FINAL_REPLY_POSTED, 3 dias atrás)
            c880507 = _create_s5_case(
                "880507",
                "Paciente Conferencia Negativa",
                64,
                3,
                "Unidade Origem S5 Delta",
                "anterior",
            )
            record_doctor_procedure_decisions(
                c880507,
                {declared: ("denied", "Ausência de critério hemodinâmico recente")},
                user=doctor,
                role="doctor",
            )
            post_doctor_denial_reply(c880507, user=doctor, role="doctor")
            CaseProcedure.objects.filter(case=c880507, procedure_type=declared).update(
                doctor_decided_at=timezone.now() - timedelta(days=3)
            )
            c880507.refresh_from_db()
            if c880507.status != CaseStatus.FINAL_REPLY_POSTED:
                raise CommandError(
                    f"seed_verify --reapresentacao-s5: 880507 não atingiu "
                    f"FINAL_REPLY_POSTED (status={c880507.status})."
                )
            c880508 = _create_s5_case(
                "880508",
                "Paciente Conferencia Negativa",
                64,
                1,
                "Unidade Origem S5 Delta",
                "atual",
            )
            if not CaseEvent.objects.filter(
                case=c880508, event_type=CaseEventType.CASE_LLM2_COMPLETED
            ).exists():
                CaseEvent.objects.create(
                    case=c880508,
                    event_type=CaseEventType.CASE_LLM2_COMPLETED,
                    actor_type=ActorType.SYSTEM,
                    actor=None,
                    actor_role="system",
                    payload={},
                )

            cases_by_num = {
                "880501": c880501,
                "880502": c880502,
                "880503": c880503,
                "880504": c880504,
                "880505": c880505,
                "880506": c880506,
                "880507": c880507,
                "880508": c880508,
            }

        expected_pairs = (
            ("880502", "880501"),
            ("880504", "880503"),
            ("880506", "880505"),
            ("880508", "880507"),
        )
        for inc_num, prior_num in expected_pairs:
            inc_case = cases_by_num[inc_num]
            prior_case = cases_by_num[prior_num]
            candidates = list_nir_reappearance_candidates(inc_case)
            if len(candidates) != 1 or candidates[0].prior.case_id != prior_case.case_id:
                raise CommandError(
                    f"seed_verify --reapresentacao-s5: {inc_num} esperava candidato único "
                    f"{prior_num} ({prior_case.case_id}), obtido "
                    f"{[str(c.prior.case_id) for c in candidates]}."
                )

        payload = {num: str(cases_by_num[num].case_id) for num in s5_numbers}
        self.stdout.write(f"S5_SEED_JSON {json.dumps(payload)}")
        self.stdout.write(self.style.SUCCESS("seed_verify --reapresentacao-s5 concluído"))

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
