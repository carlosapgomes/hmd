"""Formulário de login (slice 004, R2).

Padrão do ats-web adaptado: ``LoginForm`` (usuário+senha).
"""

from django import forms


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
