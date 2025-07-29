from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils import timezone
from django.contrib.auth.hashers import make_password, check_password
import pyotp


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
    
    backup_codes =models.TextField(
        blank=True,
        null=True,
        verbose_name="Codes de secours"
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
        verbose_name="Numéro de compte"
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

    def verify_backup_code(self, code):
        if not self.backup_codes:
            return False
        backup_codes = self.backup_codes.split(',')
        if code in backup_codes:
            backup_codes.remove(code)
            self.backup_codes = ','.join(backup_codes)
            self.save()
            return True
        return False

    def __str__(self):
        return f"{self.name} ({self.email})"