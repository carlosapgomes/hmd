"""URLs de conta e sessão (slice 004, R5).

Incluídas na raiz em ``config/urls.py``: home autenticada em ``/``, login,
logout e perfil.
"""

from django.urls import URLPattern, path

from . import views

urlpatterns: list[URLPattern] = [
    path("", views.home_view, name="home"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("profile/", views.profile_view, name="profile"),
]
