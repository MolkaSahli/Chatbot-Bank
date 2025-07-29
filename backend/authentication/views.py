from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from django.contrib.auth import authenticate
import pyotp
import secrets
import string
from .serializers import *
from rest_framework.views import APIView

@api_view(['POST'])
@permission_classes([AllowAny])
def login(request):
    try:
        print("🔍 Données reçues:", request.data)
        
        serializer = LoginSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.validated_data['user']
            
            # Générer les tokens immédiatement dans tous les cas
            refresh = RefreshToken.for_user(user)
            access_token = str(refresh.access_token)
            refresh_token = str(refresh)

            # Préparer les données utilisateur
            user_data = {
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'name': getattr(user, 'name', ''),
                'account_number': getattr(user, 'account_number', ''),
                'is_2fa_enabled': getattr(user, 'is_2fa_enabled', False),
                'is_first_login': getattr(user, 'is_first_login', False)
            }

            # Préparer la réponse de base
            response_data = {
                'success': True,  # Toujours success=True car l'authentification de base a réussi
                'access_token': access_token,
                'refresh_token': refresh_token,
                'user': user_data
            }

            # Vérifier si c'est la première connexion
            if getattr(user, 'is_first_login', False):
                response_data['requires_setup'] = True
                response_data['message'] = 'Première connexion détectée'
                print("🔍 Première connexion détectée")
                return Response(response_data)

            # Vérifier si 2FA est requis
            if getattr(user, 'is_2fa_enabled', False):
                response_data['requires_2fa'] = True
                response_data['message'] = 'Code 2FA requis pour finaliser la connexion'
                print("🔍 2FA requis pour cet utilisateur")
                return Response(response_data)

            # Connexion directe si pas de 2FA
            print("🔍 Connexion directe sans 2FA")
            return Response(response_data)

        # Gestion des erreurs de validation
        print("🔥 Erreurs de validation:", serializer.errors)
        return Response({
            'success': False,
            'error': {
                'message': 'Identifiants incorrects'
            }
        }, status=status.HTTP_400_BAD_REQUEST)

    except Exception as e:
        print("🔥 Erreur interne dans login view:", str(e))
        import traceback
        traceback.print_exc()
        return Response({
            'success': False,
            'error': {'message': 'Erreur interne du serveur'}
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class UserDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        """
        Récupère les informations de l'utilisateur authentifié.
        """
        user = request.user
        serializer = UserSerializer(user)
        return Response(serializer.data)

@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def first_login_setup(request):
    """Configuration lors de la première connexion"""
    user = request.user
    
    if not user.is_first_login:
        return Response({
            'error': 'Cette action n\'est disponible que lors de la première connexion'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    # Changement de mot de passe
    if 'new_password' in request.data:
        serializer = FirstLoginSetupSerializer(data=request.data)
        if serializer.is_valid():
            user.set_password(serializer.validated_data['new_password'])
            user.temp_password = None  # Effacer le mot de passe temporaire
            user.is_first_login = False
            user.save()
            
            return Response({
                'message': 'Mot de passe mis à jour avec succès',
                'next_step': 'setup_2fa'
            })
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    return Response({
        'error': 'Nouveau mot de passe requis'
    }, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def setup_2fa_first_login(request):
    """Configuration 2FA lors de la première connexion"""
    user = request.user
    
    if not user.is_first_login:
        return Response({
            'error': 'Cette action n\'est disponible que lors de la première connexion'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    serializer = Setup2FASerializer()
    qr_data = serializer.get_qr_code(user)
    
    return Response(qr_data)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def complete_first_login_setup(request):
    user = request.user
    
    if user.is_first_login:
        return Response({
            'error': 'Cette action est disponible uniquement lors de la première connexion'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    secret = request.data.get('secret')
    totp_code = request.data.get('totp_code')
    
    if not secret or not totp_code:
        return Response({
            'error': 'Secret et code TOTP requis'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    totp = pyotp.TOTP(secret)
    if not totp.verify(totp_code, valid_window=1):
        return Response({
            'error': 'Code TOTP invalide'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    user.totp_secret = secret
    user.is_2fa_enabled = True
    user.is_first_login = False
    user.save()
    
    from rest_framework_simplejwt.tokens import RefreshToken
    refresh = RefreshToken.for_user(user)
    return Response({
        'message': 'Configuration terminée avec succès',
        'access_token': str(refresh.access_token),
        'refresh_token': str(refresh),
        'user': {
            'id': user.id,
            'email': user.email,
            'name': user.name,
            'account_number': user.account_number,
            'is_2fa_enabled': user.is_2fa_enabled,
            'is_first_login': user.is_first_login
        }
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def setup_2fa(request):
    """Configuration 2FA pour utilisateurs existants"""
    user = request.user
    
    if user.is_first_login:
        return Response({
            'error': 'Veuillez d\'abord terminer la configuration initiale'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    if user.is_2fa_enabled:
        return Response({
            'error': '2FA déjà activé'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    serializer = Setup2FASerializer()
    qr_data = serializer.get_qr_code(user)
    
    return Response(qr_data)
"""
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def verify_2fa_setup(request):
    try:
        user = request.user
        
        # 1. Vérification du code TOTP
        totp_code = request.data.get('totp_code')
        if not totp_code or not pyotp.TOTP(user.totp_secret).verify(totp_code, valid_window=2):
            return Response({'error': 'Code TOTP invalide'}, status=400)

        # 2. Régénération des tokens (même s'ils sont encore valides)
        refresh = RefreshToken.for_user(user)
        
        return Response({
            'access_token': str(refresh.access_token),
            'refresh_token': str(refresh),
            'user': {
                'id': user.id,
                'is_2fa_enabled': True
            }
        })

    except Exception as e:
        return Response({'error': str(e)}, status=500)

"""

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def verify_2fa(request):
    try:
        # 1. Validation du code
        totp_code = request.data.get('totp_code', '').strip()
        if not totp_code.isdigit() or len(totp_code) != 6:
            return Response(
                {"error": "Code TOTP invalide (6 chiffres requis)"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 2. Vérification TOTP
        if not pyotp.TOTP(request.user.totp_secret).verify(totp_code, valid_window=1):
            return Response(
                {"error": "Code TOTP incorrect ou expiré"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 3. Génération des nouveaux tokens
        refresh = RefreshToken.for_user(request.user)
        return Response({
            "access_token": str(refresh.access_token),
            "refresh_token": str(refresh),
            "user": {
                "id": request.user.id,
                "email": request.user.email
            }
        }, status=status.HTTP_200_OK)

    except Exception as e:
        return Response(
            {"error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def disable_2fa(request):
    """Désactiver 2FA"""
    user = request.user
    
    if user.is_first_login:
        return Response({
            'error': 'Configuration initiale non terminée'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    user.disable_2fa()
    
    return Response({
        'message': '2FA désactivé avec succès'
    })

def generate_backup_codes():
    """Génère 10 codes de secours aléatoires"""
    codes = []
    for _ in range(10):
        code = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        codes.append(code)
    return codes
