"""Presenter do card «Sumário apresentado ao médico» (slice 002, D5/R2).

Serviço puro ``build_case_summary_context(case)`` (sem request) que monta o
dict de contexto do card a partir dos artefatos persistidos — o template
apenas renderiza. A re-identificação acontece **exclusivamente aqui, na
renderização**: ``reidentify_text`` no ``summary_text`` e
``reidentify_structure`` no ``structured_data`` com o mapa do caso — a
barreira "LLM só vê tokens" não muda (nenhuma chamada LLM neste módulo).

As seções usam a API compartilhada ``build_structure_sections``
(``apps.cases.presentation``) — a MESMA fonte e a MESMA forma de
apresentação do detalhe médico, sem loop reimplementado; ``summary_lines``
segue a MESMA regra do presenter médico (linhas não-vazias do texto
re-identificado), garantindo paridade entre o que o médico viu e o que o
auditor vê (D5).
"""

from __future__ import annotations

from apps.anonymization.reidentify import reidentify_structure, reidentify_text
from apps.cases.models import Case
from apps.cases.presentation import build_structure_sections


def build_case_summary_context(case: Case) -> dict[str, object]:
    """Contexto re-identificado do card «Sumário apresentado ao médico» (R2).

    ``summary_lines``: linhas não-vazias do ``summary_text`` re-identificado
    (mesma regra do detalhe médico). ``sections``: as 8 seções do artefato
    LLM1 via API compartilhada, sobre o ``structured_data`` re-identificado
    com o mapa do caso. ``has_structure``: há ao menos uma seção com linhas.
    Caso sem artefatos → linhas/seções vazias e ``has_structure`` falso; o
    template exibe os placeholders explícitos por bloco (D6).
    """
    pseudonym_map = case.pseudonym_map if isinstance(case.pseudonym_map, dict) else {}

    structured = reidentify_structure(case.structured_data, pseudonym_map)
    summary_text = reidentify_text(case, case.summary_text)

    summary_lines = (
        [line.strip() for line in summary_text.splitlines() if line.strip()]
        if summary_text.strip()
        else []
    )
    sections = build_structure_sections(structured)

    return {
        "summary_lines": summary_lines,
        "sections": sections,
        "has_structure": any(section["lines"] for section in sections),
    }
