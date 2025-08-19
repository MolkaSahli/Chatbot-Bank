from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

class BankingService:
    """Service bancaire réel avec connexion à la base de données"""
    
    @staticmethod
    def get_user_accounts(client) -> Dict:
        """Récupère tous les comptes d'un client"""
        try:
            from .models import BankAccount
            
            accounts = BankAccount.objects.filter(
                client=client,
                is_active=True
            ).order_by('-is_primary', '-created_at')
            
            accounts_data = []
            for account in accounts:
                accounts_data.append({
                    'id': account.id,
                    'account_number': account.account_number,
                    'account_type': account.account_type,
                    'account_type_display': account.get_account_type_display(),
                    'balance': float(account.balance),
                    'currency': account.currency,
                    'is_primary': account.is_primary,
                    'is_active': account.is_active,
                    'created_at': account.created_at.isoformat()
                })
            
            total_balance = sum(acc['balance'] for acc in accounts_data)
            
            return {
                'success': True,
                'accounts': accounts_data,
                'total_balance': total_balance,
                'accounts_count': len(accounts_data)
            }
            
        except Exception as e:
            logger.error(f"Erreur get_user_accounts: {str(e)}")
            return {
                'success': False,
                'error': f'Erreur lors de la récupération des comptes: {str(e)}'
            }
    
    @staticmethod
    def get_account_balance(client, account_number: Optional[str] = None) -> Dict:
        """Récupère le solde d'un compte spécifique ou du compte principal"""
        try:
            from .models import BankAccount
            
            if account_number:
                # Récupérer un compte spécifique
                try:
                    account = BankAccount.objects.get(
                        client=client,
                        account_number=account_number,
                        is_active=True
                    )
                except BankAccount.DoesNotExist:
                    return {
                        'success': False,
                        'error': f'Compte {account_number} non trouvé ou inactif'
                    }
            else:
                # Récupérer le compte principal
                account = BankAccount.objects.filter(
                    client=client,
                    is_primary=True,
                    is_active=True
                ).first()
                
                if not account:
                    # Si pas de compte principal, prendre le premier compte actif
                    account = BankAccount.objects.filter(
                        client=client,
                        is_active=True
                    ).first()
                    
                if not account:
                    return {
                        'success': False,
                        'error': 'Aucun compte actif trouvé'
                    }
            
            return {
                'success': True,
                'account_number': account.account_number,
                'account_type': account.account_type,
                'account_type_display': account.get_account_type_display(),
                'balance': float(account.balance),
                'currency': account.currency,
                'formatted_balance': f"{account.balance:,.2f} {account.currency}",
                'is_primary': account.is_primary
            }
            
        except Exception as e:
            logger.error(f"Erreur get_account_balance: {str(e)}")
            return {
                'success': False,
                'error': f'Erreur lors de la récupération du solde: {str(e)}'
            }
    
    @staticmethod
    def get_transaction_history(client, account_number: Optional[str] = None, 
                              limit: int = 10, transaction_type: Optional[str] = None) -> Dict:
        """Récupère l'historique des transactions"""
        try:
            from .models import BankAccount, Transaction
            
            # Base query
            query = Transaction.objects.select_related('account').filter(
                account__client=client,
                account__is_active=True
            )
            
            # Filtrer par compte spécifique si fourni
            if account_number:
                query = query.filter(account__account_number=account_number)
            
            # Filtrer par type de transaction si fourni
            if transaction_type:
                query = query.filter(transaction_type=transaction_type)
            
            # Ordonner par date décroissante et limiter
            transactions = query.order_by('-created_at')[:limit]
            
            transactions_data = []
            for trans in transactions:
                transactions_data.append({
                    'id': trans.id,
                    'account_number': trans.account.account_number,
                    'transaction_type': trans.transaction_type,
                    'transaction_type_display': trans.get_transaction_type_display(),
                    'amount': float(trans.amount),
                    'description': trans.description,
                    'recipient_account': trans.recipient_account,
                    'recipient_name': trans.recipient_name,
                    'reference': trans.reference,
                    'status': trans.status,
                    'status_display': trans.get_status_display(),
                    'created_at': trans.created_at.isoformat(),
                    'processed_at': trans.processed_at.isoformat() if trans.processed_at else None,
                    'initiated_by_chatbot': trans.initiated_by_chatbot
                })
            
            return {
                'success': True,
                'transactions': transactions_data,
                'count': len(transactions_data),
                'account_filter': account_number,
                'type_filter': transaction_type
            }
            
        except Exception as e:
            logger.error(f"Erreur get_transaction_history: {str(e)}")
            return {
                'success': False,
                'error': f'Erreur lors de la récupération de l\'historique: {str(e)}'
            }
    
    @staticmethod
    def transfer_money(client, amount: Decimal, recipient_account: str, 
                      recipient_name: str, description: str = '', 
                      from_account_number: Optional[str] = None) -> Dict:
        """Effectue un virement entre comptes"""
        try:
            from .models import BankAccount, Transaction
            
            # Validation du montant
            if amount <= 0:
                return {
                    'success': False,
                    'error': 'Le montant doit être positif'
                }
            
            # Récupérer le compte source
            if from_account_number:
                try:
                    source_account = BankAccount.objects.get(
                        client=client,
                        account_number=from_account_number,
                        is_active=True
                    )
                except BankAccount.DoesNotExist:
                    return {
                        'success': False,
                        'error': f'Compte source {from_account_number} non trouvé'
                    }
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
                        'error': 'Aucun compte source disponible'
                    }
            
            # Vérifier le solde suffisant
            if source_account.balance < amount:
                return {
                    'success': False,
                    'error': f'Solde insuffisant. Solde actuel: {source_account.balance:,.2f} {source_account.currency}'
                }
            
            # Transaction atomique
            with transaction.atomic():
                # Débiter le compte source
                source_account.balance -= amount
                source_account.save()
                
                # Créer la transaction de débit
                debit_transaction = Transaction.objects.create(
                    account=source_account,
                    transaction_type='transfer',
                    amount=amount,
                    description=description or f'Virement vers {recipient_name}',
                    recipient_account=recipient_account,
                    recipient_name=recipient_name,
                    status='completed',
                    processed_at=timezone.now(),
                    initiated_by_chatbot=True,  # Marquer comme initié par le chatbot
                    reference=f'TRF-{timezone.now().strftime("%Y%m%d")}-{Transaction.objects.count() + 1:06d}'
                )
                
                # Vérifier si le compte destinataire existe dans notre système
                try:
                    dest_account = BankAccount.objects.get(
                        account_number=recipient_account,
                        is_active=True
                    )
                    
                    # Si c'est un compte interne, créditer automatiquement
                    dest_account.balance += amount
                    dest_account.save()
                    
                    # Créer la transaction de crédit pour le destinataire
                    Transaction.objects.create(
                        account=dest_account,
                        transaction_type='transfer',
                        amount=amount,
                        description=description or f'Virement de {client.name or client.username}',
                        recipient_account=source_account.account_number,
                        recipient_name=client.name or client.username,
                        status='completed',
                        processed_at=timezone.now(),
                        initiated_by_chatbot=True,
                        reference=debit_transaction.reference
                    )
                    
                    internal_transfer = True
                    
                except BankAccount.DoesNotExist:
                    # Compte externe - la transaction est marquée comme terminée côté source
                    internal_transfer = False
                
                return {
                    'success': True,
                    'message': 'Virement effectué avec succès',
                    'transaction_id': debit_transaction.id,
                    'reference': debit_transaction.reference,
                    'amount': float(amount),
                    'currency': source_account.currency,
                    'source_account': source_account.account_number,
                    'recipient_account': recipient_account,
                    'recipient_name': recipient_name,
                    'new_balance': float(source_account.balance),
                    'internal_transfer': internal_transfer,
                    'processed_at': debit_transaction.processed_at.isoformat()
                }
                
        except Exception as e:
            logger.error(f"Erreur transfer_money: {str(e)}")
            return {
                'success': False,
                'error': f'Erreur lors du virement: {str(e)}'
            }
    
    @staticmethod
    def create_payment(client, amount: Decimal, merchant: str, 
                      description: str = '', from_account_number: Optional[str] = None) -> Dict:
        """Effectue un paiement vers un marchand"""
        try:
            from .models import BankAccount, Transaction
            
            # Validation du montant
            if amount <= 0:
                return {
                    'success': False,
                    'error': 'Le montant doit être positif'
                }
            
            # Récupérer le compte source
            if from_account_number:
                try:
                    source_account = BankAccount.objects.get(
                        client=client,
                        account_number=from_account_number,
                        is_active=True
                    )
                except BankAccount.DoesNotExist:
                    return {
                        'success': False,
                        'error': f'Compte {from_account_number} non trouvé'
                    }
            else:
                source_account = BankAccount.objects.filter(
                    client=client,
                    is_primary=True,
                    is_active=True
                ).first()
                
                if not source_account:
                    return {
                        'success': False,
                        'error': 'Aucun compte disponible'
                    }
            
            # Vérifier le solde
            if source_account.balance < amount:
                return {
                    'success': False,
                    'error': f'Solde insuffisant. Solde actuel: {source_account.balance:,.2f} {source_account.currency}'
                }
            
            # Transaction atomique
            with transaction.atomic():
                # Débiter le compte
                source_account.balance -= amount
                source_account.save()
                
                # Créer la transaction
                payment_transaction = Transaction.objects.create(
                    account=source_account,
                    transaction_type='payment',
                    amount=amount,
                    description=description or f'Paiement à {merchant}',
                    recipient_name=merchant,
                    status='completed',
                    processed_at=timezone.now(),
                    initiated_by_chatbot=True,
                    reference=f'PAY-{timezone.now().strftime("%Y%m%d")}-{Transaction.objects.count() + 1:06d}'
                )
                
                return {
                    'success': True,
                    'message': 'Paiement effectué avec succès',
                    'transaction_id': payment_transaction.id,
                    'reference': payment_transaction.reference,
                    'amount': float(amount),
                    'currency': source_account.currency,
                    'merchant': merchant,
                    'new_balance': float(source_account.balance),
                    'processed_at': payment_transaction.processed_at.isoformat()
                }
                
        except Exception as e:
            logger.error(f"Erreur create_payment: {str(e)}")
            return {
                'success': False,
                'error': f'Erreur lors du paiement: {str(e)}'
            }
    
    @staticmethod  
    def get_client_accounts(client) -> List[Dict]:
        """Récupère les comptes d'un client (version simplifiée)"""
        try:
            from .models import BankAccount
            
            accounts = BankAccount.objects.filter(
                client=client,
                is_active=True
            )
            
            return [
                {
                    'account_number': account.account_number,
                    'account_type': account.account_type,
                    'balance': float(account.balance),
                    'currency': account.currency,
                    'is_primary': account.is_primary
                }
                for account in accounts
            ]
            
        except Exception as e:
            logger.error(f"Erreur get_client_accounts: {str(e)}")
            return []