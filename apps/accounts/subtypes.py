"""Helper puro de subtipos de médico (doctor-queue-decision, slice 001, R3).

Alimenta o access control e o filtro de fila dos slices seguintes sem
duplicar a regra: conjunto vazio = generalista (vê qualquer subtipo).
Assume ``User`` autenticado — usuários anônimos/inativos não ocorrem como
instância deste model. Sem I/O de rede.
"""

from __future__ import annotations

from .models import User


def user_doctor_subtypes(user: User) -> set[str]:
    """Subtipos de médico do usuário (``set()`` = generalista)."""
    return set(user.specialties.values_list("name", flat=True))
