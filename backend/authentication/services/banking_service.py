from decimal import Decimal
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Union
from django.db import transaction
from django.utils import timezone
from django.core.cache import cache
from django.db.models import Q, Sum, Count
from django.core.exceptions import ObjectDoesNotExist, ValidationError
import logging
from ..models import Client, BankAccount, Transaction, RecurringPayment
from decimal import InvalidOperation
import re

logger = logging.getLogger(__name__)

class BankingService:
    """Service bancaire optimisé avec gestion d'erreurs robuste - VERSION CORRIGÉE"""
    
    # Configuration du cache
    CACHE_TIMEOUT = 300  # 5 minutes
    
    # Constantes pour les devises et formats
    SUPPORTED_CURRENCIES = {
        'TND': {'symbol': 'Dt', 'format': 'tunisian'},
        'Dt': {'symbol': 'Dt', 'format': 'tunisian'},
        'EUR': {'symbol': '€', 'format': 'european'},
        'USD': {'symbol': '$', 'format': 'american'}
    }
    
    @staticmethod
    def _format_currency(amount: Decimal, currency: str) -> str:
        """Formate le montant selon la devise avec gestion étendue"""
        if not isinstance(amount, (Decimal, float, int)):
            return "0,00"
        
        amount = Decimal(str(amount))
        currency_info = BankingService.SUPPORTED_CURRENCIES.get(currency, {
            'symbol': currency, 
            'format': 'generic'
        })
        
        symbol = currency_info['symbol']
        format_type = currency_info['format']
        
        if format_type == 'tunisian':
            formatted = f"{amount:,.2f}".replace(',', ' ').replace('.', ',')
            return f"{formatted} {symbol}"
        elif format_type == 'european':
            formatted = f"{amount:,.2f}".replace(',', ' ').replace('.', ',')
            return f"{formatted} {symbol}"
        elif format_type == 'american':
            return f"{symbol}{amount:,.2f}"
        else:
            return f"{amount:,.2f} {symbol}"
    
    @staticmethod
    def format_transaction_history_response(history_data: Dict, parameters: Dict = None) -> str:
        """
        Formate l'historique des transactions pour l'affichage utilisateur - VERSION CORRIGÉE
        """
        if not history_data.get('success'):
            return history_data.get('message', "Erreur lors de la récupération de l'historique.")

        transactions = history_data.get('transactions', [])
        total_count = history_data.get('total_count', 0)
        
        if not transactions:
            # Message personnalisé selon les filtres appliqués
            if parameters:
                if parameters.get('period'):
                    period_names = {
                        'week': 'cette semaine',
                        'month': 'ce mois',
                        'year': 'cette année',
                        'yesterday': 'hier',
                        'today': 'aujourd\'hui'
                    }
                    period_text = period_names.get(parameters['period'], 'la période demandée')
                    return f"Aucune transaction trouvée pour {period_text}."
                elif parameters.get('transaction_type'):
                    return f"Aucune transaction de type '{parameters['transaction_type']}' trouvée."
            
            return "Aucune transaction trouvée dans votre historique."

        response_parts = []
        
        # ---- En-tête personnalisé ----
        if parameters:
            if parameters.get('period'):
                period_names = {
                    'week': 'cette semaine',
                    'month': 'ce mois',
                    'year': 'cette année',
                    'yesterday': 'hier',
                    'today': 'aujourd\'hui',
                    'days': f'{parameters.get("period_count", "")} derniers jours'.strip(),
                    'weeks': f'{parameters.get("period_count", "")} dernières semaines'.strip(),
                    'months': f'{parameters.get("period_count", "")} derniers mois'.strip()
                }
                period_text = period_names.get(parameters['period'], '')
                response_parts.append(f"📋 Transactions {period_text} ({len(transactions)}) :")
            elif parameters.get('user_requested_count'):
                count = parameters['user_requested_count']
                if count == 1:
                    response_parts.append("📋 Votre dernière transaction :")
                else:
                    response_parts.append(f"📋 Vos {len(transactions)} dernières transactions (sur {total_count}) :")
            elif parameters.get('start_date') or parameters.get('end_date'):
                response_parts.append(f"📋 Transactions pour la période demandée ({len(transactions)}) :")
            else:
                response_parts.append(f"📋 Vos {len(transactions)} dernières transactions :")
        else:
            response_parts.append(f"📋 Vos {len(transactions)} dernières transactions :")
        
        response_parts.append("")

        # ---- Détail des transactions ----
        for i, trans in enumerate(transactions, 1):
            type_icons = {
                "payment": "💳",
                "transfer": "💸", 
                "deposit": "💰",
                "withdrawal": "🏧",
                "debit": "➖",
                "credit": "➕"
            }
            
            type_code = trans.get("type_code", "")
            icon = type_icons.get(type_code, "📄")
            
            # Formatage du montant
            amount_str = "N/A"
            if trans.get("formatted_amount"):
                amount_str = trans["formatted_amount"]
            elif trans.get("amount"):
                try:
                    amount = float(trans["amount"])
                    currency = trans.get("currency", "TND")
                    amount_str = f"{amount:,.2f} {currency}".replace(",", " ")
                except (ValueError, TypeError):
                    amount_str = str(trans["amount"])

            transaction_type = trans.get("type", "Transaction")
            date_str = trans.get("formatted_date", trans.get("date", "Date inconnue"))

            # Ligne principale
            response_parts.append(f"{icon} {i}. {date_str}")
            response_parts.append(f"   {transaction_type} - {amount_str}")

            # Description
            if trans.get("description"):
                description = trans["description"]
                if len(description) > 50:
                    description = description[:47] + "..."
                response_parts.append(f"   📝 {description}")

            # Destinataire
            recipient = trans.get("recipient") or trans.get("recipient_name")
            if recipient:
                response_parts.append(f"   👤 {recipient}")

            # Référence
            reference = trans.get("reference")
            if reference:
                response_parts.append(f"   🔢 Réf: {reference}")

            # Compte (si plusieurs comptes)
            if trans.get("account_number"):
                response_parts.append(f"   🏦 Compte: {trans['account_number']}")

            # Statut si pas completed
            status_code = trans.get("status_code", "").lower()
            if status_code and status_code != "completed":
                status_icons = {
                    "pending": "⏳",
                    "failed": "❌", 
                    "cancelled": "🚫",
                    "processing": "⏳"
                }
                status_icon = status_icons.get(status_code, "⚠️")
                status_text = trans.get("status", "Statut inconnu")
                response_parts.append(f"   {status_icon} {status_text}")

            response_parts.append("")

        # ---- Pied de page ----
        if total_count > len(transactions):
            remaining = total_count - len(transactions)
            response_parts.append(f"💡 {remaining} transaction(s) supplémentaire(s) disponible(s).")
            response_parts.append("Demandez un nombre plus élevé pour voir plus de transactions.")

        return "\n".join(response_parts)



    @staticmethod
    def _get_cache_key(prefix: str, identifier: Union[int, str]) -> str:
        """Génère une clé de cache standardisée"""
        return f"banking_{prefix}_{identifier}"
    
    @staticmethod
    def _invalidate_client_cache(client: Client) -> None:
        """Invalide tous les caches liés à un client"""
        cache_keys = [
            BankingService._get_cache_key("accounts", client.id),
            BankingService._get_cache_key("summary", client.id),
            BankingService._get_cache_key("transactions", client.id)
        ]
        cache.delete_many(cache_keys)
    
    @staticmethod
    def _validate_and_convert_amount(amount: Union[Decimal, float, str]) -> tuple:
        """
        Valide et convertit un montant en Decimal
        Retourne (amount_decimal, error_message)
        """
        try:
            if isinstance(amount, str):
                # Nettoyer le montant
                amount = amount.strip().replace(' ', '').replace(',', '.')
                # Supprimer tout caractère non numérique sauf le point décimal
                amount = re.sub(r'[^\d\.]', '', amount)
                
                if not amount or amount == '.':
                    return None, 'Montant invalide. Veuillez saisir un nombre valide.'
            
            amount_decimal = Decimal(str(amount))
            
            if amount_decimal <= 0:
                return None, 'Le montant doit être supérieur à zéro.'
            
            return amount_decimal, None
            
        except (ValueError, TypeError, InvalidOperation):
            return None, f'Montant invalide "{amount}". Veuillez saisir un nombre valide.'
    
    @staticmethod
    def _get_source_account(client: Client, from_account_id: Optional[int] = None, 
                          from_account_number: Optional[str] = None) -> tuple:
        """
        Récupère le compte source avec validation
        Retourne (account, error_message)
        """
        try:
            account_query = client.bank_accounts.filter(is_active=True)
            
            if from_account_id:
                source_account = account_query.filter(id=from_account_id).first()
                if not source_account:
                    return None, f'Compte avec ID {from_account_id} non trouvé ou inactif.'
            elif from_account_number:
                source_account = account_query.filter(account_number=from_account_number).first()
                if not source_account:
                    return None, f'Compte {from_account_number} non trouvé ou inactif.'
            else:
                source_account = account_query.order_by('-is_primary', '-created_at').first()
                if not source_account:
                    return None, 'Aucun compte actif disponible.'
                logger.info(f"Utilisation du compte par défaut: {source_account.account_number}")
            
            # Vérification d'accès sécurisée
            if not BankingService.check_client_account_access(client, source_account.account_number):
                return None, 'Accès non autorisé à ce compte.'
            
            return source_account, None
            
        except Exception as e:
            logger.error(f"Erreur _get_source_account: {str(e)}")
            return None, f'Erreur lors de la récupération du compte: {str(e)}'
    
    @staticmethod
    def _check_account_balance(account: BankAccount, amount: Decimal) -> tuple:
        """
        Vérifie si le compte a un solde suffisant
        Retourne (can_debit, error_message)
        """
        try:
            if hasattr(account, 'can_debit') and callable(getattr(account, 'can_debit')):
                can_debit = account.can_debit(amount)
            else:
                can_debit = account.balance >= amount
                
            if not can_debit:
                formatted_balance = BankingService._format_currency(account.balance, account.currency)
                formatted_amount = BankingService._format_currency(amount, account.currency)
                return False, f'Solde insuffisant. Montant demandé: {formatted_amount}, Solde disponible: {formatted_balance}'
            
            return True, None
            
        except Exception as e:
            logger.error(f"Erreur _check_account_balance: {str(e)}")
            if account.balance < amount:
                return False, 'Solde insuffisant pour effectuer cette opération.'
            return True, None

    @staticmethod
    def enhanced_make_payment(
        client: Client,
        amount: Union[Decimal, float, str],
        merchant: str,
        bill_number: str,
        description: str = "",
        from_account_number: Optional[str] = None,
        from_account_id: Optional[int] = None,
        payment_type: str = 'bill'
    ) -> Dict:
        """
        Paiement de facture avec gestion complète - VERSION ENTIÈREMENT CORRIGÉE
        """
        try:
            logger.info(f"Début paiement facture - Client: {client.id}, Montant: {amount}, Fournisseur: {merchant}, N° Facture: {bill_number}")
            
            # === 1. VALIDATION DU MONTANT ===
            amount_decimal, amount_error = BankingService._validate_and_convert_amount(amount)
            if amount_error:
                return {'success': False, 'message': amount_error}
            
            # === 2. VALIDATION DES CHAMPS OBLIGATOIRES ===
            if not merchant or not merchant.strip():
                return {'success': False, 'message': 'Le nom du fournisseur est obligatoire.'}
            
            if not bill_number or not bill_number.strip():
                return {'success': False, 'message': 'Le numéro de facture est obligatoire.'}
            
            # Nettoyer les données
            merchant = merchant.strip()
            bill_number = bill_number.strip()
            description = description.strip() if description else ""
            
            # === 3. VALIDATION DU NUMÉRO DE FACTURE ===
            if len(bill_number) < 3:
                return {'success': False, 'message': 'Numéro de facture invalide (minimum 3 caractères).'}
            
            if not re.search(r'[A-Za-z0-9]', bill_number):
                return {'success': False, 'message': 'Format de numéro de facture invalide.'}
            
            # === 4. TRANSACTION ATOMIQUE ===
            with transaction.atomic():
                # Récupérer le compte source
                source_account, account_error = BankingService._get_source_account(
                    client, from_account_id, from_account_number
                )
                if account_error:
                    return {'success': False, 'message': account_error}
                
                # Vérifier le solde
                can_debit, balance_error = BankingService._check_account_balance(source_account, amount_decimal)
                if not can_debit:
                    return {
                        'success': False,
                        'message': balance_error,
                        'available_balance': float(source_account.balance),
                        'requested_amount': float(amount_decimal)
                    }
                
                # Sauvegarder l'ancien solde
                old_balance = source_account.balance
                
                try:
                    # Débiter le compte
                    source_account.balance = source_account.balance - amount_decimal
                    source_account.updated_at = timezone.now()
                    source_account.save(update_fields=['balance', 'updated_at'])
                    logger.info(f"Compte débité: {old_balance} -> {source_account.balance}")
                    
                except Exception as debit_error:
                    logger.error(f"Erreur lors du débit: {debit_error}")
                    raise
                
                try:
                    # Créer la transaction
                    if description:
                        transaction_description = f"{description} - Facture {bill_number} - {merchant}"
                    else:
                        transaction_description = f"Paiement facture {bill_number} - {merchant}"
                    
                    transaction_data = {
                        'account': source_account,
                        'transaction_type': 'payment',
                        'amount': amount_decimal,
                        'description': transaction_description,
                        'recipient_name': merchant,
                        'recipient_account': bill_number[:30],
                        'status': 'completed',
                        'processed_at': timezone.now(),
                    }
                    
                    # Ajouter le champ chatbot s'il existe
                    if hasattr(Transaction, 'initiated_by_chatbot'):
                        transaction_data['initiated_by_chatbot'] = True
                    
                    transaction_obj = Transaction.objects.create(**transaction_data)
                    logger.info(f"Transaction créée: ID {transaction_obj.id}")
                    
                except Exception as transaction_error:
                    logger.error(f"Erreur création transaction: {transaction_error}")
                    # Restaurer le solde
                    source_account.balance = old_balance
                    source_account.save(update_fields=['balance', 'updated_at'])
                    raise
                
                # Générer la référence
                try:
                    reference = f"PAY{transaction_obj.id:08d}"
                    if hasattr(transaction_obj, 'reference'):
                        transaction_obj.reference = reference
                        transaction_obj.save(update_fields=['reference'])
                except Exception:
                    reference = f"PAY{transaction_obj.id}"
                
                # Invalider le cache
                try:
                    BankingService._invalidate_client_cache(client)
                except Exception as cache_error:
                    logger.warning(f"Erreur invalidation cache: {cache_error}")
                
                # Formatage pour la réponse
                formatted_amount = BankingService._format_currency(amount_decimal, source_account.currency)
                formatted_new_balance = BankingService._format_currency(source_account.balance, source_account.currency)
                
                logger.info(f"Paiement facture réussi - Transaction ID: {transaction_obj.id}")
                
                return {
                    'success': True,
                    'message': f'Paiement de facture {bill_number} de {formatted_amount} à {merchant} effectué avec succès.',
                    'transaction_id': transaction_obj.id,
                    'reference': reference,
                    'amount': float(amount_decimal),
                    'formatted_amount': formatted_amount,
                    'merchant': merchant,
                    'bill_number': bill_number,
                    'old_balance': float(old_balance),
                    'new_balance': float(source_account.balance),
                    'formatted_new_balance': formatted_new_balance,
                    'source_account': source_account.account_number,
                    'currency': source_account.currency,
                    'processed_at': transaction_obj.processed_at.isoformat(),
                    'date_paiement': timezone.now().strftime('%d/%m/%Y à %H:%M'),
                    'description': transaction_description
                }
                    
        except Exception as e:
            logger.error(f"Erreur générale paiement facture: {str(e)}", exc_info=True)
            return {
                'success': False,
                'message': 'Erreur technique lors du paiement. Veuillez réessayer.',
                'error_details': str(e)
            }
    """
    @staticmethod
    def transfer_money(
        client: Client, 
        amount: Union[Decimal, float, str], 
        recipient_account: str,
        recipient_name: str,
        description: str = "",
        from_account_number: Optional[str] = None,
        from_account_id: Optional[int] = None,
        validate_recipient: bool = True
    ) -> Dict:
        
        #Effectue un virement avec validation étendue - VERSION CORRIGÉE
        
        try:
            logger.info(f"Début virement - Client: {client.id}, Montant: {amount}, Vers: {recipient_name} ({recipient_account})")
            
            # === 1. VALIDATION DU MONTANT ===
            amount_decimal, amount_error = BankingService._validate_and_convert_amount(amount)
            if amount_error:
                return {'success': False, 'message': amount_error}
            
            # === 2. VALIDATION DES CHAMPS OBLIGATOIRES ===
            if not recipient_account or not recipient_account.strip():
                return {'success': False, 'message': 'Le numéro de compte destinataire est obligatoire.'}
            
            if not recipient_name or not recipient_name.strip():
                return {'success': False, 'message': 'Le nom du destinataire est obligatoire.'}
            
            # Nettoyer les données
            recipient_account = recipient_account.strip()
            recipient_name = recipient_name.strip()
            description = description.strip() if description else ""
            
            # === 3. VALIDATION DU COMPTE DESTINATAIRE ===
            if len(recipient_account) < 5:
                return {'success': False, 'message': 'Le numéro de compte destinataire semble invalide (trop court).'}
            
            # === 4. TRANSACTION ATOMIQUE ===
            with transaction.atomic():
                # Récupérer le compte source
                source_account, account_error = BankingService._get_source_account(
                    client, from_account_id, from_account_number
                )
                if account_error:
                    return {'success': False, 'message': account_error}
                
                # Validation du destinataire
                if validate_recipient:
                    if recipient_account.upper() == source_account.account_number.upper():
                        return {'success': False, 'message': 'Impossible de faire un virement vers le même compte.'}
                
                # Chercher le compte destinataire dans la base
                destination_account = None
                try:
                    destination_account = BankAccount.objects.filter(
                        account_number=recipient_account,
                        is_active=True
                    ).first()
                    
                    if destination_account:
                        logger.info(f"Compte destinataire trouvé : {destination_account.account_number}")
                    else:
                        logger.info(f"Compte destinataire {recipient_account} non trouvé dans notre système - virement externe")
                        
                except Exception as e:
                    logger.warning(f"Erreur recherche compte destinataire : {e}")
                    destination_account = None

                # Vérifier le solde
                can_debit, balance_error = BankingService._check_account_balance(source_account, amount_decimal)
                if not can_debit:
                    return {
                        'success': False,
                        'message': balance_error,
                        'available_balance': float(source_account.balance),
                        'requested_amount': float(amount_decimal)
                    }
                
                # Sauvegarder les anciens soldes
                old_source_balance = source_account.balance
                old_destination_balance = destination_account.balance if destination_account else None
                
                # === MISE À JOUR DES SOLDES ===
                try:
                    # 1. DÉBITER LE COMPTE SOURCE
                    source_account.balance = source_account.balance - amount_decimal
                    source_account.updated_at = timezone.now()
                    source_account.save(update_fields=['balance', 'updated_at'])
                    logger.info(f"Compte source débité: {old_source_balance} -> {source_account.balance}")
                    
                    # 2. CRÉDITER LE COMPTE DESTINATAIRE (si interne)
                    
                    destination_account.balance = destination_account.balance + amount_decimal
                    destination_account.updated_at = timezone.now()
                    destination_account.save(update_fields=['balance', 'updated_at'])
                    logger.info(f"Compte destinataire crédité: {old_destination_balance} -> {destination_account.balance}")

                except Exception as balance_error:
                    logger.error(f"Erreur lors de la mise à jour des soldes: {balance_error}")
                    raise
                
                # === CRÉATION DES TRANSACTIONS ===
                transaction_description = description or f"Virement vers {recipient_name}"
                
                try:
                    # Transaction pour le compte SOURCE (débit)
                    source_transaction_data = {
                        'account': source_account,
                        'transaction_type': 'transfer',
                        'amount': amount_decimal,
                        'description': transaction_description,
                        'recipient_account': recipient_account,
                        'recipient_name': recipient_name,
                        'status': 'completed',
                        'processed_at': timezone.now(),
                    }
                    
                    if hasattr(Transaction, 'initiated_by_chatbot'):
                        source_transaction_data['initiated_by_chatbot'] = True
                    
                    source_transaction = Transaction.objects.create(**source_transaction_data)
                    logger.info(f"Transaction source créée: ID {source_transaction.id}")
                    
                    # Transaction pour le compte DESTINATAIRE (crédit) - SI INTERNE
                    destination_transaction = None
                    if destination_account:
                        destination_transaction_data = {
                            'account': destination_account,
                            'transaction_type': 'deposit',  # Type crédit
                            'amount': amount_decimal,
                            'description': f"Virement reçu de {source_account.account_number} - {client.first_name} {client.last_name}",
                            'recipient_account': source_account.account_number,
                            'recipient_name': f"{client.first_name} {client.last_name}",
                            'status': 'completed',
                            'processed_at': timezone.now(),
                        }
                        
                        if hasattr(Transaction, 'initiated_by_chatbot'):
                            destination_transaction_data['initiated_by_chatbot'] = True
                        
                        destination_transaction = Transaction.objects.create(**destination_transaction_data)
                        logger.info(f"Transaction destinataire créée: ID {destination_transaction.id}")

                except Exception as transaction_error:
                    logger.error(f"Erreur création transactions: {transaction_error}")
                    # Restaurer les soldes en cas d'erreur
                    source_account.balance = old_source_balance
                    source_account.save(update_fields=['balance', 'updated_at'])
                    if destination_account and old_destination_balance is not None:
                        destination_account.balance = old_destination_balance
                        destination_account.save(update_fields=['balance', 'updated_at'])
                    raise
                
                # === GÉNÉRATION DE LA RÉFÉRENCE ===
                try:
                    reference = f"VIR{source_transaction.id:08d}"
                    if hasattr(source_transaction, 'reference'):
                        source_transaction.reference = reference
                        source_transaction.save(update_fields=['reference'])
                        
                    # Lier les transactions avec la même référence si destinataire interne
                    if destination_transaction and hasattr(destination_transaction, 'reference'):
                        destination_transaction.reference = reference
                        destination_transaction.save(update_fields=['reference'])
                        
                except Exception:
                    reference = f"VIR{source_transaction.id}"
                
                # Invalider le cache
                try:
                    BankingService._invalidate_client_cache(client)
                    if destination_account:
                        try:
                            destination_client = destination_account.client
                            BankingService._invalidate_client_cache(destination_client)
                        except:
                            pass
                except Exception as cache_error:
                    logger.warning(f"Erreur invalidation cache: {cache_error}")
                
                # === RÉPONSE ===
                formatted_amount = BankingService._format_currency(amount_decimal, source_account.currency)
                formatted_new_balance = BankingService._format_currency(source_account.balance, source_account.currency)
                
                logger.info(f"Virement réussi - Transaction ID: {source_transaction.id}")
                
                return {
                    'success': True,
                    'message': f'Virement de {formatted_amount} vers {recipient_name} effectué avec succès.',
                    'transaction_id': source_transaction.id,
                    'reference': reference,
                    'amount': float(amount_decimal),
                    'formatted_amount': formatted_amount,
                    'old_balance': float(old_source_balance),
                    'new_balance': float(source_account.balance),
                    'formatted_new_balance': formatted_new_balance,
                    'recipient_name': recipient_name,
                    'recipient_account': recipient_account,
                    'description': transaction_description,
                    'processed_at': source_transaction.processed_at.isoformat(),
                    'source_account': source_account.account_number,
                    'currency': source_account.currency,
                    'transfer_type': 'internal' if destination_account else 'external',
                    # Informations destinataire si interne
                    'destination_new_balance': float(destination_account.balance) if destination_account else None,
                    'destination_transaction_id': destination_transaction.id if destination_transaction else None
                }
        
        except Exception as e:
            logger.error(f"Erreur générale transfer_money: {str(e)}", exc_info=True)
            return {
                'success': False,
                'message': 'Erreur technique lors du virement. Veuillez réessayer.',
                'error_details': str(e)
            }
    """
    
    @staticmethod
    def transfer_money(
        client, 
        amount: Union[Decimal, float, str], 
        recipient_account: str,
        recipient_name: str, 
        description: str = '', 
        from_account_number: Optional[str] = None,
        from_account_id: Optional[int] = None,  # ✅ AJOUTÉ pour compatibilité
        validate_recipient: bool = True  # ✅ AJOUTÉ pour compatibilité
    ) -> Dict:
        """Effectue un virement entre comptes - VERSION HARMONISÉE"""
        try:
            from django.db import transaction
            from ..models import BankAccount, Transaction
            from django.utils import timezone
            from decimal import Decimal
            
            logger = logging.getLogger(__name__)
            logger.info(f"Début virement - Client: {client.id}, Montant: {amount}, Vers: {recipient_name} ({recipient_account})")
            
            # === VALIDATION ET CONVERSION DU MONTANT ===
            try:
                if isinstance(amount, str):
                    amount = Decimal(amount.replace(',', '.'))
                elif isinstance(amount, float):
                    amount = Decimal(str(amount))
                elif not isinstance(amount, Decimal):
                    amount = Decimal(str(amount))
                    
                if amount <= 0:
                    return {
                        'success': False,
                        'message': 'Le montant doit être positif'
                    }
            except (ValueError, TypeError) as e:
                return {
                    'success': False,
                    'message': 'Montant invalide'
                }
            
            # === VALIDATION DES CHAMPS OBLIGATOIRES ===
            if not recipient_account or not recipient_account.strip():
                return {'success': False, 'message': 'Le numéro de compte destinataire est obligatoire.'}
            
            if not recipient_name or not recipient_name.strip():
                return {'success': False, 'message': 'Le nom du destinataire est obligatoire.'}
            
            # Nettoyer les données
            recipient_account = recipient_account.strip()
            recipient_name = recipient_name.strip()
            description = description.strip() if description else ""
            
            # === RÉCUPÉRER LE COMPTE SOURCE ===
            source_account = None
            
            # Option 1: Par ID de compte (priorité)
            if from_account_id:
                try:
                    source_account = BankAccount.objects.get(
                        id=from_account_id,
                        client=client,
                        is_active=True
                    )
                except BankAccount.DoesNotExist:
                    return {
                        'success': False,
                        'message': f'Compte source avec ID {from_account_id} non trouvé'
                    }
            
            # Option 2: Par numéro de compte
            elif from_account_number:
                try:
                    source_account = BankAccount.objects.get(
                        client=client,
                        account_number=from_account_number,
                        is_active=True
                    )
                except BankAccount.DoesNotExist:
                    return {
                        'success': False,
                        'message': f'Compte source {from_account_number} non trouvé'
                    }
            
            # Option 3: Compte principal ou premier compte disponible
            else:
                # Utiliser le compte principal
                source_account = BankAccount.objects.filter(
                    client=client,
                    is_primary=True,
                    is_active=True
                ).first()
                
                if not source_account:
                    source_account = BankAccount.objects.filter(
                        client=client,
                        is_active=True
                    ).first()
                    
                if not source_account:
                    return {
                        'success': False,
                        'message': 'Aucun compte source disponible'
                    }
            
            # === VALIDATIONS ===
            # Validation du destinataire
            if validate_recipient:
                if recipient_account.upper() == source_account.account_number.upper():
                    return {'success': False, 'message': 'Impossible de faire un virement vers le même compte.'}
            
            # Vérifier le solde suffisant
            if source_account.balance < amount:
                return {
                    'success': False,
                    'message': f'Solde insuffisant. Solde actuel: {source_account.balance:,.2f} {source_account.currency}',
                    'available_balance': float(source_account.balance),
                    'requested_amount': float(amount)
                }
            
            # === RECHERCHE DU COMPTE DESTINATAIRE ===
            destination_account = None
            
            try:
                logger.info(f"🔍 Recherche du compte destinataire: '{recipient_account}'")
                
                # Nettoyer le numéro de compte
                clean_recipient = str(recipient_account).strip()
                
                # Recherche directe
                destination_account = BankAccount.objects.select_related('client').filter(
                    account_number__iexact=clean_recipient,
                    is_active=True
                ).first()
                
                if destination_account:
                    logger.info(f"✅ Compte destinataire trouvé: {destination_account.account_number} (ID: {destination_account.id})")
                else:
                    logger.info(f"❌ Compte destinataire non trouvé - virement externe")
                    
            except Exception as e:
                logger.error(f"Erreur recherche compte destinataire: {e}")
                destination_account = None
            
            # === TRANSACTION ATOMIQUE ===
            with transaction.atomic():
                # Sauvegarder les anciens soldes
                old_source_balance = source_account.balance
                old_destination_balance = destination_account.balance if destination_account else None
                
                try:
                    # 1. DÉBITER LE COMPTE SOURCE
                    source_account.balance -= amount
                    source_account.save()
                    logger.info(f"Compte source débité: {old_source_balance} -> {source_account.balance}")
                    
                    # 2. CRÉDITER LE COMPTE DESTINATAIRE (si interne)
                    if destination_account:
                        destination_account.balance += amount
                        destination_account.save()
                        logger.info(f"Compte destinataire crédité: {old_destination_balance} -> {destination_account.balance}")
                    
                except Exception as balance_error:
                    logger.error(f"Erreur lors de la mise à jour des soldes: {balance_error}")
                    raise
                
                try:
                    # 3. CRÉER LA TRANSACTION SOURCE (débit)
                    transaction_description = description or f'Virement vers {recipient_name}'
                    reference = f'TRF-{timezone.now().strftime("%Y%m%d")}-{Transaction.objects.count() + 1:06d}'
                    
                    source_transaction = Transaction.objects.create(
                        account=source_account,
                        transaction_type='transfer',
                        amount=amount,
                        description=transaction_description,
                        recipient_account=recipient_account,
                        recipient_name=recipient_name,
                        status='completed',
                        processed_at=timezone.now(),
                        reference=reference
                    )
                    
                    # Ajouter le champ chatbot si disponible
                    if hasattr(source_transaction, 'initiated_by_chatbot'):
                        source_transaction.initiated_by_chatbot = True
                        source_transaction.save()
                    
                    logger.info(f"Transaction source créée: ID {source_transaction.id}")
                    
                    # 4. CRÉER LA TRANSACTION DESTINATAIRE (si interne)
                    destination_transaction = None
                    if destination_account:
                        destination_transaction = Transaction.objects.create(
                            account=destination_account,
                            transaction_type='deposit',  # ou 'transfer' selon votre logique
                            amount=amount,
                            description=f'Virement reçu de {client.first_name} {client.last_name}',
                            recipient_account=source_account.account_number,
                            recipient_name=f"{client.first_name} {client.last_name}",
                            status='completed',
                            processed_at=timezone.now(),
                            reference=reference
                        )
                        
                        if hasattr(destination_transaction, 'initiated_by_chatbot'):
                            destination_transaction.initiated_by_chatbot = True
                            destination_transaction.save()
                        
                        logger.info(f"Transaction destinataire créée: ID {destination_transaction.id}")
                    
                except Exception as transaction_error:
                    logger.error(f"Erreur création transactions: {transaction_error}")
                    # Restaurer les soldes en cas d'erreur
                    source_account.balance = old_source_balance
                    source_account.save()
                    if destination_account and old_destination_balance is not None:
                        destination_account.balance = old_destination_balance
                        destination_account.save()
                    raise
                
                # === RÉPONSE ===
                logger.info(f"Virement réussi - Transaction ID: {source_transaction.id}")
                
                return {
                    'success': True,
                    'message': f'Virement de {amount} {source_account.currency} vers {recipient_name} effectué avec succès.',
                    'transaction_id': source_transaction.id,
                    'reference': reference,
                    'amount': float(amount),
                    'currency': source_account.currency,
                    'source_account': source_account.account_number,
                    'recipient_account': recipient_account,
                    'recipient_name': recipient_name,
                    'new_balance': float(source_account.balance),
                    'old_balance': float(old_source_balance),
                    'internal_transfer': destination_account is not None,
                    'processed_at': source_transaction.processed_at.isoformat(),
                    'destination_new_balance': float(destination_account.balance) if destination_account else None,
                    'destination_transaction_id': destination_transaction.id if destination_transaction else None
                }
        
        except Exception as e:
            logger.error(f"Erreur générale transfer_money: {str(e)}")
            return {
                'success': False,
                'message': 'Erreur technique lors du virement. Veuillez réessayer.',
                'error_details': str(e)
            }

    @staticmethod
    def make_payment(
        client: Client,
        amount: Union[Decimal, float, str],
        merchant: str,
        description: str = "",
        from_account_number: Optional[str] = None,
        from_account_id: Optional[int] = None,
        payment_method: str = 'standard',
        bill_number: Optional[str] = None
    ) -> Dict:
        """
        Effectue un paiement simple ou de facture - VERSION CORRIGÉE
        """
        # Si c'est un paiement de facture, utiliser la méthode spécialisée
        if bill_number:
            return BankingService.enhanced_make_payment(
                client=client,
                amount=amount,
                merchant=merchant,
                bill_number=bill_number,
                description=description,
                from_account_number=from_account_number,
                from_account_id=from_account_id,
                payment_type='bill'
            )
        
        # Paiement standard
        try:
            logger.info(f"Début paiement standard - Client: {client.id}, Montant: {amount}, Marchand: {merchant}")
            
            # === 1. VALIDATION DU MONTANT ===
            amount_decimal, amount_error = BankingService._validate_and_convert_amount(amount)
            if amount_error:
                return {'success': False, 'message': amount_error}
            
            # === 2. VALIDATION DES DONNÉES ===
            if not merchant or not merchant.strip():
                return {'success': False, 'message': 'Le nom du marchand est obligatoire.'}
            
            merchant = merchant.strip()
            description = description.strip() if description else ""
            
            # === 3. TRANSACTION ATOMIQUE ===
            with transaction.atomic():
                # Récupérer le compte source
                source_account, account_error = BankingService._get_source_account(
                    client, from_account_id, from_account_number
                )
                if account_error:
                    return {'success': False, 'message': account_error}
                
                # Vérifier le solde
                can_debit, balance_error = BankingService._check_account_balance(source_account, amount_decimal)
                if not can_debit:
                    return {
                        'success': False,
                        'message': balance_error,
                        'available_balance': float(source_account.balance),
                        'requested_amount': float(amount_decimal)
                    }
                
                # Sauvegarder l'ancien solde
                old_balance = source_account.balance
                
                try:
                    # Débiter le compte
                    source_account.balance = source_account.balance - amount_decimal
                    source_account.updated_at = timezone.now()
                    source_account.save(update_fields=['balance', 'updated_at'])
                    
                except Exception as debit_error:
                    logger.error(f"Erreur lors du débit: {debit_error}")
                    raise
                
                try:
                    # Créer la transaction
                    transaction_description = description or f"Paiement à {merchant}"
                    
                    transaction_data = {
                        'account': source_account,
                        'transaction_type': 'payment',
                        'amount': amount_decimal,
                        'description': transaction_description,
                        'recipient_name': merchant,
                        'status': 'completed',
                        'processed_at': timezone.now(),
                    }
                    
                    if hasattr(Transaction, 'initiated_by_chatbot'):
                        transaction_data['initiated_by_chatbot'] = True
                    
                    transaction_obj = Transaction.objects.create(**transaction_data)
                    
                except Exception as transaction_error:
                    logger.error(f"Erreur création transaction: {transaction_error}")
                    # Restaurer le solde
                    source_account.balance = old_balance
                    source_account.save(update_fields=['balance', 'updated_at'])
                    raise
                
                # Générer la référence
                try:
                    reference = f"PAY{transaction_obj.id:08d}"
                    if hasattr(transaction_obj, 'reference'):
                        transaction_obj.reference = reference
                        transaction_obj.save(update_fields=['reference'])
                except Exception:
                    reference = f"PAY{transaction_obj.id}"
                
                # Invalider les caches
                try:
                    BankingService._invalidate_client_cache(client)
                except Exception:
                    pass
                
                # Formatage pour réponse
                formatted_amount = BankingService._format_currency(amount_decimal, source_account.currency)
                formatted_new_balance = BankingService._format_currency(source_account.balance, source_account.currency)
                
                return {
                    'success': True,
                    'message': f'Paiement de {formatted_amount} à {merchant} effectué avec succès.',
                    'transaction_id': transaction_obj.id,
                    'reference': reference,
                    'amount': float(amount_decimal),
                    'formatted_amount': formatted_amount,
                    'old_balance': float(old_balance),
                    'new_balance': float(source_account.balance),
                    'formatted_new_balance': formatted_new_balance,
                    'merchant': merchant,
                    'payment_method': payment_method,
                    'processed_at': transaction_obj.processed_at.isoformat(),
                    'source_account': source_account.account_number
                }
                    
        except Exception as e:
            logger.error(f"Erreur make_payment: {str(e)}")
            return {
                'success': False,
                'message': 'Erreur lors du paiement. Veuillez réessayer.',
                'error_details': str(e)
            }

    # === MÉTHODES EXISTANTES CONSERVÉES ===
    
    @staticmethod
    def get_client_accounts(client: Client, force_refresh: bool = False) -> List[Dict]:
        """Récupère tous les comptes d'un client avec cache optimisé"""
        cache_key = BankingService._get_cache_key("accounts", client.id)
        
        if not force_refresh:
            cached_accounts = cache.get(cache_key)
            if cached_accounts is not None:
                logger.debug(f"Cache hit pour comptes client {client.id}")
                return cached_accounts
        
        try:
            logger.info(f"Récupération comptes pour client {client.id}")
            
            accounts = client.bank_accounts.filter(is_active=True).select_related().order_by(
                '-is_primary', '-created_at'
            )
            
            result = []
            for account in accounts:
                try:
                    account_data = {
                        'id': account.id,
                        'account_number': account.account_number,
                        'account_type': account.get_account_type_display(),
                        'account_type_code': account.account_type,
                        'balance': float(account.balance),
                        'currency': account.currency,
                        'is_primary': account.is_primary,
                        'created_at': account.created_at.isoformat() if account.created_at else None,
                        'formatted_balance': BankingService._format_currency(account.balance, account.currency),
                        'status': 'active' if account.is_active else 'inactive'
                    }
                    result.append(account_data)
                except Exception as e:
                    logger.warning(f"Erreur formatting compte {account.id}: {str(e)}")
                    continue
            
            cache.set(cache_key, result, BankingService.CACHE_TIMEOUT)
            logger.info(f"Trouvé {len(result)} comptes pour client {client.id}")
            
            return result
            
        except Exception as e:
            logger.error(f"Erreur get_client_accounts pour client {client.id}: {str(e)}")
            return []
    
    @staticmethod
    def get_client_by_id(user_id: int) -> Optional[Client]:
        """Récupère un client par ID utilisateur (à adapter selon votre modèle)"""
        try:
            client = Client.objects.get(id=user_id, is_active=True)  # Assumons un modèle Client avec Django ORM
            return client
        except Client.DoesNotExist:
            logger.error(f"Client ID {user_id} non trouvé")
            return None
        except Exception as e:
            logger.error(f"Erreur lors de la récupération du client {user_id}: {str(e)}")
            return None

    @staticmethod
    def get_account_balance(
        client: Client, 
        account_number: Optional[str] = None,
        account_id: Optional[int] = None
    ) -> Dict:
        """Récupère le solde d'un compte avec recherche flexible"""
        try:
            logger.info(f"Récupération solde - Client: {client.id}, Compte: {account_number or account_id}")
            
            account_query = client.bank_accounts.filter(is_active=True)
            
            if account_id:
                account = account_query.filter(id=account_id).first()
            elif account_number:
                account = account_query.filter(account_number=account_number).first()
            else:
                account = account_query.order_by('-is_primary', '-created_at').first()
            
            if not account:
                message = 'Aucun compte actif trouvé.'
                if account_number:
                    message = f'Compte {account_number} non trouvé.'
                elif account_id:
                    message = f'Compte ID {account_id} non trouvé.'
                
                return {
                    'success': False,
                    'message': message,
                    'balance': None,
                    'account_number': account_number,
                    'account_id': account_id
                }
            
            if not BankingService.check_client_account_access(client, account.account_number):
                return {
                    'success': False,
                    'message': 'Accès non autorisé à ce compte.',
                    'balance': None
                }
            
            logger.info(f"Compte trouvé: {account.account_number}, solde: {account.balance} {account.currency}")
            
            return {
                'success': True,
                'account_id': account.id,
                'account_number': account.account_number,
                'account_type': account.get_account_type_display(),
                'account_type_code': account.account_type,
                'balance': float(account.balance),
                'currency': account.currency,
                'formatted_balance': BankingService._format_currency(account.balance, account.currency),
                'is_primary': account.is_primary,
                'last_updated': timezone.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Erreur get_account_balance: {str(e)}")
            return {
                'success': False,
                'message': f'Erreur technique: {str(e)}',
                'balance': None,
                'error_details': str(e)
            }

    @staticmethod
    def get_transaction_history(
        client: Client, 
        account_number: Optional[str] = None,
        account_id: Optional[int] = None,
        limit: Optional[int] = None,
        transaction_type: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        use_cache: bool = True
    ) -> Dict:
        """Récupère l'historique des transactions avec filtres avancés - VERSION CORRIGÉE avec Africa/Tunis"""
        try:
            logger.info(f"Récupération historique transactions - Client: {client.id}, Compte: {account_number or account_id or 'tous'}")
            
            # Gestion du cache
            cache_key = None
            if use_cache and not (start_date or end_date or transaction_type):
                from datetime import datetime
                import pytz
                tz = pytz.timezone('Africa/Tunis')
                current_date = datetime.now(tz)
                default_start = tz.localize(datetime(current_date.year, current_date.month, 1))
                default_end = current_date
                cache_key = BankingService._get_cache_key(
                    "transactions", 
                    f"{client.id}_{account_number or account_id or 'all'}_{default_start.date()}_{default_end.date()}"
                )
                cached_data = cache.get(cache_key)
                if cached_data:
                    logger.debug(f"Cache hit pour transactions client {client.id}")
                    return cached_data
            
            # Variables pour stocker le compte et la requête
            account = None
            transactions_query = None
            
            # Construction de la requête selon le type de recherche (inchangé)
            if account_id:
                try:
                    account = client.bank_accounts.filter(id=account_id, is_active=True).first()
                    if not account:
                        return {
                            'success': False,
                            'message': f'Compte ID {account_id} non trouvé ou inactif.',
                            'transactions': [],
                            'total_count': 0
                        }
                    transactions_query = account.transactions.all()
                    logger.info(f"Recherche par account_id: {account_id} - Compte: {account.account_number}")
                    
                except Exception as e:
                    logger.error(f"Erreur recherche compte par ID {account_id}: {str(e)}")
                    return {
                        'success': False,
                        'message': f'Erreur lors de la recherche du compte ID {account_id}.',
                        'transactions': [],
                        'error_details': str(e)
                    }
                    
            elif account_number:
                try:
                    account = client.bank_accounts.filter(
                        account_number=account_number, 
                        is_active=True
                    ).first()
                    if not account:
                        return {
                            'success': False,
                            'message': f'Compte {account_number} non trouvé ou inactif.',
                            'transactions': [],
                            'total_count': 0
                        }
                    transactions_query = account.transactions.all()
                    logger.info(f"Recherche par account_number: {account_number}")
                    
                except Exception as e:
                    logger.error(f"Erreur recherche compte par numéro {account_number}: {str(e)}")
                    return {
                        'success': False,
                        'message': f'Erreur lors de la recherche du compte {account_number}.',
                        'transactions': [],
                        'error_details': str(e)
                    }
            else:
                try:
                    accounts = client.bank_accounts.filter(is_active=True)
                    if not accounts.exists():
                        return {
                            'success': True,
                            'message': 'Aucun compte actif trouvé.',
                            'transactions': [],
                            'total_count': 0
                        }
                    transactions_query = Transaction.objects.filter(account__in=accounts)
                    logger.info(f"Recherche sur tous les comptes actifs ({accounts.count()} comptes)")
                    
                except Exception as e:
                    logger.error(f"Erreur recherche tous comptes client {client.id}: {str(e)}")
                    return {
                        'success': False,
                        'message': 'Erreur lors de la recherche des comptes.',
                        'transactions': [],
                        'error_details': str(e)
                    }
            
            # Vérification que nous avons une requête valide (inchangé)
            if transactions_query is None:
                logger.error("transactions_query est None - problème de logique")
                return {
                    'success': False,
                    'message': 'Erreur interne lors de la construction de la requête.',
                    'transactions': [],
                    'total_count': 0
                }
            
            # Appliquer les filtres de type de transaction (inchangé)
            valid_transaction_types = ['debit', 'credit', 'transfer', 'payment', 'withdrawal', 'deposit']
            if transaction_type and transaction_type.lower() in valid_transaction_types:
                transactions_query = transactions_query.filter(transaction_type=transaction_type.lower())
                logger.info(f"Filtre appliqué - type: {transaction_type}")
            
            # Appliquer les filtres de date avec mois par défaut si non spécifié
            if not start_date or not end_date:
                from datetime import datetime
                import pytz
                tz = pytz.timezone('Africa/Tunis')
                current_date = datetime.now(tz)
                default_start = tz.localize(datetime(current_date.year, current_date.month, 1))
                default_end = current_date
                if not start_date:
                    start_date = default_start
                if not end_date:
                    end_date = default_end
            
            transactions_query = transactions_query.filter(created_at__gte=start_date)
            transactions_query = transactions_query.filter(created_at__lte=end_date)
            logger.info(f"Filtres appliqués - Date: {start_date} à {end_date}")
            
            try:
                # Optimiser la requête avec select_related (inchangé)
                transactions_query = transactions_query.select_related('account').order_by('-created_at')
                
                # Compter le total avant pagination
                total_count = transactions_query.count()
                logger.info(f"Total transactions trouvées: {total_count}")
                
                # Appliquer la pagination uniquement si limit est spécifié et > 0
                if limit is not None and limit > 0:
                    recent_transactions = transactions_query[:limit]
                else:
                    recent_transactions = transactions_query  # Toutes les transactions
                
            except Exception as e:
                logger.error(f"Erreur lors de l'exécution de la requête: {str(e)}")
                return {
                    'success': False,
                    'message': 'Erreur lors de la récupération des transactions.',
                    'transactions': [],
                    'error_details': str(e)
                }
            
            # Formatage des résultats (inchangé)
            result = {
                'success': True,
                'total_count': total_count,
                'limit': limit,
                'has_more': total_count > (limit if limit is not None and limit > 0 else total_count),
                'transactions': [],
                'filters_applied': {
                    'account_number': account_number,
                    'account_id': account_id,
                    'transaction_type': transaction_type,
                    'start_date': start_date.isoformat() if start_date else None,
                    'end_date': end_date.isoformat() if end_date else None
                }
            }
            
            # Traitement de chaque transaction (inchangé)
            for t in recent_transactions:
                try:
                    transaction_data = {
                        'id': t.id,
                        'type': t.get_transaction_type_display() if hasattr(t, 'get_transaction_type_display') else t.transaction_type,
                        'type_code': t.transaction_type,
                        'amount': float(t.amount),
                        'description': t.description or '',
                        'recipient': t.recipient_name or '',
                        'recipient_account': t.recipient_account or '',
                        'status': t.get_status_display() if hasattr(t, 'get_status_display') else t.status,
                        'status_code': t.status,
                        'date': t.created_at.isoformat() if t.created_at else None,
                        'account_number': t.account.account_number,
                        'currency': t.account.currency,
                    }
                    
                    try:
                        transaction_data['formatted_amount'] = BankingService._format_currency(
                            t.amount, 
                            t.account.currency
                        )
                    except Exception:
                        transaction_data['formatted_amount'] = f"{t.amount} {t.account.currency}"
                    
                    if hasattr(t, 'processed_at') and t.processed_at:
                        transaction_data['processed_date'] = t.processed_at.isoformat()
                    
                    if hasattr(t, 'reference'):
                        transaction_data['reference'] = t.reference or ''
                    
                    if hasattr(t, 'initiated_by_chatbot'):
                        transaction_data['initiated_by_chatbot'] = t.initiated_by_chatbot
                    
                    if hasattr(t.account, 'get_account_type_display'):
                        transaction_data['account_type'] = t.account.get_account_type_display()
                    
                    if t.created_at:
                        transaction_data['formatted_date'] = t.created_at.strftime('%d/%m/%Y à %H:%M')
                    
                    result['transactions'].append(transaction_data)
                    
                except Exception as e:
                    logger.warning(f"Erreur formatage transaction ID {getattr(t, 'id', 'inconnu')}: {str(e)}")
                    try:
                        basic_transaction = {
                            'id': getattr(t, 'id', 0),
                            'type': getattr(t, 'transaction_type', 'unknown'),
                            'type_code': getattr(t, 'transaction_type', 'unknown'),
                            'amount': float(getattr(t, 'amount', 0)),
                            'formatted_amount': f"{getattr(t, 'amount', 0)} {getattr(t.account, 'currency', 'TND')}",
                            'description': getattr(t, 'description', '') or 'Transaction',
                            'date': getattr(t, 'created_at').isoformat() if getattr(t, 'created_at', None) else None,
                            'formatted_date': getattr(t, 'created_at').strftime('%d/%m/%Y à %H:%M') if getattr(t, 'created_at', None) else 'Date inconnue',
                            'account_number': getattr(t.account, 'account_number', 'Inconnu'),
                            'currency': getattr(t.account, 'currency', 'TND'),
                            'status': getattr(t, 'status', 'unknown'),
                            'status_code': getattr(t, 'status', 'unknown')
                        }
                        result['transactions'].append(basic_transaction)
                    except Exception:
                        continue
            
            # Message de résultat (inchangé)
            if result['transactions']:
                if account:
                    result['message'] = f"Historique de {len(result['transactions'])} transaction(s) pour le compte {account.account_number}."
                else:
                    result['message'] = f"Historique de {len(result['transactions'])} transaction(s) sur tous vos comptes."
            else:
                result['message'] = "Aucune transaction trouvée pour les critères spécifiés."
            
            # Mise en cache avec dates par défaut
            if cache_key and use_cache and len(result['transactions']) > 0:
                try:
                    cache.set(cache_key, result, BankingService.CACHE_TIMEOUT)
                    logger.debug(f"Résultat mis en cache: {cache_key}")
                except Exception as cache_error:
                    logger.warning(f"Erreur mise en cache: {cache_error}")
            
            logger.info(f"Récupéré {len(result['transactions'])} transactions pour client {client.id}")
            return result
            
        except Exception as e:
            logger.error(f"Erreur générale get_transaction_history pour client {getattr(client, 'id', 'inconnu')}: {str(e)}", exc_info=True)
            return {
                'success': False,
                'message': f'Erreur technique lors de la récupération des transactions: {str(e)}',
                'transactions': [],
                'total_count': 0,
                'error_details': str(e)
            }

    @staticmethod
    def get_recurring_payments(client: Client, active_only: bool = True) -> Dict:
        """Récupère tous les paiements récurrents d'un client avec détails enrichis"""
        try:
            accounts = client.bank_accounts.filter(is_active=True)
            
            try:
                if active_only:
                    recurring_payments = RecurringPayment.objects.filter(
                        account__in=accounts,
                        is_active=True
                    ).order_by('-created_at')
                else:
                    recurring_payments = RecurringPayment.objects.filter(
                        account__in=accounts
                    ).order_by('-created_at')
            except:
                # Si le modèle RecurringPayment n'existe pas
                return {
                    'success': True,
                    'recurring_payments': [],
                    'total_count': 0,
                    'active_count': 0,
                    'message': 'Aucun paiement récurrent configuré.'
                }
            
            result = []
            for payment in recurring_payments:
                try:
                    freq_names = {
                        'daily': 'Quotidien',
                        'weekly': 'Hebdomadaire', 
                        'monthly': 'Mensuel',
                        'quarterly': 'Trimestriel',
                        'semestrially': 'Semestriel',
                        'yearly': 'Annuel'
                    }
                    
                    payment_data = {
                        'id': payment.id,
                        'amount': float(payment.amount),
                        'formatted_amount': BankingService._format_currency(
                            payment.amount, 
                            payment.account.currency
                        ),
                        'recipient_name': payment.recipient_name,
                        'recipient_account': payment.recipient_account,
                        'frequency': payment.frequency,
                        'frequency_display': freq_names.get(payment.frequency, payment.frequency),
                        'next_payment_date': payment.next_payment_date.strftime('%d/%m/%Y') if payment.next_payment_date else 'N/A',
                        'next_payment_iso': payment.next_payment_date.isoformat() if payment.next_payment_date else None,
                        'description': payment.description,
                        'is_active': payment.is_active,
                        'created_at': payment.created_at.strftime('%d/%m/%Y') if payment.created_at else 'N/A',
                        'end_date': payment.end_date.strftime('%d/%m/%Y') if hasattr(payment, 'end_date') and payment.end_date else None,
                        'account_number': payment.account.account_number,
                        'status': 'Actif' if payment.is_active else 'Inactif'
                    }
                    
                    result.append(payment_data)
                    
                except Exception as e:
                    logger.warning(f"Erreur formatage paiement récurrent {payment.id}: {str(e)}")
                    continue
            
            return {
                'success': True,
                'recurring_payments': result,
                'total_count': len(result),
                'active_count': len([p for p in result if p['is_active']])
            }
            
        except Exception as e:
            logger.error(f"Erreur get_recurring_payments: {str(e)}")
            return {
                'success': False,
                'message': f'Erreur lors de la récupération: {str(e)}',
                'recurring_payments': [],
                'error_details': str(e)
            }

    @staticmethod
    def setup_recurring_payment(
        client: Client,
        amount: Union[Decimal, float, str],
        recipient_account: str,
        recipient_name: str,
        frequency: str,
        description: str = "",
        from_account_number: Optional[str] = None,
        from_account_id: Optional[int] = None,
        end_date: Optional[datetime] = None,
        start_date: Optional[datetime] = None
    ) -> Dict:
        """Configure un paiement récurrent avec validation étendue"""
        try:
            # Validation du montant
            amount_decimal, amount_error = BankingService._validate_and_convert_amount(amount)
            if amount_error:
                return {'success': False, 'message': amount_error}
            
            # Validation des données
            if not recipient_account or not recipient_account.strip():
                return {'success': False, 'message': 'Compte destinataire requis.'}
            
            if not recipient_name or not recipient_name.strip():
                return {'success': False, 'message': 'Nom du destinataire requis.'}
            
            # Récupérer le compte source
            source_account, account_error = BankingService._get_source_account(
                client, from_account_id, from_account_number
            )
            if account_error:
                return {'success': False, 'message': account_error}
            
            # Validation de la fréquence
            valid_frequencies = ['daily', 'weekly', 'monthly', 'quarterly', 'yearly']
            if frequency not in valid_frequencies:
                return {
                    'success': False,
                    'message': f'Fréquence invalide. Choix: {", ".join(valid_frequencies)}',
                    'valid_frequencies': valid_frequencies
                }
            
            # Calculer la prochaine date de paiement
            base_date = start_date or timezone.now()
            frequency_mapping = {
                'daily': timedelta(days=1),
                'weekly': timedelta(weeks=1),
                'monthly': timedelta(days=30),
                'quarterly': timedelta(days=90),
                'yearly': timedelta(days=365)
            }
            
            next_payment = base_date + frequency_mapping[frequency]
            
            # Validation de la date de fin
            if end_date and end_date <= next_payment:
                return {
                    'success': False,
                    'message': 'La date de fin doit être après la première échéance.'
                }
            
            # Créer le paiement récurrent
            recurring_data = {
                'account': source_account,
                'recipient_account': recipient_account.strip(),
                'recipient_name': recipient_name.strip(),
                'amount': amount_decimal,
                'description': description.strip() or f"Paiement récurrent à {recipient_name}",
                'frequency': frequency,
                'next_payment_date': next_payment,
                'is_active': True
            }
            
            if end_date:
                recurring_data['end_date'] = end_date
            
            recurring_payment = RecurringPayment.objects.create(**recurring_data)
            
            formatted_amount = BankingService._format_currency(amount_decimal, source_account.currency)
            
            logger.info(f"Paiement récurrent créé - ID: {recurring_payment.id}")
            
            return {
                'success': True,
                'message': f'Paiement récurrent de {formatted_amount} configuré avec succès.',
                'recurring_payment_id': recurring_payment.id,
                'amount': float(amount_decimal),
                'formatted_amount': formatted_amount,
                'frequency': frequency,
                'next_payment_date': next_payment.isoformat(),
                'end_date': end_date.isoformat() if end_date else None,
                'recipient_name': recipient_name,
                'recipient_account': recipient_account,
                'source_account': source_account.account_number
            }
            
        except Exception as e:
            logger.error(f"Erreur setup_recurring_payment: {str(e)}")
            return {
                'success': False,
                'message': f'Erreur lors de la configuration: {str(e)}',
                'error_details': str(e)
            }

    @staticmethod
    def get_all_client_accounts(client: Client, include_inactive: bool = False) -> Dict:
        """Récupère TOUS les comptes d'un client avec informations détaillées"""
        try:
            logger.info(f"Récupération de tous les comptes pour client {client.id}")
            
            if include_inactive:
                accounts = client.bank_accounts.all().order_by('-is_primary', '-is_active', '-created_at')
            else:
                accounts = client.bank_accounts.filter(is_active=True).order_by('-is_primary', '-created_at')
            
            if not accounts.exists():
                return {
                    'success': True,
                    'message': 'Aucun compte trouvé pour ce client.',
                    'accounts': [],
                    'total_accounts': 0,
                    'total_balance': 0.0,
                    'formatted_total_balance': '0,00 Dt'
                }
            
            result = []
            total_balance = Decimal('0.00')
            primary_currency = 'TND'
            
            for account in accounts:
                try:
                    if account.is_active:
                        total_balance += account.balance
                    
                    if account.is_primary or (not primary_currency and account.currency):
                        primary_currency = account.currency
                    
                    account_data = {
                        'id': account.id,
                        'account_number': account.account_number,
                        'account_type': account.get_account_type_display(),
                        'account_type_code': account.account_type,
                        'balance': float(account.balance),
                        'currency': account.currency,
                        'is_primary': account.is_primary,
                        'is_active': account.is_active,
                        'created_at': account.created_at.isoformat() if account.created_at else None,
                        'formatted_balance': BankingService._format_currency(account.balance, account.currency),
                        'status': 'Actif' if account.is_active else 'Inactif',
                        'status_code': 'active' if account.is_active else 'inactive'
                    }
                    result.append(account_data)
                    
                except Exception as e:
                    logger.warning(f"Erreur formatting compte {account.id}: {str(e)}")
                    continue
            
            active_accounts = [acc for acc in result if acc['is_active']]
            
            logger.info(f"Trouvé {len(result)} comptes pour client {client.id} ({len(active_accounts)} actifs)")
            
            return {
                'success': True,
                'message': f'Vous avez {len(active_accounts)} compte(s) actif(s).',
                'accounts': result,
                'total_accounts': len(result),
                'active_accounts': len(active_accounts),
                'total_balance': float(total_balance),
                'formatted_total_balance': BankingService._format_currency(total_balance, primary_currency),
                'primary_currency': primary_currency,
                'client_name': getattr(client, 'get_display_name', lambda: f"{client.first_name} {client.last_name}")()
            }
            
        except Exception as e:
            logger.error(f"Erreur get_all_client_accounts pour client {client.id}: {str(e)}")
            return {
                'success': False,
                'message': f'Erreur lors de la récupération des comptes: {str(e)}',
                'accounts': [],
                'total_accounts': 0,
                'error_details': str(e)
            }

    @staticmethod
    def get_client_summary(client: Client) -> Dict:
        """Récupère un résumé complet des finances du client"""
        try:
            accounts = client.bank_accounts.filter(is_active=True)
            
            # Calcul du solde total
            total_balance = sum(account.balance for account in accounts) if accounts.exists() else Decimal('0.00')
            
            # Dernières transactions (5 dernières)
            recent_transactions = Transaction.objects.filter(
                account__in=accounts
            ).order_by('-created_at')[:]
            
            # Paiements récurrents actifs
            try:
                recurring_payments_count = RecurringPayment.objects.filter(
                    account__in=accounts,
                    is_active=True
                ).count()
            except:
                recurring_payments_count = 0
            
            # Déterminer la devise principale
            primary_currency = accounts.first().currency if accounts.exists() else 'TND'
            
            return {
                'success': True,
                'client_name': getattr(client, 'get_display_name', lambda: f"{client.first_name} {client.last_name}")(),
                'total_accounts': accounts.count(),
                'total_balance': float(total_balance),
                'formatted_total_balance': BankingService._format_currency(total_balance, primary_currency),
                'currency': primary_currency,
                'recent_transactions_count': recent_transactions.count(),
                'active_recurring_payments': recurring_payments_count,
                'accounts_summary': [
                    {
                        'type': account.get_account_type_display(),
                        'balance': float(account.balance),
                        'formatted_balance': BankingService._format_currency(account.balance, account.currency),
                        'is_primary': account.is_primary,
                        'currency': account.currency
                    }
                    for account in accounts
                ]
            }
            
        except Exception as e:
            logger.error(f"Erreur get_client_summary: {str(e)}")
            return {
                'success': False,
                'message': f'Erreur lors de la récupération du résumé: {str(e)}'
            }

    @staticmethod
    def check_client_account_access(client: Client, account_number: str) -> bool:
        """Vérifie si un client a accès à un compte donné"""
        try:
            return client.bank_accounts.filter(
                account_number=account_number,
                is_active=True
            ).exists()
        except Exception:
            return False

    # === MÉTHODES ALIAS POUR COMPATIBILITÉ ===
    
    @staticmethod
    def enhanced_transfer_money(
        client: Client, 
        amount: Union[Decimal, float, str], 
        recipient_account: str,
        recipient_name: str,
        description: str = "",
        from_account_number: Optional[str] = None,
        from_account_id: Optional[int] = None,
        validate_recipient: bool = True
    ) -> Dict:
        """Alias pour transfer_money"""
        return BankingService.transfer_money(
            client=client,
            amount=amount,
            recipient_account=recipient_account,
            recipient_name=recipient_name,
            description=description,
            from_account_number=from_account_number,
            from_account_id=from_account_id,
            validate_recipient=validate_recipient
        )
    
    @staticmethod
    def create_payment(
        client: Client,
        amount: Union[Decimal, float, str],
        merchant: str,
        description: str = "",
        from_account_number: Optional[str] = None,
        from_account_id: Optional[int] = None
    ) -> Dict:
        """Alias pour make_payment"""
        return BankingService.make_payment(
            client=client,
            amount=amount,
            merchant=merchant,
            description=description,
            from_account_number=from_account_number,
            from_account_id=from_account_id
        )

    @staticmethod
    def get_user_accounts(client: Optional[Client] = None,
                          user_id: Optional[int] = None,
                          email: Optional[str] = None) -> Dict:
        """Récupère les comptes d'un utilisateur avec gestion d'erreurs améliorée"""
        try:
            # Gestion des appels raccourcis
            if client is not None and not isinstance(client, Client):
                if isinstance(client, int):
                    user_id = client
                    client = None
                elif isinstance(client, str):
                    if '@' in client:
                        email = client
                        client = None

            # Résolution du client
            if client is None:
                if user_id is not None:
                    client = Client.objects.get(pk=user_id)
                elif email is not None:
                    client = Client.objects.get(email=email)
                else:
                    return {
                        'success': False,
                        'message': 'Aucun identifiant de client fourni.',
                        'accounts': []
                    }

            # Utiliser la méthode existante
            accounts = BankingService.get_client_accounts(client)

            return {
                'success': True,
                'client_id': client.id,
                'client_name': getattr(client, 'get_display_name', lambda: f"{client.first_name} {client.last_name}")(),
                'accounts': accounts,
                'total_accounts': len(accounts)
            }

        except Client.DoesNotExist:
            logger.error(f"Client non trouvé - user_id: {user_id}, email: {email}")
            return {
                'success': False,
                'message': 'Client non trouvé.',
                'accounts': []
            }
        except Exception as e:
            logger.error(f"Erreur get_user_accounts: {str(e)}")
            return {
                'success': False,
                'message': f'Erreur lors de la récupération des comptes: {str(e)}',
                'accounts': []
            }

    @staticmethod
    def format_accounts_response(accounts_data: Dict) -> str:
        """Formate la réponse des comptes pour l'affichage utilisateur"""
        if not accounts_data.get('success'):
            return accounts_data.get('message', 'Erreur lors de la récupération des comptes.')
        
        accounts = accounts_data.get('accounts', [])
        
        if not accounts:
            return "Vous n'avez aucun compte bancaire."
        
        response_parts = []
        
        # En-tête
        total_accounts = accounts_data.get('total_accounts', len(accounts))
        active_accounts = accounts_data.get('active_accounts', len([a for a in accounts if a.get('is_active', True)]))
        
        response_parts.append(f"📋 Vos comptes bancaires ({active_accounts} actif{'s' if active_accounts > 1 else ''}) :")
        response_parts.append("")
        
        # Détail des comptes
        for i, account in enumerate(accounts, 1):
            status_icon = "✅" if account.get('is_active', True) else "❌"
            primary_icon = "⭐" if account.get('is_primary') else ""
            
            account_line = f"{status_icon} {primary_icon} Compte {i} - {account.get('account_type', 'Type inconnu')}"
            response_parts.append(account_line)
            response_parts.append(f"   Numéro: {account.get('account_number', 'N/A')}")
            response_parts.append(f"   Solde: {account.get('formatted_balance', 'N/A')}")
            response_parts.append("")
        
        # Total
        if accounts_data.get('formatted_total_balance'):
            response_parts.append(f"💰 Solde total: {accounts_data['formatted_total_balance']}")
        
        return "\n".join(response_parts)