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
from django.contrib.auth.forms import (
    ReadOnlyPasswordHashField,
    ReadOnlyPasswordHashWidget,
)
from django.contrib.auth.forms import (
    UserChangeForm as BaseUserChangeForm,
)
from django.forms.models import BaseModelForm
from django.http import HttpRequest
from django.urls import URLPattern

from .models import Role, User

HELP_TEXT_SENHA_FORA_DO_APLICATIVO = (
    "Gerida fora do aplicativo — a troca de senha é feita pessoalmente na TI do hospital."
)


class ReadOnlyPasswordHashSemBotaoWidget(ReadOnlyPasswordHashWidget):
    """Widget do campo ``password`` sem o botão de troca (change bloqueio-troca-de-senha).

    O template do projeto (``accounts/widgets/read_only_password_hash.html``)
    é o do Django sem a linha do botão ``../password/`` — a rota de senha
    por usuário não existe mais, então o botão seria um link morto. O resumo
    do hash (algoritmo/última alteração) permanece, útil à TI.
    """

    template_name = "accounts/widgets/read_only_password_hash.html"


class UserChangeFormSemTrocaDeSenha(BaseUserChangeForm):  # type: ignore[type-arg]
    """``UserChangeForm`` sem affordance de troca de senha (R4 do slice 002).

    O campo ``password`` usa o widget sem botão e o ``help_text`` é neutro
    para qualquer usuário — inclusive os sem senha utilizável, para quem o
    form padrão instrui a «definir uma senha» (o ``__init__`` da base
    sobrescreve o help_text nesse caso, então ele é reafirmado após o
    ``super().__init__``).
    """

    password = ReadOnlyPasswordHashField(
        widget=ReadOnlyPasswordHashSemBotaoWidget(),
        help_text=HELP_TEXT_SENHA_FORA_DO_APLICATIVO,
    )

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        password = self.fields.get("password")
        if password is not None:
            password.help_text = HELP_TEXT_SENHA_FORA_DO_APLICATIVO


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Admin simples do ``Role`` (papéis fixos)."""

    list_display = ("name",)
    search_fields = ("name",)


@admin.register(User)
class UserAdmin(BaseUserAdmin):  # type: ignore[type-arg]
    """UserAdmin com os campos do HMD e a regra de senha do AD (R2/R3)."""

    form = UserChangeFormSemTrocaDeSenha
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
        (
            "Profissional",
            {"fields": ("professional_council", "professional_council_number")},
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
        (
            "Profissional",
            {"fields": ("professional_council", "professional_council_number")},
        ),
    )

    def get_urls(self) -> list[URLPattern]:
        """Sem rota de senha por usuário (change bloqueio-troca-de-senha).

        Filtra de ``super().get_urls()``, **por nome**, o padrão
        ``auth_user_password_change`` — a rota deixa de existir
        (``reverse`` levanta ``NoReverseMatch``). A requisição a
        ``/admin/accounts/user/<id>/password/`` casa o padrão de
        compatibilidade ``<path:object_id>/`` do ``ModelAdmin`` (302
        para a página de edição, que redireciona 302 ao índice do admin
        com o aviso de objeto inexistente) — sem filtrar esse padrão,
        sem sobrescrever o redirect de objeto inexistente e sem gravar
        senha alguma (decisão do dono, D3). Os demais
        padrões seguem na ordem original. A criação de usuário
        (``add_fieldsets``) é fora de escopo e permanece intacta.
        """
        return [
            pattern
            for pattern in super().get_urls()
            if getattr(pattern, "name", None) != "auth_user_password_change"
        ]

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
