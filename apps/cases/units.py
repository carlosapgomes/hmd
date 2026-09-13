"""Rótulos exibíveis das unidades de agendamento (change unit-labels-env, D2).

Fonte única da resolução: os rótulos vêm de ``settings.UNIT_LABELS`` (envs
``HMD_UNIT_1_LABEL``/``HMD_UNIT_2_LABEL`` com os defaults canônicos). A
exibição de produto consome daqui; ``SchedulingUnit`` permanece o
canônico/default do model (``get_FOO_display``, migration) e a validação de
entrada segue ``unit ∈ {1, 2}``.
"""

from __future__ import annotations

from django.conf import settings


def unit_labels() -> dict[int, str]:
    """Fonte única dos rótulos das unidades — lidos de settings, sem cache."""
    return dict(settings.UNIT_LABELS)


def unit_label(value: int) -> str:
    """Rótulo da unidade ``value``; vazio quando não há rótulo (fallback atual)."""
    return unit_labels().get(value, "")
