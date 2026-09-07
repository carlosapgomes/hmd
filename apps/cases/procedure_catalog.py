"""Catálogo dos 13 tipos de procedimento do HMD — code-first (change 03, slice 001).

Fonte clínica autoritativa: ``temp/parametrosHMD.md`` e
``temp/plano-implementacao-hmd.md`` §2 (tabela dos 13 tipos, seções S1–S8 e
lista de suporte anestésico). O catálogo é o registro consultável consumido
pelo núcleo de casos; os thresholds vivem aqui como **dados** — a
interpretação como policy é do change 06. O lookup é fail-fast: tipo fora do
catálogo levanta ``KeyError`` nomeando o tipo (divergência deliberada vs.
``ats-web``, que tem fallback para EDA). Novos tipos = mudança de código em
change explícito, sem migração de dados.

Nota de fonte compartilhada (INR): em pacientes em hemodiálise, TP/TTPa (e o
INR) devem ser colhidos **após a última sessão** — regra de coleta do
``parametrosHMD.md`` que acompanha toda exigência de ``INR < 1,5``.
"""

from __future__ import annotations

from dataclasses import dataclass

# Identificadores canônicos das seções de critérios (ordem da fonte).
SECTION_IDS: tuple[str, ...] = (
    "S1",
    "S2",
    "S3",
    "S4",
    "S5",
    "S6",
    "S7",
    "S8",
)

# Subtipos de doctor usados no roteamento de fila (plano §2 / spec).
VALID_DOCTOR_SUBTYPES: tuple[str, ...] = ("angio", "neuro", "cardio", "radio")

# Número fechado de procedimentos do HMD (contrato do change 01/P2).
EXPECTED_PROCEDURE_COUNT = 13


@dataclass(frozen=True)
class ProcedureProfile:
    """Perfil consultável de um tipo de procedimento do catálogo."""

    procedure_type: str
    label: str
    criteria_section: str
    doctor_subtipo: str
    anesthetic_support: bool


@dataclass(frozen=True)
class CriteriaSection:
    """Critérios clínicos de uma seção S1–S8 (thresholds como dados).

    Campos numéricos opcionais só preenchidos quando a seção os exige;
    ``conditions`` carrega os requisitos clínicos qualitativos da fonte
    (ex.: "PA estável", "sem sinais de choque", "sem infecção sistêmica
    ativa").
    """

    section_id: str
    procedure_types: tuple[str, ...]
    # Plaquetas mínimas (mm³).
    platelets_min: int | None = None
    # INR máximo aceito (INR < valor).
    inr_max: float | None = None
    # Hemoglobina mínima (g/dL).
    hemoglobin_min: float | None = None
    # Creatinina sérica máxima (mg/dL) — Cr < valor.
    creatinine_max: float | None = None
    # Faixa de pressão arterial sistólica (mmHg) quando a seção é numérica.
    systolic_bp_min: int | None = None
    systolic_bp_max: int | None = None
    # Glicemia máxima (mg/dL) — glicemia < valor.
    glucose_max: int | None = None
    # Eletrólitos: K normal exigido pela seção.
    potassium_required: bool = False
    # Requisitos clínicos qualitativos adicionais da fonte.
    conditions: tuple[str, ...] = ()


# Registro ordenado exatamente como na tabela da fonte clínica (§2 do plano).
PROCEDURE_PROFILES: tuple[ProcedureProfile, ...] = (
    ProcedureProfile("art_perif", "Arteriografia periférica", "S1", "angio", False),
    ProcedureProfile("flebografia", "Flebografia", "S1", "angio", False),
    ProcedureProfile(
        "angio_art_perif",
        "Angioplastia arterial periférica (membros)",
        "S2",
        "angio",
        True,
    ),
    ProcedureProfile("angio_venosa_central", "Angioplastia venosa central", "S3", "angio", True),
    ProcedureProfile("angio_fav", "Angioplastia de fístula arteriovenosa", "S4", "angio", True),
    ProcedureProfile(
        "permicath",
        "Implante de cateter de longa permanência (permicath)",
        "S5",
        "angio",
        True,
    ),
    ProcedureProfile("filtro_cava", "Implante de filtro de veia cava", "S6", "angio", True),
    ProcedureProfile("angio_carotidas", "Angioplastia de carótidas", "S2", "angio", True),
    ProcedureProfile("art_cerebral", "Arteriografia cerebral", "S1", "neuro", True),
    ProcedureProfile("cat_cardiaco", "Cateterismo cardíaco", "S1", "cardio", True),
    ProcedureProfile("angio_coronariana", "Angioplastia coronariana", "S2", "cardio", True),
    ProcedureProfile("dren_biliar", "Drenagem biliar percutânea", "S7", "radio", True),
    ProcedureProfile("nefrostomia", "Nefrostomia percutânea", "S8", "radio", True),
)

_PROFILES_BY_TYPE: dict[str, ProcedureProfile] = {
    profile.procedure_type: profile for profile in PROCEDURE_PROFILES
}

# Thresholds por seção + tipos que usam cada seção (consultável por seção e,
# via ``get_criteria_section``/perfis, por tipo).
CRITERIA_SECTIONS: dict[str, CriteriaSection] = {
    "S1": CriteriaSection(
        section_id="S1",
        procedure_types=("art_perif", "flebografia", "art_cerebral", "cat_cardiaco"),
        platelets_min=100_000,
        inr_max=1.5,
        hemoglobin_min=9.0,
        creatinine_max=1.5,
        systolic_bp_min=90,
        systolic_bp_max=190,
        potassium_required=True,
        conditions=("sem sinais de choque",),
    ),
    "S2": CriteriaSection(
        section_id="S2",
        procedure_types=("angio_art_perif", "angio_carotidas", "angio_coronariana"),
        platelets_min=100_000,
        inr_max=1.5,
        hemoglobin_min=9.0,
        creatinine_max=1.5,
        systolic_bp_max=180,
        glucose_max=180,
        potassium_required=True,
        conditions=("pressão arterial controlada",),
    ),
    "S3": CriteriaSection(
        section_id="S3",
        procedure_types=("angio_venosa_central",),
        platelets_min=100_000,
        inr_max=1.5,
        hemoglobin_min=9.0,
        potassium_required=True,
    ),
    "S4": CriteriaSection(
        section_id="S4",
        procedure_types=("angio_fav",),
        platelets_min=100_000,
        inr_max=1.5,
        systolic_bp_min=100,
        systolic_bp_max=160,
        conditions=("condição do acesso vascular (fístula) avaliada previamente",),
    ),
    "S5": CriteriaSection(
        section_id="S5",
        procedure_types=("permicath",),
        platelets_min=100_000,
        inr_max=1.5,
        hemoglobin_min=8.0,
        systolic_bp_min=100,
        systolic_bp_max=160,
        potassium_required=True,
        conditions=(
            "sem infecção sistêmica ativa",
            "acesso vascular avaliado previamente por ultrassom",
        ),
    ),
    "S6": CriteriaSection(
        section_id="S6",
        procedure_types=("filtro_cava",),
        platelets_min=50_000,
        inr_max=1.5,
        conditions=("PA estável",),
    ),
    "S7": CriteriaSection(
        section_id="S7",
        procedure_types=("dren_biliar",),
        platelets_min=50_000,
        inr_max=1.5,
        hemoglobin_min=8.0,
        conditions=("PA estável",),
    ),
    "S8": CriteriaSection(
        section_id="S8",
        procedure_types=("nefrostomia",),
        platelets_min=50_000,
        inr_max=1.5,
        hemoglobin_min=8.0,
        conditions=("PA estável",),
    ),
}


def get_procedure_profile(procedure_type: str) -> ProcedureProfile:
    """Resolve o perfil de um tipo do catálogo.

    Fail-fast (divergência HMD vs. ats-web): tipo desconhecido levanta
    ``KeyError`` nomeando o tipo — nunca há fallback silencioso.
    """
    try:
        return _PROFILES_BY_TYPE[procedure_type]
    except KeyError:
        raise KeyError(f"procedimento fora do catálogo: {procedure_type!r}") from None


def get_criteria_section(procedure_type: str) -> CriteriaSection:
    """Retorna a seção de critérios (com thresholds) usada por um tipo."""
    profile = get_procedure_profile(procedure_type)
    return CRITERIA_SECTIONS[profile.criteria_section]


def catalog_problems() -> tuple[str, ...]:
    """Problemas de coerência do registro (vazio = catálogo íntegro).

    Valida: 13 tipos, sem duplicatas, subtipos válidos, seções S1–S8
    existentes e seção↔tipos coerente com ``CRITERIA_SECTIONS``. Usado pelo
    comando ``seed_procedure_catalog`` e pelos testes (R6).
    """
    problems: list[str] = []

    registered = tuple(p.procedure_type for p in PROCEDURE_PROFILES)
    if len(registered) != EXPECTED_PROCEDURE_COUNT:
        problems.append(
            f"esperados {EXPECTED_PROCEDURE_COUNT} perfis, encontrados {len(registered)}"
        )
    duplicates = sorted({t for t in registered if registered.count(t) > 1})
    if duplicates:
        problems.append(f"tipos duplicados: {', '.join(duplicates)}")

    invalid_subtypes = sorted(
        {p.doctor_subtipo for p in PROCEDURE_PROFILES} - set(VALID_DOCTOR_SUBTYPES)
    )
    if invalid_subtypes:
        problems.append(f"subtipos inválidos: {', '.join(invalid_subtypes)}")

    missing_sections = sorted(set(SECTION_IDS) - set(CRITERIA_SECTIONS))
    if missing_sections:
        problems.append(f"seções ausentes em CRITERIA_SECTIONS: {', '.join(missing_sections)}")
    referenced = {p.criteria_section for p in PROCEDURE_PROFILES}
    unknown_sections = sorted(referenced - set(CRITERIA_SECTIONS))
    if unknown_sections:
        problems.append(
            f"seções referenciadas pelos perfis e inexistentes: {', '.join(unknown_sections)}"
        )

    for section_id, section in CRITERIA_SECTIONS.items():
        if section.section_id != section_id:
            problems.append(f"seção {section_id}: id interno divergente ({section.section_id!r})")
        expected_types = {
            p.procedure_type for p in PROCEDURE_PROFILES if p.criteria_section == section_id
        }
        if set(section.procedure_types) != expected_types:
            problems.append(
                f"seção {section_id}: tipos divergentes dos perfis "
                f"({', '.join(section.procedure_types)})"
            )
    if set(CRITERIA_SECTIONS) != set(SECTION_IDS):
        problems.append("CRITERIA_SECTIONS deve conter exatamente S1–S8")

    return tuple(problems)
