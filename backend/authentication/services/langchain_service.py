import json
import re
import asyncio
import time
from typing import Dict, List, Optional
from langchain_ollama import OllamaLLM
from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain
from langchain.memory import ConversationBufferMemory
from langchain.schema import BaseMessage, HumanMessage, AIMessage
import logging
import random
import difflib
from fuzzywuzzy import fuzz
from fuzzywuzzy import process
import unicodedata
from datetime import datetime, timedelta, timezone
import re
from .banking_service import BankingService
import pytz
from dateutil.relativedelta import relativedelta


logger = logging.getLogger(__name__)

class BankingChatbotService:
    def __init__(self, model_name: str = "llama3.2:3b", verbose: bool = False, timeout: int = None):
        # Configuration sans timeout par défaut
        if timeout is None:
            self.llm = OllamaLLM(model=model_name)
            self.timeout = None
        else:
            actual_timeout = max(timeout, 60)
            self.llm = OllamaLLM(model=model_name, timeout=actual_timeout)
            self.timeout = actual_timeout
            
        self.memory = ConversationBufferMemory(
            memory_key="chat_history",
            return_messages=True,
            max_token_limit=3000
        )
        
        # État de conversation pour le contexte
        self.conversation_context = {
            'waiting_for_info': False,
            'current_intent': None,
            'partial_parameters': {},
            'last_request_time': None
        }
        
        # CORRECTION: Patterns complètement refactorisés pour éviter les chevauchements
        self.intent_patterns = {
            # PATTERNS POUR CHECK_BALANCE - Très spécifiques au solde unique
            'check_balance': [
                r'\b(?:quel|combien)\s+(?:est\s+)?(?:le\s+)?solde\b(?!\s+(?:de\s+)?(?:tous|mes|plusieurs|chaque))',
                r'\bmon\s+solde\b(?!\s+(?:de\s+)?(?:tous|mes|plusieurs|chaque))',
                r'\b(?:voir|consulter|afficher)\s+(?:le\s+)?solde\b(?!\s+(?:de\s+)?(?:tous|mes|plusieurs|chaque))',
                r'\bsolde\s+(?:de\s+)?(?:mon\s+)?compte\s+principal\b',
                r'\bsolde\s+actuel\b(?!\s+(?:de\s+)?(?:tous|mes|plusieurs))',
                r'\bsolde\s+disponible\b(?!\s+(?:de\s+)?(?:tous|mes|plusieurs))',
                r'\bcombien\s+(?:j\'?ai|ai-je)\s+(?:dans|sur)\s+(?:le\s+)?compte\b(?!\s*s)',
                r'\bétat\s+(?:de\s+)?(?:mon\s+)?compte\s+principal\b',
                r'\bbalance\s+(?:du\s+)?compte\b(?!\s*s)',
                r'^solde$',  # Juste le mot "solde"
                r'^\w*\s+solde$',  # "mon solde", "le solde", etc.
            ],
            
            # PATTERNS POUR GET_ACCOUNTS - Très clairs sur la pluralité/liste
            'get_accounts': [
                # Indicateurs de pluralité/liste explicites
                r'\b(?:liste|lister|afficher)\s+(?:(?:de\s+)?(?:mes\s+|tous\s+)?comptes?|(?:mes\s+)?comptes?)\b',
                r'\b(?:tous|toutes)\s+(?:mes\s+)?comptes?\b',
                r'\bmes\s+comptes?\b(?!\s+(?:courant|épargne|principal)\s*$)',  # "mes comptes" mais pas "mes comptes courant"
                r'\bautres?\s+comptes?\b',
                r'\bdifférents?\s+comptes?\b',
                r'\bplusieurs\s+comptes?\b',
                r'\bcombien\s+(?:de\s+)?comptes?\s+(?:ai-je|j\'?ai|possède)\b',
                r'\bquels?\s+sont\s+mes\s+comptes?\b',
                r'\bvoir\s+(?:tous\s+)?(?:mes\s+)?comptes?\b',
                r'\bensemble\s+(?:de\s+)?(?:mes\s+)?comptes?\b',
                # Patterns avec "solde" + indicateurs de pluralité
                r'\bsolde\s+(?:de\s+)?(?:tous|mes|chaque|plusieurs)\s+(?:mes\s+)?comptes?\b',
                r'\b(?:tous|mes|chaque|plusieurs)\s+(?:mes\s+)?comptes?\s+.*solde\b',
                r'\bvoir\s+(?:le\s+)?solde\s+(?:de\s+)?(?:tous|mes|chaque|plusieurs)\b',
                # Patterns spécifiques
                r'\bnuméros?\s+(?:de\s+)?comptes?\b',
                r'\bliste\s+(?:des\s+)?rib\b',
                r'\btotalité\s+(?:de\s+)?(?:mes\s+)?comptes?\b',
            ],
            
            # PATTERNS POUR TRANSFER_MONEY - Simplifiés et plus permissifs
            'transfer_money': [
                r'\b(?:virement|virements?)\b',
                r'\b(?:transférer|virer)\s+(?:de\s+l\'?)?argent\b',
                r'\bfaire\s+(?:un\s+)?virement\b',
                r'\benvoyer\s+\d+.*(?:vers|à|pour)\b',
                r'\bvirer\s+\d+',
                r'\btransférer\s+\d+'
            ],
            
            'payment': [
                r'\bpaye?r.*facture\b',
                r'\bfacture.*(?:steg|sonede|ooredoo|orange)\b',
                r'\brègle?r.*facture\b',
                r'\bpaiement.*facture\b',
                # Services spécifiques uniquement
                r'\bpayer\s+(?:steg|sonede|ooredoo|orange)\b',
                r'\bfacture\s+(?:électricité|eau|gaz|téléphone)\b'
            ],
            
            'recurring_payment': [
                r'\bpaiement.*(?:récurrent|automatique|périodique|régulier)\b',
                r'\bvirement.*(?:automatique|périodique|récurrent|mensuel|hebdomadaire)\b',
                r'\bconfigurer.*paiement.*(?:récurrent|automatique|régulier)\b',
                r'\bmettre.*place.*virement.*(?:mensuel|hebdomadaire|automatique)\b',
                r'\bprogrammer.*(?:paiement|virement)\b',
                r'\bpaiement.*(?:mensuel|hebdomadaire).*automatique\b',
                r'\bvirement.*automatique.*(?:chaque|tous)\b',
                r'\bpayer.*chaque.*(mois|semaine|année)\b',
                r'\bvirement.*automatique.*le.*\d+\b',
                r'\bprélèvement.*automatique\b',
                r'\bpaiement.*mensuel.*(steg|sonede|cnam)\b',
                r'\bfacture.*automatique\b',
                r'\bdébiter.*chaque.*mois\b',
                r'\bpaiement.*régulier.*service\b',
                r'\bvirement.*périodique.*vers\b',
                r'\bconfigurer.*paiement.*le.*\d+.*chaque\b'
            ],
            
            'greeting': [
                r'^(?:bonjour|bonsoir|salut|hello|hey|hi|coucou)(?:\s+|$)',
                r'\bbonne.*(?:journée|soirée|matinée)\b',
                r'^(?:ça va|comment allez-vous)'
            ],
            
            'goodbye': [
                r'\b(?:au revoir|bye|à bientôt|merci.*au revoir)\b',
                r'^(?:bye|quit|exit|sortir)(?:\s+|$)',
                r'\bà plus|à tout à l\'heure\b',
                r'\bbonne.*journée.*fin\b',
                r'\bmerci.*(?:bye|au revoir)\b'
            ],
            
            'transaction_history': [
                r'\bhistorique\b(?!\s+(?:de\s+)?(?:virement|paiement|facture))',
                r'\b(?:dernieres?|dernières?)\s+(?:transactions?|operations?)\b',
                r'\b(?:liste|voir|consulter)\s+(?:.*\s+)?(?:transactions?|operations?|historique)\b',
                r'\b(?:transactions?|operations?)\s+(?:recentes?|récentes?)\b',
                r'\b(?:mouvement|activite|activité)s?\s+(?:du\s+)?compte\b',
                r'\bextrait\s+(?:de\s+)?compte\b',
                r'\breleve?\s+(?:de\s+)?compte\b',
                
                # Patterns simples (haute priorité)
                r'^historique$',
                r'^transactions?$',
                r'^operations?$',
                r'^\w{1,4}\s+historique$',

                r'\bmouvement.*compte\b',
                r'\bvoir.*(?:transactions?|operations?)\b',
                r'\bafficher.*(?:transactions?|operations?|historique)\b',
                r'\bconsulter.*(?:transactions?|operations?|historique)\b',
                r'\bliste.*(?:transactions?|operations?)\b'
            ]
        }
        
        # CORRECTION: Poids ajustés pour favoriser la distinction
        self.intent_weights = {
            'greeting': 1.0,
            'goodbye': 1.0,
            'check_balance': 0.85,  # Réduit légèrement
            'get_accounts': 0.95,   # Augmenté pour favoriser la liste
            'transfer_money': 0.90, # Augmenté
            'payment': 0.90,
            'recurring_payment': 0.80,
            'transaction_history': 0.95
        }
        
        # CORRECTION MAJEURE: Patterns d'extraction améliorés avec focus sur RIB tunisiens
        self.extraction_patterns = {
            'amount': [
                # Montants avec devise explicite
                r'(?:montant.*?)?(\d+(?:[\s,]?\d{3})*(?:[,.]?\d{1,2})?)\s*(?:dt|dinar|euro|€|dinars?)',
                # Montants dans contexte
                r'(?P<amount>\d+(?:[.,]\d{1,2})?)\s*(?:dt|dinar|€)'
                r'(?:somme.*?|coût.*?|prix.*?|valeur.*?)(\d+(?:[\s,]?\d{3})*(?:[,.]?\d{1,2})?)',
                # Montants avec "est" (ex: "montant est 100Dt")
                r'(?:montant|somme)\s+(?:est|de)\s*(\d+(?:[\s,]?\d{3})*(?:[,.]?\d{1,2})?)',
                # Montants isolés (avec validation contextuelle)
                r'(\d+(?:[\s,]?\d{3})*(?:[,.]?\d{1,2})?)\s*dt',
                r'(\d+(?:[\s,]?\d{3})*(?:[,.]?\d{1,2})?)€'
            ],
            'account_number': [
                # NOUVEAU: RIB tunisiens (20 chiffres)
                r'(?:rib|compte|numéro).*?(\d{20})',
                r'\b(\d{20})\b(?=\s|$)',
                
                # NOUVEAU: Numéros de comptes 13 chiffres (SEULEMENT - pas de lettres)
                r'(?:compte|numéro|vers).*?(\d{13})',
                r'\b(\d{13})\b(?=\s|$)',
                
                # Extraction après mots-clés spécifiques - SEULEMENT chiffres
                r'(?:avec\s+le\s+)?(?:numéro|compte|rib)\s*(?:de\s+compte\s+)?[:\-]?\s*(\d{13,20})',
                r'le\s+numéro\s+(?:de\s+compte\s+)?(\d{13,20})',
                r'vers.*?(?:compte|numéro)\s*(\d{13,20})',
                r'destinataire.*?(\d{13,20})',
                
                # Patterns génériques - SEULEMENT chiffres de 13 à 20 digits
                r'\b(\d{13,20})\b(?=\s|$)',
                r'(?:rib|compte|numéro).*?(\d{13,20})',
                
                # Fallback pour anciens formats mais plus restrictifs
                r'\b(\d{10,12})\b(?=\s|$)',  
                        ],
            'recipient_name': [
                # AMÉLIORATION: Patterns plus robustes pour noms
                # Noms avec prépositions (ordre d'importance)
                r'\bà\s+([A-Za-zÀ-ÿ\s\-\'\.\_\d]{2,40})(?:\s+(?:avec|le|numéro|compte|rib)|$)',
                r'(?:vers|pour|au nom de|destinataire)\s+([A-Za-zÀ-ÿ\s\-\'\.\_\d]{2,40})(?:\s+(?:avec|le|numéro|compte|rib)|$)',
                r'bénéficiaire\s*[:\-]?\s*([A-Za-zÀ-ÿ\s\-\'\.\_\d]{2,40})(?:\s+(?:avec|le|numéro|compte|rib)|$)',
                
                # Titres de civilité
                r'(?:monsieur|madame|m\.|mme|mr)\s+([A-Za-zÀ-ÿ\s\-\'\.\_\d]{2,40})(?:\s+(?:avec|le|numéro|compte|rib)|$)',
                
                # Contexte de virement
                r'virement.*?(?:vers|à|pour)\s+([A-Za-zÀ-ÿ\s\-\'\.\_\d]{2,40})(?:\s+(?:avec|le|numéro|compte|rib)|$)',
                r'envoyer.*?(?:vers|à|pour)\s+([A-Za-zÀ-ÿ\s\-\'\.\_\d]{2,40})(?:\s+(?:avec|le|numéro|compte|rib)|$)',
                
                # Noms simples après mots-clés
                r'nom\s*[:\-]?\s*([A-Za-zÀ-ÿ\s\-\'\.\_\d]{2,40})(?:\s+(?:avec|le|numéro|compte|rib)|$)',
                
                # NOUVEAU: Support pour noms avec underscores et chiffres (ex: juliette_30)
                r'(?:^|\s)([A-Za-zÀ-ÿ]+(?:[_\-][A-Za-z0-9]+)*)(?:\s+(?:avec|le|numéro|compte|rib)|\s|$)',
            ],
            'merchant': [
                r'facture.*?(?:de|chez)\s*([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                
                # Services tunisiens spécifiques
                r'(?:steg|sonede|tunisie telecom|ooredoo|orange|cnam|cnss|topnet|hexabyte)',
                r'(?:star|comar|gat|ami|maghrebia|carte|salim|biat|astree|lloyd|hannibal)',
                r'(?:amen leasing|attijari|atl|uib leasing|bh leasing|stb leasing|cetelem)',
                
                r'chez\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                r'payer.*?(?:à|chez)\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                r'service\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                r'fournisseur\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                r'entreprise\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                r'société\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                r'organisme\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                r'(?:facture|paiement|payer).*(?:de|pour|chez)\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})(?:\s|$)',
                r'(?:régler|réglé).*(?:à|chez)\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})(?:\s|$)',
            ],
            'bill_number': [
                # Patterns avec contexte explicite
                r'(?:facture|numéro|référence|n°|ref)\s*[:\-]?\s*([A-Z0-9\-]{6,20})',
                r'ref\s*[:\-]?\s*([A-Z0-9\-]{6,20})',
                r'\b(FAC\d+|FACT\d+|REF\d+|BILL\d+)\b',
                
                # NOUVEAU: Patterns spécifiques tunisiens
                # STEG
                r'\b(F\d{10,13})\b',
                r'steg.*?(\d{11,14})\b',
                
                # SONEDE  
                r'\b(S\d{7,9})\b',
                r'sonede.*?(\d{8,10})\b',
                
                # Tunisie Telecom
                r'\b(TT\d{6,10})\b',
                r'(?:tunisie telecom|telecom).*?(\d{8,12})\b',
                
                # Ooredoo/Orange
                r'\b(OO\d{6,10})\b',
                r'\b(OR\d{6,10})\b',
                r'(?:ooredoo|orange).*?([A-Z0-9]{8,12})\b',
                
                # Patterns génériques améliorés
                r'numéro.*facture.*?([A-Z0-9\-]{6,20})',
                r'facture.*numéro.*?([A-Z0-9\-]{6,20})',
                r'code.*facture.*?([A-Z0-9\-]{6,20})',
                r'référence.*paiement.*?([A-Z0-9\-]{6,20})',
                
                # Formats courants tunisiens
                r'\b([A-Z]{1,4}\d{6,12})\b',  # 1-4 lettres + 6-12 chiffres
                r'\b(\d{6,15})\b',            # Numéros purement numériques
                
                # Patterns en contexte de paiement
                r'payer.*(?:facture|ref|numéro).*?([A-Z0-9\-]{6,20})',
                r'avec.*(?:le\s+)?(?:numéro|ref|code)\s*([A-Z0-9\-]{6,20})',
            ],
            'frequency': [
                r'(quotidien|journalier|daily|jour)',
                r'(hebdomadaire|weekly|semaine)',
                r'(mensuel|monthly|mois)',
                r'(trimestriel|quarterly|trimestre)',
                r'(annuel|yearly|année)',
                r'chaque\s*(jour|semaine|mois|trimestre|année)',
                r'tous\s*les\s*(jours|semaines|mois)'
            ],
            'service_name': [
                # Services spécifiques tunisiens
                r'(?:steg|sonede|tunisie telecom|ooredoo|orange|cnam|cnss)',
                r'(?:électricité|eau|téléphone|internet|sécurité sociale|assurance)',
                r'service\s*([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                r'(?:facture|paiement).*?(?:de|chez|pour)\s*([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
            ],
            'exact_date': [
                # Dates exactes
                r'le\s*(\d{1,2})(?:\s*(?:de|du)?(?:\s*chaque)?(?:\s*mois|janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre))?',
                r'chaque\s*(\d{1,2})',
                r'tous?\s*les?\s*(\d{1,2})',
                r'(\d{1,2})\s*(?:de|du)\s*chaque\s*mois',
                r'prélever.*le\s*(\d{1,2})',
                r'débiter.*le\s*(\d{1,2})'
            ],
            'frequency_enhanced': [
                # Fréquences avec plus de détails
                r'(quotidien|journalier|daily|chaque\s*jour)',
                r'(hebdomadaire|weekly|chaque\s*semaine|toutes?\s*les?\s*semaines?)',
                r'(mensuel|monthly|chaque\s*mois|tous?\s*les?\s*mois)',
                r'(trimestriel|quarterly|chaque\s*trimestre|tous?\s*les?\s*trimestres?)',
                r'(semestriel|chaque\s*semestre|tous?\s*les?\s*semestres?)',
                r'(annuel|yearly|chaque\s*année?|tous?\s*les?\s*ans?)'
            ],
            'bill_number':[
                r'(?:facture|numéro|référence|n°|ref)\s*[:\-]?\s*([A-Z0-9\-]{6,20})',
                r'ref\s*[:\-]?\s*([A-Z0-9\-]{6,20})',
                r'\b(FAC\d+|FACT\d+|REF\d+|BILL\d+)\b',
                
                # NOUVEAUX patterns plus permissifs
                r'numéro.*facture.*?([A-Z0-9\-]{6,20})',
                r'facture.*numéro.*?([A-Z0-9\-]{6,20})',
                r'code.*facture.*?([A-Z0-9\-]{6,20})',
                r'référence.*paiement.*?([A-Z0-9\-]{6,20})',
                
                # Pattern pour capturer les formats courants tunisiens
                r'\b([A-Z]{2,4}\d{6,12})\b',  # Ex: STEG123456789
                r'\b(\d{8,15})\b',             # Numéros purement numériques
                
                # Pattern en contexte de paiement
                r'payer.*(?:facture|ref|numéro).*?([A-Z0-9\-]{6,20})',
                r'avec.*(?:le\s+)?(?:numéro|ref|code)\s*([A-Z0-9\-]{6,20})',
            ],
            'merchant':[
                r'facture.*?(?:de|chez)\s*([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                r'(?:steg|sonede|tunisie telecom|ooredoo|orange|cnam|cnss)',
                r'chez\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                r'payer.*?(?:à|chez)\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                
                # NOUVEAUX patterns plus robustes
                r'service\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                r'fournisseur\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                r'entreprise\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                r'société\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                r'organisme\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                
                # Patterns en fin de phrase (plus permissifs)
                r'(?:facture|paiement|payer).*(?:de|pour|chez)\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})(?:\s|$)',
                r'(?:régler|réglé).*(?:à|chez)\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})(?:\s|$)',
            ],
            'count': [
                r'(\d+)\s+(?:dernières?|derniers?)\s+transactions?',
                r'montre[- ]?moi\s+(\d+)\s+(?:transactions?|opérations?)',
                r'affiche\s+(\d+)\s+(?:transactions?|opérations?)'
            ],
            'end_date': [
                r'le\s+(\d{4}-\d{2}-\d{2})',             # format YYYY-MM-DD
                r'le\s+(\d{1,2}/\d{1,2}/\d{4})',         # format DD/MM/YYYY
                r'(\d{4}-\d{2}-\d{2})',                  # juste une date seule
                r'(\d{1,2}/\d{1,2}/\d{4})'               # juste une date seule
            ],
        }

        self.spelling_corrections = {
            # Salutations
            'bpnjour': 'bonjour',
            'bonjout': 'bonjour',
            'bonsoit': 'bonsoir',
            'salue': 'salut',
            'helo': 'hello',
            'coucpu': 'coucou',
            
            # Mots bancaires courants
            'compte': ['compte', 'comptes'],
            'comptes': ['compte', 'comptes'],
            'compt': 'compte',
            'conpte': 'compte',
            'copte': 'compte',
            'comptez': 'comptes',
            'solde': ['solde', 'soldes'],
            'slde': 'solde',
            'soldes': 'solde',
            'virement': ['virement', 'virements'],
            'viremnt': 'virement',
            'virment': 'virement',
            'viremenet': 'virement',
            'paiement': ['paiement', 'paiements'],
            'paiment': 'paiement',
            'payement': 'paiement',
            'paiemnt': 'paiement',
            'facture': ['facture', 'factures'],
            'factur': 'facture',
            'factue': 'facture',
            'fatcure': 'facture',
            'banque': 'banque',
            'banqu': 'banque',
            'bnaque': 'banque',
            'montant': 'montant',
            'mntant': 'montant',
            'motant': 'montant',
            'destinataire': 'destinataire',
            'destinatair': 'destinataire',
            'destiataire': 'destinataire',
            'historique': 'historique',
            'histrique': 'historique',
            'historiqe': 'historique',
            'transaction': ['transaction', 'transactions'],
            'transation': 'transaction',
            'transction': 'transaction',
            
            # Services tunisiens
            'stge': 'steg',
            'soneade': 'sonede',
            'soneda': 'sonede',
            'ooredo': 'ooredoo',
            'oredoo': 'ooredoo',
            'ornage': 'orange',
            'ornge': 'orange',
            
            # Mots de liaison et actions
            'ver': 'vers',
            'pour': 'pour',
            'poru': 'pour',
            'chez': 'chez',
            'chz': 'chez',
            'avec': 'avec',
            'avce': 'avec',
            'faire': 'faire',
            'fair': 'faire',
            'effectuer': 'effectuer',
            'efetuer': 'effectuer',
            'consulter': 'consulter',
            'conulter': 'consulter',
            'voir': 'voir',
            'voire': 'voir',
            
            # Négations et confirmations
            'oui': 'oui',
            'oi': 'oui',
            'ui': 'oui',
            'non': 'non',
            'nn': 'non',
            'merci': 'merci',
            'mrci': 'merci',
            'mercy': 'merci',
        }

        self.banking_keywords = {
            'salutations': ['bonjour', 'bonsoir', 'salut', 'hello', 'coucou'],
            'comptes': ['compte', 'comptes', 'solde', 'soldes'],
            'transactions': ['virement', 'virements', 'paiement', 'paiements', 'transaction', 'transactions'],
            'services': ['facture', 'factures', 'steg', 'sonede', 'ooredoo', 'orange'],
            'actions': ['faire', 'effectuer', 'consulter', 'voir', 'lister', 'afficher'],
            'destinataires': ['vers', 'pour', 'chez', 'destinataire', 'bénéficiaire']
        }

        self.system_prompt = """Tu es l'assistant bancaire virtuel de Amen Banque (tunisien) très efficace et patient. Tu prends le temps nécessaire pour bien comprendre et répondre.

    Tes capacités principales :
    - Consulter le solde des comptes
    - Lister tous les comptes du client
    - Effectuer des virements  
    - Effectuer des paiements de factures (avec numéro de facture, nom complet/raison sociale, RIB)
    - Configurer des paiements récurrents
    - Consulter l'historique des transactions
    - Répondre aux questions bancaires

    IMPORTANT : Tu dois TOUJOURS répondre avec un JSON valide dans ce format exact :
    {{
        "intent": "nom_intention",
        "confidence": 0.85,
        "response": "ta réponse claire et concise",
        "parameters": {{
            "amount": montant_numérique,
            "recipient_account": "numéro_compte",
            "recipient_name": "nom_destinataire",
            "merchant": "nom_marchand",
            "bill_number": "numéro_facture",
            "description": "description_transaction",
            "frequency": "fréquence_paiement"
        }},
        "requires_action": true/false,
        "action_type": "nom_action"
    }}

    Intentions disponibles : check_balance, get_accounts, transfer_money, payment, recurring_payment, transaction_history, greeting, goodbye, general_inquiry

    Actions disponibles : check_balance, get_accounts, transfer_money, payment, recurring_payment, transaction_history

    RÈGLES SPÉCIFIQUES IMPORTANTES :

    1. **Distinction entre consulter solde et lister comptes** :
       - "solde" / "combien ai-je" → check_balance
       - "liste comptes" / "mes comptes" / "autres comptes" → get_accounts

    2. **Pour les PAIEMENTS DE FACTURES** :
       - TOUJOURS demander : numéro de facture, nom complet/raison sociale, montant exact
       - Format: "Pour payer votre facture, j'ai besoin du numéro de facture, du nom complet/raison sociale et du montant exact."

    3. **Pour les VIREMENTS** :
       - TOUJOURS demander si non données : montant, numéro de compte destinataire ET nom du bénéficiaire
       - Si seul le nom est donné → demander aussi le numéro de compte
       - Si seul le compte est donné → demander aussi le nom

    4. **Extraction de paramètres** :
       - Cherche TOUS les paramètres dans le message
       - Pour virements : montant + compte + nom obligatoires
       - Pour paiements : montant + numéro facture + nom marchand obligatoires

    5. **Gestion du contexte conversationnel** :
       - Si informations partielles → garde le contexte et demande le manquant
       - Confirme toujours avant d'exécuter une transaction

    Historique : {chat_history}
    Message : {user_input}

    Analyse le message, détermine l'intention correcte et extrait TOUS les paramètres possibles. Réponds UNIQUEMENT avec le JSON :"""

        self.prompt = PromptTemplate(
            input_variables=["chat_history", "user_input"],
            template=self.system_prompt
        )
        
        self.chain = LLMChain(
            llm=self.llm,
            prompt=self.prompt,
            memory=self.memory,
            verbose=verbose
        )
    
    def is_likely_account_number(self, account_number: str) -> bool:
        if not account_number:
            return False
        
        clean = account_number.strip()

        if not clean.isdigit():
            return False
        
        return len(clean) in [13, 20]

    
    def get_quick_response(self, user_input: str) -> Optional[Dict]:
        user_lower = user_input.lower().strip()
        
        # CORRECTION: Patterns de salutations plus stricts
        if re.match(r'^(bonjour|bonsoir|salut|hello|hey|hi|coucou)(?:\s.*|$)', user_lower):
            responses = [
                "Bonjour ! Je suis votre assistant bancaire Amen Bank. Comment puis-je vous aider ?",
                "Bonjour ! En quoi puis-je vous assister avec vos services bancaires ?",
                "Salut ! Comment puis-je vous aider aujourd'hui ?"
            ]
            return {
                "intent": "greeting",
                "confidence": 0.95,
                "response": random.choice(responses),
                "parameters": {},
                "requires_action": False,
                "action_type": None,
                "quick_response": True
            }
        
        # Patterns d'au revoir
        if re.search(r'\b(au revoir|bye|à bientôt|à plus)\b', user_lower):
            responses = [
                "Au revoir ! N'hésitez pas à revenir. Bonne journée !",
                "À bientôt ! Passez une excellente journée !",
                "Au revoir ! Je reste disponible pour vos futurs besoins bancaires."
            ]
            return {
                "intent": "goodbye",
                "confidence": 0.95,
                "response": random.choice(responses),
                "parameters": {},
                "requires_action": False,
                "action_type": None,
                "quick_response": True
            }
        
        # Réponses de courtoisie
        if re.match(r'^(merci|thank you|merci beaucoup)(?:\s.*|$)', user_lower):
            return {
                "intent": "acknowledgment",
                "confidence": 0.90,
                "response": "De rien ! Y a-t-il autre chose que je puisse faire pour vous ?",
                "parameters": {},
                "requires_action": False,
                "action_type": None,
                "quick_response": True
            }
        
        return None
    def process_message(self, user_input: str, user_context: Dict = None) -> Dict:
        
        try:
            corrected_input, has_corrections, original_input = self.preprocess_user_input(user_input)
            quick_response = self.get_quick_response(corrected_input)
            if quick_response:
                logger.info(f"Réponse ultra-rapide utilisée pour: {corrected_input}")
                if has_corrections:
                    quick_response['correction_applied'] = True
                    quick_response['original_input'] = original_input
                    quick_response['corrected_input'] = corrected_input
                self.add_context_to_memory(corrected_input, quick_response.get("response", ""))
                return quick_response

            quick_response = self.quick_intent_detection(corrected_input)
            if quick_response and quick_response['confidence'] > 0.50:
                logger.info(f"Réponse rapide utilisée pour: {corrected_input} (Intent: {quick_response['intent']}, Confiance: {quick_response['confidence']:.2f})")

                if quick_response.get('intent') == 'transfer_money':
                    new_params = self.extract_parameters(corrected_input, 'transfer_money')
                    for k, v in new_params.items():
                        if v and not quick_response['parameters'].get(k):
                            quick_response['parameters'][k] = v
                    validation = self.validate_transaction_parameters('transfer_money', quick_response['parameters'])

                    if  validation['is_valid']:
                        quick_response['requires_action'] = True
                        quick_response['action_type'] = 'transfer_money'
                        quick_response['response'] = f"Parfait ! Je procède au virement de {quick_response['parameters']['amount']} DT vers {quick_response['parameters']['recipient_name']} (compte {quick_response['parameters']['recipient_account']})..."
                        quick_response['confidence'] = 0.95
                        
                        logger.info(f"Virement complet détecté - Paramètres: {quick_response['parameters']}")
                    
                    else:
                        self.conversation_context['waiting_for_info'] = True
                        self.conversation_context['current_intent'] = 'transfer_money'
                        self.conversation_context['partial_parameters'] = quick_response['parameters']
                        self.conversation_context['last_request_time'] = time.time()
                        
                        missing_params = validation['missing_parameters']
                        missing_str = self._format_missing_parameters(missing_params, 'transfer_money')

                        if quick_response['parameters']:
                            already_provided = []
                            if quick_response['parameters'].get('amount'):
                                already_provided.append(f"montant: {quick_response['parameters']['amount']} DT")
                            if quick_response['parameters'].get('recipient_name'):
                                already_provided.append(f"destinataire: {quick_response['parameters']['recipient_name']}")
                            if quick_response['parameters'].get('recipient_account'):
                                already_provided.append(f"compte: {quick_response['parameters']['recipient_account']}")
                            
                            if already_provided:
                                provided_str = ", ".join(already_provided)
                                quick_response['response'] = f"J'ai bien noté : {provided_str}. Pour finaliser le virement, j'ai encore besoin de : {missing_str}."
                            else:
                                quick_response['response'] = f"Pour effectuer le virement, j'ai besoin de : {missing_str}."
                        else:
                            quick_response['response'] = f"Pour effectuer le virement, j'ai besoin de : {missing_str}."
                        
                        quick_response['requires_action'] = False
                        quick_response['confidence'] = 0.85

                elif quick_response.get('action_type') == 'recurring_payment':
                    specialized_response = self.handle_recurring_payment_intent(corrected_input)
                    if specialized_response:
                        if has_corrections:
                            specialized_response['correction_applied'] = True
                            specialized_response['original_input'] = original_input
                            specialized_response['corrected_input'] = corrected_input
                        self.add_context_to_memory(corrected_input, specialized_response.get("response", ""))
                        return specialized_response
                
                elif quick_response.get('intent') == 'payment':
                    
                    quick_response['action_type'] = 'payment'

                    validation = self.validate_payment_parameters(quick_response['parameters'])
                    if not validation['is_valid']:
                        self.conversation_context['waiting_for_info'] = True
                        self.conversation_context['current_intent'] = 'payment'
                        self.conversation_context['partial_parameters'] = quick_response['parameters']
                        self.conversation_context['last_request_time'] = time.time()
                        
                        missing_str = self._format_missing_parameters(validation['missing_parameters'], 'payment')
                        quick_response['response'] = f"Pour traiter le paiement de votre facture, j'ai besoin de : {missing_str}. Pouvez-vous me les fournir ?"
                        quick_response['requires_action'] = False
                        quick_response['confidence'] = 0.85

                elif quick_response.get('intent') == 'transaction_history':
                    user_id = user_context.get('user_id') if user_context else None
                    if not user_id:
                        quick_response['response'] = "Erreur : Utilisateur non identifié."
                        quick_response['requires_action'] = False
                        return quick_response
                    
                    # Récupérer l'objet Client via BankingService
                    client = BankingService.get_client_by_id(user_id)

                    params = quick_response['parameters']
                    tz = pytz.timezone('Africa/Tunis')
                    current_date = datetime.now(tz)
                    history = BankingService.get_transaction_history(
                        client=client,
                        account_number=params.get('account_number'),
                        account_id=params.get('account_id'),
                        limit=None,  # Toutes les transactions du mois
                        transaction_type=params.get('transaction_type'),
                        start_date=params.get('start_date'),
                        end_date=params.get('end_date'),
                        use_cache=True
                    )
                    
                    if history['success']:
                        if history['transactions']:
                            account_info = history['filters_applied']['account_number'] or 'tous'
                            month_year = params.get('start_date').strftime('%B %Y') if params.get('start_date') else current_date.strftime('%B %Y')
                            response_text = f"Voici vos transactions du mois de {month_year} pour le compte {account_info} :\n"
                            for t in history['transactions']:
                                response_text += f"- {t['type']} de {t['formatted_amount']} à {t.get('recipient', 'inconnu') or t.get('merchant', 'inconnu')}"
                                if t.get('reference') or t.get('bill_number'):
                                    response_text += f" (ref: {t.get('reference') or t.get('bill_number')})"
                                response_text += f" le {t['formatted_date']}\n"
                        else:
                            response_text = history['message']
                    else:
                        response_text = history['message']
                    quick_response['response'] = response_text
                    quick_response['requires_action'] = False
                    quick_response['confidence'] = 0.95

                if has_corrections:
                    quick_response['correction_applied'] = True
                    quick_response['original_input'] = original_input
                    quick_response['corrected_input'] = corrected_input
                
                self.add_context_to_memory(corrected_input, quick_response.get("response", ""))
                return quick_response
            start_time = time.time()
            
            if user_context:
                enhanced_input = f"Contexte: {json.dumps(user_context, ensure_ascii=False)}\nMessage: {corrected_input}"
            else:
                enhanced_input = corrected_input
            
            logger.info(f"Traitement LLM pour: {corrected_input}")
            response = self.chain.run(user_input=enhanced_input)
            processing_time = time.time() - start_time
            logger.info(f"Temps de traitement: {processing_time:.2f}s")
            parsed_response = self._parse_response(response)

            if has_corrections:
                parsed_response['correction_applied'] = True
                parsed_response['original_input'] = original_input
                parsed_response['corrected_input'] = corrected_input

            if parsed_response.get('requires_action'):
                action_type = parsed_response.get('action_type')
                if action_type == 'transfer_money':
                    validation = self.validate_transaction_parameters(action_type, parsed_response.get('parameters', {}))

                    if validation['is_valid']:
                        logger.info(f"Virement complet via LLM - Paramètres: {parsed_response.get('parameters', {})}")
                        parsed_response['requires_action'] = True  # S'assurer que l'action sera exécutée
                        params = parsed_response.get('parameters', {})
                        if all(params.get(key) for key in ['amount', 'recipient_name', 'recipient_account']):
                            parsed_response['response'] = f"Virement de {params['amount']} DT vers {params['recipient_name']} (compte {params['recipient_account']}). Traitement en cours..."
                    
                    else:
                        self.conversation_context['waiting_for_info'] = True
                        self.conversation_context['current_intent'] = action_type
                        self.conversation_context['partial_parameters'] = parsed_response.get('parameters', {})
                        self.conversation_context['last_request_time'] = time.time()
                        
                        missing_params = validation['missing_parameters']
                        missing_str = self._format_missing_parameters(missing_params, action_type)
                        parsed_response['response'] = f"Pour effectuer le virement, j'ai besoin de : {missing_str}. Pouvez-vous me les donner ?"
                        parsed_response['requires_action'] = False
                elif action_type == 'recurring_payment':
                    validation = self.validate_recurring_payment_parameters(parsed_response.get('parameters', {}))
                    if not validation['is_valid']:
                        self.conversation_context['waiting_for_info'] = True
                        self.conversation_context['current_intent'] = action_type
                        self.conversation_context['partial_parameters'] = parsed_response.get('parameters', {})
                        self.conversation_context['last_request_time'] = time.time()
                        
                        missing_str = self.format_missing_recurring_parameters(validation['missing_parameters'])
                        warnings_text = ""
                        if validation['warnings']:
                            warnings_text = f" Attention: {', '.join(validation['warnings'])}"
                        parsed_response['response'] = f"Pour configurer votre paiement récurrent, j'ai besoin de: {missing_str}.{warnings_text}"
                        parsed_response['requires_action'] = False
                elif action_type == 'payment':
                    validation = self.validate_transaction_parameters(action_type, parsed_response.get('parameters', {}))
                    
                    if not validation['is_valid']:
                        self.conversation_context['waiting_for_info'] = True
                        self.conversation_context['current_intent'] = action_type
                        self.conversation_context['partial_parameters'] = parsed_response.get('parameters', {})
                        self.conversation_context['last_request_time'] = time.time()
                        missing_params = validation['missing_parameters']
                        missing_str = self._format_missing_parameters(missing_params, action_type)
                        parsed_response['response'] = f"Pour traiter le paiement de votre facture, j'ai besoin de : {missing_str}. Pouvez-vous me les fournir ?"
                        parsed_response['requires_action'] = False

            if parsed_response.get("intent") != "error":
                self.add_context_to_memory(corrected_input, parsed_response.get("response", ""))
            
            return parsed_response
            
        except Exception as e:
            logger.error(f"Erreur process_message: {str(e)}")
            return {
                "intent": "error",
                "confidence": 0.0,
                "response": "Je rencontre un problème technique. Pouvez-vous reformuler votre demande ?",
                "parameters": {},
                "requires_action": False,
                "action_type": None,
                "error": str(e)
            }

    def is_likely_person_name(self, text: str) -> bool:
        """AMÉLIORATION: Vérification robuste pour noms avec support underscore et chiffres"""
        text = text.strip()
        if self.is_likely_account_number(text):
            return False
        
        if len(text) < 2 or len(text) > 50:
            return False
 
        if not re.search(r'[A-Za-zÀ-ÿ]', text):
            return False
        
        if not re.match(r'^[A-Za-zÀ-ÿ\s\-\'\.\_\d]+', text):
            return False
        
        banking_keywords = ['compte', 'solde', 'virement', 'facture', 'paiement', 'banque']
        if text.lower() in banking_keywords:
            return False
        
        if re.match(r'^[A-Za-zÀ-ÿ]+(?:[_\-][A-Za-z0-9]+)*', text):
            return True
        
        return True
    def preprocess_user_input(self, user_input: str) -> tuple:
        """Préprocesse l'entrée utilisateur et retourne le texte original et corrigé"""
        original_input = user_input.strip()

        cleaned_input = re.sub(r'\s+', ' ', original_input)
        corrected_input = self.correct_sentence(cleaned_input)
        has_corrections = original_input.lower() != corrected_input.lower()
        
        return corrected_input, has_corrections, original_input
    
    def correct_sentence(self, sentence: str) -> str:
        """Corrige une phrase entière en préservant la ponctuation"""
        words = re.findall(r'\b\w+\b|[.,!?;]', sentence)
        corrected_words = []
        sentence_lower = sentence.lower()
        context_keywords = []
        if any(word in sentence_lower for word in ['solde', 'compte', 'consulter']):
            context_keywords = self.banking_keywords['comptes']
        elif any(word in sentence_lower for word in ['virement', 'paiement', 'facture']):
            context_keywords = self.banking_keywords['transactions']
        elif any(word in sentence_lower for word in ['bonjour', 'salut', 'hello']):
            context_keywords = self.banking_keywords['salutations']
        
        for word in words:
            if re.match(r'\w+', word): 
                corrected_word = self.correct_word(word, context_keywords)
                corrected_words.append(corrected_word)
            else:
                corrected_words.append(word)  # Garder la ponctuation
        
        return ' '.join(corrected_words)
    def extract_amount_improved(self, text: str) -> float:
        t = text

        # 1) Retirer les numéros de compte (10 à 20 chiffres)
        t = re.sub(r'\b\d{13,20}\b', ' ', t)

        patterns = [
            r'(\d+(?:[.]\d{1,2})?)\s*(?:dt|dinar|dinars?)\b',
            r'(\d+(?:[.,]\d{1,2})?)\s*€',
            r'montant.*?(\d+(?:[.,]\d{1,2})?)',
            # fallback plus strict : bornes non-chiffrées
            r'(?<!\d)(\d{1,6}(?:[.,]\d{1,2})?)(?!\d)'
        ]

        for i, pattern in enumerate(patterns):
            m = re.search(pattern, t, re.IGNORECASE)
            if m:
                try:
                    amount = float(m.group(1).replace(',', '.'))
                    if 0 < amount <= 1_000_000:
                        if i == len(patterns)-1:
                            # 2) pour le fallback, rejeter si mot interdit à proximité
                            span_s, span_e = m.span(1)
                            window = text[max(0, span_s-12):min(len(text), span_e+12)].lower()
                            if re.search(r'\b(compte|rib|num(?:éro)?)\b', window):
                                continue
                        return amount
                except ValueError:
                    continue
        return None

    
    
    def correct_word(self, word: str, context_keywords: list = None) -> str:
        """Corrige un mot en utilisant plusieurs stratégies"""
        word_lower = word.lower().strip()
        
        # Stratégie 1: Correction directe depuis le dictionnaire
        if word_lower in self.spelling_corrections:
            correction = self.spelling_corrections[word_lower]
            if isinstance(correction, list):
                return correction[0]  
            return correction
        best_match = None
        best_score = 0
        min_score = 70  
        
        # Chercher dans tous les mots-clés bancaires
        all_banking_words = []
        for category in self.banking_keywords.values():
            all_banking_words.extend(category)
        
        # Si on a un contexte spécifique, prioriser ces mots
        if context_keywords:
            search_words = context_keywords + all_banking_words
        else:
            search_words = all_banking_words
        
        for banking_word in search_words:
            score = self.calculate_similarity(word, banking_word)
            if score > best_score and score >= min_score:
                best_score = score
                best_match = banking_word
        
        # Stratégie 3: Recherche dans le dictionnaire de corrections par similarité
        if not best_match:
            for correct_word in self.spelling_corrections.keys():
                score = self.calculate_similarity(word, correct_word)
                if score > best_score and score >= min_score:
                    best_score = score
                    correction = self.spelling_corrections[correct_word]
                    best_match = correction if isinstance(correction, str) else correction[0]
        
        return best_match if best_match else word
    
    def validate_payment_parameters(self, params: Dict) -> Dict:
        missing = []
        warnings = []
        
        # Paramètres obligatoires de base
        required_base = ['amount', 'merchant','bill_number']
        
        for param in required_base:
            if not params.get(param):
                missing.append(param)
        
        # Logique spéciale pour bill_number
        merchant = params.get('merchant', '').lower()
        is_known_service = params.get('is_known_service', False)
        
        # Services tunisiens connus : bill_number optionnel
        known_services = [
            'steg', 'sonede', 'ooredoo', 'orange', 'cnam', 'cnss', 'telecom',
            'électricité', 'electricite', 'eau', 'gaz', 'tunisie telecom', 
            'tunisie télécom', 'télécom', 'assurance maladie', 'sécurité sociale',
            'securite sociale', 'topnet', 'hexabyte', 'globalnet', 'planet',
            'star', 'comar', 'gat', 'ami assurances', 'maghrebia', 'carte assurances',
            'salim', 'biat', 'astree', 'lloyd', 'hannibal', 'zitouna', 'amen leasing',
            'attijari leasing', 'atl', 'uib leasing', 'bh leasing', 'stb leasing',
            'btl', 'wifack', 'tlf', 'cil', 'cetelem'
        ]
        service_detected = any(service in merchant for service in known_services)
        
        if not service_detected and not is_known_service:
            # Pour les services non connus, bill_number obligatoire
            if not params.get('bill_number'):
                missing.append('bill_number')
        
        # Validations supplémentaires
        if params.get('amount'):
            try:
                amount = float(params['amount'])
                if amount <= 0:
                    warnings.append('Le montant doit être positif')
                elif amount > 50000:  # Limite raisonnable pour factures
                    warnings.append('Montant inhabituellement élevé pour une facture')
            except (ValueError, TypeError):
                warnings.append('Format de montant invalide')
        
        return {
            'is_valid': len(missing) == 0,
            'missing_parameters': missing,
            'warnings': warnings,
            'has_warnings': len(warnings) > 0
        }

    def normalize_text(self, text: str) -> str:
        """Normalise le texte en supprimant les accents et caractères spéciaux"""
        # Supprimer les accents
        text = unicodedata.normalize('NFD', text)
        text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')
        # Convertir en minuscules
        return text.lower().strip()

    def calculate_similarity(self, word1: str, word2: str) -> float:
        """Calcule la similarité entre deux mots avec plusieurs méthodes"""
        # Normaliser les mots
        word1_norm = self.normalize_text(word1)
        word2_norm = self.normalize_text(word2)
        
        # Méthode 1: Ratio simple
        ratio1 = fuzz.ratio(word1_norm, word2_norm)
        
        # Méthode 2: Ratio partiel (pour les mots contenus)
        ratio2 = fuzz.partial_ratio(word1_norm, word2_norm)
        
        # Méthode 3: Distance de Levenshtein avec difflib
        ratio3 = difflib.SequenceMatcher(None, word1_norm, word2_norm).ratio() * 100
        
        # Retourner le score le plus élevé
        return max(ratio1, ratio2, ratio3)
    def add_context_to_memory(self, user_message: str, bot_response: str):
        """Ajoute la conversation à la mémoire"""
        try:
            self.memory.chat_memory.add_message(HumanMessage(content=user_message))
            self.memory.chat_memory.add_message(AIMessage(content=bot_response))
            
            if len(self.memory.chat_memory.messages) > 30:
                self.memory.chat_memory.messages = self.memory.chat_memory.messages[-30:]
                
        except Exception as e:
            logger.warning(f"Erreur ajout mémoire: {str(e)}")
    
    def clear_memory(self):
        """Efface la mémoire de conversation"""
        try:
            self.memory.clear()
            # Réinitialiser aussi le contexte de conversation
            self.conversation_context = {
                'waiting_for_info': False,
                'current_intent': None,
                'partial_parameters': {},
                'last_request_time': None
            }
            logger.info("Mémoire et contexte effacés")
        except Exception as e:
            logger.error(f"Erreur effacement mémoire: {str(e)}")
    
    def reset_context(self):
        """Réinitialise le contexte de conversation"""
        self.conversation_context = {
            'waiting_for_info': False,
            'current_intent': None,
            'partial_parameters': {},
            'last_request_time': None
        }
        logger.info("Contexte conversationnel réinitialisé")
    
    def get_context_info(self) -> Dict:
        """Récupère les informations du contexte actuel"""
        return {
            'waiting_for_info': self.conversation_context['waiting_for_info'],
            'current_intent': self.conversation_context['current_intent'],
            'partial_parameters': self.conversation_context['partial_parameters'],
            'has_context': self.conversation_context['waiting_for_info'],
            'context_age_seconds': (
                time.time() - self.conversation_context['last_request_time'] 
                if self.conversation_context['last_request_time'] else 0
            )
        }
    
    def handle_transaction_history_intent(self, user_input: str) -> Dict:
        """
        Gestion de l'historique des transactions - VERSION CORRIGÉE
        """
        # Extraction des paramètres
        parameters = self.extract_transaction_history_parameters(user_input)
        
        # Validation des paramètres
        validation = self.validate_transaction_history_parameters(parameters)
        if not validation['is_valid']:
            return {
                "intent": "transaction_history",
                "confidence": 0.95,
                "response": f"Paramètres invalides: {', '.join(validation.get('warnings', []))}",
                "parameters": parameters,
                "requires_action": False,
                "action_type": None
            }
        
        parameters = validation['parameters']
        
        # CORRECTION: Vérifier si le service bancaire est disponible
        if not self.banking_service:
            return {
                "intent": "transaction_history",
                "confidence": 0.95,
                "response": "Service d'historique temporairement indisponible.",
                "parameters": parameters,
                "requires_action": False,
                "action_type": None
            }
        
        try:
            # CORRECTION: Appel correct sans client
            history_data = self.banking_service.get_transaction_history(
                account_number=parameters.get('account_number'),
                limit=parameters.get('limit', 10),
                transaction_type=parameters.get('transaction_type'),
                start_date=parameters.get('start_date'),
                end_date=parameters.get('end_date'),
                use_cache=True
            )
            
            if history_data.get("success"):
                response_text = self.banking_service.format_transaction_history_response(
                    history_data, parameters
                )
            else:
                response_text = history_data.get("message", "Erreur lors de la récupération de l'historique.")
            
        except Exception as e:
            logger.error(f"Erreur récupération historique: {str(e)}")
            response_text = "Impossible de récupérer l'historique pour le moment."
        
        return {
            "intent": "transaction_history",
            "confidence": 0.95,
            "response": response_text,
            "parameters": parameters,
            "requires_action": False,
            "action_type": None
        }


    def get_conversation_history(self) -> List[BaseMessage]:
        """Retourne l'historique de conversation"""
        try:
            return self.memory.chat_memory.messages
        except Exception as e:
            logger.error(f"Erreur récupération historique: {str(e)}")
            return []
    def quick_intent_detection(self, user_input: str) -> Optional[Dict]:
        """CORRECTION: Détection rapide avec le nouveau système amélioré"""
        return self.detect_best_intent(user_input)
    def detect_best_intent(self, user_input: str) -> Optional[Dict]:
        """CORRECTION: Détection avec logique de priorité améliorée"""
        
        # Vérifier d'abord le contexte conversationnel
        context_response = self.handle_context_continuation(user_input)
        if context_response:
            return context_response
        
        # Calculer les scores pour chaque intention
        intent_scores = {}
        for intent in self.intent_patterns.keys():
            score = self.calculate_intent_score(user_input, intent)
            if score > 0:
                intent_scores[intent] = score
        
        if not intent_scores:
            return None
        
        # NOUVEAU: Appliquer des règles de priorité
        user_lower = user_input.lower().strip()
        
        # Règle spéciale: Si "liste" ou "mes comptes" → forcer get_accounts
        if re.search(r'\b(?:liste|lister)\b', user_lower) and re.search(r'\bcomptes?\b', user_lower):
            if 'get_accounts' in intent_scores:
                intent_scores['get_accounts'] = max(0.9, intent_scores['get_accounts'])
        
        # Règle spéciale: Si "virement" ou "transférer" → forcer transfer_money
        if re.search(r'\b(?:virement|virements?|transférer|transfer)\b', user_lower):
            if 'transfer_money' in intent_scores:
                intent_scores['transfer_money'] = max(0.9, intent_scores['transfer_money'])
        if re.search(r'\b(?:payer|paiement|facture|factures|règlement|reglement|régler|regler)\b', user_lower):
            if 'payment' in intent_scores:
                intent_scores['payment'] = max(0.9, intent_scores['payment'])
         # Règle spéciale: Si "historique" ou "transactions" → forcer transaction_history
        if re.search(r'\b(?:historique|history|transactions?|mouvements?|opérations?|operations?)\b', user_lower):
            if 'transaction_history' in intent_scores:
                intent_scores['transaction_history'] = max(0.9, intent_scores['transaction_history'])
        
        # Règle spéciale additionnelle pour l'historique avec des mots-clés temporels
        if re.search(r'\b(?:dernières?|derniers?|récentes?|recentes?|précédentes?|precedentes?)\b', user_lower) and \
        re.search(r'\b(?:transactions?|opérations?|operations?|mouvements?)\b', user_lower):
            if 'transaction_history' in intent_scores:
                intent_scores['transaction_history'] = max(0.9, intent_scores['transaction_history'])
        
        # NOUVEAU: Gestion du mois par défaut et extraction d'un mois spécifique avec Africa/Tunis
        params = self.extract_parameters(user_input, 'transaction_history')
        tz = pytz.timezone('Africa/Tunis')
        current_date = datetime.now(tz)
        start_date = tz.localize(datetime(current_date.year, current_date.month, 1))
        end_date = current_date  # Jusqu'à aujourd'hui
        
        # Vérifier si un mois spécifique est demandé
        month_match = re.search(r'\bde\s+mois\s+de\s+(\w+)(?:\s+(\d{4}))?', user_lower)
        if month_match and 'transaction_history' in intent_scores:
            month_name = month_match.group(1)
            year = month_match.group(2) or current_date.year  # Année par défaut = année actuelle si non spécifiée
            month_map = {
                'janvier': 1, 'février': 2, 'mars': 3, 'avril': 4, 'mai': 5, 'juin': 6,
                'juillet': 7, 'août': 8, 'septembre': 9, 'octobre': 10, 'novembre': 11, 'décembre': 12
            }
            month_num = month_map.get(month_name)
            if month_num:
                start_date = tz.localize(datetime(int(year), month_num, 1))
                end_date = tz.localize(datetime(int(year), month_num, 1) + relativedelta(months=1) - relativedelta(seconds=1))
                intent_scores['transaction_history'] = max(0.95, intent_scores['transaction_history'])  # Boost confiance
                params.update({'start_date': start_date, 'end_date': end_date})

        # Prendre l'intention avec le meilleur score
        best_intent = max(intent_scores, key=intent_scores.get)
        confidence = intent_scores[best_intent]
        
        # Seuil minimal de confiance ajusté
        if confidence < 0.4:
            return None
        
        logger.info(f"Scores calculés: {intent_scores}")
        logger.info(f"Meilleure intention: {best_intent} (confiance: {confidence:.2f})")
        
        # Extraire les paramètres
        parameters = self.extract_parameters(user_input, best_intent)
        
        response_dict = self._create_quick_response(best_intent, user_input)
        response_dict['parameters'].update(parameters)
        response_dict['confidence'] = min(0.95, confidence)
        
        return response_dict
    def handle_context_continuation(self, user_input: str) -> Optional[Dict]:
        """Gère la continuation du contexte conversationnel avec validation renforcée"""
        if not self.conversation_context['waiting_for_info']:
            return None
        
        current_intent = self.conversation_context['current_intent']
        partial_params = self.conversation_context['partial_parameters'].copy()
        user_input_clean = user_input.strip()
        
        if current_intent == 'transfer_money':
            # NOUVEAU: Extraction intelligente pour format "nom : juliette num de compte : 1984573201694 montant : 600dt"
            
            # Extraction du nom si manquant
            if not partial_params.get('recipient_name'):
                name = self.extract_recipient_name(user_input)
                if name:
                    partial_params['recipient_name'] = name
                    print(f"DEBUG: Nom extrait: {name}")
            
            # Extraction du compte si manquant
            if not partial_params.get('recipient_account'):
                account = self.extract_account_number(user_input)
                if account:
                    partial_params['recipient_account'] = account
                    print(f"DEBUG: Compte extrait: {account}")
            
            # Extraction du montant si manquant
            if not partial_params.get('amount'):
                amount = self.extract_amount_improved(user_input)
                if amount:
                    partial_params['amount'] = amount
                    print(f"DEBUG: Montant extrait: {amount}")
            
            # NOUVELLE LOGIQUE: Extraction globale pour capturer plusieurs paramètres à la fois
            new_params = self.extract_parameters(user_input, current_intent)
            for key, value in new_params.items():
                if value and not partial_params.get(key):
                    partial_params[key] = value
                    print(f"DEBUG: Paramètre ajouté {key}: {value}")
            
            # Validation finale
            validation = self.validate_transaction_parameters(current_intent, partial_params)
            
            if validation['is_valid']:
                # Tous les paramètres sont présents
                self.conversation_context['waiting_for_info'] = False
                self.conversation_context['current_intent'] = None
                self.conversation_context['partial_parameters'] = {}
                
                return {
                    "intent": current_intent,
                    "confidence": 0.95,
                    "response": f"Parfait ! Je procède au virement de {partial_params['amount']} DT vers {partial_params['recipient_name']} (compte {partial_params['recipient_account']})...",
                    "parameters": partial_params,
                    "requires_action": True,
                    "action_type": current_intent
                }
            else:
                # Il manque encore des paramètres
                missing_params = validation['missing_parameters']
                self.conversation_context['partial_parameters'] = partial_params
                
                # Créer message avec ce qui a été ajouté
                added_info = []
                for key, value in new_params.items():
                    if value and key in ['amount', 'recipient_name', 'recipient_account']:
                        param_names = {
                            'amount': f'montant: {value} DT',
                            'recipient_name': f'destinataire: {value}',
                            'recipient_account': f'compte: {value}'
                        }
                        added_info.append(param_names[key])
                
                missing_str = self._format_missing_parameters(missing_params, current_intent)
                
                if added_info:
                    added_str = ", ".join(added_info)
                    response_text = f"Merci ! J'ai ajouté : {added_str}. Il me manque encore : {missing_str}."
                else:
                    response_text = f"Il me manque encore : {missing_str}."
                
                return {
                    "intent": current_intent,
                    "confidence": 0.90,
                    "response": response_text,
                    "parameters": partial_params,
                    "requires_action": False,
                    "action_type": None
                }
        elif current_intent == 'payment':
            if not partial_params.get('merchant'):
                merchant = self.extract_merchant_name(user_input)  # Utilise extract_merchant_name
                if merchant:
                    partial_params['merchant'] = merchant
                    logger.debug(f"Nom du marchand extrait: {merchant}")
            
            # Extraction du montant si manquant
            if not partial_params.get('amount'):
                amount = self.extract_amount_improved(user_input)  # Utilise extract_amount_improved
                if amount:
                    partial_params['amount'] = amount
                    logger.debug(f"Montant extrait: {amount}")
            
            # Extraction du numéro de facture si manquant
            if not partial_params.get('bill_number'):
                bill_number = self.extract_bill_number(user_input)  # Utilise extract_bill_number
                if bill_number:
                    partial_params['bill_number'] = bill_number
                    logger.debug(f"Numéro de facture extrait: {bill_number}")
            
            # Extraction globale pour capturer plusieurs paramètres à la fois
            new_params = self.extract_parameters(user_input, current_intent)  # Utilise extract_parameters
            for key, value in new_params.items():
                if value and not partial_params.get(key):
                    partial_params[key] = value
                    logger.debug(f"Paramètre ajouté {key}: {value}")
            
            # Validation finale avec validate_payment_parameters
            validation = self.validate_payment_parameters(partial_params)  # Utilise validate_payment_parameters
            
            if validation['is_valid']:
                # Tous les paramètres obligatoires sont présents
                self.conversation_context['waiting_for_info'] = False
                self.conversation_context['current_intent'] = None
                self.conversation_context['partial_parameters'] = {}
                
                # Générer une réponse adaptée, incluant bill_number si présent
                response_text = f"Parfait ! Je procède au paiement de {self.format_amount(partial_params['amount'])} à {partial_params['merchant']}"
                if partial_params.get('bill_number'):
                    response_text += f" pour la facture {partial_params['bill_number']}"
                response_text += "..."
                
                return {
                    "intent": current_intent,
                    "confidence": 0.95,
                    "response": response_text,
                    "parameters": partial_params,
                    "requires_action": True,
                    "action_type": current_intent
                }
            else:
                # Il manque encore des paramètres
                missing_params = validation['missing_parameters']
                self.conversation_context['partial_parameters'] = partial_params
                self.conversation_context['last_request_time'] = time.time()
                
                # Créer message avec ce qui a été ajouté
                added_info = []
                for key, value in new_params.items():
                    if value and key in ['amount', 'merchant', 'bill_number']:
                        param_names = {
                            'amount': f"montant: {self.format_amount(value)}",
                            'merchant': f"marchand: {value}",
                            'bill_number': f"numéro de facture: {value}"
                        }
                        added_info.append(param_names[key])
                
                missing_str = self._format_missing_parameters(missing_params, current_intent)  # Utilise _format_missing_parameters
                
                if added_info:
                    added_str = ", ".join(added_info)
                    response_text = f"Merci ! J'ai ajouté : {added_str}. Il me manque encore : {missing_str}."
                else:
                    response_text = f"Pour traiter le paiement de votre facture, j'ai besoin de : {missing_str}."
                
                # Ajouter des avertissements si présents
                if validation.get('warnings'):
                    response_text += f" ⚠️ Attention : {', '.join(validation['warnings'])}"
                
                return {
                    "intent": current_intent,
                    "confidence": 0.90,
                    "response": response_text,
                    "parameters": partial_params,
                    "requires_action": False,
                    "action_type": None
                }
        elif current_intent == 'transaction_history':
            # NOUVEAU: Gestion de l'historique des transactions
            
            # Extraction du numéro de compte si manquant
            if not partial_params.get('account_number'):
                account = self.extract_account_number(user_input)
                if account:
                    partial_params['account_number'] = account
                    logger.debug(f"Numéro de compte extrait: {account}")
            
            # Extraction d'autres paramètres si nécessaire
            if not partial_params.get('limit') or partial_params.get('limit') == 10:
                # Essayer d'extraire une limite spécifique
                number_patterns = [
                    r'(\d+)\s+(?:dernières?|dernieres?)\s+(?:transactions?|operations?)',
                    r'(?:les?\s+)?(\d+)\s+(?:dernières?|dernieres?)\s+(?:transactions?|operations?)',
                    r'(?:voir|afficher|montre)\s+(?:les?\s+)?(\d+)',
                    r'(\d+)\s+(?:transactions?|operations?)',
                ]
                
                for pattern in number_patterns:
                    match = re.search(pattern, user_input, re.IGNORECASE)
                    if match:
                        try:
                            count = int(match.group(1))
                            if 1 <= count <= 100:
                                partial_params['limit'] = count
                                partial_params['user_requested_count'] = count
                                logger.debug(f"Limite extraite: {count}")
                                break
                        except (ValueError, IndexError):
                            continue
            
            # Extraction globale pour capturer d'autres paramètres
            new_params = self.extract_transaction_history_parameters(user_input)
            for key, value in new_params.items():
                if value and not partial_params.get(key):
                    partial_params[key] = value
                    logger.debug(f"Paramètre historique ajouté {key}: {value}")
            
            # Validation finale - mais on procède même sans numéro de compte
            validation = self.validate_transaction_history_parameters(partial_params)
            
            # Toujours procéder à l'action, même sans numéro de compte
            self.conversation_context['waiting_for_info'] = False
            self.conversation_context['current_intent'] = None
            self.conversation_context['partial_parameters'] = {}
            
            # Générer une réponse personnalisée
            if partial_params.get('user_requested_count'):
                count = partial_params['user_requested_count']
                if count == 1:
                    response_text = "Parfait ! Je récupère votre dernière transaction..."
                else:
                    response_text = f"Parfait ! Je récupère vos {count} dernières transactions..."
            elif partial_params.get('specific_date'):
                response_text = f"Parfait ! Je récupère vos transactions du {partial_params['start_date'].strftime('%d/%m/%Y')}..."
            elif partial_params.get('period_name'):
                response_text = f"Parfait ! Je récupère vos transactions de {partial_params['period_name']}..."
            else:
                response_text = "Parfait ! Je récupère l'historique récent de vos transactions..."
            
            # Ajouter des avertissements si présents (mais non critiques)
            if validation.get('warnings'):
                warnings = [w for w in validation['warnings'] if 'requis' not in w.lower()]
                if warnings:
                    response_text += f" ⚠️ Note : {', '.join(warnings)}"
            
            return {
                "intent": current_intent,
                "confidence": 0.95,
                "response": response_text,
                "parameters": partial_params,
                "requires_action": True,
                "action_type": current_intent
            }
    
        # Logique pour autres intents (payment, recurring_payment)...
        return None

    def _get_action_french_name(self, action: str) -> str:
        """Retourne le nom français de l'action"""
        names = {
            'transfer_money': 'virement',
            'payment': 'paiement',
            'recurring_payment': 'paiement récurrent',
            'check_balance': 'consultation de solde',
            'get_accounts': 'liste des comptes',
            'transaction_history': 'historique des transactions'
        }
        return names.get(action, action)
    def calculate_intent_score(self, user_input: str, intent: str) -> float:
        """CORRECTION: Version complètement refactorisée du calcul de score"""
        user_lower = user_input.lower().strip()
        patterns = self.intent_patterns.get(intent, [])
        base_weight = self.intent_weights.get(intent, 0.5)
        
        score = 0.0
        matches = 0
        
        # NOUVEAU: Logique spécialisée pour les intentions sensibles
        if intent == 'check_balance':
            # Vérifications négatives STRICTES pour check_balance
            list_indicators = [
                r'\b(?:liste|lister|afficher)\b',
                r'\b(?:tous|toutes|plusieurs|mes)\s+comptes?\b',
                r'\bautres?\s+comptes?\b', 
                r'\bcombien\s+(?:de\s+)?comptes?\b',
                r'\bsolde.*(?:tous|mes|chaque|plusieurs)\b'
            ]
            
            # Si on trouve des indicateurs de liste, score = 0
            for negative_pattern in list_indicators:
                if re.search(negative_pattern, user_lower):
                    return 0.0
            
            # Bonus pour les patterns très spécifiques au solde unique
            specific_balance_patterns = [
                r'^solde',
                r'^\w+\s+solde',
                r'\bmon\s+solde\b(?!\s+(?:de\s+)?(?:tous|mes))',
                r'\bcombien\s+(?:j\'?ai|ai-je)\b'
            ]
            
            specific_matches = sum(1 for pattern in specific_balance_patterns 
                                 if re.search(pattern, user_lower))
            if specific_matches > 0:
                base_weight += 0.3
        
        elif intent == 'get_accounts':
            # Bonus pour les indicateurs de pluralité/liste
            list_indicators = [
                r'\b(?:liste|lister|afficher)\b',
                r'\b(?:tous|toutes|plusieurs)\b',
                r'\bmes\s+comptes?\b',
                r'\bautres?\s+comptes?\b',
                r'\bcombien\s+(?:de\s+)?comptes?\b',
                r'\bcomptes\b'  # Forme plurielle
            ]
            
            list_matches = sum(1 for pattern in list_indicators 
                             if re.search(pattern, user_lower))
            if list_matches > 0:
                base_weight += 0.2 * list_matches
            
            # Bonus supplémentaire si solde + pluralité
            if re.search(r'\bsolde\b', user_lower) and re.search(r'\b(?:tous|mes|plusieurs|chaque)\b', user_lower):
                base_weight += 0.3
        
        elif intent == 'transaction_history':
            high_priority_keywords = [
                r'\bhistorique\b',
                r'\btransactions?\b',
                r'\bopérations?\b',
                r'\bdernières?\b',
                r'\bdernieres?\b'
            ]
            
            keyword_matches = sum(1 for pattern in high_priority_keywords 
                                if re.search(pattern, user_lower))
            
            if keyword_matches > 0:
                base_weight += 0.2 * keyword_matches
            
            typical_phrases = [
                r'\bvoir.*(?:historique|transactions?|opérations?)\b',
                r'\bafficher.*(?:historique|transactions?|opérations?)\b',
                r'\bmontrer.*(?:historique|transactions?|opérations?)\b',
                r'\bconsulter.*(?:historique|transactions?|opérations?)\b',
                r'\bliste.*(?:transactions?|opérations?)\b'
            ]

            phrase_matches = sum(1 for pattern in typical_phrases 
                           if re.search(pattern, user_lower))
            if phrase_matches > 0:
                base_weight += 0.2 * phrase_matches

            for pattern in patterns:
                if re.search(pattern, user_lower):
                    matches += 1
                    score += 0.4  # Score plus élevé par pattern
            
            if matches == 0 and keyword_matches == 0:
                return 0.0
            
            # Score final amélioré
            pattern_score = (score / len(patterns)) if len(patterns) > 0 else 0
            match_bonus = min(0.4, matches * 0.15)
            final_score = (pattern_score + match_bonus) * base_weight
            
            return min(1.0, final_score)

            # Bonus spécial pour "donne moi l'historique" type phrases
            if re.search(r'\b(?:donne|montre|affiche).*(?:moi|nous).*historique\b', user_lower):
                base_weight += 0.4
            
            # Calcul normal des patterns
            for pattern in patterns:
                if re.search(pattern, user_lower):
                    matches += 1
                    score += 0.3
            
            if matches == 0:
                return 0.0
            
            # Score final
            pattern_score = score / len(patterns) if patterns else 0
            final_score = (pattern_score + min(0.3, matches * 0.1)) * base_weight
            
            return min(1.0, final_score)

        elif intent == 'transfer_money':
            # Bonus pour les mots-clés de virement
            transfer_keywords = [
                r'\b(?:virement|virements?)\b',
                r'\b(?:transfer|transférer|virer|envoyer)\b',
                r'\bfaire\s+(?:un\s+)?virement\b'
            ]
            
            transfer_matches = sum(1 for pattern in transfer_keywords 
                                 if re.search(pattern, user_lower))
            if transfer_matches > 0:
                base_weight += 0.2 * transfer_matches
            
            # Bonus si montant + destinataire détectés
            if re.search(r'\d+', user_lower) and re.search(r'\b(?:vers|à|pour)\b', user_lower):
                base_weight += 0.2
        
        # Calcul normal des patterns
        for pattern in patterns:
            if re.search(pattern, user_lower):
                matches += 1
                # Score basé sur la longueur et complexité du pattern
                pattern_score = min(0.3, len(pattern) / 50)
                score += pattern_score
        
        if matches == 0:
            return 0.0
        
        # Score final avec pondération améliorée
        pattern_score = (score / len(patterns)) if len(patterns) > 0 else 0
        match_bonus = min(0.3, matches * 0.1)
        final_score = (pattern_score + match_bonus) * base_weight
        
        return min(1.0, final_score)
    def _parse_response(self, response: str) -> Dict:
        """Parse la réponse JSON du modèle avec stratégies multiples améliorées"""
        try:
            response = response.strip()
            logger.debug(f"Parsing: {response}")
            
            # Stratégie 1: JSON direct
            if response.startswith('{') and response.endswith('}'):
                try:
                    parsed = json.loads(response)
                    if self._validate_response_structure(parsed):
                        return parsed
                except json.JSONDecodeError:
                    pass
            
            # Stratégie 2: Extraction JSON avec regex
            json_patterns = [
                r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}',
                r'\{(?:[^{}]|{[^{}]*})*\}',
                r'```json\s*(\{.*?\})\s*```',
                r'```\s*(\{.*?\})\s*```'
            ]
            
            for pattern in json_patterns:
                matches = re.finditer(pattern, response, re.DOTALL)
                for match in matches:
                    try:
                        json_str = match.group(1) if match.lastindex else match.group()
                        parsed = json.loads(json_str)
                        if self._validate_response_structure(parsed):
                            return parsed
                    except (json.JSONDecodeError, AttributeError):
                        continue
            
            # Stratégie 3: Construction basée sur mots-clés avec extraction de paramètres
            return self._extract_intent_from_text(response)
            
        except Exception as e:
            logger.error(f"Erreur parsing: {str(e)}")
            return self._create_fallback_response(response)
    
    def extract_bill_number(self, text: str) -> str:
        """Extraction améliorée du numéro de facture avec validation renforcée"""
    
        priority_patterns = [
            # Patterns avec contexte explicite (priorité haute)
            r'(?:facture|numéro|référence|ref|n°)\s*[:\-]?\s*([A-Z0-9\-]{6,20})',
            r'numéro.*facture.*?([A-Z0-9\-]{6,20})',
            r'code.*facture.*?([A-Z0-9\-]{6,20})',
            r'référence.*paiement.*?([A-Z0-9\-]{6,20})',
            
            # NOUVEAU: Patterns spécifiques tunisiens
            # STEG - 11 à 14 chiffres OU format F + chiffres
            r'\b(F\d{10,13})\b',
            r'\b(\d{11,14})\b(?=\s|$)',
            
            # SONEDE - 8 à 10 chiffres OU format S + chiffres
            r'\b(S\d{7,9})\b',
            r'\b(\d{8,10})\b(?=\s|$)',
            
            # Tunisie Telecom - 8 à 12 chiffres OU format TT + chiffres
            r'\b(TT\d{6,10})\b',
            r'\b(\d{8,12})\b(?=\s|$)',
            
            # Ooredoo/Orange - format alphanumérique ou numérique
            r'\b(OO\d{6,10})\b',
            r'\b(OR\d{6,10})\b',
            r'\b([A-Z]{2}\d{6,10})\b',
            
            # Patterns génériques avec préfixes connus
            r'\b(FAC\d+|FACT\d+|REF\d+|BILL\d+|STEG\d+|SON\d+)\b',
            r'\b([A-Z]{1,4}\d{6,12})\b',
            
            # Patterns en contexte
            r'avec.*(?:le\s+)?(?:numéro|ref)\s*([A-Z0-9\-]{6,15})',
            r'payer.*(?:ref|numéro).*?([A-Z0-9\-]{6,15})',
            
            # Fallback pour formats numériques purs
            r'\b(\d{6,15})\b(?=\s|$)',
        ]
        
        for pattern in priority_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                bill_num = match.group(1).strip().upper()
                
                # Validation du numéro de facture
                if self.is_valid_bill_number(bill_num):
                    return bill_num
        
        return None
    
    def is_valid_bill_number(self, bill_num: str) -> bool:
        """Valide un numéro de facture selon les formats tunisiens"""
        if not bill_num or len(bill_num) < 6 or len(bill_num) > 20:
            return False
        
        bill_upper = bill_num.upper()
        
        # Formats spécifiques tunisiens
        valid_patterns = [
            # STEG
            r'^F\d{10,13}$',      # Format F + 10-13 chiffres
            r'^\d{11,14}$',       # 11-14 chiffres purs
            
            # SONEDE
            r'^S\d{7,9}$',        # Format S + 7-9 chiffres
            r'^\d{8,10}$',        # 8-10 chiffres purs
            
            # Tunisie Telecom
            r'^TT\d{6,10}$',      # Format TT + 6-10 chiffres
            r'^\d{8,12}$',        # 8-12 chiffres purs
            
            # Ooredoo/Orange
            r'^(OO|OR)\d{6,10}$', # Format OO/OR + 6-10 chiffres
            r'^[A-Z]{2}\d{6,10}$', # 2 lettres + 6-10 chiffres
            
            # Assurances et autres services
            r'^[A-Z]{1,4}\d{6,12}$', # 1-4 lettres + 6-12 chiffres
            
            # Formats génériques
            r'^(FAC|FACT|REF|BILL)\d{6,12}$',
            r'^\d{6,15}$',        # Numéros purs 6-15 chiffres
        ]
        
        return any(re.match(pattern, bill_upper) for pattern in valid_patterns)

        
    def _validate_response_structure(self, parsed: Dict) -> bool:
        """Valide la structure de la réponse avec validation étendue"""
        required_keys = {"intent", "confidence", "response", "parameters", "requires_action", "action_type"}
        
        if not all(key in parsed for key in required_keys):
            return False
        
        if not isinstance(parsed.get("confidence"), (int, float)):
            return False
        
        if not isinstance(parsed.get("requires_action"), bool):
            return False
        
        if parsed.get("confidence", 0) < 0 or parsed.get("confidence", 0) > 1:
            return False
        
        valid_intents = [
            'check_balance', 'get_accounts', 'transfer_money', 'payment', 'recurring_payment',
            'transaction_history', 'greeting', 'goodbye', 'general_inquiry', 'error'
        ]
        
        if parsed.get("intent") not in valid_intents:
            return False
        
        return True
    def extract_parameters(self, user_input: str, intent: str) -> Dict:
        """CORRECTION: Extraction des paramètres avec meilleure précision"""
        user_lower = user_input.lower().strip()
        parameters = {}
        
        try:
        # EXTRACTION MONTANT AMÉLIORÉE
            amount = self.extract_amount_improved(user_input)
            if amount:
                parameters['amount'] = amount
            
            if intent == 'transfer_money':
                # Extraction compte destinataire
                account = self.extract_account_number(user_input)
                if account:
                    parameters['recipient_account'] = account
                
                # Extraction nom destinataire
                name = self.extract_recipient_name(user_input)
                if name:
                    parameters['recipient_name'] = name
                    
            elif intent == 'payment':
                # Extraction numéro facture
                bill_num = self.extract_bill_number(user_input)
                if bill_num:
                    parameters['bill_number'] = bill_num
                
                # Extraction merchant/service
                merchant = self.extract_merchant_name(user_input)
                if merchant:
                    parameters['merchant'] = merchant

            elif intent == 'transaction_history':
                # Utiliser la nouvelle méthode spécialisée
                history_params = self.extract_transaction_history_parameters(user_input)
                parameters.update(history_params)
        
        except Exception as e:
            logger.error(f"Erreur extraction paramètres: {str(e)}")
        
        return parameters
    
    def clean_extracted_account(account_number: str) -> str:
        """
        Nettoie et valide un numéro de compte extrait
        """
        if not account_number:
            return ""
        
        # Nettoyer: garder seulement les chiffres
        clean = ''.join(c for c in str(account_number) if c.isdigit())
        
        # Valider la longueur
        if len(clean) == 20:
            return clean  # RIB valide
        elif len(clean) == 13:
            return clean  # Numéro de compte valide
        elif len(clean) >= 10 and len(clean) <= 12:
            # Fallback: pad à 13 chiffres si nécessaire
            return clean.zfill(13)
        else:
            return ""

    def extract_account_number(self,text: str) -> list:
        """
        Extrait les numéros de comptes du texte avec validation stricte
        """
        
        patterns = [
            # RIB tunisiens (20 chiffres SEULEMENT)
            r'(?:rib|compte|numéro).*?(\d{20})',
            r'\b(\d{20})\b(?=\s|$)',
            
            # Numéros de comptes 13 chiffres SEULEMENT  
            r'(?:compte|numéro|vers).*?(\d{13})',
            r'\b(\d{13})\b(?=\s|$)',
            
            # Avec mots-clés
            r'(?:avec\s+le\s+)?(?:numéro|compte|rib)\s*(?:de\s+compte\s+)?[:\-]?\s*(\d{13,20})',
            r'le\s+numéro\s+(?:de\s+compte\s+)?(\d{13,20})',
            r'vers.*?(?:compte|numéro)\s*(\d{13,20})',
            r'destinataire.*?(\d{13,20})',
            
            # NOUVEAU: Pattern pour format "num de compte : 1984573201694"
            r'num(?:éro)?\s+de\s+compte\s*[:\-]?\s*(\d{10,20})',
            r'n°\s*compte\s*[:\-]?\s*(\d{10,20})',
            r'compte\s*[:\-]\s*(\d{10,20})',
            
            # Fallback pour autres formats
            r'\b(\d{10,12})\b(?=\s|$)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text.lower(), re.IGNORECASE)
            if match:
                account = match.group(1).strip()
                if self.is_likely_account_number(account):
                    return account
        return None
    
    def extract_recipient_name(self, text: str) -> Optional[str]:
        # étape 1 : extraction brute avec regex
        candidate = None
        match = re.search(r'(?:destinataire|vers|pour)\s+(.+)', text, re.IGNORECASE)
        if match:
            candidate = match.group(1).strip()

        # étape 2 : choisir ce qu'on envoie au LLM
        target_text = candidate if candidate else text.strip()

        prompt = f"""
        Tu es un extracteur de texte.
        Tâche : identifier un nom de bénéficiaire dans une phrase.
        Règles :
        - Si un nom de personne existe, réponds uniquement par ce nom.
        - Si aucun nom n'existe, réponds exactement : NONE.
        - Ne réponds jamais avec une phrase ou une explication.

        Exemples :
        Texte: "je veux effectuer un virement"
        Réponse: NONE

        Texte: "destinataire Juliette avec numéro de compte 1984573201694"
        Réponse: Juliette

        Texte: "vers Raouf Sahli numéro de compte 0012345678901"
        Réponse: Raouf Sahli

        Texte: "à mon frère"
        Réponse: NONE

        Texte à analyser: "{text}"
        Réponse:
        """

        llm_answer = self.llm.invoke(prompt).strip()

        # garder uniquement la première ligne au cas où
        cleaned = llm_answer.splitlines()[0].strip()

        if cleaned.upper() == "NONE":
            return None

        # petit nettoyage final (supprimer caractères parasites)
        cleaned = re.sub(r'[^A-Za-zÀ-ÿ\s\'\-]', '', cleaned)

        return cleaned.title()



    """
    def extract_recipient_name(self, text: str) -> Optional[str]:
        patterns = [
            r'(?:vers|pour|au nom de|destinataire)\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\-\'\.]{1,38})'
            r'(?=\s+(?:avec|le|la|num(?:éro)?|compte|rib)\b|$)',
            r'(?:à|chez)\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\-\'\.]{1,38})'
            r'(?=\s+(?:avec|le|la|num(?:éro)?|compte|rib)\b|$)'
        ]
        stopwords = {'je','tu', 'il', 'elle', 'nous', 'vous', 'eux', 'lui', 'numero','numéro','compte','rib','solde','dt','dinar','dinars', 'montant','avec', 'et'}

        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                name = m.group(1).strip()
                # Post-traitement : enlever mots parasites en fin
                tokens = [t for t in re.split(r'\s+', name) if t]
                while tokens and tokens[-1].lower() in stopwords:
                    tokens.pop()
                name = ' '.join(tokens)
                if 1 < len(name) <= 50 and self.is_likely_person_name(name):
                    return name.title()
        return None
    """

    def extract_merchant_name(self, text: str) -> str:
        """Extraction améliorée du nom marchand avec services tunisiens complets"""
    
        # Services tunisiens complets avec variations
        tunisian_services = {
            # Utilités publiques
            'steg': 'STEG (Électricité et Gaz)',
            'sonede': 'SONEDE (Eau)',
            'électricité': 'STEG',
            'electricite': 'STEG', 
            'eau': 'SONEDE',
            'gaz': 'STEG',
            
            # Télécoms
            'tunisie telecom': 'Tunisie Telecom',
            'tunisie télécom': 'Tunisie Telecom',
            'ooredoo': 'Ooredoo Tunisie',
            'orange': 'Orange Tunisie',
            'telecom': 'Tunisie Telecom',
            'télécom': 'Tunisie Telecom',
            
            # Social & Santé
            'cnam': 'CNAM (Assurance Maladie)',
            'cnss': 'CNSS (Sécurité Sociale)',
            'assurance maladie': 'CNAM',
            'sécurité sociale': 'CNSS',
            'securite sociale': 'CNSS',
            
            # Internet
            'topnet': 'TopNet',
            'hexabyte': 'Hexabyte',
            'globalnet': 'GlobalNet',
            'planet': 'Planet Tunisie',
            
            # Assurances
            'star': 'STAR Assurances',
            'comar': 'COMAR Assurances',
            'gat': 'GAT Assurances',
            'ami assurances': 'AMI Assurances',
            'maghrebia': 'Maghrebia Assurances',
            'carte assurances': 'Carte Assurances',
            'salim': 'Salim Assurances',
            'biat': 'Assurances BIAT',
            'astree': 'Astree Assurances',
            'lloyd': 'Lloyd Tunisien',
            'hannibal': 'Hannibal Assurances',
            'zitouna': 'Zitouna Takaful',
            
            # Leasing
            'amen leasing': 'Amen Leasing',
            'attijari leasing': 'Attijari Leasing',
            'atl': 'Arab Tunisian Lease',
            'uib leasing': 'UIB Leasing',
            'bh leasing': 'BH Leasing',
            'stb leasing': 'STB Leasing',
            'btl': 'BTL Leasing',
            'wifack': 'El Wifack Leasing',
            'tlf': 'Tunisie Leasing & Factoring',
            'cil': 'CIL Leasing',
            'cetelem': 'CETELEM Tunisie',
        }
        
        text_lower = text.lower()
        
        # Vérifier d'abord les services connus
        for service_key, service_name in tunisian_services.items():
            if service_key in text_lower:
                return service_name
        
        # Patterns d'extraction génériques
        patterns = [
            r'facture.*?(?:de|chez)\s*([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
            r'chez\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
            r'payer.*?(?:à|chez)\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
            r'service\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
            r'fournisseur\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
            r'entreprise\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
            r'société\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
            r'organisme\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
            r'(?:facture|paiement|payer).*(?:de|pour|chez)\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})(?:\s|$)',
            r'(?:régler|réglé).*(?:à|chez)\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})(?:\s|$)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                merchant = match.group(1).strip()
                if len(merchant) > 1:
                    # Vérifier si c'est un service connu
                    merchant_lower = merchant.lower()
                    for service_key, service_name in tunisian_services.items():
                        if service_key in merchant_lower:
                            return service_name
                    return merchant.title()
        
        return None

    
    def _extract_intent_from_text(self, text: str) -> Dict:
        """Extrait l'intention à partir du texte de réponse avec logique améliorée"""
        text_lower = text.lower()
        
        # Utiliser le système de scoring pour déterminer l'intention
        intent_scores = {}
        for intent in self.intent_patterns.keys():
            score = self.calculate_intent_score(text, intent)
            if score > 0:
                intent_scores[intent] = score
        
        if intent_scores:
            best_intent = max(intent_scores, key=intent_scores.get)
            confidence = min(0.9, 0.6 + (intent_scores[best_intent] * 0.3))
            
            # Extraire les paramètres du texte original
            parameters = self.extract_parameters(text, best_intent)
            
            # Déterminer si une action est requise
            requires_action = best_intent in ['check_balance', 'get_accounts', 'transaction_history', 'transfer_money', 'payment', 'recurring_payment']
            action_type = best_intent if requires_action else None
            
            return {
                "intent": best_intent,
                "confidence": confidence,
                "response": self._generate_response_for_intent(best_intent, text),
                "parameters": parameters,
                "requires_action": requires_action,
                "action_type": action_type
            }
        
        return self._create_fallback_response(text)
    
    def _generate_response_for_intent(self, intent: str, original_text: str) -> str:
        """Génère une réponse appropriée selon l'intention avec messages améliorés"""
        responses = {
            'check_balance': "Je consulte le solde de votre compte principal...",
            'get_accounts': "Je récupère la liste complète de tous vos comptes...",
            'transfer_money': "Je prépare votre virement...",
            'payment': "Je traite votre paiement de facture...",
            'recurring_payment': "Je configure votre paiement récurrent...",
            'transaction_history': "Je récupère votre historique de transactions...",
            'greeting': "Bonjour ! Comment puis-je vous aider avec vos services bancaires Amen Bank ?",
            'goodbye': "Au revoir ! Bonne journée !",
            'general_inquiry': "Je suis votre assistant bancaire Amen Bank. En quoi puis-je vous aider ?"
        }
        
        return responses.get(intent, "Comment puis-je vous aider avec vos services bancaires ?")

    def _create_fallback_response(self, original_text: str) -> Dict:
        """Crée une réponse de secours avec message personnalisé"""
        return {
            "intent": "general_inquiry",
            "confidence": 0.5,
            "response": "Je suis votre assistant bancaire Amen Bank. Comment puis-je vous aider avec vos comptes, virements, paiements ou autres services ?",
            "parameters": {},
            "requires_action": False,
            "action_type": None
        }
    def test_intent_detection(self, test_phrases: List[str]) -> Dict:
        """Teste la détection d'intentions sur une liste de phrases"""
        results = {}
        
        for phrase in test_phrases:
            try:
                corrected_input, has_corrections, original_input = self.preprocess_user_input(phrase)
                
                # Tester la détection rapide
                quick_response = self.quick_intent_detection(corrected_input)
                
                if quick_response:
                    results[phrase] = {
                        'original': phrase,
                        'corrected': corrected_input if has_corrections else None,
                        'intent': quick_response['intent'],
                        'confidence': quick_response['confidence'],
                        'parameters': quick_response['parameters'],
                        'method': 'quick_detection'
                    }
                else:
                    results[phrase] = {
                        'original': phrase,
                        'corrected': corrected_input if has_corrections else None,
                        'intent': 'unknown',
                        'confidence': 0.0,
                        'parameters': {},
                        'method': 'failed'
                    }
                    
            except Exception as e:
                results[phrase] = {
                    'original': phrase,
                    'error': str(e)
                }
        
        return results
    def get_intent_scores_debug(self, user_input: str) -> Dict:
        """Version debug qui retourne tous les scores calculés"""
        corrected_input, has_corrections, original_input = self.preprocess_user_input(user_input)
        
        intent_scores = {}
        for intent in self.intent_patterns.keys():
            score = self.calculate_intent_score(corrected_input, intent)
            intent_scores[intent] = score
        
        # Trier par score décroissant
        sorted_scores = dict(sorted(intent_scores.items(), key=lambda x: x[1], reverse=True))
        
        return {
            'input': user_input,
            'corrected_input': corrected_input if has_corrections else None,
            'scores': sorted_scores,
            'best_intent': max(sorted_scores, key=sorted_scores.get) if sorted_scores else None,
            'best_score': max(sorted_scores.values()) if sorted_scores else 0
        }    

    def _format_missing_parameters(self, missing_params: List[str], intent: str) -> str:
        """Formate les paramètres manquants selon le contexte"""
        param_names = {
            'amount': 'le montant exact',
            'recipient_account': 'le numéro de compte destinataire',
            'recipient_name': 'le nom complet du bénéficiaire',
            'merchant': 'le nom complet ou raison sociale',
            'bill_number': 'le numéro de facture',
            'frequency': 'la fréquence (mensuel, hebdomadaire, etc.)'
        }
        
        if intent == 'payment':
            # Messages spéciaux pour les paiements de factures
            custom_messages = {
                'bill_number': 'le numéro de facture',
                'merchant': 'le nom complet ou raison sociale du fournisseur',
                'amount': 'le montant exact à payer'
            }
            param_names.update(custom_messages)
        
        formatted = []
        for param in missing_params:
            formatted.append(param_names.get(param, param))
        
        return ' et '.join(formatted) if len(formatted) <= 2 else ', '.join(formatted[:-1]) + ' et ' + formatted[-1]
    
    def _create_quick_response(self, intent: str, user_input: str) -> Dict:
        """CORRECTION: Messages de réponse plus spécifiques"""
        responses = {
            'greeting': {
                "intent": "greeting",
                "confidence": 0.95,
                "response": "Bonjour ! Je suis votre assistant bancaire Amen Bank. Comment puis-je vous aider aujourd'hui ?",
                "parameters": {},
                "requires_action": False,
                "action_type": None
            },
            'goodbye': {
                "intent": "goodbye", 
                "confidence": 0.95,
                "response": "Au revoir ! N'hésitez pas à revenir pour vos services bancaires. Bonne journée !",
                "parameters": {},
                "requires_action": False,
                "action_type": None
            },
            'check_balance': {
                "intent": "check_balance",
                "confidence": 0.90,
                "response": "Je consulte le solde de votre compte principal...",
                "parameters": {},
                "requires_action": True,
                "action_type": "check_balance"
            },
            'get_accounts': {
                "intent": "get_accounts",
                "confidence": 0.90,
                "response": "Je récupère la liste complète de tous vos comptes...",
                "parameters": {},
                "requires_action": True,
                "action_type": "get_accounts"
            },
            'transaction_history': {
                "intent": "transaction_history",
                "confidence": 0.90,
                "response": "Je récupère votre historique de transactions...",
                "parameters": {},
                "requires_action": True,
                "action_type": "transaction_history"
            },
            'transfer_money': {
                "intent": "transfer_money",
                "confidence": 0.85,
                "response": "Je prépare votre virement. Veuillez me fournir le montant, le compte destinataire et le nom du bénéficiaire.",
                "parameters": {},
                "requires_action": False,
                "action_type": None
            },
            'payment': {
                "intent": "payment",
                "confidence": 0.85,
                "response": "Je traite votre paiement de facture. Veuillez me fournir le numéro de facture, le montant et le nom du fournisseur.",
                "parameters": {},
                "requires_action": False,
                "action_type": None
            },
            'recurring_payment': {
                "intent": "recurring_payment",
                "confidence": 0.85,
                "response": "Je configure votre paiement récurrent...",
                "parameters": {},
                "requires_action": True,
                "action_type": "recurring_payment"
            }
        }
        
        return responses.get(intent, {
            "intent": "general_inquiry",
            "confidence": 0.70,
            "response": "Je suis votre assistant bancaire Amen Bank. Comment puis-je vous aider ?",
            "parameters": {},
            "requires_action": False,
            "action_type": None
        })
    
    def validate_transaction_history_parameters(self, parameters: Dict) -> Dict:
        """Validation des paramètres d'historique - VERSION CORRIGÉE"""
        warnings = []
        
        # Valider la limite
        limit = parameters.get('limit', 10)
        if limit < 1:
            parameters['limit'] = 10
            warnings.append("Limite minimale fixée à 10 transactions")
        elif limit > 100:
            parameters['limit'] = 100
            warnings.append("Limite réduite à 100 transactions maximum")
        
        # Validation des dates
        start_date = parameters.get('start_date')
        end_date = parameters.get('end_date')
        
        if start_date and end_date and start_date > end_date:
            warnings.append("Date de début postérieure à la date de fin - dates ignorées")
            parameters.pop('start_date', None)
            parameters.pop('end_date', None)
        
        # Validation du type de transaction
        valid_types = ['transfer', 'payment', 'debit', 'credit', 'withdrawal', 'deposit']
        if parameters.get('transaction_type') and parameters['transaction_type'] not in valid_types:
            warnings.append(f"Type de transaction non reconnu: {parameters['transaction_type']}")
            parameters.pop('transaction_type', None)
        
        # NOUVEAU: Validation du numéro de compte
        if not parameters.get('account_number'):
            warnings.append("Numéro de compte requis pour consulter l'historique")
        
        return {
            'is_valid': len([w for w in warnings if 'requis' in w]) == 0,  # Valide si pas d'erreur critique
            'parameters': parameters,
            'warnings': warnings
        }


    def format_amount(self, amount, currency='DT') -> str:
        """Formate un montant avec la devise"""
        if not amount:
            return "Montant non spécifié"
        
        try:
            # Convertir en float si c'est une chaîne
            if isinstance(amount, str):
                amount = float(amount.replace(',', '.').replace(' ', ''))
            
            # Formater avec espaces comme séparateurs de milliers
            formatted = f"{amount:,.2f}".replace(',', ' ')
            
            # Ajouter la devise
            return f"{formatted} {currency}"
        
        except (ValueError, TypeError):
            return str(amount) if amount else "Montant non spécifié"

    def validate_transaction_parameters(self, intent: str, parameters: Dict) -> Dict:
        """CORRECTION: Validation améliorée des paramètres"""
        missing = []
        
        if intent == 'transfer_money':
            required = ['amount', 'recipient_account', 'recipient_name']
            missing = [param for param in required if not parameters.get(param)]
            
        elif intent == 'payment':
            # NOUVEAU: Validation stricte pour les paiements de factures
            required = ['amount', 'bill_number', 'merchant']
            missing = [param for param in required if not parameters.get(param)]
        
        elif intent == 'transaction_history':
            validation = self.validate_transaction_history_parameters(parameters)
            return validation

        elif intent == 'recurring_payment':
            required = ['amount', 'recipient_account', 'recipient_name', 'frequency']
            missing = [param for param in required if not parameters.get(param)]
        
        return {
            'is_valid': len(missing) == 0,
            'missing_parameters': missing
        }
    def validate_recurring_payment_parameters(self, parameters: Dict) -> Dict:
        """
        Validation spécifique pour les paiements récurrents avec plus de précision
        """
        missing = []
        warnings = []
        
        # Paramètres obligatoires
        required = ['amount', 'frequency']
        
        # Vérifier destinataire OU service
        if not parameters.get('recipient_name') and not parameters.get('service_name'):
            missing.append('recipient_or_service')
        
        # Vérifier compte destinataire pour virements (pas pour services)
        if parameters.get('recipient_name') and not parameters.get('recipient_account'):
            missing.append('recipient_account')
        
        # Autres paramètres obligatoires
        for param in required:
            if not parameters.get(param):
                missing.append(param)
        
        # Vérifications de cohérence
        if parameters.get('amount'):
            try:
                amount = float(parameters['amount'])
                if amount <= 0:
                    warnings.append('Le montant doit être positif')
            except (ValueError, TypeError):
                warnings.append('Format de montant invalide')
        
        # Validation de la date exacte
        exact_date = parameters.get('exact_date')
        if exact_date:
            try:
                day = int(exact_date)
                if day < 1 or day > 31:
                    warnings.append('Jour invalide (doit être entre 1 et 31)')
            except (ValueError, TypeError):
                warnings.append('Format de date invalide')
        
        return {
            'is_valid': len(missing) == 0,
            'missing_parameters': missing,
            'warnings': warnings,
            'has_warnings': len(warnings) > 0
        }
    def format_missing_recurring_parameters(self, missing_params: List[str]) -> str:
        """
        Formatage spécialisé pour les paramètres de paiements récurrents
        """
        param_names = {
            'amount': 'le montant exact',
            'frequency': 'la fréquence (hebdomadaire, mensuel, etc.)',
            'recipient_or_service': 'le nom du destinataire OU le service (STEG, SONEDE, etc.)',
            'recipient_account': 'le numéro de compte/RIB du destinataire',
            'service_name': 'le nom du service',
            'exact_date': 'la date exacte de prélèvement (ex: le 15 de chaque mois)'
        }
        
        formatted = []
        for param in missing_params:
            formatted.append(param_names.get(param, param))
        
        if len(formatted) == 1:
            return formatted[0]
        elif len(formatted) == 2:
            return f"{formatted[0]} et {formatted[1]}"
        else:
            return ', '.join(formatted[:-1]) + f" et {formatted[-1]}"

    def extract_payment_parameters_improved(self, user_input: str) -> Dict:
        """Extraction améliorée spécifiquement pour les paiements de factures"""
        user_lower = user_input.lower().strip()
        parameters = {}
        
        try:
            # 1. Extraction du montant (utiliser la fonction existante améliorée)
            amount = self.extract_amount_improved(user_input)
            if amount:
                parameters['amount'] = amount
            
            # 2. Extraction du numéro de facture avec validation
            bill_number = self.extract_bill_number_improved(user_input)
            if bill_number:
                parameters['bill_number'] = bill_number
            
            # 3. Extraction du merchant/service avec priorité aux services connus
            merchant = self.extract_merchant_improved(user_input)
            if merchant:
                parameters['merchant'] = merchant
            
            # 4. Détection automatique de services tunisiens connus
            known_services = {
                'steg': 'STEG (Électricité et Gaz)',
                'sonede': 'SONEDE (Eau)',
                'ooredoo': 'Ooredoo Tunisie',
                'orange': 'Orange Tunisie',
                'tunisie telecom': 'Tunisie Telecom',
                'cnam': 'CNAM (Assurance Maladie)',
                'cnss': 'CNSS (Sécurité Sociale)',
                'électricité': 'STEG',
                'eau': 'SONEDE',
                'téléphone': 'Opérateur Télécom'
            }
            
            for service_key, service_name in known_services.items():
                if service_key in user_lower and not parameters.get('merchant'):
                    parameters['merchant'] = service_name
                    parameters['is_known_service'] = True
                    break
        
        except Exception as e:
            logger.error(f"Erreur extraction paramètres paiement: {str(e)}")
        
        return parameters

    def extract_recurring_payment_parameters(self, user_input: str) -> Dict:
        """
        Extraction spécialisée pour les paiements récurrents
        """
        user_lower = user_input.lower().strip()
        parameters = {}
        
        # Extraction de base (montant, compte, nom)
        base_params = self.extract_parameters(user_input, 'recurring_payment')
        parameters.update(base_params)
        
        # Services spécifiques tunisiens
        services_map = {
            'steg': 'STEG (Électricité et Gaz)',
            'sonede': 'SONEDE (Eau)',
            'tunisie telecom': 'Tunisie Telecom',
            'ooredoo': 'Ooredoo Tunisie',
            'orange': 'Orange Tunisie',
            'cnam': 'CNAM (Assurance Maladie)',
            'cnss': 'CNSS (Sécurité Sociale)',
            'électricité': 'STEG (Électricité)',
            'eau': 'SONEDE (Eau)',
            'téléphone': 'Opérateur Télécom',
            'internet': 'Fournisseur Internet'
        }
        
        for key, full_name in services_map.items():
            if key in user_lower:
                parameters['service_name'] = full_name
                # Pour les services, pas besoin de RIB
                parameters['is_service_payment'] = True
                break
        
        # Extraction de la date exacte
        for pattern in self.extraction_patterns.get('exact_date', []):
            match = re.search(pattern, user_lower)
            if match:
                day = match.group(1)
                try:
                    day_num = int(day)
                    if 1 <= day_num <= 31:
                        parameters['exact_date'] = day_num
                        break
                except ValueError:
                    continue
        
        # Fréquence améliorée
        frequency_mapping = {
            'quotidien': 'daily',
            'journalier': 'daily', 
            'jour': 'daily',
            'hebdomadaire': 'weekly', 
            'semaine': 'weekly',
            'mensuel': 'monthly', 
            'mois': 'monthly',
            'trimestriel': 'quarterly', 
            'trimestre': 'quarterly',
            'semestriel': 'semestrially',
            'semestre': 'semestrially',
            'annuel': 'yearly', 
            'année': 'yearly',
            'ans': 'yearly'
        }
        
        for pattern in self.extraction_patterns.get('frequency_enhanced', []):
            match = re.search(pattern, user_lower)
            if match:
                freq_text = match.group(1) if match.lastindex else match.group()
                for key, value in frequency_mapping.items():
                    if key in freq_text:
                        parameters['frequency'] = value
                        break
                if parameters.get('frequency'):
                    break
        
        return parameters
    
    def extract_transaction_history_parameters(self, user_input: str) -> Dict:
        """Extraction des paramètres pour l'historique des transactions - VERSION CORRIGÉE"""
        user_lower = user_input.lower().strip()
        parameters = {
            'limit': 10,  # Valeur par défaut
            'include_amount': True,
            'format': 'detailed'
        }
        
        # NOUVEAU: Extraction du numéro de compte
        account_patterns = [
            r'(ACC[-]?\d{10,16})',  # Format ACC-1234567890
            r'compte\s+([A-Z0-9\-]{8,20})',
            r'(\d{20})',  # RIB tunisien
            r'\b([A-Z]{2,4}[-]?\d{8,16})\b'
        ]
        
        for pattern in account_patterns:
            match = re.search(pattern, user_input, re.IGNORECASE)
            if match:
                account = match.group(1).strip().upper()
                if self.is_likely_account_number(account):
                    parameters['account_number'] = account
                    break
        
        # Patterns pour extraction du nombre (ordre de priorité)
        number_patterns = [
            r'(\d+)\s+(?:dernières?|dernieres?)\s+(?:transactions?|operations?)',
            r'(?:les?\s+)?(\d+)\s+(?:dernières?|dernieres?)\s+(?:transactions?|operations?)',
            r'(?:voir|afficher|montre)\s+(?:les?\s+)?(\d+)',
            r'(\d+)\s+(?:transactions?|operations?)',
            r'historique.*?(\d+)',
        ]
        
        requested_count = None
        for pattern in number_patterns:
            match = re.search(pattern, user_input, re.IGNORECASE)
            if match:
                try:
                    count = int(match.group(1))
                    if 1 <= count <= 100:
                        requested_count = count
                        break
                except (ValueError, IndexError):
                    continue
        
        # Définir la limite finale
        if requested_count:
            parameters['limit'] = requested_count
            parameters['user_requested_count'] = requested_count
        else:
            parameters['limit'] = 10
            parameters['user_requested_count'] = None
        
        # NOUVEAU: Extraction de dates améliorée pour format français
        date_patterns = [
            r'(\d{1,2})/(\d{1,2})/(\d{4})',  # DD/MM/YYYY
            r'(\d{1,2})-(\d{1,2})-(\d{4})',  # DD-MM-YYYY
            r'le\s+(\d{1,2})/(\d{1,2})/(\d{4})',
            r'pour\s+(\d{1,2})/(\d{1,2})/(\d{4})',
        ]
        
        for pattern in date_patterns:
            match = re.search(pattern, user_input, re.IGNORECASE)
            if match:
                try:
                    day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
                    if 1 <= day <= 31 and 1 <= month <= 12 and year >= 2000:
                        date_obj = datetime(year, month, day)
                        
                        # Si c'est une date spécifique, définir start et end sur le même jour
                        parameters['start_date'] = date_obj.replace(hour=0, minute=0, second=0, microsecond=0)
                        parameters['end_date'] = date_obj.replace(hour=23, minute=59, second=59, microsecond=999999)
                        parameters['specific_date'] = True
                        break
                except (ValueError, IndexError):
                    continue
        
        # Gestion des mois en français
        month_names = {
            'janvier': 1, 'février': 2, 'mars': 3, 'avril': 4, 'mai': 5, 'juin': 6,
            'juillet': 7, 'août': 8, 'aout': 8, 'septembre': 9, 'octobre': 10, 'novembre': 11, 'décembre': 12
        }
        
        # Pattern pour "mois de août" ou "août"
        month_pattern = r'(?:mois\s+de\s+)?(' + '|'.join(month_names.keys()) + r')'
        month_match = re.search(month_pattern, user_lower)
        if month_match and not parameters.get('start_date'):
            month_name = month_match.group(1)
            month_num = month_names[month_name]
            
            # Utiliser l'année courante par défaut
            from datetime import datetime
            current_year = datetime.now().year
            
            # Premier jour du mois
            start_date = datetime(current_year, month_num, 1, 0, 0, 0, 0)
            
            # Dernier jour du mois
            if month_num == 12:
                end_date = datetime(current_year + 1, 1, 1, 0, 0, 0, 0) - timedelta(microseconds=1)
            else:
                end_date = datetime(current_year, month_num + 1, 1, 0, 0, 0, 0) - timedelta(microseconds=1)
            
            parameters['start_date'] = start_date
            parameters['end_date'] = end_date
            parameters['period'] = 'month'
            parameters['period_name'] = month_name
        
        # Extraction du type de transaction
        transaction_types = {
            r'\bvirement[s]?\b': 'transfer',
            r'\bpaiement[s]?\b': 'payment',
            r'\bretrait[s]?\b': 'withdrawal',
            r'\bdépôt[s]?\b': 'deposit',
            r'\bdébit[s]?\b': 'debit',
            r'\bcrédit[s]?\b': 'credit',
        }
        
        for pattern, trans_type in transaction_types.items():
            if re.search(pattern, user_input, re.IGNORECASE):
                parameters['transaction_type'] = trans_type
                break
        
        return parameters

    def handle_recurring_payment_intent(self, user_input: str) -> Dict:
        """
        Gestion spécialisée des paiements récurrents
        """
        parameters = self.extract_recurring_payment_parameters(user_input)
        validation = self.validate_recurring_payment_parameters(parameters)
        
        if validation['is_valid']:
            # Créer un message détaillé de confirmation
            details = []
            
            if parameters.get('amount'):
                details.append(f"Montant: {parameters['amount']} DT")
            
            if parameters.get('service_name'):
                details.append(f"Service: {parameters['service_name']}")
            elif parameters.get('recipient_name'):
                details.append(f"Destinataire: {parameters['recipient_name']}")
            
            if parameters.get('frequency'):
                freq_names = {
                    'daily': 'quotidien',
                    'weekly': 'hebdomadaire', 
                    'monthly': 'mensuel',
                    'quarterly': 'trimestriel',
                    'semestrially': 'semestriel',
                    'yearly': 'annuel'
                }
                details.append(f"Fréquence: {freq_names.get(parameters['frequency'], parameters['frequency'])}")
            
            if parameters.get('exact_date'):
                if parameters.get('frequency') == 'monthly':
                    details.append(f"Date: le {parameters['exact_date']} de chaque mois")
                else:
                    details.append(f"Date: le {parameters['exact_date']}")
            
            response_text = f"Configuration du paiement récurrent:\n" + "\n".join(f"• {detail}" for detail in details) + "\n\nConfirmez-vous ces informations ?"
            
            return {
                "intent": "recurring_payment",
                "confidence": 0.95,
                "response": response_text,
                "parameters": parameters,
                "requires_action": True,
                "action_type": "recurring_payment"
            }
        
        else:
            missing_str = self.format_missing_recurring_parameters(validation['missing_parameters'])
            warnings_text = ""
            if validation['warnings']:
                warnings_text = f"\n⚠️ Attention: {', '.join(validation['warnings'])}"
            
            response_text = f"Pour configurer votre paiement récurrent, j'ai besoin de: {missing_str}.{warnings_text}"
            
            # Sauvegarder le contexte
            self.conversation_context['waiting_for_info'] = True
            self.conversation_context['current_intent'] = 'recurring_payment'
            self.conversation_context['partial_parameters'] = parameters
            
            return {
                "intent": "recurring_payment",
                "confidence": 0.90,
                "response": response_text,
                "parameters": parameters,
                "requires_action": False,
                "action_type": None
            }
    async def process_message_async(self, user_input: str, user_context: Dict = None) -> Dict:
        """Version asynchrone avec logique améliorée"""
        try:
            # Preprocessing avec correction orthographique
            corrected_input, has_corrections, original_input = self.preprocess_user_input(user_input)
            
            # Essai de détection rapide avec nouveau système
            quick_response = self.get_quick_response(corrected_input)
            if quick_response:
                logger.info(f"Réponse ultra-rapide utilisée pour: {corrected_input}")
                if has_corrections:
                    quick_response['correction_applied'] = True
                    quick_response['original_input'] = original_input
                    quick_response['corrected_input'] = corrected_input
                self.add_context_to_memory(corrected_input, quick_response.get("response", ""))
                return quick_response

            quick_response = self.quick_intent_detection(corrected_input)
            if quick_response and quick_response['confidence'] > 0.70:
                logger.info(f"Réponse rapide utilisée pour: {corrected_input}")
                
                # CORRECTION: Logique améliorée pour les virements et paiements
                if quick_response.get('action_type') == 'transfer_money':
                    validation = self.validate_transaction_parameters('transfer_money', quick_response['parameters'])
                    if not validation['is_valid']:
                        self.conversation_context['waiting_for_info'] = True
                        self.conversation_context['current_intent'] = 'transfer_money'
                        self.conversation_context['partial_parameters'] = quick_response['parameters']
                        self.conversation_context['last_request_time'] = time.time()
                        
                        missing_str = self._format_missing_parameters(validation['missing_parameters'], 'transfer_money')
                        quick_response['response'] = f"Pour effectuer le virement, j'ai besoin de : {missing_str}. Pouvez-vous me les donner ?"
                        quick_response['requires_action'] = False
                        quick_response['confidence'] = 0.85
                
                elif quick_response.get('action_type') == 'payment':
                    validation = self.validate_transaction_parameters('payment', quick_response['parameters'])
                    if not validation['is_valid']:
                        self.conversation_context['waiting_for_info'] = True
                        self.conversation_context['current_intent'] = 'payment'
                        self.conversation_context['partial_parameters'] = quick_response['parameters']
                        self.conversation_context['last_request_time'] = time.time()
                        
                        missing_str = self._format_missing_parameters(validation['missing_parameters'], 'payment')
                        quick_response['response'] = f"Pour traiter le paiement de votre facture, j'ai besoin de : {missing_str}. Pouvez-vous me les fournir ?"
                        quick_response['requires_action'] = False
                        quick_response['confidence'] = 0.85
                
                if has_corrections:
                    quick_response['correction_applied'] = True
                    quick_response['original_input'] = original_input
                    quick_response['corrected_input'] = corrected_input
                    
                self.add_context_to_memory(corrected_input, quick_response.get("response", ""))
                return quick_response
            
            # Sinon, utiliser le modèle LLM
            return await self._process_with_llm(corrected_input, user_context)
            
        except Exception as e:
            logger.error(f"Erreur dans process_message_async: {str(e)}")
            return {
                "intent": "error",
                "confidence": 0.0,
                "response": "Je rencontre un problème technique. Pouvez-vous réessayer ?",
                "parameters": {},
                "requires_action": False,
                "action_type": None,
                "error": str(e)
            }
    async def _process_with_llm(self, user_input: str, user_context: Dict = None) -> Dict:
        """Traitement avec le modèle LLM amélioré"""
        try:
            # Préparer l'entrée avec contexte
            if user_context:
                enhanced_input = f"Contexte: {json.dumps(user_context, ensure_ascii=False)}\nMessage: {user_input}"
            else:
                enhanced_input = user_input
            
            logger.info(f"Traitement LLM pour: {user_input}")
            start_time = time.time()
            
            response = await asyncio.get_event_loop().run_in_executor(
                None, 
                self.chain.run,
                enhanced_input
            )
            
            processing_time = time.time() - start_time
            logger.info(f"Temps de traitement LLM: {processing_time:.2f}s")
            
            # Parser la réponse
            parsed_response = self._parse_response(response)
            
            # Validation supplémentaire des paramètres pour les transactions
            if parsed_response.get('requires_action') and parsed_response.get('action_type') in ['transfer_money', 'payment', 'recurring_payment']:
                validation = self.validate_transaction_parameters(
                    parsed_response['action_type'],
                    parsed_response.get('parameters', {})
                )
                
                if not validation['is_valid']:
                    # Configurer le contexte pour attendre les infos manquantes
                    self.conversation_context['waiting_for_info'] = True
                    self.conversation_context['current_intent'] = parsed_response['action_type']
                    self.conversation_context['partial_parameters'] = parsed_response.get('parameters', {})
                    self.conversation_context['last_request_time'] = time.time()
                    
                    missing_params = validation['missing_parameters']
                    missing_str = self._format_missing_parameters(missing_params, parsed_response['action_type'])
                    
                    if parsed_response['action_type'] == 'payment':
                        parsed_response['response'] = f"Pour traiter le paiement de votre facture, j'ai besoin de : {missing_str}. Pouvez-vous me les fournir ?"
                    elif parsed_response['action_type'] == 'transfer_money':
                        parsed_response['response'] = f"Pour effectuer le virement, j'ai besoin de : {missing_str}. Pouvez-vous me les donner ?"
                    else:
                        parsed_response['response'] = f"Pour effectuer cette transaction, j'ai besoin de : {missing_str}. Pouvez-vous me les fournir ?"
                    
                    parsed_response['requires_action'] = False
            
            # Ajouter à la mémoire si succès
            if parsed_response.get("intent") != "error":
                self.add_context_to_memory(user_input, parsed_response.get("response", ""))
            
            return parsed_response
            
        except Exception as e:
            logger.error(f"Erreur _process_with_llm: {str(e)}")
            return {
                "intent": "error",
                "confidence": 0.0,
                "response": "Je rencontre un problème technique. Pouvez-vous reformuler votre demande ?",
                "parameters": {},
                "requires_action": False,
                "action_type": None,
                "error": str(e)
            }
        
    