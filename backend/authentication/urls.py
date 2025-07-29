from django.urls import path
from . import views
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

urlpatterns = [
    path('', views.login, name='login'),
    path('api/auth/user/', views.UserDetailView.as_view, name='user-detail'),
    path('api/auth/login/', views.login, name='login'),
    path('api/auth/first-login/setup/', views.first_login_setup, name='first_login_setup'),
    path('api/auth/first-login/2fa/', views.setup_2fa_first_login, name='setup_2fa_first_login'),
    path('api/auth/first-login/complete/', views.complete_first_login_setup, name='complete_first_login_setup'),
    path('api/auth/2fa/setup/', views.setup_2fa, name='setup_2fa'),
    path('api/auth/2fa/verify/', views.verify_2fa, name='verify_2fa_setup'),
    path('api/auth/2fa/disable/', views.disable_2fa, name='disable_2fa'),
    path('api/auth/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
]