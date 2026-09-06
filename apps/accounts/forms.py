"""Formulários de login e troca de senha local (slice 004, R2).

Padrão do ats-web adaptado: ``LoginForm`` (usuário+senha) e
``HospitalPasswordChangeForm`` (troca de senha com widgets Bootstrap).
"""

from django import forms
from django.contrib.auth.forms import PasswordChangeForm

from apps.accounts.models import User


class LoginForm(forms.Form):
    """Formulário de login local (usuário + senha)."""

    username = forms.CharField(
        label="Usuário",
        max_length=150,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Nome de usuário",
                "autofocus": True,
                "autocomplete": "username",
            }
        ),
    )
    password = forms.CharField(
        label="Senha",
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "Sua senha",
                "autocomplete": "current-password",
            }
        ),
    )


class HospitalPasswordChangeForm(PasswordChangeForm):
    """PasswordChangeForm com campos aderentes ao tema Bootstrap do HMD.

    Adiciona a classe ``form-control`` aos três campos de senha para que os
    inputs sigam o estilo do restante do formulário.
    """

    def __init__(self, user: User, *args: object, **kwargs: object) -> None:
        super().__init__(user, *args, **kwargs)
        for field_name in ("old_password", "new_password1", "new_password2"):
            classes = self.fields[field_name].widget.attrs.get("class", "")
            if "form-control" not in classes:
                self.fields[field_name].widget.attrs["class"] = (classes + " form-control").strip()
