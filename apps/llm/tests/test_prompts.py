"""Testes do slice 003 (change llm-pipeline-per-type): PromptTemplate + seeds + montagem.

Cobre R1–R5:

- R1: ``PromptTemplate`` versionado (unique ``(name, version)``; no máximo 1
  ativo por nome via constraint **parcial no banco** — melhoria HMD registrada,
  divergência deliberada do clean/save do ats-web); ``get_active_prompt`` com
  falha explícita;
- R2: ``seed_prompts`` idempotente — 28 templates (2 system neutros + 26 user
  por tipo), reexecutar não duplica nem sobrescreve conteúdo editado;
- R3: ``build_case_prompts`` — system neutro único do estágio, users na ordem
  canônica do catálogo, placeholders por estágio, montagem nunca vê conteúdo
  do caso; tipo sem prompt ativo → erro nomeando o tipo;
- R4: ``prompt_usage`` enxuto (names+versions) para payloads de evento;
- R5: todos os acima comprovados por teste.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.db import IntegrityError

from apps.cases.procedure_catalog import PROCEDURE_PROFILES
from apps.llm.models import ActivePromptNotFoundError, PromptTemplate
from apps.llm.prompts_seed import PROMPT_SEED_CONTENTS, system_prompt_name, user_prompt_name
from apps.llm.services import build_case_prompts, prompt_usage

# 13 tipos do catálogo (ordem canônica já é a do registro).
CATALOG_TYPES: tuple[str, ...] = tuple(p.procedure_type for p in PROCEDURE_PROFILES)

# 28 nomes canônicos do seed: 2 systems neutros + 26 users (13 tipos × 2 estágios).
EXPECTED_SEED_NAMES: set[str] = {system_prompt_name(stage) for stage in ("llm1", "llm2")} | {
    user_prompt_name(procedure_type, stage)
    for procedure_type in CATALOG_TYPES
    for stage in ("llm1", "llm2")
}

ALL_PLACEHOLDERS = (
    "{texto_anonimizado}",
    "{visao_estruturada}",
    "{policy}",
    "{prior_case}",
)


def _run_seed() -> None:
    """Executa ``seed_prompts`` descartando a saída (helper dos testes)."""
    call_command("seed_prompts", stdout=StringIO())


# ── R1: model + constraint parcial + get_active_prompt ──────────────────────


@pytest.mark.django_db
def test_single_active_per_name() -> None:
    """R1/R5: a constraint parcial rejeita o segundo ativo do mesmo nome."""
    PromptTemplate.objects.create(name="llm1.system", version=1, content="v1")
    with pytest.raises(IntegrityError):
        PromptTemplate.objects.create(name="llm1.system", version=2, content="v2")


@pytest.mark.django_db
def test_unique_name_version() -> None:
    """R1: (name, version) é único mesmo entre versões inativas."""
    PromptTemplate.objects.create(
        name="proc.art_perif.llm1.user", version=1, content="v1", is_active=False
    )
    with pytest.raises(IntegrityError):
        PromptTemplate.objects.create(
            name="proc.art_perif.llm1.user", version=1, content="v1 duplicado", is_active=False
        )


@pytest.mark.django_db
def test_different_names_can_both_be_active() -> None:
    """R1: a constraint é por nome — nomes diferentes têm ativos independentes."""
    PromptTemplate.objects.create(name="llm1.system", version=1, content="s1")
    PromptTemplate.objects.create(name="llm2.system", version=1, content="s2")
    assert PromptTemplate.objects.filter(is_active=True).count() == 2


@pytest.mark.django_db
def test_get_active_prompt_returns_active() -> None:
    """R1: retorna a versão ativa; versões inativas do mesmo nome não contam."""
    PromptTemplate.objects.create(name="llm1.system", version=1, content="v1", is_active=False)
    PromptTemplate.objects.create(name="llm1.system", version=2, content="v2", is_active=True)
    active = PromptTemplate.get_active_prompt("llm1.system")
    assert active is not None
    assert active.version == 2
    assert active.content == "v2"


@pytest.mark.django_db
def test_get_active_prompt_no_active_error() -> None:
    """R1: sem versão ativa (nem mesmo row existente), erro nomeando o prompt."""
    PromptTemplate.objects.create(name="llm1.system", version=1, content="v1", is_active=False)
    with pytest.raises(ActivePromptNotFoundError) as excinfo:
        PromptTemplate.get_active_prompt("llm1.system")
    assert "llm1.system" in str(excinfo.value)


# ── R2: seeds idempotentes ──────────────────────────────────────────────────


@pytest.mark.django_db
def test_seed_count_28() -> None:
    """R2: o seed cria os 28 templates (2 system + 26 user), todos ativos."""
    _run_seed()
    assert PromptTemplate.objects.count() == len(EXPECTED_SEED_NAMES) == 28
    registered = set(PromptTemplate.objects.values_list("name", flat=True))
    assert registered == EXPECTED_SEED_NAMES
    assert PromptTemplate.objects.filter(is_active=True).count() == 28
    # Conteúdo versionado presente para todos os 28 nomes (nunca vazio).
    assert len(PROMPT_SEED_CONTENTS) == 28
    for name in EXPECTED_SEED_NAMES:
        assert PROMPT_SEED_CONTENTS[name].strip()


@pytest.mark.django_db
def test_seed_idempotent() -> None:
    """R2: reexecutar não duplica nem cria versões extras."""
    _run_seed()
    _run_seed()
    assert PromptTemplate.objects.count() == 28
    for name in EXPECTED_SEED_NAMES:
        rows = list(PromptTemplate.objects.filter(name=name).order_by("version"))
        assert len(rows) == 1, name
        assert rows[0].version == 1
        assert rows[0].is_active is True


@pytest.mark.django_db
def test_seed_preserves_edits() -> None:
    """R2: conteúdo editado da versão ativa não é sobrescrito ao re-seedar."""
    _run_seed()
    edited = PromptTemplate.get_active_prompt("llm1.system")
    edited.content = "conteúdo personalizado (edição manual)"
    edited.save()
    _run_seed()
    assert PromptTemplate.objects.count() == 28
    after = PromptTemplate.get_active_prompt("llm1.system")
    assert after.pk == edited.pk
    assert after.version == 1
    assert after.content == "conteúdo personalizado (edição manual)"
    assert after.is_active is True


# ── R3: build_case_prompts ─────────────────────────────────────────────────


@pytest.mark.django_db
def test_build_single_type() -> None:
    """R3: 1 tipo → system neutro único + user com o bloco do tipo e o
    placeholder do estágio llm1 ({texto_anonimizado}) — sem conteúdo do caso."""
    _run_seed()
    messages = build_case_prompts("llm1", ("art_perif",))
    assert [m["role"] for m in messages] == ["system", "user"]

    system = messages[0]
    assert system["role"] == "system"
    assert system["content"] == PromptTemplate.get_active_prompt("llm1.system").content

    user_content = messages[1]["content"]
    assert "Arteriografia periférica" in user_content
    assert "{texto_anonimizado}" in user_content
    # Estágio llm1 não carrega os placeholders do estágio llm2.
    for placeholder in ("{visao_estruturada}", "{policy}", "{prior_case}"):
        assert placeholder not in user_content


@pytest.mark.django_db
def test_build_multi_type_canonical_order() -> None:
    """R3: 3 tipos fora de ordem → users na ordem canônica do catálogo;
    system neutro único e idêntico ao de 1 tipo."""
    _run_seed()
    single = build_case_prompts("llm1", ("art_perif",))
    messages = build_case_prompts("llm1", ("nefrostomia", "art_perif", "filtro_cava"))
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[0]["content"] == single[0]["content"]

    user_content = messages[1]["content"]
    positions = (
        user_content.index("Arteriografia periférica"),
        user_content.index("Implante de filtro de veia cava"),
        user_content.index("Nefrostomia percutânea"),
    )
    assert positions == tuple(sorted(positions))


@pytest.mark.django_db
def test_build_llm2_placeholders() -> None:
    """R3: estágio llm2 monta os 4 placeholders (payload completo do estágio)."""
    _run_seed()
    messages = build_case_prompts("llm2", ("art_perif",))
    user_content = messages[1]["content"]
    for placeholder in ALL_PLACEHOLDERS:
        assert placeholder in user_content
    assert messages[0]["content"] == PromptTemplate.get_active_prompt("llm2.system").content


@pytest.mark.django_db
def test_build_extra_context_appended() -> None:
    """R3: extra_context (ex.: instrução corretiva de retry) entra no fim do user."""
    _run_seed()
    messages = build_case_prompts(
        "llm1", ("art_perif",), extra_context="Instrução corretiva: resposta anterior inválida."
    )
    assert messages[1]["content"].endswith("Instrução corretiva: resposta anterior inválida.")


@pytest.mark.django_db
def test_missing_active_named_error() -> None:
    """R3: tipo sem prompt ativo → erro nomeando o tipo."""
    _run_seed()
    name = user_prompt_name("art_perif", "llm1")
    PromptTemplate.objects.filter(name=name).update(is_active=False)
    with pytest.raises(ActivePromptNotFoundError) as excinfo:
        build_case_prompts("llm1", ("art_perif",))
    assert "art_perif" in str(excinfo.value)
    assert "llm1" in str(excinfo.value)


@pytest.mark.django_db
def test_missing_active_system_named_error() -> None:
    """R3: system do estágio sem versão ativa também falha de forma explícita."""
    _run_seed()
    PromptTemplate.objects.filter(name="llm1.system").update(is_active=False)
    with pytest.raises(ActivePromptNotFoundError) as excinfo:
        build_case_prompts("llm1", ("art_perif",))
    assert "llm1.system" in str(excinfo.value)


# ── R4: prompt_usage (payload enxuto de evento) ─────────────────────────────


@pytest.mark.django_db
def test_prompt_usage_payload() -> None:
    """R4: names+versions dos prompts ativos usados — enxuto, ordem canônica."""
    _run_seed()
    usage = prompt_usage("llm1", ("filtro_cava", "art_perif"))
    assert usage == {
        "llm1.system": 1,
        user_prompt_name("art_perif", "llm1"): 1,
        user_prompt_name("filtro_cava", "llm1"): 1,
    }
