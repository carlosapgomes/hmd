"""Seed administrativo idempotente do HMD (slice 003, R4/R6).

Cria os cinco papéis fixos (``nir``, ``doctor``, ``scheduler``, ``manager``,
``admin``) e um superusuário multi-role com os cinco papéis. O username vem da
env não-sensível ``DJANGO_SUPERUSER_USERNAME``; a senha vem da env
``DJANGO_SUPERUSER_PASSWORD`` OU do ARQUIVO ``DJANGO_SUPERUSER_PASSWORD_FILE``
(change pilot-deployment-v0-1-1, slice 005/R2 — arquivo com precedência e
fail-closed, nunca senha em env do compose). Pode rodar quantas vezes for
preciso: nada é duplicado.

Uso:
    DJANGO_SUPERUSER_USERNAME=admin DJANGO_SUPERUSER_PASSWORD=secret \\
        uv run python manage.py seed_admin --settings=config.settings.dev
"""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Role
from config.settings.db import _read_secret

User = get_user_model()

ALL_ROLES = ["nir", "doctor", "scheduler", "manager", "admin"]


class Command(BaseCommand):
    help = "Cria os papéis fixos e o superusuário multi-role (idempotente)"

    def handle(self, *args: object, **options: object) -> None:
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME")
        # Arquivo com precedência sobre a env (slice 005/R2), mesma semântica
        # do banco: arquivo ilegível/vazio aborta com ImproperlyConfigured.
        password = _read_secret(os.environ, "DJANGO_SUPERUSER_PASSWORD_FILE")
        if password is None:
            password = os.environ.get("DJANGO_SUPERUSER_PASSWORD")
        if not username or not password:
            raise CommandError(
                "DJANGO_SUPERUSER_USERNAME e DJANGO_SUPERUSER_PASSWORD (ou "
                "DJANGO_SUPERUSER_PASSWORD_FILE) devem estar definidas no "
                "ambiente para o seed_admin."
            )

        # Papéis fixos (get_or_create → idempotente).
        roles = list[Role]()
        for role_name in ALL_ROLES:
            role, created = Role.objects.get_or_create(name=role_name)
            roles.append(role)
            if created:
                self.stdout.write(f"  Created role: {role_name}")

        # Superusuário multi-role (get_or_create → não duplica em re-execução).
        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "is_superuser": True,
                "is_staff": True,
                "is_active": True,
            },
        )
        if created:
            user.set_password(password)
            user.save(update_fields=["password"])
            self.stdout.write(self.style.SUCCESS(f"  Created user: {username}"))
        else:
            # Re-execução: reforça flags de superusuário e papéis, sem resetar
            # a senha definida localmente nem duplicar nada.
            user.is_superuser = True
            user.is_staff = True
            user.is_active = True
            user.save(update_fields=["is_superuser", "is_staff", "is_active"])
            self.stdout.write(f"  User already exists: {username}")

        # add() é idempotente: re-execuções não duplicam a associação.
        user.roles.add(*roles)
        self.stdout.write(self.style.SUCCESS(f"  Assigned {len(roles)} roles to {username}"))
