#!/usr/bin/env python
"""Utilitário de linha de comando do Django (HMD)."""

import os
import sys


def main() -> None:
    """Roda as tarefas administrativas."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Não foi possível importar o Django. Ele está instalado e "
            "disponível no PYTHONPATH? Você esqueceu de ativar o virtualenv?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
