from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils import timezone
from django.contrib.auth.hashers import make_password, check_password
import pyotp
from decimal import Decimal
from django.utils.crypto import get_random_string



class Client(AbstractUser):
    # Champs personnalisés supplémentaires
    GENDER_CHOICES = [
        ('M', 'Masculin'),
        ('F', 'Féminin'),
    ]
    
    name = models.CharField(max_length=100, verbose_name="Nom complet")
    gender = models.CharField(
        max_length=1,
        choices=GENDER_CHOICES,
        verbose_name="Genre"
    )
    age = models.PositiveIntegerField(verbose_name="Âge",null=True)
    state = models.CharField(max_length=50, verbose_name="État/Région")
    city = models.CharField(max_length=50, verbose_name="Ville")
    
    contact = models.CharField(
        max_length=20,
        verbose_name="Téléphone"
    )
    
    email = models.EmailField(
        max_length=100,
        unique=True,
        verbose_name="Adresse email"
    )
    is_2fa_enabled = models.BooleanField(
        default=False,
        verbose_name="2FA activé"
    )
    is_first_login = models.BooleanField(default=True, verbose_name="Première connexion")
    totp_secret = models.CharField(
        max_length=32,
        blank=True,
        null=True,
        verbose_name="Clé secrète TOTP"
    )
    
    date_joined = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Date d'inscription"
    )
    last_updated = models.DateTimeField(
        auto_now=True,
        verbose_name="Dernière mise à jour"
    )
    account_number = models.CharField(
        max_length=20,
        unique=True,
        verbose_name="Numéro de compte",
        default=get_random_string(length=10)
    )
    preferred_language = models.CharField(
        max_length=10,
        default='fr',
        choices=[('fr', 'Français'), ('en', 'English')],
        verbose_name="Langue préférée"
    )
    chatbot_enabled = models.BooleanField(
        default=True,
        verbose_name="Chatbot activé"
    )

    class Meta:
        verbose_name = "Client"
        verbose_name_plural = "Clients"
        ordering = ['-date_joined']
        constraints = [
        models.UniqueConstraint(fields=['email'], name='unique_email'),
        models.UniqueConstraint(fields=['account_number'], name='unique_account'),
    ]
    
    def set_password(self, raw_password):
        self.password = make_password(raw_password)

    def check_password(self, raw_password):
        return check_password(raw_password, self.password)

    def generate_totp_secret(self):
        """Génère et sauvegarde un nouveau secret TOTP"""
        self.totp_secret = pyotp.random_base32()
        self.save()
        return self.totp_secret
    
    def verify_totp(self, code):
        """Vérifie un code TOTP"""
        if not self.totp_secret:
            return False
        totp = pyotp.TOTP(self.totp_secret)
        return totp.verify(code, valid_window=1)

    def get_display_name(self):
        """Retourne le nom d'affichage préféré"""
        return self.name or self.first_name or self.username
    
    def get_total_balance(self):
        """Calcule le solde total de tous les comptes"""
        return sum(account.balance for account in self.bank_accounts.filter(is_active=True))

    def has_sufficient_balance(self, amount, account_type=None):
        """Vérifie si l'utilisateur a un solde suffisant"""
        if account_type:
            account = self.bank_accounts.filter(account_type=account_type, is_active=True).first()
            return account and account.balance >= Decimal(str(amount))
        return self.get_total_balance() >= Decimal(str(amount))
    
    def __str__(self):
        return self.username
    
class BankAccount(models.Model):
    ACCOUNT_TYPES = [
        ('checking', 'Compte Courant'),
        ('savings', 'Compte Épargne'),
        ('business', 'Compte Professionnel'),
        ('credit', 'Compte Crédit')
    ]
    
    client = models.ForeignKey(
        Client, 
        on_delete=models.CASCADE, 
        related_name='bank_accounts',
        verbose_name="Client"
    )
    account_number = models.CharField(
        max_length=30, 
        unique=True,
        verbose_name="Numéro de compte bancaire"
    )
    account_type = models.CharField(
        max_length=20, 
        choices=ACCOUNT_TYPES,
        verbose_name="Type de compte"
    )
    balance = models.DecimalField(
        max_digits=15, 
        decimal_places=2, 
        default=0,
        verbose_name="Solde"
    )
    currency = models.CharField(
        max_length=3,
        default='EUR',
        verbose_name="Devise"
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Compte actif"
    )
    is_primary = models.BooleanField(
        default=False,
        verbose_name="Compte principal"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Date de création"
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Dernière mise à jour"
    )

    class Meta:
        verbose_name = "Compte Bancaire"
        verbose_name_plural = "Comptes Bancaires"
        ordering = ['-is_primary', '-created_at']

    def __str__(self):
        return f"{self.client.get_display_name()} - {self.account_number} ({self.get_account_type_display()})"

    def can_debit(self, amount):
        """Vérifie si un débit est possible"""
        return self.is_active and self.balance >= Decimal(str(amount))


class Transaction(models.Model):
    TRANSACTION_TYPES = [
        ('debit', 'Débit'),
        ('credit', 'Crédit'),
        ('transfer', 'Virement'),
        ('payment', 'Paiement'),
        ('withdrawal', 'Retrait'),
        ('deposit', 'Dépôt')
    ]
    
    TRANSACTION_STATUS = [
        ('pending', 'En attente'),
        ('processing', 'En cours'),
        ('completed', 'Terminé'),
        ('failed', 'Échoué'),
        ('cancelled', 'Annulé')
    ]
    
    account = models.ForeignKey(
        BankAccount, 
        on_delete=models.CASCADE, 
        related_name='transactions',
        verbose_name="Compte"
    )
    transaction_type = models.CharField(
        max_length=15, 
        choices=TRANSACTION_TYPES,
        verbose_name="Type de transaction"
    )
    amount = models.DecimalField(
        max_digits=15, 
        decimal_places=2,
        verbose_name="Montant"
    )
    description = models.TextField(verbose_name="Description")
    recipient_account = models.CharField(
        max_length=30, 
        blank=True, 
        null=True,
        verbose_name="Compte destinataire"
    )
    recipient_name = models.CharField(
        max_length=100, 
        blank=True, 
        null=True,
        verbose_name="Nom du destinataire"
    )
    reference = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name="Référence"
    )
    status = models.CharField(
        max_length=15,
        choices=TRANSACTION_STATUS,
        default='pending',
        verbose_name="Statut"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Date de création"
    )
    processed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Date de traitement"
    )
    
    # Champs pour l'audit et la sécurité
    initiated_by_chatbot = models.BooleanField(
        default=False,
        verbose_name="Initié par chatbot"
    )
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        verbose_name="Adresse IP"
    )

    class Meta:
        verbose_name = "Transaction"
        verbose_name_plural = "Transactions"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_transaction_type_display()} - {self.amount}€ - {self.created_at.strftime('%d/%m/%Y')}"

    def mark_as_completed(self):
        """Marque la transaction comme terminée"""
        self.status = 'completed'
        self.processed_at = timezone.now()
        self.save()


class RecurringPayment(models.Model):
    FREQUENCY_CHOICES = [
        ('daily', 'Quotidien'),
        ('weekly', 'Hebdomadaire'),
        ('monthly', 'Mensuel'),
        ('quarterly', 'Trimestriel'),
        ('yearly', 'Annuel')
    ]
    
    account = models.ForeignKey(
        BankAccount, 
        on_delete=models.CASCADE, 
        related_name='recurring_payments',
        verbose_name="Compte"
    )
    recipient_account = models.CharField(
        max_length=30,
        verbose_name="Compte destinataire"
    )
    recipient_name = models.CharField(
        max_length=100,
        verbose_name="Nom du destinataire"
    )
    amount = models.DecimalField(
        max_digits=15, 
        decimal_places=2,
        verbose_name="Montant"
    )
    description = models.TextField(verbose_name="Description")
    frequency = models.CharField(
        max_length=15, 
        choices=FREQUENCY_CHOICES,
        verbose_name="Fréquence"
    )
    next_payment_date = models.DateTimeField(verbose_name="Prochaine date de paiement")
    end_date = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Date de fin"
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Actif"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Date de création"
    )
    
    class Meta:
        verbose_name = "Paiement Récurrent"
        verbose_name_plural = "Paiements Récurrents"
        ordering = ['-created_at']

    def __str__(self):
        return f"Paiement récurrent: {self.description} - {self.amount}€"


# Modèles pour le Chatbot
class ChatConversation(models.Model):
    client = models.ForeignKey(
        Client, 
        on_delete=models.CASCADE, 
        related_name='chat_conversations',
        verbose_name="Client"
    )
    session_id = models.CharField(
        max_length=100, 
        unique=True,
        verbose_name="ID de session"
    )
    title = models.CharField(
        max_length=200,
        blank=True,
        null=True,
        verbose_name="Titre de la conversation"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Date de création"
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Dernière mise à jour"
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Conversation active"
    )
    ended_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Date de fin"
    )

    class Meta:
        verbose_name = "Conversation Chat"
        verbose_name_plural = "Conversations Chat"
        ordering = ['-updated_at']

    def __str__(self):
        return f"Conversation {self.session_id} - {self.client.get_display_name()}"

    def get_messages_count(self):
        return self.messages.count()

    def end_conversation(self):
        """Termine la conversation"""
        self.is_active = False
        self.ended_at = timezone.now()
        self.save()


class ChatMessage(models.Model):
    MESSAGE_TYPES = [
        ('user', 'Utilisateur'),
        ('bot', 'Chatbot'),
        ('system', 'Système')
    ]
    
    conversation = models.ForeignKey(
        ChatConversation, 
        on_delete=models.CASCADE, 
        related_name='messages',
        verbose_name="Conversation"
    )
    message_type = models.CharField(
        max_length=10, 
        choices=MESSAGE_TYPES,
        verbose_name="Type de message"
    )
    content = models.TextField(verbose_name="Contenu")
    metadata = models.JSONField(
        default=dict, 
        blank=True,
        verbose_name="Métadonnées"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Date de création"
    )
    
    # Champs pour l'analyse et l'amélioration
    intent_detected = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name="Intention détectée"
    )
    confidence_score = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Score de confiance"
    )
    processing_time = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Temps de traitement (ms)"
    )

    class Meta:
        verbose_name = "Message Chat"
        verbose_name_plural = "Messages Chat"
        ordering = ['created_at']

    def __str__(self):
        return f"{self.get_message_type_display()}: {self.content[:50]}..."