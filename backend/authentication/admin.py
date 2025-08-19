from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Client,BankAccount, Transaction
import secrets
import string

@admin.register(Client)
class ClientAdmin(UserAdmin):
    list_display = ['username', 'name', 'account_number', 'email', 'is_2fa_enabled', 'is_first_login', 'date_joined', 'is_active']
    list_filter = ['is_2fa_enabled', 'is_first_login', 'gender', 'state', 'is_active', 'is_staff']
    search_fields = ['name', 'email', 'account_number', 'username']
    readonly_fields = ['date_joined', 'last_login']
    
    # Fieldsets pour l'édition d'un utilisateur existant
    fieldsets = (
        (None, {
            'fields': ('username', 'password')
        }),
        ('Informations personnelles', {
            'fields': ('name', 'email', 'gender', 'age', 'state', 'city', 'contact')
        }),
        ('Informations bancaires', {
            'fields': ('account_number',)
        }),
        ('Authentification 2FA', {
            'fields': ('is_first_login', 'is_2fa_enabled')
        }),
        ('Permissions', {
            'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions'),
        }),
        ('Dates importantes', {
            'fields': ('last_login', 'date_joined'),
        }),
    )
    
    # Fieldsets pour la création d'un nouvel utilisateur
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'password1', 'password2')
        }),
        ('Informations personnelles', {
            'classes': ('wide',),
            'fields': ('name', 'email', 'gender', 'age', 'state', 'city', 'contact')
        }),
        ('Informations bancaires', {
            'classes': ('wide',),
            'fields': ('account_number',)
        }),
        ('Permissions', {
            'classes': ('wide',),
            'fields': ('is_active', 'is_staff', 'is_superuser')
        }),
    )
    
    def save_model(self, request, obj, form, change):
        if not change:  # Nouveau client
            # Définir automatiquement is_first_login et is_2fa_enabled
            obj.is_first_login = True
            obj.is_2fa_enabled = False
            
        super().save_model(request, obj, form, change)
    
    def get_readonly_fields(self, request, obj=None):
        if not obj:  
            return ['date_joined', 'last_login']
        return ['date_joined', 'last_login']  
    
admin.site.register(BankAccount)
admin.site.register(Transaction)