"""Templatetags de ``apps.accounts`` (role-labels-ptbr, R2).

Primeiro templatetags do repo: expõe o filtro ``role_label`` reusando a fonte
única ``apps.accounts.role_labels`` — nenhuma tabela de tradução paralela.
Descoberto por ``APP_DIRS=True`` (sem override de ``loaders``).
"""

from __future__ import annotations

from django import template

from apps.accounts.role_labels import role_label

register = template.Library()
register.filter("role_label", role_label)
