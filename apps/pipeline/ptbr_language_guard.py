"""Language guard pt-BR (slice 004, R2, herança do ats-web).

Comportamento real do ats-web (``apps/pipeline/ptbr_language_guard.py``):
detecção de resíduo de outro idioma nos campos narrativos por **lista
canônica de marcadores** — não por métrica de proporção. A lista é versionada
neste módulo (``LANGUAGE_GUARD_VERSION``): termos ingleses fortes que não
ocorrem legitimamente em texto clínico narrativo pt-BR, casados por borda de
palavra e case-insensitive. Quem consome (llm1_service) decide quais campos
do artefato são narrativos.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# Versão da lista canônica de marcadores (dado versionado — edição exige bump).
LANGUAGE_GUARD_VERSION = 1

# Marcadores de outro idioma (inglês) em texto narrativo pt-BR. Palavras
# inglesas comuns em relato clínico que não são vocábulos do português — um
# hit legítimo indica resíduo de idioma (não proporção: qualquer ocorrência
# rejeita a resposta e aciona o retry corretivo).
_FORBIDDEN_ENGLISH_TERMS_PATTERN = re.compile(
    r"\b("
    r"accept|accepted|admission|admitted|because|denied|deny|discharge|"
    r"discharged|history|however|insufficient|medications|none|patient|"
    r"reason|recommendation|recommended|required|summary|support|therefore|"
    r"unknown"
    r")\b",
    re.IGNORECASE,
)


def collect_forbidden_terms(*, texts: Iterable[str]) -> list[str]:
    """Marcadores de outro idioma encontrados nos textos narrativos.

    Devolve os termos únicos em minúsculas, ordenados — vazio = idioma ok.
    """
    found: set[str] = set()
    for text in texts:
        for match in _FORBIDDEN_ENGLISH_TERMS_PATTERN.finditer(text):
            found.add(match.group(0).lower())
    return sorted(found)
