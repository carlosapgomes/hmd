"""Views de conta e sessão (login AD/break-glass, perfil e switch-role).

Fluxo do ADR-0003: o login local comum foi substituído por autenticação AD
(Kerberos, usuários com ``ad_upn``) com break-glass local de superusuário
(change ad-kerberos-authentication); logout, perfil com troca de senha local
(enquanto o break-glass existir) e home autenticada placeholder. O papel ativo
em sessão e o switch-role chegam no slice 005; o guard de intranet no 006.
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
from .ratelimit import clear_login_failures, is_login_locked, register_failed_login

# Mensagens genéricas de login (change ad-kerberos, slice 003/R4/D5): o usuário
# externo nunca recebe código KDC, existência de CPF nem status interno da
# conta. A view distingue apenas "credenciais inválidas" de "serviço de
# autenticação indisponível" — decisão pela marcação request-scoped
# ``request.kerberos_unavailable`` feita pelo ``KerberosBackend`` (D4).
INVALID_CREDENTIALS_MESSAGE = "Usuário ou senha inválidos."
SERVICE_UNAVAILABLE_MESSAGE = (
    "Não foi possível falar com o serviço de autenticação. Tente novamente."
)


def _require_user(request: HttpRequest) -> User:
    """Usuário autenticado de uma view protegida por ``@login_required``."""
    user = request.user
    if not isinstance(user, User):
        raise PermissionDenied
    return user


def login_view(request: HttpRequest) -> HttpResponse:
    """Login (Kerberos AD p/ usuários com ``ad_upn``; local só break-glass).

    GET renderiza o formulário; POST autentica com a ordem de backends das
    settings (D4). Falha mostra mensagem genérica — credenciais inválidas ou
    serviço indisponível, conforme a marcação request-scoped do backend
    (R4/D5); nenhuma mensagem revela motivo interno. O anti-lockout local
    (slice 004/D7) roda ANTES de consultar backends/KDC: limiar atingido =
    recusa com a mesma mensagem genérica, sem revelar bloqueio; sucesso zera
    os contadores. Login redireciona direto para a home (papel
    ativo/switch-role é o slice 005).
    """
    if request.user.is_authenticated:
        return redirect(reverse("home"))

    if request.method == "POST":
        form = LoginForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data["username"]
            password = form.cleaned_data["password"]
            # Anti-lockout local (slice 004/D7): checagem ANTES de consultar
            # qualquer backend/KDC; bloqueado = recusa com a mensagem genérica
            # de credenciais (sem revelar o bloqueio) e sem registrar nova
            # falha. Limiar/janela/duração vêm das settings (R3).
            if is_login_locked(request, username):
                messages.error(request, INVALID_CREDENTIALS_MESSAGE)
            else:
                user = authenticate(request, username=username, password=password)
                if user is not None:
                    # Sucesso zera os contadores do CPF (R2/R4).
                    clear_login_failures(request, username)
                    login(request, user)
                    return redirect(reverse("home"))
                # Tentativa malsucedida conta para o limiar local.
                register_failed_login(request, username)
                if getattr(request, "kerberos_unavailable", False):
                    messages.error(request, SERVICE_UNAVAILABLE_MESSAGE)
                else:
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
