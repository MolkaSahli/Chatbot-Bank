from django.urls import path
from . import views
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

urlpatterns = [
    #---------------------------Authentication Endpoints----------------------------------
    path('api/auth/login/', views.login, name='login'),
    path('api/auth/user/', views.UserDetailView.as_view(), name='user-detail'),
    path('api/auth/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    
    # First login setup endpoints
    path('api/auth/first-login/setup/', views.first_login_setup, name='first_login_setup'),
    path('api/auth/first-login/2fa/', views.setup_2fa_first_login, name='setup_2fa_first_login'),
    path('api/auth/first-login/complete/', views.complete_first_login_setup, name='complete_first_login_setup'),
    
    # 2FA management endpoints
    path('api/auth/2fa/setup/', views.setup_2fa, name='setup_2fa'),
    path('api/auth/2fa/verify/', views.verify_2fa, name='verify_2fa'),
    path('api/auth/2fa/disable/', views.disable_2fa, name='disable_2fa'),
    
    #---------------------------Chat/Chatbot Endpoints----------------------------------
    # Core chat functionality
    path('api/chat/start/', views.start_conversation, name='start_conversation'),
    path('api/chat/send/', views.send_message, name='send_message'),
    path('api/chat/end/<str:session_id>/', views.end_conversation, name='end_conversation'),
    
    # Conversation management
    path('api/chat/conversations/', views.get_conversations, name='get_conversations'),
    path('api/chat/history/<str:session_id>/', views.get_conversation_history, name='get_conversation_history'),
    
    #---------------------------System Management Endpoints----------------------------------
    # Chatbot status and management
    path('api/system/chatbot/status/', views.chatbot_status, name='chatbot_status'),
    path('api/system/chatbot/reset/', views.reset_chatbot_memory, name='reset_chatbot_memory'),
    path('api/system/health/', views.system_health_check, name='system_health_check'),
    
    #---------------------------Analytics and Export Endpoints----------------------------------
    # Analytics
    path('api/analytics/chatbot/', views.get_chatbot_analytics, name='get_chatbot_analytics'),
    
    # Export functionality
    path('api/export/conversations/', views.export_conversation_data, name='export_conversation_data'),
    
    #---------------------------Debug Endpoints (Remove in Production)----------------------------------
    # Debug endpoints - should be removed or restricted in production
    path('api/debug/user-context/', views.debug_user_context, name='debug_user_context'),
    path('api/debug/test-intents/', views.test_chatbot_intents, name='test_chatbot_intents'),
    
    #---------------------------Legacy/Compatibility Endpoints----------------------------------
    # Legacy endpoint for backward compatibility
    path('', views.login, name='login_legacy'),
]