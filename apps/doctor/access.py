"""Access control da fila/detalhe/decisão médica (doctor-queue-decision, D2).

Matriz fechada do D2 resolvida pelo **papel ativo** da sessão (decisão do dono
2026-09-08): o acesso é composicional via multi-role — a linha da matriz é a
do papel ativo, nunca o conjunto de papéis do usuário nem atributos globais
como ``is_superuser``. O guard de view
(``apps.accounts.decorators.role_required("doctor", "admin")``) restringe o
papel ativo; este módulo resolve a fatia por subtipo sobre o papel ativo:

- papel ativo ``admin`` → todos os casos (``is_superuser`` NÃO é critério
  próprio: superuser com papel ativo ``doctor`` é médico como qualquer outro);
- papel ativo ``doctor`` generalista (sem subtipos) → todos os casos;
- papel ativo ``doctor`` com subtipos S → casos com ≥1 tipo declarado cujo
  ``doctor_subtipo`` esteja em S;
- demais papéis ativos não chegam a este predicado (guard) — resposta
  fail-closed por segurança.

``can_access_case`` é o **predicado único** compartilhado pela fila (slice 002)
e reutilizado pelo detalhe e pela decisão (slices 003/004).
``DoctorAccess`` computa os fatos do usuário **1× por request** (P2 — sem
re-consultar roles/specialties por card): as views da fila constroem um
snapshot e o reutilizam nos filtros SQL, nas contagens e no re-check por card.
"""

from __future__ import annotations

from collections.abc import Iterable

from apps.accounts.models import User
from apps.accounts.subtypes import user_doctor_subtypes
from apps.cases.models import Case
from apps.cases.procedure_catalog import PROCEDURE_PROFILES, VALID_DOCTOR_SUBTYPES
from apps.cases.procedures import get_declared_procedure_types

# Papéis ativos da matriz D2 que chegam a este módulo (guard
# ``role_required("doctor", "admin")``).
DOCTOR_ROLE_NAME = "doctor"
ADMIN_ROLE_NAME = "admin"

_VALID_SUBTYPES_INDEX = {subtype: index for index, subtype in enumerate(VALID_DOCTOR_SUBTYPES)}

# Tipos do catálogo por subtipo do médico (regra médica): médico com subtipos
# S decide os casos com ≥1 tipo declarado cujo subtipo está em S.
_TYPES_BY_DOCTOR_SUBTYPE: dict[str, frozenset[str]] = {
    subtype: frozenset(
        profile.procedure_type
        for profile in PROCEDURE_PROFILES
        if profile.doctor_subtipo == subtype
    )
    for subtype in VALID_DOCTOR_SUBTYPES
}


class DoctorAccess:
    """Fatos de acesso do usuário por papel ativo (D2) — 1× por request (P2).

    Derivado apenas do papel ativo da sessão e das ``specialties`` do usuário
    (papel ativo ``doctor``) — nenhuma consulta por conjunto de papéis nem por
    superusuário: a matriz chaveia no papel ativo.

    Atributos estáveis por request:
    - ``allowed_types``: tipos declarados que autorizam um caso; ``None`` =
      todos (linha admin/generalista); conjunto vazio = ninguém (papel ativo
      fora da matriz, fail-closed);
    - ``filterable``: subtipos do dropdown/filtro (R4/D5);
    - ``allows_declared_types``: predicado por conjunto declarado do caso.
    """

    def __init__(self, user: User, *, active_role: str) -> None:
        if active_role == ADMIN_ROLE_NAME:
            self.allowed_types: frozenset[str] | None = None
            self.filterable: tuple[str, ...] = VALID_DOCTOR_SUBTYPES
        elif active_role == DOCTOR_ROLE_NAME:
            subtypes = frozenset(user_doctor_subtypes(user))
            if not subtypes:
                self.allowed_types = None
                self.filterable = VALID_DOCTOR_SUBTYPES
            else:
                self.allowed_types = frozenset(
                    procedure_type
                    for subtype in subtypes
                    for procedure_type in _TYPES_BY_DOCTOR_SUBTYPE[subtype]
                )
                self.filterable = tuple(sorted(subtypes, key=_VALID_SUBTYPES_INDEX.__getitem__))
        else:
            # Fora da matriz (o guard bloqueia antes): nada fica acessível.
            self.allowed_types = frozenset()
            self.filterable = ()

    def allows_declared_types(self, declared_types: Iterable[str]) -> bool:
        """Predicado D2 sobre o conjunto declarado do caso.

        ``allowed_types is None`` (linha admin/médico generalista) → True;
        senão True apenas quando ≥1 tipo declarado está entre os permitidos
        (interseção com o conjunto do médico não vazia). Usado no re-check por
        card da fila com os tipos derivados das rows já pré-carregadas
        (sem query por caso — P2).
        """
        if self.allowed_types is None:
            return True
        return any(procedure_type in self.allowed_types for procedure_type in declared_types)


def filterable_subtypes(user: User, *, active_role: str) -> tuple[str, ...]:
    """Subtipos do filtro/dropdown do usuário (R4/D5) por papel ativo.

    Linha admin e médico generalista (doctor sem subtipos) → qualquer subtipo
    do catálogo; doctor com subtipos S → S. Ordem canônica de
    ``VALID_DOCTOR_SUBTYPES``.
    """
    return DoctorAccess(user, active_role=active_role).filterable


def declared_types_allowed(user: User, *, active_role: str) -> frozenset[str] | None:
    """Tipos declarados que autorizam um caso ao papel ativo; ``None`` = todos.

    Base única da regra, compartilhada pelo predicado ``can_access_case`` e
    pelo filtro SQL da fila (D5 — consistência fila×detalhe): ``None`` para a
    linha admin e o médico generalista; senão, os tipos de procedimento cujo
    ``doctor_subtipo`` está entre os subtipos do médico (papel ativo doctor) —
    um caso é acessível quando a interseção entre os seus tipos declarados e
    este conjunto é não vazia.
    """
    return DoctorAccess(user, active_role=active_role).allowed_types


def can_access_case(user: User, case: Case, *, active_role: str) -> bool:
    """Predicado único de acesso ao caso (matriz D2, linha por papel ativo).

    True para o papel ativo ``admin`` e o médico generalista; médico com
    subtipos S → True apenas quando o caso tem ≥1 tipo declarado cujo
    ``doctor_subtipo`` está em S. O guard ``role_required("doctor", "admin")``
    já validou o papel ativo na view; ``is_superuser`` não é critério próprio —
    superuser com papel ativo ``doctor`` segue a regra médica como qualquer
    médico.
    """
    access = DoctorAccess(user, active_role=active_role)
    return access.allows_declared_types(get_declared_procedure_types(case))
