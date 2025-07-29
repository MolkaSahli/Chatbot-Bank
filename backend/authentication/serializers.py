from rest_framework import serializers
from django.contrib.auth import authenticate
from .models import Client
import pyotp
import qrcode
from io import BytesIO
import base64
from django.contrib.auth.models import User
from django.contrib.auth import get_user_model


User = get_user_model()

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email']

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
                # Retourne l’utilisateur sans générer les tokens maintenant
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
