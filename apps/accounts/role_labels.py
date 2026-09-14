"""Fonte única dos rótulos de papel em português (role-labels-ptbr, R1/D1).

As chaves de papel (``Role.name``, ``active_role`` de sessão, guards) são os
identificadores canônicos de dados/sessão/permissionamento e NÃO mudam — só a
exibição ao usuário passa por este mapeamento. Os 5 papéis do seed aparecem
EXPLICITAMENTE (inclusive ``nir``/``admin``, que exibem a própria chave) para
que o mapeamento seja assertável por inteiro, não implícito no fallback.
"""

from __future__ import annotations

ROLE_LABELS: dict[str, str] = {
    "doctor": "médico",
    "scheduler": "agendador",
    "manager": "supervisor",
    "nir": "nir",
    "admin": "admin",
}


def role_label(value: str) -> str:
    """Rótulo exibível de ``value``; fallback = a própria chave (D3).

    Chave fora do mapeamento (ex.: ``system`` das trilhas, ``nurse`` de teste)
    exibe a própria chave em vez de quebrar ou esconder o papel.
    """
    return ROLE_LABELS.get(value, value)
