"""Fixtures do diretório de testes de ``apps/accounts`` (slice 004).

O cache LocMem do Django é global ao processo e o ``django_db`` do
pytest-django não o limpa entre testes (diferente do ``TestCase`` do Django).
O anti-lockout do slice 004 usa esse cache para os contadores por CPF/IP+CPF —
sem a limpeza, o estado de um teste vazaria para o próximo.
"""

from collections.abc import Iterator

import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _clear_cache_between_tests() -> Iterator[None]:
    """Zera o cache antes e depois de cada teste do diretório."""
    cache.clear()
    yield
    cache.clear()
