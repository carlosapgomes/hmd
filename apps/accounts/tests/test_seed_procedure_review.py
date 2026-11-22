"""Seed ``--procedure-review``: prompts primeiro, fixtures idempotentes (R5/D9).

Subprocesso com banco ``hmd_verify_*`` descartável próprio (nunca dev/prod/
harness): migrate → ``seed_verify --procedure-review`` sem prompts prévios
→ asserts via ORM; reexecução antes/depois de confirmação real (serviço +
cadeia pós-commit com o adaptador verify); conflito de fixture incompatível
é erro explícito sem sobrescrita.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

EXPECTED_NUMBERS = ("990101", "990102", "990103", "990104")
EXPECTED_DECLARED = {
    "990101": "art_perif",
    "990102": "flebografia",
    "990103": "nefrostomia",
    "990104": "dren_biliar",
}


def _db_name() -> str:
    return f"hmd_verify_seed_{uuid.uuid4().hex[:12]}"


def _media_root(db_name: str) -> str:
    """MEDIA_ROOT compartilhado da run de teste (harness: HMD_VERIFY_MEDIA_ROOT)."""
    import tempfile

    return str(Path(tempfile.gettempdir()) / f"hmd-seed-media-{db_name}")


def _env(db_name: str) -> dict[str, str]:
    env = dict(os.environ)
    env["DJANGO_SETTINGS_MODULE"] = "config.settings.verify"
    env["VERIFY_DB_HOST"] = "127.0.0.1"
    env["VERIFY_DB_PORT"] = os.environ.get("TEST_DB_PORT", "55435")
    env["VERIFY_DB_NAME"] = db_name
    env["VERIFY_DB_USER"] = os.environ.get("VERIFY_DB_USER", "hmd")
    env["VERIFY_DB_PASSWORD"] = os.environ.get("VERIFY_DB_PASSWORD", "hmd_dev")
    # Seed, confirmação e inspeção escrevem/lêem no MESMO media dir: sem
    # isso, o fallback por-processo da settings isolaria os arquivos entre
    # os subprocessos (fonte ilegível). Mesmo contrato do harness.
    env["HMD_VERIFY_MEDIA_ROOT"] = _media_root(db_name)
    env["PYTHONPATH"] = str(ROOT)
    env.pop("DATABASE_URL", None)
    return env


def _pg_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PGHOST"] = "127.0.0.1"
    env["PGPORT"] = os.environ.get("TEST_DB_PORT", "55435")
    env["PGUSER"] = os.environ.get("VERIFY_DB_USER", "hmd")
    env["PGPASSWORD"] = os.environ.get("VERIFY_DB_PASSWORD", "hmd_dev")
    return env


def _run(args: list[str], db_name: str, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        env=_env(db_name),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _run_py(script: str, db_name: str, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=_env(db_name),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _output(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


@pytest.fixture
def verify_db() -> object:
    """Banco de verificação descartável próprio com migrations aplicadas."""
    import shutil

    db_name = _db_name()
    media_root = Path(_media_root(db_name))
    media_root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["dropdb", "--if-exists", db_name],
        env=_pg_env(),
        capture_output=True,
        timeout=120,
    )
    created = subprocess.run(
        ["createdb", db_name], env=_pg_env(), capture_output=True, text=True, timeout=120
    )
    assert created.returncode == 0, created.stderr
    migrated = _run(["manage.py", "migrate", "--settings=config.settings.verify"], db_name)
    assert migrated.returncode == 0, _output(migrated)
    yield db_name
    subprocess.run(
        ["dropdb", "--if-exists", db_name],
        env=_pg_env(),
        capture_output=True,
        timeout=120,
    )
    shutil.rmtree(media_root, ignore_errors=True)


_INSPECT = (
    "import django; django.setup()\n"
    "from django.db.models import Count\n"
    "from apps.cases.models import Case, CaseEvent\n"
    "from apps.cases.events import CaseEventType\n"
    "from apps.cases.procedures import get_declared_procedure_types\n"
    "from apps.llm.models import PromptTemplate\n"
    "print('PROMPTS-ACTIVE:', PromptTemplate.objects.filter(is_active=True).count())\n"
    "for number in ['990101', '990102', '990103', '990104']:\n"
    "    cases = list(Case.objects.filter(agency_record_number=number))\n"
    "    print('CASES', number, len(cases))\n"
    "    for case in cases:\n"
    "        print('CASE', number, case.status, case.manual_review_required,\n"
    "              case.manual_review_reason, sorted(get_declared_procedure_types(case)))\n"
    "        print('ENABLED', number,\n"
    "              case.events.filter(event_type=CaseEventType.CASE_PROCEDURE_REVIEW_ENABLED).count())\n"
    "        print('CONFIRMED', number,\n"
    "              case.events.filter(event_type=CaseEventType.CASE_PROCEDURE_REVIEW_CONFIRMED).count())\n"
    "print('DOCS:', sum(c.documents.count() for c in Case.objects.all()))\n"
    "print('EVENTS:', CaseEvent.objects.count())\n"
)


def _inspect(db_name: str) -> dict[str, object]:
    result = _run_py(_INSPECT, db_name)
    assert result.returncode == 0, _output(result)
    parsed: dict[str, object] = {
        "prompts": 0,
        "docs": 0,
        "events": 0,
        "cases": {},
        "details": {},
        "enabled": {},
        "confirmed": {},
    }
    cases = parsed["cases"]
    details = parsed["details"]
    enabled = parsed["enabled"]
    confirmed = parsed["confirmed"]
    assert isinstance(cases, dict) and isinstance(details, dict)
    assert isinstance(enabled, dict) and isinstance(confirmed, dict)
    for line in result.stdout.splitlines():
        if line.startswith("PROMPTS-ACTIVE:"):
            parsed["prompts"] = int(line.split(":", 1)[1].strip())
        elif line.startswith("DOCS:"):
            parsed["docs"] = int(line.split(":", 1)[1].strip())
        elif line.startswith("EVENTS:"):
            parsed["events"] = int(line.split(":", 1)[1].strip())
        elif line.startswith("CASES "):
            _, number, count = line.split()
            cases[number] = int(count)
        elif line.startswith(("ENABLED ", "CONFIRMED ", "CASE ")):
            kind, number, rest = line.split(" ", 2)
            target = {"CASE": details, "ENABLED": enabled, "CONFIRMED": confirmed}[kind]
            target[number] = rest
    return parsed


class TestSeedProcedureReview:
    def test_prompts_first_and_stable_fixtures(self, verify_db: object) -> None:
        """Banco sem prompts: a opção semeia prompts antes e cria as 4 retenções."""
        db_name = str(verify_db)
        result = _run(
            ["manage.py", "seed_verify", "--procedure-review", "--settings=config.settings.verify"],
            db_name,
        )
        assert result.returncode == 0, _output(result)
        state = _inspect(db_name)
        assert state["prompts"] == 31, state
        cases = state["cases"]
        details = state["details"]
        enabled = state["enabled"]
        confirmed = state["confirmed"]
        assert isinstance(cases, dict) and isinstance(details, dict)
        assert isinstance(enabled, dict) and isinstance(confirmed, dict)
        for number in EXPECTED_NUMBERS:
            assert cases.get(number) == 1, state
            assert "LLM_EXTRACTING True procedure_divergence" in details[number], state
            assert f"['{EXPECTED_DECLARED[number]}']" in details[number], state
            assert enabled.get(number) == "1", state
            assert confirmed.get(number) == "0", state

    def test_idempotent_rerun_preserves_progress(self, verify_db: object) -> None:
        """Reexecução não duplica nem reseta: mesmos ids/arquivos/eventos/estado."""
        db_name = str(verify_db)
        first = _run(
            ["manage.py", "seed_verify", "--procedure-review", "--settings=config.settings.verify"],
            db_name,
        )
        assert first.returncode == 0, _output(first)
        before = _inspect(db_name)
        second = _run(
            ["manage.py", "seed_verify", "--procedure-review", "--settings=config.settings.verify"],
            db_name,
        )
        assert second.returncode == 0, _output(second)
        after = _inspect(db_name)
        assert after == before
        assert "Kept case 990101" in second.stdout

    def test_rerun_after_real_confirmation_preserves_it(self, verify_db: object) -> None:
        """Confirmação real (serviço + cadeia) sobrevive à reexecução do setup."""
        db_name = str(verify_db)
        seeded = _run(
            ["manage.py", "seed_verify", "--procedure-review", "--settings=config.settings.verify"],
            db_name,
        )
        assert seeded.returncode == 0, _output(seeded)
        confirm = _run_py(
            "import django; django.setup()\n"
            "from apps.accounts.models import User\n"
            "from apps.cases.models import Case\n"
            "from apps.cases.procedure_review import latest_review_event, source_fingerprint_for_case\n"
            "from apps.intake.services import confirm_case_procedure_review\n"
            "user = User.objects.get(username='verify-nir')\n"
            "case = Case.objects.get(agency_record_number='990101')\n"
            "review = latest_review_event(case)\n"
            "confirm_case_procedure_review(case=case, user=user, role='nir',\n"
            "    procedure_type='art_perif', review_acknowledged='on',\n"
            "    review_justification='Confirmo apos leitura do relatorio.',\n"
            "    review_event_id=review.pk, source_fingerprint=source_fingerprint_for_case(case))\n"
            "case.refresh_from_db()\n"
            "assert case.status == 'AWAITING_DOCTOR', case.status\n"
            "print('CONFIRM-OK')\n",
            db_name,
        )
        assert confirm.returncode == 0, _output(confirm)
        assert "CONFIRM-OK" in confirm.stdout
        rerun = _run(
            ["manage.py", "seed_verify", "--procedure-review", "--settings=config.settings.verify"],
            db_name,
        )
        assert rerun.returncode == 0, _output(rerun)
        state = _inspect(db_name)
        cases = state["cases"]
        details = state["details"]
        confirmed = state["confirmed"]
        assert isinstance(cases, dict) and isinstance(details, dict) and isinstance(confirmed, dict)
        assert cases.get("990101") == 1, state
        assert confirmed.get("990101") == "1", state
        assert "AWAITING_DOCTOR" in details["990101"], state

    def test_fixture_conflict_is_explicit_error(self, verify_db: object) -> None:
        """Nº ocupado por caso incompatível: falha sem sobrescrever."""
        db_name = str(verify_db)
        seeded = _run(
            ["manage.py", "seed_verify", "--procedure-review", "--settings=config.settings.verify"],
            db_name,
        )
        assert seeded.returncode == 0, _output(seeded)
        before = _inspect(db_name)
        clash = _run_py(
            "import django; django.setup()\n"
            "from io import StringIO\n"
            "from django.core.files.uploadedfile import SimpleUploadedFile\n"
            "from apps.accounts.models import User\n"
            "from apps.cases.models import Case\n"
            "from apps.intake.services import create_case_with_documents\n"
            "user = User.objects.get(username='verify-nir')\n"
            "case = create_case_with_documents(user=user, role='nir',\n"
            "    file=SimpleUploadedFile('x.pdf', b'%PDF-1.4 clash', content_type='application/pdf'),\n"
            "    procedure_type='cat_cardiaco')\n"
            "Case.objects.filter(pk=case.pk).update(agency_record_number='990102')\n"
            "print('CLASH-OK')\n",
            db_name,
        )
        assert clash.returncode == 0, _output(clash)
        rerun = _run(
            ["manage.py", "seed_verify", "--procedure-review", "--settings=config.settings.verify"],
            db_name,
        )
        assert rerun.returncode != 0
        assert "conflito de fixture" in _output(rerun)
        after = _inspect(db_name)
        after_cases = after["cases"]
        before_cases = before["cases"]
        assert isinstance(after_cases, dict) and isinstance(before_cases, dict)
        assert after_cases.get("990101") == before_cases.get("990101")
