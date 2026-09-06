"""Views de autenticação local transitória, home placeholder e switch-role.

Fluxo R2/R4/R5 do ADR-0003 (estágio 1): login/logout locais, perfil com
troca de senha local e home autenticada placeholder. O papel ativo em sessão
e o switch-role chegam no slice 005 (R1/R2); o guard de intranet no slice 006.
"""

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.accounts.models import User

from .forms import HospitalPasswordChangeForm, LoginForm

# Mensagem genérica: não revela se o motivo é senha errada ou conta
# bloqueada/removida (R1 — sem vazamento de motivo interno).
INVALID_CREDENTIALS_MESSAGE = "Usuário ou senha inválidos."


def _require_user(request: HttpRequest) -> User:
    """Usuário autenticado de uma view protegida por ``@login_required``."""
    user = request.user
    if not isinstance(user, User):
        raise PermissionDenied
    return user


def login_view(request: HttpRequest) -> HttpResponse:
    """Login local transitório: GET renderiza o formulário, POST autentica.

    Login redireciona direto para a home (papel ativo/switch-role é o slice
    005 — fora do escopo deste slice).
    """
    if request.user.is_authenticated:
        return redirect(reverse("home"))

    if request.method == "POST":
        form = LoginForm(request.POST)
        if form.is_valid():
            user = authenticate(
                request,
                username=form.cleaned_data["username"],
                password=form.cleaned_data["password"],
            )
            if user is not None:
                login(request, user)
                return redirect(reverse("home"))
            messages.error(request, INVALID_CREDENTIALS_MESSAGE)
    else:
        form = LoginForm()

    return render(request, "accounts/login.html", {"form": form})


@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    """Encerra a sessão e volta ao login (R2)."""
    logout(request)
    return redirect(reverse("login"))


@login_required
def profile_view(request: HttpRequest) -> HttpResponse:
    """Perfil do usuário com troca de senha local (R2).

    Exibe nome, display name, papéis e conselho profissional; o POST processa
    ``HospitalPasswordChangeForm`` enquanto a autenticação local existir.
    """
    user = _require_user(request)

    if request.method == "POST":
        form = HospitalPasswordChangeForm(user, request.POST)
        if form.is_valid():
            form.save()
            update_session_auth_hash(request, user)
            messages.success(request, "Senha alterada com sucesso.")
            return redirect(reverse("profile"))
    else:
        form = HospitalPasswordChangeForm(user)

    user_roles = list(user.roles.order_by("name").values_list("name", flat=True))
    return render(
        request,
        "accounts/profile.html",
        {"form": form, "user_roles": user_roles},
    )


@login_required
def switch_role_view(request: HttpRequest) -> HttpResponse:
    """Troca o papel ativo da sessão (slice 005, R2).

    GET lista os papéis do usuário; POST valida que o papel escolhido
    pertence ao usuário, grava ``session["active_role"]`` e redireciona para a
    home. As homes por papel continuam placeholders neste change.
    """
    user = _require_user(request)
    user_roles = list(user.roles.order_by("name").values_list("name", flat=True))

    if request.method == "POST":
        role_name = request.POST.get("role", "")
        if role_name in user_roles:
            request.session["active_role"] = role_name
            return redirect(reverse("home"))
        messages.error(request, "Papel inválido ou não atribuído.")

    return render(request, "accounts/switch_role.html", {"user_roles": user_roles})


@login_required
def home_view(request: HttpRequest) -> HttpResponse:
    """Home autenticada placeholder (R4): boas-vindas com nome e papéis.

    Os changes posteriores substituem este conteúdo pelas filas reais de
    trabalho de cada papel.
    """
    user = _require_user(request)
    role_names = list(user.roles.order_by("name").values_list("name", flat=True))
    return render(request, "accounts/home.html", {"role_names": role_names})
