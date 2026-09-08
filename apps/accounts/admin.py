"""Django admin de ``apps.accounts`` (change ad-kerberos, slice 001 R2/R3).

Superfície de provisionamento administrativo: usuários do AD entram com o
``ad_upn`` completo e papéis são atribuídos pelo M2M ``roles``. Usuários com
``ad_upn`` (identidade AD, D3) nunca têm senha local utilizável — o
``save_model`` aplica ``set_unusable_password()`` sempre que o ``ad_upn`` está
preenchido; usuários locais existentes preservam a senha (R3).
"""

from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.forms.models import BaseModelForm
from django.http import HttpRequest

from .models import Role, User


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Admin simples do ``Role`` (papéis fixos)."""

    list_display = ("name",)
    search_fields = ("name",)


@admin.register(User)
class UserAdmin(BaseUserAdmin):  # type: ignore[type-arg]
    """UserAdmin com os campos do HMD e a regra de senha do AD (R2/R3)."""

    list_display = ("username", "ad_upn", "account_status", "is_staff")
    list_filter = ("account_status",)
    search_fields = ("username", "ad_upn")
    filter_horizontal = ("groups", "user_permissions", "roles", "specialties")
    fieldsets = (
        *(BaseUserAdmin.fieldsets or ()),
        (
            "Acesso institucional",
            {"fields": ("ad_upn", "account_status", "roles", "specialties")},
        ),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "ad_upn",
                    "usable_password",
                    "password1",
                    "password2",
                ),
            },
        ),
        (
            "Acesso institucional",
            {"fields": ("account_status", "roles", "specialties")},
        ),
    )

    def save_model(
        self,
        request: HttpRequest,
        obj: User,
        form: BaseModelForm[User],
        change: bool,
    ) -> None:
        """Usuário com ``ad_upn`` não possui senha local utilizável (R3)."""
        if obj.ad_upn:
            obj.set_unusable_password()
        super().save_model(request, obj, form, change)
