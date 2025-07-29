from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Client
import secrets
import string

@admin.register(Client)
class ClientAdmin(UserAdmin):
    list_display = ['name', 'account_number', 'email', 'is_2fa_enabled', 'is_first_login', 'date_joined']
    list_filter = ['is_2fa_enabled', 'is_first_login', 'gender', 'state']
    search_fields = ['name', 'email', 'account_number']
    readonly_fields = ['date_joined', 'last_updated']
    
    fieldsets = (
        ('Informations personnelles', {
            'fields': ('name', 'gender', 'age', 'state', 'city', 'contact', 'email')
        }),
        ('Informations bancaires', {
            'fields': ('account_number',)
        }),
        ('Authentification', {
            'fields': ('username', 'password', 'is_first_login', 'is_2fa_enabled')
        }),
        ('Permissions', {
            'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions'),
        }),
        ('Dates importantes', {
            'fields': ('date_joined', 'last_updated'),
        }),
    )
    
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('name', 'gender', 'age', 'state', 'city', 'contact', 'email', 'account_number', 'username', 'password1', 'password2')
        }),
    )
    
    def save_model(self, request, obj, form, change):
        if not change:  # Nouveau client
            # Définir automatiquement is_first_login et is_2fa_enabled
            obj.is_first_login = True
            obj.is_2fa_enabled = False
            
        super().save_model(request, obj, form, change)
    
    def get_readonly_fields(self, request, obj=None):
        if not obj:  # Lors de l'ajout d'un nouvel objet
            return self.readonly_fields
        return self.readonly_fields + ['username']