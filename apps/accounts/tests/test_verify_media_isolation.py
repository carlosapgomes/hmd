"""Isolamento de mídia por run da verificação (slice PVP-002, R5).

``MEDIA_ROOT`` da verify segue o mesmo padrão do ``STATIC_ROOT`` por run:
``HMD_VERIFY_MEDIA_ROOT`` sob o diretório da run; fallback em temp FORA do
checkout (nunca ``BASE_DIR/media``). O estilo espelha
``apps/accounts/tests/test_verify_guards.py`` (subprocesso com settings
explícitas, sem tocar banco).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CONTROL_HMD = ROOT / ".pi/skills/verify-hmd/scripts/control-hmd"
DRIVE_PAPEIS = ROOT / ".pi/skills/verify-hmd/scripts/browser/drive-detalhe-papeis.mjs"

_CHECK_MEDIA_ROOT = (
    "import os\n"
    "import django\n"
    "django.setup()\n"
    "from django.conf import settings\n"
    "print(settings.MEDIA_ROOT)\n"
)


def _run_python(
    args: list[str],
    env_extra: dict[str, str] | None = None,
    remove: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    """Executa o interpretador do ambiente a partir da raiz do checkout."""
    env = dict(os.environ)
    for key in remove:
        env.pop(key, None)
    for key in ("OPENROUTER_API_KEY", "OPENROUTER_API_KEY_FILE"):
        env.pop(key, None)
    if env_extra is not None:
        env.update(env_extra)
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _media_root_from_subprocess(env_extra: dict[str, str]) -> tuple[str, int]:
    result = _run_python(
        ["-c", _CHECK_MEDIA_ROOT],
        {"DJANGO_SETTINGS_MODULE": "config.settings.verify", **env_extra},
    )
    output = (result.stdout + result.stderr).strip()
    last_line = output.splitlines()[-1] if output else ""
    return last_line, result.returncode


def _refuse_outside_run(env_value: str) -> str:
    """Resolve o caminho de mídia para dentro de tmp_path (harness válido)."""
    return env_value


def _extract_bash_function(path: Path, name: str) -> str:
    """Extrai verbatim uma função bash de um script (testa o código real)."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if line.startswith(f"{name}()"))
    body = [lines[start]]
    for line in lines[start + 1 :]:
        body.append(line)
        if line.rstrip("\n") == "}":
            break
    else:  # pragma: no cover - guarda de extração, não do produto
        raise AssertionError(f"função {name} sem fechamento em {path}")
    return "".join(body)


def _media_path_ok(candidate: str, run_dir: str) -> bool:
    """Executa a guarda real de mídia do control-hmd (exit 0 = dentro da run)."""
    guard = _extract_bash_function(CONTROL_HMD, "media_path_ok")
    script = guard + f"\nmedia_path_ok {candidate!r} {run_dir!r}"
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.returncode == 0


def _node_media_under_run(candidate: str, run_dir: str) -> bool:
    """Executa a guarda real de mídia do drive (exit 0 = dentro da run)."""
    source = DRIVE_PAPEIS.read_text(encoding="utf-8")
    begin = source.index("function isMediaUnderRun")
    depth = 0
    end = begin
    for pos in range(begin, len(source)):
        if source[pos] == "{":
            depth += 1
        elif source[pos] == "}":
            depth -= 1
            if depth == 0:
                end = pos + 1
                break
    else:  # pragma: no cover - guarda de extração, não do produto
        raise AssertionError("isMediaUnderRun sem fechamento no drive")
    function = source[begin:end]
    script = (
        "import { realpathSync } from 'node:fs';\n"
        "import path from 'node:path';\n"
        + function
        + "\nconst [candidate, runDir] = JSON.parse(process.argv[2]);\n"
        "if (!isMediaUnderRun(candidate, runDir)) process.exit(1);\n"
    )
    import tempfile as _tempfile

    with _tempfile.NamedTemporaryFile(
        mode="w", suffix=".mjs", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(script)
        script_path = handle.name
    try:
        result = subprocess.run(
            ["node", script_path, json.dumps([candidate, run_dir])],
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        os.unlink(script_path)
    return result.returncode == 0


_COUNTER_SCRIPT_TEMPLATE = """\
import {{ readFileSync }} from 'node:fs';
import path from 'node:path';
{helpers}
const [logFile, sinceRaw, servePath] = JSON.parse(process.argv[2]);
const since = Number(sinceRaw);
if (!Number.isInteger(since)) process.exit(3);
console.log(String(countEmbedFetchesFromFile(logFile, since, servePath)));
"""


def _node_drive_helpers(begin_marker: str, end_marker: str) -> str:
    """Extrai verbatim um trecho de helpers do drive (testa o código real)."""
    source = DRIVE_PAPEIS.read_text(encoding="utf-8")
    begin = source.index(begin_marker)
    end = source.index(end_marker, begin)
    return source[begin:end]


def _node_count_embed_fetches(log_file: str, since: int, serve_path: str) -> tuple[int, int]:
    """Executa o contador real do drive (exit 0 = leitura ok; stdout = contagem)."""
    helpers = _node_drive_helpers(
        "function readServerLogLines", "// Verificação direta (NÃO é prova do embed"
    )
    script = _COUNTER_SCRIPT_TEMPLATE.format(helpers=helpers)
    import tempfile as _tempfile

    with _tempfile.NamedTemporaryFile(
        mode="w", suffix=".mjs", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(script)
        script_path = handle.name
    try:
        result = subprocess.run(
            ["node", script_path, json.dumps([log_file, since, serve_path])],
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        os.unlink(script_path)
    if result.returncode != 0:
        return result.returncode, -1
    return result.returncode, int(result.stdout.strip())


class TestMediaPathGuardsAdversarial:
    """P1-1: guardas de mídia recusam traversal/symlink/fora-da-run (bash e node)."""

    @pytest.fixture(params=["bash", "node"])
    def guard(self, request: pytest.FixtureRequest) -> Callable[[str, str], bool]:
        """A mesma regra nos dois pontos (control-hmd e drive)."""
        if request.param == "bash":
            return _media_path_ok
        return _node_media_under_run

    def test_legit_media_root_accepted(
        self, guard: Callable[[str, str], bool], tmp_path: Path
    ) -> None:
        """O root legítimo da run passa na guarda."""
        run_dir = tmp_path / "hmd-verify-aaa"
        (run_dir / "media").mkdir(parents=True)

        assert guard(str(run_dir / "media"), str(run_dir)) is True

    def test_traversal_outside_run_refused(
        self, guard: Callable[[str, str], bool], tmp_path: Path
    ) -> None:
        """`<run>/../../../media` resolve fora da run: recusado."""
        run_dir = tmp_path / "hmd-verify-aaa"
        run_dir.mkdir()

        assert guard(str(run_dir / ".." / ".." / "media"), str(run_dir)) is False

    def test_symlink_pointing_outside_refused(
        self, guard: Callable[[str, str], bool], tmp_path: Path
    ) -> None:
        """Symlink dentro da run apontando para fora: recusado."""
        run_dir = tmp_path / "hmd-verify-aaa"
        run_dir.mkdir()
        outside = tmp_path / "outside-media"
        outside.mkdir()
        (run_dir / "media").symlink_to(outside, target_is_directory=True)

        assert guard(str(run_dir / "media"), str(run_dir)) is False

    def test_sibling_run_media_refused(
        self, guard: Callable[[str, str], bool], tmp_path: Path
    ) -> None:
        """Mídia de outra run nunca é descendente desta run."""
        run_a = tmp_path / "hmd-verify-aaa"
        run_b = tmp_path / "hmd-verify-bbb"
        (run_b / "media").mkdir(parents=True)

        assert guard(str(run_b / "media"), str(run_a)) is False

    def test_checkout_media_refused(
        self, guard: Callable[[str, str], bool], tmp_path: Path
    ) -> None:
        """O `media/` do checkout nunca passa como mídia da run."""
        run_dir = tmp_path / "hmd-verify-aaa"
        run_dir.mkdir()

        assert guard(str(ROOT / "media"), str(run_dir)) is False

    def test_run_dir_itself_refused(
        self, guard: Callable[[str, str], bool], tmp_path: Path
    ) -> None:
        """O próprio diretório da run NÃO é descendente estrito: recusado."""
        run_dir = tmp_path / "hmd-verify-aaa"
        run_dir.mkdir()

        assert guard(str(run_dir), str(run_dir)) is False

    def test_symlink_ancestor_with_missing_target_refused(
        self, guard: Callable[[str, str], bool], tmp_path: Path
    ) -> None:
        """Symlink em ancestral com destino final inexistente: recusado.

        O candidato nunca pode passar textualmente: ou o ancestral existente
        mais próximo é resolvido canonicamente (e então aponta para fora da
        run), ou o candidato inexistente é recusado de plano.
        """
        run_dir = tmp_path / "hmd-verify-aaa"
        run_dir.mkdir()
        (run_dir / "link-externo").symlink_to(
            tmp_path / "alvo-inexistente", target_is_directory=True
        )

        assert guard(str(run_dir / "link-externo" / "novo"), str(run_dir)) is False


class TestDriveEmbedFetchCounter:
    """P1-a: o contador de rede do drive é fail-loud e sem off-by-one."""

    def _write_log(self, log_file: Path, lines: list[str]) -> None:
        log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_counts_first_line_after_mark(self, tmp_path: Path) -> None:
        """A primeira linha nova após o marco é contada (sem off-by-one)."""
        log_file = tmp_path / "server.log"
        self._write_log(log_file, ['"GET /x/ HTTP/1.1" 200'])
        mark = _node_drive_helpers(
            "function readServerLogLines", "// Verificação direta (NÃO é prova do embed"
        )
        script = (
            "import { readFileSync } from 'node:fs';\n"
            "import path from 'node:path';\n"
            "const artifacts = "
            + json.dumps(str(tmp_path))
            + ";\n"
            + mark
            + "\nconsole.log(String(markPhase()));\n"
        )
        import tempfile as _tempfile

        with _tempfile.NamedTemporaryFile(
            mode="w", suffix=".mjs", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(script)
            script_path = handle.name
        try:
            result = subprocess.run(
                ["node", script_path], capture_output=True, text=True, timeout=60
            )
        finally:
            os.unlink(script_path)
        assert result.returncode == 0
        since = int(result.stdout.strip())
        self._write_log(
            log_file,
            [
                '"GET /x/ HTTP/1.1" 200',
                '"GET /intake/cases/aaa/documents/1/ HTTP/1.1" 200 812',
            ],
        )
        _, count = _node_count_embed_fetches(str(log_file), since, "/intake/cases/aaa/documents/1/")

        assert count == 1

    def test_matches_real_request_path(self, tmp_path: Path) -> None:
        """O caminho casado é o real (`GET /intake/cases/.../documents/...`)."""
        log_file = tmp_path / "server.log"
        self._write_log(
            log_file,
            ['"GET /intake/cases/aaa/documents/1/ HTTP/1.1" 200 812'],
        )
        _, bare = _node_count_embed_fetches(str(log_file), 0, "/documents/")
        _, full = _node_count_embed_fetches(str(log_file), 0, "/intake/cases/aaa/documents/1/")

        assert full == 1
        assert bare == 0

    def test_excludes_probe_marker(self, tmp_path: Path) -> None:
        """Entradas com `?probe=1` são verificações diretas, não prova."""
        log_file = tmp_path / "server.log"
        self._write_log(
            log_file,
            ['"GET /intake/cases/aaa/documents/1/?probe=1 HTTP/1.1" 200 812'],
        )
        _, count = _node_count_embed_fetches(str(log_file), 0, "/intake/cases/aaa/documents/1/")

        assert count == 0

    def test_missing_log_is_loud(self, tmp_path: Path) -> None:
        """Falha de leitura nunca vira zero silencioso (exit != 0)."""
        exit_code, _ = _node_count_embed_fetches(str(tmp_path / "ausente.log"), 0, "/documents/")

        assert exit_code != 0


class TestVerifyMediaIsolation:
    """R5: MEDIA_ROOT por run, fallback fora do checkout e guarda de path."""

    def test_media_root_uses_run_env_under_run_dir(self, tmp_path: Path) -> None:
        """HMD_VERIFY_MEDIA_ROOT explícito vira MEDIA_ROOT (dentro da run)."""
        media_root = tmp_path / "media"
        value, returncode = _media_root_from_subprocess(
            {"HMD_VERIFY_MEDIA_ROOT": str(media_root)},
        )

        assert returncode == 0
        assert value == str(media_root)
        # Dentro da run simulada, nunca no checkout.
        assert Path(value) != ROOT / "media"
        assert ROOT.as_posix() not in Path(value).as_posix()

    def test_media_root_fallback_is_temp_outside_checkout(self) -> None:
        """Sem env, o fallback é temp por-processo — nunca BASE_DIR/media."""
        value, returncode = _media_root_from_subprocess({})

        assert returncode == 0
        assert value != str(ROOT / "media")
        assert value.startswith(tempfile.gettempdir())

    def test_media_root_env_outside_checkout_is_accepted(self, tmp_path: Path) -> None:
        """Env fora do checkout é aceito quando explícito (cleanup guarda a run)."""
        outside = tmp_path / "other-run" / "media"
        value, returncode = _media_root_from_subprocess(
            {"HMD_VERIFY_MEDIA_ROOT": str(_refuse_outside_run(str(outside)))},
        )

        assert returncode == 0
        assert value == str(outside)

    def test_files_written_under_run_media_never_touch_checkout(self, tmp_path: Path) -> None:
        """Arquivo gravado no MEDIA_ROOT da run cai na run, não no checkout."""
        media_root = tmp_path / "run-media"
        checkout_media = ROOT / "media"
        before = (
            {entry.name for entry in checkout_media.iterdir()} if checkout_media.exists() else set()
        )
        code = (
            "import django\n"
            "django.setup()\n"
            "from django.core.files.storage import default_storage\n"
            "from django.core.files.base import ContentFile\n"
            "name = default_storage.save('prova-run.pdf', ContentFile(b'%PDF-1.4 prova'))\n"
            "print(default_storage.path(name))\n"
        )
        result = _run_python(
            ["-c", code],
            {
                "DJANGO_SETTINGS_MODULE": "config.settings.verify",
                "HMD_VERIFY_MEDIA_ROOT": str(media_root),
            },
        )
        assert result.returncode == 0, result.stderr
        stored = result.stdout.strip().splitlines()[-1]
        assert stored.startswith(str(media_root))
        assert (media_root / "prova-run.pdf").exists()
        after = (
            {entry.name for entry in checkout_media.iterdir()} if checkout_media.exists() else set()
        )
        # O checkout não ganha nada com a escrita da run.
        assert after == before
