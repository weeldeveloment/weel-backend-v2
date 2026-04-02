from django.urls import path
from .views import AdminLoginView, AdminMeView, AdminRefreshTokenView, AdminRegisterView
from .users_views import AdminClientsListView, AdminPartnersListView

urlpatterns = [
    path('login/', AdminLoginView.as_view(), name='admin-login'),
    path('me/', AdminMeView.as_view(), name='admin-me'),
    path('token/refresh/', AdminRefreshTokenView.as_view(), name='admin-token-refresh'),
    path('register/', AdminRegisterView.as_view(), name='admin-register'),
    # Users management
    path('users/clients/', AdminClientsListView.as_view(), name='admin-clients-list'),
    path('users/partners/', AdminPartnersListView.as_view(), name='admin-partners-list'),
]
