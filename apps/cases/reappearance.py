"""Classificador conservador de identidade de reapresentação (slice 001, D1).

Serviço de domínio PURO (sem DB, sem I/O) compartilhado pelo pipeline
(prior-case, S1) e pelos fluxos de fila/conferência (S2/S5): decide, para um
PAR de casos, se há evidência forte, necessidade de conferência NIR ou
ausência de candidato — a partir de nome normalizado, regulação, CNS válido,
origem e idades, sempre com o MESMO conjunto efetivo declarado de
procedimentos.

Matriz D1 (nome igual + procedimento igual exigidos em todos os ramos):

| evidência                            | resultado            |
| regulação igual não vazia OU CNS válido igual | forte, se único e sem conflito |
| forte com idades informadas e diferença > 1   | conferência NIR      |
| nome + origem iguais; idades ausentes ou diff <= 1 | conferência NIR |
| somente nome + origem e diferença > 1          | sem candidato        |
| nome diferente, procedimento diferente ou sem segunda evidência | sem candidato |

CNS válidos diferentes — mesmo com regulação igual — impedem a automação e
geram conferência com conflito visível; regulações diferentes NÃO são
conflito por si sós (motivo da feature). Idade zero é informada (válida);
``None`` é ausente. Vazio nunca é igualdade. Mais de um candidato ativo
plausível impede a seleção automática — a unicidade é decidida pelo chamador
(prior-case/ fila), nunca por cadeia transitiva aqui.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

# Veredito do par: ``strong`` (automação permitida), ``needs_confirmation``
# (somente conferência NIR — sem efeito até S5) ou ``no_match``.
ReappearanceVerdict = Literal["strong", "needs_confirmation", "no_match"]

# Origens canônicas do match (compartilhadas com o pipeline e o presenter —
# D3): ``occurrence_number`` (regulação), ``patient_cns`` (CNS estrutural),
# ``nir_confirmed`` (conferência NIR vigente — consumida só em S5) e ``none``
# (evento sem match). ``name_birthdate_fallback`` existe apenas no modo legado
# e em eventos históricos.
PriorCaseOrigin = Literal[
    "occurrence_number",
    "patient_cns",
    "nir_confirmed",
    "none",
    "name_birthdate_fallback",
]

# Rótulos pt-BR por origem (fonte única consumida pelo presenter médico).
PRIOR_ORIGIN_LABELS: dict[str, str] = {
    "occurrence_number": "Nº de ocorrência",
    "patient_cns": "CNS",
    "nir_confirmed": "Confirmado pelo NIR",
    "name_birthdate_fallback": "Nome e nascimento",
}


@dataclass(frozen=True)
class IdentitySignals:
    """Sinais de identidade de um caso para o pareamento (valores brutos).

    ``patient_cns`` deve chegar validado-ou-vazio (o wrapper de anonimização
    só persiste dígitos de ``extract_patient_cns``); ``patient_age`` usa
    ``None`` para ausente (zero é idade informada válida).
    """

    name: str = ""
    record_number: str = ""
    patient_cns: str = ""
    origin_unit: str = ""
    patient_age: int | None = None
    procedures: tuple[str, ...] = ()


@dataclass(frozen=True)
class Classification:
    """Veredito do par com origem e conflito.

    ``origin`` é a origem do match SOMENTE quando ``verdict == "strong"``
    (``occurrence_number`` precede ``patient_cns``); em ``needs_confirmation``
    a origem só existe após a confirmação (``nir_confirmed``, S5) — aqui é
    ``None``. ``conflict`` marca CNS divergentes (visível à conferência).
    """

    verdict: ReappearanceVerdict
    origin: PriorCaseOrigin | None
    conflict: bool = False


def normalize_person_name(name: str) -> str:
    """Nome normalizado para o pareamento: sem acentos/maiúsculas/sem espaços.

    Implementação pura em Python (NFKD → ASCII + upper + remoção de
    whitespace) — canônica do linkage (``apps.pipeline.prior_case`` a
    reexporta como ``normalize_name``); sem distância aproximada.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", "", ascii_text).upper()


def normalize_origin_unit(value: str) -> str:
    """Origem normalizada: caixa/acentos/espaços repetidos (D1, sem aliases)."""
    decomposed = unicodedata.normalize("NFKD", value.strip())
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_text).upper().strip()


def effective_procedure_sets_equal(first: Iterable[str], second: Iterable[str]) -> bool:
    """Igualdade do conjunto EFETIVO declarado de tipos (D1).

    Compara como conjuntos (ordem irrelevante, sem duplicatas); tipo diferente
    nunca se funde — nem por CNS. Conjuntos vazios nunca são iguais aqui (sem
    procedimento não há o que parear).
    """
    first_set = set(first)
    second_set = set(second)
    if not first_set or not second_set:
        return False
    return first_set == second_set


def _valid_cns_digits(value: str) -> bool:
    """CNS válido (reuso de ``valid_cns`` sem dependência de import do módulo)."""
    from apps.anonymization.recognizers import valid_cns

    return valid_cns(value)


def _ages_compatible(first: int | None, second: int | None) -> bool:
    """Idades compatíveis: ausentes ou diferença de até um ano (D1).

    ``None`` é ausente (não impede); zero é informado. Diferença > 1 com ambas
    informadas exige conferência — nunca automação.
    """
    if first is None or second is None:
        return True
    return abs(first - second) <= 1


def classify_pair(current: IdentitySignals, prior: IdentitySignals) -> Classification:
    """Classifica o par (atual, prévio) pela matriz D1 (puro, sem DB)."""
    if not normalize_person_name(current.name) or not normalize_person_name(prior.name):
        return Classification(verdict="no_match", origin=None)
    if normalize_person_name(current.name) != normalize_person_name(prior.name):
        return Classification(verdict="no_match", origin=None)
    if not effective_procedure_sets_equal(current.procedures, prior.procedures):
        return Classification(verdict="no_match", origin=None)

    current_number = current.record_number.strip()
    prior_number = prior.record_number.strip()
    number_equal = bool(current_number) and current_number == prior_number

    current_cns = current.patient_cns.strip()
    prior_cns = prior.patient_cns.strip()
    current_cns_valid = bool(current_cns) and _valid_cns_digits(current_cns)
    prior_cns_valid = bool(prior_cns) and _valid_cns_digits(prior_cns)
    cns_equal = bool(current_cns_valid and prior_cns_valid and current_cns == prior_cns)
    # CNS válidos diferentes — mesmo com regulação igual — impedem a automação
    # e geram conferência com conflito visível (D1, linha 5).
    cns_conflict = bool(current_cns_valid and prior_cns_valid and current_cns != prior_cns)
    if cns_conflict:
        return Classification(verdict="needs_confirmation", origin=None, conflict=True)

    if number_equal or cns_equal:
        if not _ages_compatible(current.patient_age, prior.patient_age):
            return Classification(verdict="needs_confirmation", origin=None)
        origin: PriorCaseOrigin | None = "occurrence_number" if number_equal else "patient_cns"
        return Classification(verdict="strong", origin=origin)

    current_origin = normalize_origin_unit(current.origin_unit)
    prior_origin = normalize_origin_unit(prior.origin_unit)
    if current_origin and current_origin == prior_origin:
        if _ages_compatible(current.patient_age, prior.patient_age):
            return Classification(verdict="needs_confirmation", origin=None)
        return Classification(verdict="no_match", origin=None)

    return Classification(verdict="no_match", origin=None)
