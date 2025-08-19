from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import authenticate
from django.utils import timezone
from django.views import View
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
import pyotp
import logging
import time
import json
import uuid
import csv
from io import StringIO
from decimal import Decimal
from typing import Dict
from datetime import timedelta

# Imports locaux
from .serializers import *
from .models import *
from .services.langchain_service import BankingChatbotService
from .services.banking_service import BankingService

logger = logging.getLogger(__name__)

# Instance globale du chatbot
chatbot_service = None

def get_chatbot_instance():
    """Récupère ou crée l'instance du chatbot amélioré"""
    global chatbot_service
    if chatbot_service is None:
        chatbot_service = BankingChatbotService(
            model_name="llama3.2:3b",
            timeout=120,
            verbose=True
        )
    return chatbot_service

#---------------------------Authentication Section----------------------------------

@api_view(['POST'])
@permission_classes([AllowAny])
def login(request):
    try:
        print("Ã°Å¸â€Â DonnÃƒÂ©es reÃƒÂ§ues:", request.data)
        
        serializer = LoginSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.validated_data['user']
            
            # GÃƒÂ©nÃƒÂ©rer les tokens immÃƒÂ©diatement dans tous les cas
            refresh = RefreshToken.for_user(user)
            access_token = str(refresh.access_token)
            refresh_token = str(refresh)

            # PrÃƒÂ©parer les donnÃƒÂ©es utilisateur
            user_data = {
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'name': getattr(user, 'name', ''),
                'account_number': getattr(user, 'account_number', ''),
                'is_2fa_enabled': getattr(user, 'is_2fa_enabled', False),
                'is_first_login': getattr(user, 'is_first_login', False)
            }

            # PrÃƒÂ©parer la rÃƒÂ©ponse de base
            response_data = {
                'success': True,  # Toujours success=True car l'authentification de base a rÃƒÂ©ussi
                'access_token': access_token,
                'refresh_token': refresh_token,
                'user': user_data
            }

            # VÃƒÂ©rifier si c'est la premiÃƒÂ¨re connexion
            if getattr(user, 'is_first_login', False):
                response_data['requires_setup'] = True
                response_data['message'] = 'PremiÃƒÂ¨re connexion dÃƒÂ©tectÃƒÂ©e'
                print("Ã°Å¸â€Â PremiÃƒÂ¨re connexion dÃƒÂ©tectÃƒÂ©e")
                return Response(response_data)

            # VÃƒÂ©rifier si 2FA est requis
            if getattr(user, 'is_2fa_enabled', False):
                response_data['requires_2fa'] = True
                response_data['message'] = 'Code 2FA requis pour finaliser la connexion'
                print("Ã°Å¸â€Â 2FA requis pour cet utilisateur")
                return Response(response_data)

            # Connexion directe si pas de 2FA
            print("Ã°Å¸â€Â Connexion directe sans 2FA")
            return Response(response_data)

        # Gestion des erreurs de validation
        print("Ã°Å¸â€Â¥ Erreurs de validation:", serializer.errors)
        return Response({
            'success': False,
            'error': {
                'message': 'Identifiants incorrects'
            }
        }, status=status.HTTP_400_BAD_REQUEST)

    except Exception as e:
        print("Ã°Å¸â€Â¥ Erreur interne dans login view:", str(e))
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
        RÃƒÂ©cupÃƒÂ¨re les informations de l'utilisateur authentifiÃƒÂ©.
        """
        user = request.user
        serializer = UserSerializer(user)
        return Response(serializer.data)

@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def first_login_setup(request):
    """Configuration lors de la premiÃƒÂ¨re connexion"""
    user = request.user
    
    if not user.is_first_login:
        return Response({
            'error': 'Cette action n\'est disponible que lors de la premiÃƒÂ¨re connexion'
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
                'message': 'Mot de passe mis Ãƒ  jour avec succÃƒÂ¨s',
                'next_step': 'setup_2fa'
            })
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    return Response({
        'error': 'Nouveau mot de passe requis'
    }, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def setup_2fa_first_login(request):
    """Configuration 2FA lors de la premiÃƒÂ¨re connexion"""
    user = request.user
    
    if not user.is_first_login:
        return Response({
            'error': 'Cette action n\'est disponible que lors de la premiÃƒÂ¨re connexion'
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
            'error': 'Cette action est disponible uniquement lors de la premiÃƒÂ¨re connexion'
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
        'message': 'Configuration terminÃƒÂ©e avec succÃƒÂ¨s',
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
            'error': '2FA dÃƒÂ©jÃƒ  activÃƒÂ©'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    serializer = Setup2FASerializer()
    qr_data = serializer.get_qr_code(user)
    
    return Response(qr_data)

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

        if not pyotp.TOTP(request.user.totp_secret).verify(totp_code, valid_window=1):
            return Response(
                {"error": "Code TOTP incorrect ou expirÃƒÂ©"},
                status=status.HTTP_400_BAD_REQUEST
            )

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

#---------------------------Banking Actions Section----------------------------------

def execute_banking_action(user, action_type: str, parameters: dict):
    """Exécute une action bancaire via le service centralisé"""
    try:
        logger.info(f"🏦 Exécution action {action_type} pour utilisateur {user.id}")
        start_time = time.time()
        
        if action_type == 'check_balance':
            result = BankingService.get_account_balance(
                client=user,
                account_number=parameters.get('account_number'),
                account_id=parameters.get('account_id')
            )
            
        elif action_type == 'get_accounts':
            result = BankingService.get_all_client_accounts(
                client=user,
                include_inactive=parameters.get('include_inactive', False)
            )
            
        elif action_type == 'transfer_money':
            required_params = ['amount', 'recipient_account', 'recipient_name']
            missing_params = [param for param in required_params if not parameters.get(param)]
            
            if missing_params:
                return {
                    'success': False,
                    'error': f'Paramètres manquants pour le virement: {", ".join(missing_params)}',
                    'missing_parameters': missing_params,
                    'requires_user_input': True
                }
            
            result = BankingService.enhanced_transfer_money(
                client=user,
                amount=parameters['amount'],
                recipient_account=parameters['recipient_account'],
                recipient_name=parameters['recipient_name'],
                description=parameters.get('description', ''),
                from_account_number=parameters.get('from_account_number'),
                from_account_id=parameters.get('from_account_id')
            )
            
        elif action_type == 'payment':
            required_params = ['amount', 'merchant']
            missing_params = [param for param in required_params if not parameters.get(param)]
            
            if missing_params:
                return {
                    'success': False,
                    'error': f'Paramètres manquants pour le paiement: {", ".join(missing_params)}',
                    'missing_parameters': missing_params,
                    'requires_user_input': True
                }
            
            result = BankingService.enhanced_make_payment(
                client=user,
                amount=parameters['amount'],
                merchant=parameters['merchant'],
                bill_number=parameters.get('bill_number'),
                description=parameters.get('description', ''),
                from_account_number=parameters.get('from_account_number'),
                from_account_id=parameters.get('from_account_id')
            )
            
        elif action_type == 'recurring_payment':
            required_params = ['amount', 'recipient_account', 'recipient_name', 'frequency']
            missing_params = [param for param in required_params if not parameters.get(param)]
            
            if missing_params:
                return {
                    'success': False,
                    'error': f'Paramètres manquants pour le paiement récurrent: {", ".join(missing_params)}',
                    'missing_parameters': missing_params,
                    'requires_user_input': True
                }
            
            result = BankingService.setup_recurring_payment(
                client=user,
                amount=parameters['amount'],
                recipient_account=parameters['recipient_account'],
                recipient_name=parameters['recipient_name'],
                frequency=parameters['frequency'],
                description=parameters.get('description', ''),
                from_account_number=parameters.get('from_account_number'),
                from_account_id=parameters.get('from_account_id'),
                end_date=parameters.get('end_date'),
                start_date=parameters.get('start_date')
            )
            
        elif action_type == 'transaction_history':
            result = BankingService.get_transaction_history(
                client=user,
                account_number=parameters.get('account_number'),
                account_id=parameters.get('account_id'),
                limit=parameters.get('limit', 10),
                transaction_type=parameters.get('transaction_type'),
                start_date=parameters.get('start_date'),
                end_date=parameters.get('end_date'),
                use_cache=True
            )
            
        else:
            result = {
                'success': False,
                'error': f'Action non reconnue: {action_type}',
                'supported_actions': ['check_balance', 'get_accounts', 'transfer_money', 'payment', 'recurring_payment', 'transaction_history']
            }
        
        execution_time = time.time() - start_time
        logger.info(f"🏦 Action {action_type} exécutée en {execution_time:.2f}s - Succès: {result.get('success')}")
        
        # Invalider les caches si transaction réussie
        if result.get('success') and action_type in ['transfer_money', 'payment', 'recurring_payment']:
            BankingService._invalidate_client_cache(user)
        
        return result
            
    except Exception as e:
        logger.error(f"🔥 Erreur execute_banking_action: {str(e)}")
        return {
            'success': False,
            'error': f'Erreur lors de l\'exécution de l\'action: {str(e)}',
            'error_type': 'system_error'
        }

def format_banking_response(action_type: str, action_result: dict, bot_response: dict) -> str:
    """Formate la réponse bancaire de manière élégante"""
    if not action_result.get('success'):
        error_message = action_result.get('error', 'Une erreur est survenue.')
        return f"❌ Je n'ai pas pu traiter votre demande: {error_message}"
    
    if action_type == 'check_balance':
        formatted_balance = action_result.get('formatted_balance', 
                                            f"{action_result.get('balance', 0)} {action_result.get('currency', 'Dt')}")
        account_info = f" (Compte: {action_result.get('account_number', 'N/A')})" if action_result.get('account_number') else ""
        return f"💰 Votre solde actuel est de {formatted_balance}{account_info}."
        
    elif action_type == 'get_accounts':
        return BankingService.format_accounts_response(action_result)
        
    elif action_type == 'transfer_money':
        reference = action_result.get('reference', 'N/A')
        formatted_amount = action_result.get('formatted_amount', f"{action_result.get('amount', 0)} Dt")
        formatted_new_balance = action_result.get('formatted_new_balance', f"{action_result.get('new_balance', 0)} Dt")
        recipient_name = action_result.get('recipient_name', 'N/A')
        
        return (
            f"✅ Virement de {formatted_amount} vers {recipient_name} effectué avec succès !\n\n"
            f"📋 Détails du virement :\n"
            f"• Référence: {reference}\n"
            f"• Montant: {formatted_amount}\n"
            f"• Bénéficiaire: {recipient_name}\n"
            f"• Compte destinataire: {action_result.get('recipient_account', 'N/A')}\n"
            f"• Nouveau solde: {formatted_new_balance}"
        )
        
    elif action_type in ['payment', 'confirm_payment']:
        reference = action_result.get('reference', 'N/A')
        formatted_amount = action_result.get('formatted_amount', f"{action_result.get('amount', 0)} Dt")
        formatted_new_balance = action_result.get('formatted_new_balance', f"{action_result.get('new_balance', 0)} Dt")
        merchant = action_result.get('merchant', 'N/A')
        
        response = (
            f"✅ Paiement de {formatted_amount} à {merchant} effectué avec succès !\n\n"
            f"🧾 Détails du paiement :\n"
            f"• Référence: {reference}\n"
            f"• Montant: {formatted_amount}\n"
            f"• Fournisseur: {merchant}\n"
        )
        
        if action_result.get('bill_number'):
            response += f"• N° Facture: {action_result['bill_number']}\n"
            
        response += f"• Nouveau solde: {formatted_new_balance}"
        return response
        
    elif action_type == 'recurring_payment':
        formatted_amount = action_result.get('formatted_amount', f"{action_result.get('amount', 0)} Dt")
        frequency_display = action_result.get('frequency', 'mensuel')
        recipient_name = action_result.get('recipient_name', 'N/A')
        next_payment_date = action_result.get('next_payment_date', 'N/A')
        
        return (
            f"✅ Paiement récurrent de {formatted_amount} vers {recipient_name} configuré avec succès !\n\n"
            f"🔄 Détails du paiement récurrent :\n"
            f"• Montant: {formatted_amount}\n"
            f"• Bénéficiaire: {recipient_name}\n"
            f"• Fréquence: {frequency_display}\n"
            f"• Prochaine échéance: {next_payment_date[:10] if next_payment_date != 'N/A' else 'N/A'}"
        )
        
    elif action_type == 'transaction_history':
        transactions = action_result.get('transactions', [])
        transactions_count = len(transactions)
        
        if not transactions:
            return "📋 Aucune transaction trouvée."
            
        response_parts = [f"📋 Vos {transactions_count} dernières transactions :\n"]
        
        for i, trans in enumerate(transactions[:5], 1):
            date = trans.get('date', 'N/A')[:10] if trans.get('date') != 'N/A' else 'N/A'
            trans_type = trans.get('type', 'N/A')
            amount = trans.get('formatted_amount', f"{trans.get('amount', 0)} Dt")
            description = trans.get('description', 'N/A')[:30]
            
            response_parts.append(
                f"{i}. {date} - {trans_type} - {amount}"
            )
            if description != 'N/A':
                response_parts.append(f"   {description}")
        
        if action_result.get('total_count', 0) > 5:
            response_parts.append(f"\n... et {action_result['total_count'] - 5} autres transactions.")
        
        return "\n".join(response_parts)
    
    return bot_response.get('response', 'Opération traitée avec succès.')

#---------------------------Chat/Chatbot Section----------------------------------

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def start_conversation(request):
    """Démarre une nouvelle conversation avec le chatbot"""
    try:
        logger.info(f"🚀 Utilisateur {request.user.username} démarre une conversation")
        
        session_id = str(uuid.uuid4())
        
        conversation = ChatConversation.objects.create(
            client=request.user,
            session_id=session_id,
            is_active=True,
            title="Nouvelle conversation"
        )
        
        # Message de bienvenue personnalisé
        user_display_name = getattr(request.user, 'name', '') or getattr(request.user, 'first_name', '') or request.user.username
        welcome_msg = (
            f"Bonjour {user_display_name} ! 👋\n\n"
            f"Je suis votre assistant bancaire intelligent. Je peux vous aider à :\n"
            f"• 💰 Consulter votre solde et vos comptes\n"
            f"• 💸 Effectuer des virements\n"
            f"• 🧾 Payer vos factures\n"
            f"• 📋 Consulter votre historique de transactions\n\n"
            f"Comment puis-je vous aider aujourd'hui ?"
        )
        
        welcome_message = ChatMessage.objects.create(
            conversation=conversation,
            message_type='bot',
            content=welcome_msg
        )
        
        logger.info(f"✅ Conversation {conversation.id} créée avec succès pour session {session_id}")

        return Response({
            'success': True, 
            'session_id': session_id,
            'conversation_id': conversation.id,
            'title': conversation.title,
            'welcome_message': {
                'content': welcome_message.content,
                'timestamp': welcome_message.created_at.isoformat()
            },
            'created_at': conversation.created_at.isoformat()
        }, status=status.HTTP_201_CREATED)
        
    except Exception as e:
        logger.error(f"🔥 Erreur dans start_conversation: {str(e)}")
        return Response({
            'success': False, 
            'error': f'Erreur lors de la création de la conversation: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def send_message(request):
    """Endpoint principal - Analyse intelligente + Exécution optimisée"""
    try:
        logger.info(f"📩 Message reçu de {request.user.username}")
        
        # Récupérer les données
        session_id = request.data.get('session_id')
        user_message = request.data.get('message', '').strip()
        
        # Validation
        if not session_id or not user_message:
            return Response({
                'success': False,
                'error': 'session_id et message sont requis'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Récupérer la conversation
        try:
            conversation = ChatConversation.objects.get(
                session_id=session_id,
                client=request.user,
                is_active=True
            )
        except ChatConversation.DoesNotExist:
            return Response({
                'success': False,
                'error': 'Session de conversation non trouvée'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Sauvegarder le message utilisateur
        user_msg = ChatMessage.objects.create(
            conversation=conversation,
            message_type='user',
            content=user_message
        )
        
        # Préparer le contexte utilisateur
        try:
            accounts_result = BankingService.get_all_client_accounts(request.user)
            if accounts_result.get('success'):
                user_accounts = accounts_result.get('accounts', [])
            else:
                user_accounts = []
            
            user_context = {
                'user_id': request.user.id,
                'username': request.user.username,
                'name': getattr(request.user, 'name', '') or request.user.username,
                'accounts': user_accounts,
                'has_accounts': len(user_accounts) > 0,
                'primary_account': next((acc for acc in user_accounts if acc.get('is_primary')), 
                                      user_accounts[0] if user_accounts else None),
                'timestamp': timezone.now().isoformat()
            }
            
        except Exception:
            user_context = {
                'user_id': request.user.id,
                'username': request.user.username,
                'name': request.user.username,
                'accounts': [],
                'has_accounts': False,
                'timestamp': timezone.now().isoformat()
            }
        
        # Traitement par le chatbot
        try:
            chatbot = get_chatbot_instance()
            bot_response = chatbot.process_message(user_message, user_context)
            
            if not isinstance(bot_response, dict):
                raise Exception(f"Réponse chatbot invalide: {type(bot_response)}")
            
            # Exécution action bancaire si requise
            if bot_response.get('requires_action') and bot_response.get('action_type'):
                action_result = execute_banking_action(
                    request.user,
                    bot_response['action_type'],
                    bot_response.get('parameters', {})
                )
                
                # Formater la réponse selon le résultat
                if action_result.get('success'):
                    bot_response['response'] = format_banking_response(
                        bot_response['action_type'], 
                        action_result, 
                        bot_response
                    )
                    bot_response['action_result'] = action_result
                    
                elif action_result.get('requires_user_input'):
                    bot_response['response'] = action_result.get('error', 'Des informations supplémentaires sont requises.')
                    bot_response['requires_action'] = False
                    bot_response['action_result'] = action_result
                    
                else:
                    error_message = action_result.get('error', 'Une erreur est survenue.')
                    bot_response['response'] = f"❌ Je n'ai pas pu traiter votre demande: {error_message}"
                    bot_response['action_result'] = action_result
                
        except Exception as chatbot_error:
            logger.error(f"🤖 Erreur chatbot service: {chatbot_error}")
            bot_response = {
                'response': "Désolé, je rencontre actuellement des difficultés techniques. Veuillez réessayer dans un moment ou reformuler votre demande.",
                'intent': 'error',
                'confidence': 0.0,
                'requires_action': False,
                'action_type': None,
                'parameters': {},
                'error_details': str(chatbot_error)
            }
        
        # Sauvegarder la réponse du bot
        bot_msg = ChatMessage.objects.create(
            conversation=conversation,
            message_type='bot',
            content=bot_response.get('response', 'Réponse non disponible'),
            metadata={
                'intent': bot_response.get('intent'),
                'confidence': bot_response.get('confidence'),
                'action_type': bot_response.get('action_type'),
                'parameters': bot_response.get('parameters', {}),
                'action_result': bot_response.get('action_result', {}),
                'full_response': bot_response
            },
            intent_detected=bot_response.get('intent'),
            confidence_score=bot_response.get('confidence', 0.0)
        )
        
        # Réponse finale
        response_data = {
            'success': True,
            'message_id': bot_msg.id,
            'response': bot_msg.content,
            'timestamp': bot_msg.created_at.isoformat(),
            'user_message': {
                'id': user_msg.id,
                'content': user_msg.content,
                'timestamp': user_msg.created_at.isoformat()
            },
            'bot_response': {
                'id': bot_msg.id,
                'content': bot_msg.content,
                'timestamp': bot_msg.created_at.isoformat()
            },
            'intent': bot_response.get('intent'),
            'confidence': bot_response.get('confidence'),
            'action_type': bot_response.get('action_type'),
            'requires_action': bot_response.get('requires_action', False),
            'action_result': bot_response.get('action_result')
        }
        
        return Response(response_data, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"🔥 Erreur dans send_message: {str(e)}")
        return Response({
            'success': False,
            'error': f'Erreur lors du traitement du message: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_conversations(request):
    """Récupère toutes les conversations de l'utilisateur avec pagination"""
    try:
        logger.info(f"📋 Chargement des conversations pour {request.user.username}")
        
        # Paramètres de pagination
        page = int(request.GET.get('page', 1))
        page_size = int(request.GET.get('page_size', 20))
        
        conversations = ChatConversation.objects.filter(
            client=request.user
        ).order_by('-created_at')
        
        # Pagination
        start = (page - 1) * page_size
        end = start + page_size
        paginated_conversations = conversations[start:end]
        
        conversations_data = []
        for conv in paginated_conversations:
            last_message = conv.messages.last()
            message_count = conv.messages.count()
            
            conversations_data.append({
                'id': conv.id,
                'session_id': conv.session_id,
                'title': conv.title,
                'created_at': conv.created_at.isoformat(),
                'updated_at': conv.updated_at.isoformat() if hasattr(conv, 'updated_at') else conv.created_at.isoformat(),
                'is_active': conv.is_active,
                'message_count': message_count,
                'last_message': {
                    'content': last_message.content[:100] + '...' if last_message and len(last_message.content) > 100 else last_message.content if last_message else '',
                    'timestamp': last_message.created_at.isoformat() if last_message else conv.created_at.isoformat(),
                    'type': last_message.message_type if last_message else 'system'
                },
                'summary': {
                    'user_messages': conv.messages.filter(message_type='user').count(),
                    'bot_messages': conv.messages.filter(message_type='bot').count(),
                    'has_actions': conv.messages.filter(metadata__action_type__isnull=False).exists()
                }
            })
        
        total_conversations = conversations.count()
        has_next = end < total_conversations
        has_previous = page > 1
        
        return Response({
            'success': True,
            'conversations': conversations_data,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total': total_conversations,
                'has_next': has_next,
                'has_previous': has_previous,
                'total_pages': (total_conversations + page_size - 1) // page_size
            }
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"🔥 Erreur dans get_conversations: {str(e)}")
        return Response({
            'success': False,
            'error': f'Erreur lors du chargement des conversations: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_conversation_history(request, session_id):
    """Récupère l'historique d'une conversation avec détails enrichis"""
    try:
        conversation = ChatConversation.objects.get(
            session_id=session_id,
            client=request.user
        )
        
        messages = conversation.messages.all().order_by('created_at')
        
        # Construire l'historique enrichi
        messages_data = []
        for msg in messages:
            msg_data = {
                'id': msg.id,
                'type': msg.message_type,
                'content': msg.content,
                'timestamp': msg.created_at.isoformat(),
                'intent': msg.intent_detected,
                'confidence': msg.confidence_score,
            }
            
            # Ajouter les métadonnées si disponibles
            if msg.metadata:
                msg_data['metadata'] = {
                    'action_type': msg.metadata.get('action_type'),
                    'parameters': msg.metadata.get('parameters', {}),
                    'action_result': msg.metadata.get('action_result', {}),
                }
            
            messages_data.append(msg_data)
        
        # Statistiques de la conversation
        stats = {
            'total_messages': len(messages_data),
            'user_messages': len([m for m in messages_data if m['type'] == 'user']),
            'bot_messages': len([m for m in messages_data if m['type'] == 'bot']),
            'actions_executed': len([m for m in messages_data if m.get('metadata', {}).get('action_type')]),
            'average_confidence': sum([m.get('confidence', 0) for m in messages_data if m.get('confidence')]) / max(1, len([m for m in messages_data if m.get('confidence')])),
            'intents_detected': list(set([m.get('intent') for m in messages_data if m.get('intent')]))
        }
        
        return Response({
            'success': True,
            'conversation': {
                'id': conversation.id,
                'session_id': conversation.session_id,
                'title': conversation.title,
                'created_at': conversation.created_at.isoformat(),
                'is_active': conversation.is_active,
                'stats': stats
            },
            'messages': messages_data
        }, status=status.HTTP_200_OK)
        
    except ChatConversation.DoesNotExist:
        return Response({
            'success': False,
            'error': 'Conversation non trouvée'
        }, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"🔥 Erreur dans get_conversation_history: {str(e)}")
        return Response({
            'success': False,
            'error': f'Erreur lors du chargement de l\'historique: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def end_conversation(request, session_id):
    """Termine une conversation avec nettoyage"""
    try:
        conversation = ChatConversation.objects.get(
            session_id=session_id,
            client=request.user
        )
        
        # Mettre à jour le titre si pas encore fait
        if conversation.title == "Nouvelle conversation":
            first_user_message = conversation.messages.filter(message_type='user').first()
            if first_user_message:
                title_words = first_user_message.content.split()[:5]
                conversation.title = ' '.join(title_words) + ('...' if len(title_words) == 5 else '')
            else:
                conversation.title = f"Conversation du {conversation.created_at.strftime('%d/%m/%Y')}"
        
        conversation.is_active = False
        conversation.save()
        
        # Nettoyer la mémoire du chatbot
        try:
            chatbot = get_chatbot_instance()
            chatbot.clear_memory()
            logger.info(f"🧹 Mémoire chatbot nettoyée pour session {session_id}")
        except Exception as cleanup_error:
            logger.warning(f"⚠️ Erreur nettoyage mémoire chatbot: {cleanup_error}")
        
        # Statistiques finales
        final_stats = {
            'duration_minutes': int((timezone.now() - conversation.created_at).total_seconds() / 60),
            'total_messages': conversation.messages.count(),
            'actions_performed': conversation.messages.filter(metadata__action_type__isnull=False).count()
        }
        
        return Response({
            'success': True,
            'message': 'Conversation terminée avec succès',
            'final_title': conversation.title,
            'stats': final_stats
        }, status=status.HTTP_200_OK)
        
    except ChatConversation.DoesNotExist:
        return Response({
            'success': False,
            'error': 'Conversation non trouvée'
        }, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"🔥 Erreur dans end_conversation: {str(e)}")
        return Response({
            'success': False,
            'error': f'Erreur lors de la fermeture de la conversation: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

#---------------------------System Management Section----------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def chatbot_status(request):
    """Vérifie le statut du chatbot avec diagnostics avancés"""
    try:
        chatbot = get_chatbot_instance()
        
        # Tests de connexion et performance
        test_result = chatbot.test_connection()
        performance_stats = chatbot.get_performance_stats()
        
        # Test d'analyse d'intention simple
        try:
            test_analysis = chatbot.explain_intent_decision("quel est mon solde")
            analysis_working = True
            analysis_details = {
                'intent': test_analysis.get('best_intent'),
                'confidence': test_analysis.get('best_score'),
                'threshold_passed': test_analysis.get('threshold_passed')
            }
        except Exception as analysis_error:
            analysis_working = False
            analysis_details = {'error': str(analysis_error)}
        
        # Déterminer le statut global
        overall_status = 'operational'
        if not test_result.get('status') == 'success':
            overall_status = 'error'
        elif not analysis_working:
            overall_status = 'degraded'
        elif performance_stats.get('average_response_time', 0) > 10:
            overall_status = 'slow'
        
        return Response({
            'success': True,
            'status': overall_status,
            'connection_test': test_result,
            'performance_stats': performance_stats,
            'analysis_test': {
                'working': analysis_working,
                'details': analysis_details
            },
            'last_check': timezone.now().isoformat()
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Erreur chatbot_status: {str(e)}")
        return Response({
            'success': False,
            'error': f'Erreur: {str(e)}',
            'status': 'error'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def reset_chatbot_memory(request):
    """Remet à zéro la mémoire du chatbot avec confirmation"""
    try:
        chatbot = get_chatbot_instance()
        
        # Sauvegarder les stats avant reset
        stats_before = chatbot.get_performance_stats()
        
        # Reset de la mémoire
        chatbot.clear_memory()
        
        # Invalider les caches utilisateur si demandé
        if request.data.get('clear_user_cache', False):
            BankingService._invalidate_client_cache(request.user)
        
        logger.info(f"Mémoire chatbot réinitialisée pour utilisateur {request.user.id}")
        
        return Response({
            'success': True,
            'message': 'Mémoire du chatbot réinitialisée',
            'stats_before_reset': stats_before,
            'reset_timestamp': timezone.now().isoformat()
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Erreur reset_chatbot_memory: {str(e)}")
        return Response({
            'success': False,
            'error': f'Erreur: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

#---------------------------Analytics and Export Section----------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_chatbot_analytics(request):
    """Récupère les analytics détaillés du chatbot pour l'utilisateur"""
    try:
        # Statistiques des conversations de l'utilisateur
        conversations = ChatConversation.objects.filter(client=request.user)
        total_conversations = conversations.count()
        active_conversations = conversations.filter(is_active=True).count()
        
        # Statistiques des messages
        all_messages = ChatMessage.objects.filter(conversation__client=request.user)
        total_messages = all_messages.count()
        user_messages = all_messages.filter(message_type='user').count()
        bot_messages = all_messages.filter(message_type='bot').count()
        
        # Statistiques des intentions détectées
        intents_stats = {}
        bot_messages_with_intent = all_messages.filter(
            message_type='bot',
            intent_detected__isnull=False
        )
        
        for msg in bot_messages_with_intent:
            intent = msg.intent_detected
            if intent in intents_stats:
                intents_stats[intent]['count'] += 1
                intents_stats[intent]['total_confidence'] += msg.confidence_score or 0
            else:
                intents_stats[intent] = {
                    'count': 1,
                    'total_confidence': msg.confidence_score or 0
                }
        
        # Calculer les moyennes de confiance
        for intent_data in intents_stats.values():
            intent_data['average_confidence'] = intent_data['total_confidence'] / intent_data['count']
        
        # Statistiques des actions bancaires
        messages_with_actions = all_messages.filter(
            metadata__action_type__isnull=False
        )
        
        action_stats = {}
        for msg in messages_with_actions:
            action_type = msg.metadata.get('action_type')
            action_success = msg.metadata.get('action_result', {}).get('success', False)
            
            if action_type not in action_stats:
                action_stats[action_type] = {
                    'total': 0,
                    'successful': 0,
                    'failed': 0
                }
            
            action_stats[action_type]['total'] += 1
            if action_success:
                action_stats[action_type]['successful'] += 1
            else:
                action_stats[action_type]['failed'] += 1
        
        # Calculer les taux de succès
        for action_data in action_stats.values():
            action_data['success_rate'] = action_data['successful'] / action_data['total'] if action_data['total'] > 0 else 0
        
        # Activité temporelle (dernières 30 jours)
        thirty_days_ago = timezone.now() - timedelta(days=30)
        recent_conversations = conversations.filter(created_at__gte=thirty_days_ago)
        recent_messages = all_messages.filter(created_at__gte=thirty_days_ago)
        
        analytics_data = {
            'overview': {
                'total_conversations': total_conversations,
                'active_conversations': active_conversations,
                'total_messages': total_messages,
                'user_messages': user_messages,
                'bot_messages': bot_messages,
                'average_messages_per_conversation': total_messages / max(1, total_conversations)
            },
            'intents_analytics': intents_stats,
            'actions_analytics': action_stats,
            'recent_activity': {
                'conversations_last_30_days': recent_conversations.count(),
                'messages_last_30_days': recent_messages.count()
            },
            'user_engagement': {
                'average_conversation_length': all_messages.count() / max(1, total_conversations),
                'most_used_intent': max(intents_stats.items(), key=lambda x: x[1]['count'])[0] if intents_stats else None,
                'most_used_action': max(action_stats.items(), key=lambda x: x[1]['total'])[0] if action_stats else None
            }
        }
        
        return Response({
            'success': True,
            'analytics': analytics_data,
            'generated_at': timezone.now().isoformat()
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Erreur get_chatbot_analytics: {str(e)}")
        return Response({
            'success': False,
            'error': f'Erreur lors de la génération des analytics: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def export_conversation_data(request):
    """Exporte les données de conversation pour l'utilisateur"""
    try:
        export_format = request.data.get('format', 'json').lower()
        session_ids = request.data.get('session_ids', [])
        include_metadata = request.data.get('include_metadata', False)
        
        # Filtrer les conversations
        conversations_query = ChatConversation.objects.filter(client=request.user)
        if session_ids:
            conversations_query = conversations_query.filter(session_id__in=session_ids)
        
        conversations = conversations_query.order_by('-created_at')
        
        # Préparer les données d'export
        export_data = []
        
        for conv in conversations:
            messages = conv.messages.all().order_by('created_at')
            
            conversation_data = {
                'conversation_id': conv.id,
                'session_id': conv.session_id,
                'title': conv.title,
                'created_at': conv.created_at.isoformat(),
                'is_active': conv.is_active,
                'message_count': messages.count(),
                'messages': []
            }
            
            for msg in messages:
                message_data = {
                    'id': msg.id,
                    'type': msg.message_type,
                    'content': msg.content,
                    'timestamp': msg.created_at.isoformat(),
                    'intent': msg.intent_detected,
                    'confidence': msg.confidence_score
                }
                
                if include_metadata and msg.metadata:
                    message_data['metadata'] = msg.metadata
                
                conversation_data['messages'].append(message_data)
            
            export_data.append(conversation_data)
        
        # Générer la réponse selon le format
        if export_format == 'json':
            return Response({
                'success': True,
                'data': export_data,
                'export_info': {
                    'format': 'json',
                    'conversations_count': len(export_data),
                    'exported_at': timezone.now().isoformat(),
                    'include_metadata': include_metadata
                }
            }, status=status.HTTP_200_OK)
        
        elif export_format == 'csv':
            output = StringIO()
            writer = csv.writer(output)
            
            # Headers
            headers = ['conversation_id', 'session_id', 'message_type', 'content', 'timestamp', 'intent', 'confidence']
            writer.writerow(headers)
            
            # Data
            for conv in export_data:
                for msg in conv['messages']:
                    writer.writerow([
                        conv['conversation_id'],
                        conv['session_id'],
                        msg['type'],
                        msg['content'],
                        msg['timestamp'],
                        msg['intent'] or '',
                        msg['confidence'] or ''
                    ])
            
            csv_content = output.getvalue()
            output.close()
            
            response = HttpResponse(csv_content, content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename="chatbot_conversations_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'
            return response
        
        else:
            return Response({
                'success': False,
                'error': f'Format d\'export non supporté: {export_format}. Formats disponibles: json, csv'
            }, status=status.HTTP_400_BAD_REQUEST)
            
    except Exception as e:
        logger.error(f"Erreur export_conversation_data: {str(e)}")
        return Response({
            'success': False,
            'error': f'Erreur lors de l\'export: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

#---------------------------System Health and Debug Section----------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def system_health_check(request):
    """Vérification complète de la santé du système chatbot"""
    try:
        health_status = {
            'overall_status': 'healthy',
            'checks': {},
            'timestamp': timezone.now().isoformat()
        }
        
        # 1. Test connexion chatbot
        try:
            chatbot = get_chatbot_instance()
            connection_test = chatbot.test_connection()
            health_status['checks']['chatbot_connection'] = {
                'status': 'healthy' if connection_test.get('status') == 'success' else 'unhealthy',
                'details': connection_test,
                'response_time': connection_test.get('response_time', 0)
            }
        except Exception as e:
            health_status['checks']['chatbot_connection'] = {
                'status': 'unhealthy',
                'error': str(e)
            }
            health_status['overall_status'] = 'unhealthy'
        
        # 2. Test base de données
        try:
            test_query_start = time.time()
            ChatConversation.objects.filter(client=request.user).count()
            db_response_time = time.time() - test_query_start
            
            health_status['checks']['database'] = {
                'status': 'healthy',
                'response_time': db_response_time,
                'details': 'Database queries working normally'
            }
        except Exception as e:
            health_status['checks']['database'] = {
                'status': 'unhealthy',
                'error': str(e)
            }
            health_status['overall_status'] = 'unhealthy'
        
        # 3. Test service bancaire
        try:
            banking_test_start = time.time()
            banking_result = BankingService.get_all_client_accounts(request.user)
            banking_response_time = time.time() - banking_test_start
            
            health_status['checks']['banking_service'] = {
                'status': 'healthy' if banking_result.get('success') else 'degraded',
                'response_time': banking_response_time,
                'details': banking_result.get('message', 'Banking service operational')
            }
        except Exception as e:
            health_status['checks']['banking_service'] = {
                'status': 'unhealthy',
                'error': str(e)
            }
            if health_status['overall_status'] == 'healthy':
                health_status['overall_status'] = 'degraded'
        
        # 4. Test intention detection rapide
        try:
            intent_test_start = time.time()
            chatbot = get_chatbot_instance()
            test_result = chatbot.explain_intent_decision("test de santé système")
            intent_response_time = time.time() - intent_test_start
            
            health_status['checks']['intent_detection'] = {
                'status': 'healthy' if test_result.get('best_intent') else 'degraded',
                'response_time': intent_response_time,
                'confidence': test_result.get('best_score', 0),
                'details': f"Intent detection working, best intent: {test_result.get('best_intent', 'none')}"
            }
        except Exception as e:
            health_status['checks']['intent_detection'] = {
                'status': 'unhealthy',
                'error': str(e)
            }
            health_status['overall_status'] = 'unhealthy'
        
        return Response(health_status, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Erreur system_health_check: {str(e)}")
        return Response({
            'overall_status': 'unhealthy',
            'error': f'Erreur lors de la vérification de santé: {str(e)}',
            'timestamp': timezone.now().isoformat()
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def debug_user_context(request):
    """Debug - Affiche le contexte utilisateur (à supprimer en production)"""
    try:
        accounts_result = BankingService.get_all_client_accounts(request.user)
        user_accounts = accounts_result.get('accounts', []) if accounts_result.get('success') else []
        
        user_context = {
            'user_id': request.user.id,
            'username': request.user.username,
            'name': getattr(request.user, 'name', '') or request.user.username,
            'accounts': user_accounts,
            'has_accounts': len(user_accounts) > 0,
            'primary_account': next((acc for acc in user_accounts if acc.get('is_primary')), 
                                  user_accounts[0] if user_accounts else None)
        }
        
        return Response({
            'success': True,
            'user_context': user_context,
            'note': 'Endpoint de debug - à supprimer en production'
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def test_chatbot_intents(request):
    """Test des intentions du chatbot avec messages d'exemple"""
    try:
        test_messages = request.data.get('messages', [
            "quel est mon solde",
            "virement de 500dt vers ACC-123456789",
            "payer ma facture STEG",
            "mes comptes",
            "historique des transactions"
        ])
        
        chatbot = get_chatbot_instance()
        results = {}
        
        for message in test_messages:
            try:
                explanation = chatbot.explain_intent_decision(message)
                results[message] = {
                    'intent': explanation.get('best_intent'),
                    'confidence': explanation.get('best_score'),
                    'threshold_passed': explanation.get('threshold_passed'),
                    'all_scores': explanation.get('all_scores', {}),
                    'parameters': chatbot.extract_parameters(message, explanation.get('best_intent')) if explanation.get('best_intent') else {}
                }
            except Exception as e:
                results[message] = {
                    'error': str(e),
                    'intent': None,
                    'confidence': 0.0
                }
        
        # Statistiques globales
        successful_tests = len([r for r in results.values() if 'error' not in r])
        total_tests = len(results)
        average_confidence = sum([r.get('confidence', 0) for r in results.values() if 'error' not in r]) / max(1, successful_tests)
        
        return Response({
            'success': True,
            'test_results': results,
            'statistics': {
                'total_tests': total_tests,
                'successful_tests': successful_tests,
                'success_rate': successful_tests / total_tests if total_tests > 0 else 0,
                'average_confidence': average_confidence
            },
            'performance_stats': chatbot.get_performance_stats()
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Erreur test_chatbot_intents: {str(e)}")
        return Response({
            'success': False,
            'error': f'Erreur lors du test: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

