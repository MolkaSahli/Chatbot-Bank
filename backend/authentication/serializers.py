from rest_framework import serializers
from django.contrib.auth import authenticate
import pyotp
import qrcode
from io import BytesIO
import base64
from django.contrib.auth.models import User
from django.contrib.auth import get_user_model
from .models import *

# CORRIGÉ : Utiliser Client au lieu de User comme prévu dans les corrections
User = get_user_model()  # Cela devrait pointer vers Client

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'name', 'first_name', 'last_name', 
                 'account_number', 'is_2fa_enabled', 'is_first_login', 'preferred_language']
        read_only_fields = ['id', 'account_number']
        
class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    email = serializers.EmailField()
    password = serializers.CharField()
    totp_code = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        username = attrs.get('username')
        email = attrs.get('email')
        password = attrs.get('password')
        totp_code = attrs.get('totp_code')

        if username and email and password:
            try:
                user = User.objects.get(username=username, email=email)
            except User.DoesNotExist:
                raise serializers.ValidationError('Identifiants incorrects')

            if not user.check_password(password):
                raise serializers.ValidationError('Identifiants incorrects')

            # Si l'utilisateur a activé la 2FA, mais aucun code n'est encore fourni
            if getattr(user, 'is_2fa_enabled', False) and not totp_code:
                # Retourne l'utilisateur sans générer les tokens maintenant
                attrs['2fa_required'] = True
                attrs['user'] = user
                return attrs

            # ⚠️ Si 2FA est activée et le code est fourni ici, tu peux faire la vérification ici :
            # if getattr(user, 'is_2fa_enabled', False) and totp_code:
            #     if not verify_totp(user, totp_code):
            #         raise serializers.ValidationError('Code 2FA invalide')

            attrs['user'] = user
            return attrs
        else:
            raise serializers.ValidationError('Username, email et mot de passe requis')

class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField()
    new_password = serializers.CharField(min_length=8)
    confirm_password = serializers.CharField()
    
    def validate(self, attrs):
        if attrs['new_password'] != attrs['confirm_password']:
            raise serializers.ValidationError('Les mots de passe ne correspondent pas.')
        return attrs

class FirstLoginSetupSerializer(serializers.Serializer):
    new_password = serializers.CharField(min_length=8)
    confirm_password = serializers.CharField()
    
    def validate(self, attrs):
        if attrs['new_password'] != attrs['confirm_password']:
            raise serializers.ValidationError('Les mots de passe ne correspondent pas.')
        return attrs

class Setup2FASerializer(serializers.Serializer):
    def get_qr_code(self, user):
        if not user.totp_secret:
            secret = pyotp.random_base32()
            user.totp_secret = secret
            user.save()  # Sauvegarde le secret en base
        else:
            secret = user.totp_secret

        totp_uri = pyotp.totp.TOTP(secret).provisioning_uri(
            user.email,
            issuer_name="E-Bank Secure"
        )
        
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(totp_uri)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        
        qr_code_base64 = base64.b64encode(buffer.getvalue()).decode()
        
        return {
            'secret': secret,
            'qr_code': f'data:image/png;base64,{qr_code_base64}',
            'manual_entry_key': secret
        }

#--------------------------------Chatbot Section-------------------------------

class ChatMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatMessage
        fields = ['id', 'message_type', 'content', 'metadata', 'created_at',
                 'intent_detected', 'confidence_score']  # AJOUTÉ : nouveaux champs
        read_only_fields = ['id', 'created_at']

class ConversationSerializer(serializers.ModelSerializer):
    messages_count = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()  # AJOUTÉ
    
    class Meta:
        model = ChatConversation
        fields = ['id', 'session_id', 'title', 'created_at', 'updated_at', 
                 'is_active', 'messages_count', 'last_message', 'ended_at']  # AJOUTÉ : title et ended_at
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def get_messages_count(self, obj):
        return obj.get_messages_count()  # CORRIGÉ : utiliser la méthode du modèle
    
    def get_last_message(self, obj):
        """Récupère le dernier message de la conversation"""
        last_msg = obj.messages.order_by('-created_at').first()
        if last_msg:
            return {
                'content': last_msg.content[:50] + ('...' if len(last_msg.content) > 50 else ''),
                'timestamp': last_msg.created_at.isoformat(),
                'type': last_msg.message_type
            }
        return None

class BankAccountSerializer(serializers.ModelSerializer):
    account_type_display = serializers.CharField(source='get_account_type_display', read_only=True)  # AJOUTÉ
    
    class Meta:
        model = BankAccount
        fields = ['id', 'account_number', 'account_type', 'account_type_display', 
                 'balance', 'currency', 'is_primary', 'is_active', 'created_at']  # AJOUTÉ : currency et is_primary
        read_only_fields = ['id', 'created_at']

class TransactionSerializer(serializers.ModelSerializer):
    account_number = serializers.CharField(source='account.account_number', read_only=True)
    transaction_type_display = serializers.CharField(source='get_transaction_type_display', read_only=True)  # AJOUTÉ
    status_display = serializers.CharField(source='get_status_display', read_only=True)  # AJOUTÉ
    
    class Meta:
        model = Transaction
        fields = [
            'id', 'account_number', 'transaction_type', 'transaction_type_display',
            'amount', 'description', 'recipient_account', 'recipient_name', 
            'reference', 'status', 'status_display', 'created_at', 'processed_at',
            'initiated_by_chatbot'  # AJOUTÉ : nouveaux champs importants
        ]
        read_only_fields = ['id', 'created_at', 'reference']

class RecurringPaymentSerializer(serializers.ModelSerializer):
    account_number = serializers.CharField(source='account.account_number', read_only=True)
    frequency_display = serializers.CharField(source='get_frequency_display', read_only=True)  # AJOUTÉ
    
    class Meta:
        model = RecurringPayment
        fields = [
            'id', 'account_number', 'recipient_account', 'recipient_name',
            'amount', 'description', 'frequency', 'frequency_display',
            'next_payment_date', 'end_date', 'is_active', 'created_at'  # AJOUTÉ : end_date
        ]
        read_only_fields = ['id', 'created_at']

# AMÉLIORÉ : Serializers pour les requêtes chatbot
class ChatbotRequestSerializer(serializers.Serializer):
    session_id = serializers.CharField(max_length=100)
    message = serializers.CharField(max_length=1000)
    
    def validate_message(self, value):
        """Valide que le message n'est pas vide après nettoyage"""
        if not value or not value.strip():
            raise serializers.ValidationError('Le message ne peut pas être vide.')
        return value.strip()

class ChatbotResponseSerializer(serializers.Serializer):
    intent = serializers.CharField()
    confidence = serializers.FloatField()
    response = serializers.CharField()
    requires_action = serializers.BooleanField()
    action_type = serializers.CharField(allow_null=True)
    parameters = serializers.DictField(required=False, default=dict)  # AJOUTÉ

# NOUVEAU : Serializer pour les réponses du dashboard
class DashboardSerializer(serializers.Serializer):
    user = UserSerializer()
    accounts = BankAccountSerializer(many=True)
    recent_transactions = TransactionSerializer(many=True)
    total_balance = serializers.DecimalField(max_digits=15, decimal_places=2)
    active_conversations = serializers.IntegerField()

# NOUVEAU : Serializer pour les actions bancaires
class TransferRequestSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=15, decimal_places=2, min_value=0.01)
    recipient_account = serializers.CharField(max_length=20)
    recipient_name = serializers.CharField(max_length=100)
    description = serializers.CharField(max_length=255, required=False, allow_blank=True)
    from_account_number = serializers.CharField(max_length=20, required=False)
    
    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError('Le montant doit être positif.')
        if value > 50000:  # Limite arbitraire
            raise serializers.ValidationError('Le montant dépasse la limite autorisée.')
        return value

class PaymentRequestSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=15, decimal_places=2, min_value=0.01)
    merchant = serializers.CharField(max_length=100)
    description = serializers.CharField(max_length=255, required=False, allow_blank=True)
    from_account_number = serializers.CharField(max_length=20, required=False)

# NOUVEAU : Serializer pour l'historique des conversations
class ConversationHistorySerializer(serializers.ModelSerializer):
    messages = ChatMessageSerializer(many=True, read_only=True)
    
    class Meta:
        model = ChatConversation
        fields = ['id', 'session_id', 'title', 'created_at', 'updated_at', 
                 'is_active', 'ended_at', 'messages']

# NOUVEAU : Serializer pour les réponses d'actions bancaires
class BankingActionResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    transaction_id = serializers.IntegerField(required=False)
    new_balance = serializers.DecimalField(max_digits=15, decimal_places=2, required=False)
    reference = serializers.CharField(required=False)

# NOUVEAU : Serializer pour les erreurs standardisées
class ErrorResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=False)
    error = serializers.CharField()
    error_code = serializers.CharField(required=False)
    details = serializers.DictField(required=False)

# NOUVEAU : Serializer pour les réponses de succès standardisées
class SuccessResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    message = serializers.CharField(required=False)
    data = serializers.DictField(required=False)