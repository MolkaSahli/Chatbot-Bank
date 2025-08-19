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
import re


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
                # Patterns très spécifiques pour éviter confusion
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
                r'\b(?:transfer|transférer)\b',
                r'\b(?:envoyer|virer)\s+(?:de\s+l\'?)?argent\b',
                r'\bfaire\s+(?:un\s+)?virement\b',
                r'\beffectuer\s+(?:un\s+)?virement\b',
                r'\bje\s+(?:veux|voudrais|souhaite)\s+(?:faire\s+)?(?:un\s+)?virement\b',
                r'\btransfert\s+(?:de\s+)?(?:fonds?|argent)\b',
                r'\bvirer\s+\d+',
                r'\benvoyer\s+\d+',
                r'\btransférer\s+\d+',
                # Patterns avec montant et destinataire
                r'\bvirement\s+(?:de\s+)?\d+.*(?:vers|à|pour)\b',
                r'\benvoyer.*\d+.*(?:dt|dinar|€).*(?:vers|à|pour)\b',
                r'\bvirer.*\d+.*(?:dt|dinar|€).*(?:vers|à|pour)\b',
                r'\b(?:virement|envoyer|virer).*(?:vers|à|pour)\s+[A-Za-zÀ-ÿ]+',
            ],
            
            'payment': [
                r'\bpaye?r.*facture\b',
                r'\bpaiement.*facture\b',
                r'\brégler.*facture\b',
                r'\bfacture.*(?:électricité|eau|gaz|téléphone|internet|steg|sonede)\b',
                r'\brèglement.*facture\b',
                r'\beffectuer.*paiement.*facture\b',
                r'\bpayer.*(?:chez|à|pour)(?!\s+facture)\b',
                r'\bpaiement.*(?:chez|à|pour)(?!\s+facture)\b',
                r'\bacheter.*chez\b',
                r'\brégler.*(?:chez|à|pour)(?!\s+facture)\b'
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
                r'\bhistorique.*transactions?\b',
                r'\bdernières.*transactions?\b',
                r'\bvoir.*historique\b',
                r'\bmes.*transactions?\b',
                r'\bliste.*(?:opérations|transactions)\b',
                r'\bhistorique.*opérations\b',
                r'\bmes.*dernières.*opérations\b'
            ]
        }
        
        # CORRECTION: Poids ajustés pour favoriser la distinction
        self.intent_weights = {
            'greeting': 1.0,
            'goodbye': 1.0,
            'check_balance': 0.85,  # Réduit légèrement
            'get_accounts': 0.95,   # Augmenté pour favoriser la liste
            'transfer_money': 0.90, # Augmenté
            'payment': 0.85,
            'recurring_payment': 0.80,
            'transaction_history': 0.85
        }
        
        # Patterns d'extraction améliorés avec plus de précision
        self.extraction_patterns = {
            'amount': [
                # Montants avec devise explicite
                r'(?:montant.*?)?(\d+(?:[\s,]?\d{3})*(?:[,.]?\d{1,2})?)\s*(?:dt|dinar|euro|€|dinars?)',
                # Montants dans contexte
                r'(?:somme.*?|coût.*?|prix.*?|valeur.*?)(\d+(?:[\s,]?\d{3})*(?:[,.]?\d{1,2})?)',
                # Montants avec "est" (ex: "montant est 100Dt")
                r'(?:montant|somme)\s+(?:est|de)\s*(\d+(?:[\s,]?\d{3})*(?:[,.]?\d{1,2})?)',
                # Montants isolés (avec validation contextuelle)
                r'(\d+(?:[\s,]?\d{3})*(?:[,.]?\d{1,2})?)\s*dt',
                r'(\d+(?:[\s,]?\d{3})*(?:[,.]?\d{1,2})?)€'
            ],
            'account_number': [
                # Formats standards de comptes
                r'(?:compte|rib|numéro).*?([A-Z]{2,4}[-]?\d{8,12})',
                r'vers.*?(?:compte|numéro)\s*([A-Z0-9-]{8,20})',
                r'destinataire.*?([A-Z]{2,4}[-]?\d{8,12})',
                # Compte seul avec validation
                r'\b([A-Z]{2,4}[-]?\d{8,12})\b',
                r'\b(\d{10,16})\b(?=\s|$)'
            ],
            'recipient_name': [
                # Noms après prépositions
                r'(?:vers|pour|à|au nom de|destinataire)\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                r'bénéficiaire\s*[:\-]?\s*([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                r'nom\s*[:\-]?\s*([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                # Titres de civilité
                r'(?:monsieur|madame|m\.|mme|mr)\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                # Contexte de virement
                r'virement.*?(?:vers|à|pour)\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                r'envoyer.*?(?:vers|à|pour)\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})'
            ],
            'merchant': [
                # Factures et services
                r'facture.*?(?:de|chez)\s*([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                r'(?:steg|sonede|tunisie telecom|ooredoo|orange)',
                r'chez\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                r'payer.*?(?:à|chez)\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                r'entreprise\s*([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                r'société\s*([A-Za-zÀ-ÿ\s\-\'\.]{2,40})'
            ],
            'bill_number': [
                # Numéros de factures
                r'(?:facture|numéro|référence|n°)\s*[:\-]?\s*([A-Z0-9\-]{6,20})',
                r'ref\s*[:\-]?\s*([A-Z0-9\-]{6,20})',
                r'\b(FAC\d+|FACT\d+|REF\d+)\b',
                r'numéro.*facture.*?([A-Z0-9\-]{6,20})'
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
            ]
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
    
    def is_likely_account_number(self, text: str) -> bool:
        """Vérifie si le texte ressemble à un numéro de compte avec validation renforcée"""
        text = text.strip().upper()
        
        # Patterns pour numéros de compte tunisiens et internationaux
        account_patterns = [
            r'^[A-Z]{2,4}[-]?\d{8,16}$',     # Format: ACC-123456789 ou ACC123456789
            r'^[A-Z0-9]{10,20}$',            # Format: alphanumérique
            r'^\d{10,16}$'                   # Format: numérique pur (RIB)
        ]
        
        for pattern in account_patterns:
            if re.match(pattern, text):
                # Validation supplémentaire : ne doit pas être que des zéros
                if not re.match(r'^0+$', re.sub(r'[A-Z-]', '', text)):
                    return True
        
        return False
    
    def get_quick_response(self, user_input: str) -> Optional[Dict]:
        """
        CORRECTION: Réponses ultra-rapides fixes pour salutations et questions simples
        """
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

    def is_likely_person_name(self, text: str) -> bool:
        """Vérifie si le texte ressemble à un nom de personne avec validation renforcée"""
        text = text.strip()
        
        # Exclusions
        if self.is_likely_account_number(text):
            return False
        
        if len(text) < 2 or len(text) > 50:
            return False
        
        # Doit contenir au moins une lettre
        if not re.search(r'[A-Za-zÀ-ÿ]', text):
            return False
        
        # Pattern pour nom valide (lettres, espaces, tirets, apostrophes, points)
        if not re.match(r'^[A-Za-zÀ-ÿ\s\-\'\.]+', text):
            return False
        
        # Ne doit pas être un mot-clé bancaire
        banking_keywords = ['compte', 'solde', 'virement', 'facture', 'paiement', 'banque']
        if text.lower() in banking_keywords:
            return False
        
        return True
    
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
        
        # Extraction des nouveaux paramètres selon l'intention
        if current_intent == 'recurring_payment':
            new_params = self.extract_recurring_payment_parameters(user_input)
            
            # Détection intelligente pour paiements récurrents
            if not partial_params.get('service_name') and not partial_params.get('recipient_name'):
                # Chercher service ou destinataire
                services_map = {
                    'steg': 'STEG (Électricité et Gaz)',
                    'sonede': 'SONEDE (Eau)',
                    'tunisie telecom': 'Tunisie Telecom',
                    'ooredoo': 'Ooredoo Tunisie',
                    'orange': 'Orange Tunisie',
                    'cnam': 'CNAM (Assurance Maladie)',
                    'cnss': 'CNSS (Sécurité Sociale)'
                }
                
                user_lower = user_input_clean.lower()
                for key, full_name in services_map.items():
                    if key in user_lower:
                        new_params['service_name'] = full_name
                        new_params['is_service_payment'] = True
                        break
                
                # Si pas de service, vérifier si c'est un nom de personne
                if not new_params.get('service_name') and self.is_likely_person_name(user_input_clean):
                    new_params['recipient_name'] = user_input_clean.title()
            
            # Extraction de date exacte
            if not partial_params.get('exact_date'):
                date_match = re.search(r'le\s*(\d{1,2})', user_input_clean.lower())
                if date_match:
                    try:
                        day = int(date_match.group(1))
                        if 1 <= day <= 31:
                            new_params['exact_date'] = day
                    except ValueError:
                        pass
            
            # Validation avec logique spécialisée
            partial_params.update(new_params)
            validation = self.validate_recurring_payment_parameters(partial_params)
            
            if validation['is_valid']:
                # Configuration complète
                self.conversation_context['waiting_for_info'] = False
                self.conversation_context['current_intent'] = None
                self.conversation_context['partial_parameters'] = {}
                
                # Créer message de confirmation détaillé
                details = []
                if partial_params.get('amount'):
                    details.append(f"Montant: {partial_params['amount']} DT")
                
                if partial_params.get('service_name'):
                    details.append(f"Service: {partial_params['service_name']}")
                elif partial_params.get('recipient_name'):
                    details.append(f"Destinataire: {partial_params['recipient_name']}")
                
                if partial_params.get('frequency'):
                    freq_names = {
                        'daily': 'quotidien', 'weekly': 'hebdomadaire', 'monthly': 'mensuel',
                        'quarterly': 'trimestriel', 'semestrially': 'semestriel', 'yearly': 'annuel'
                    }
                    details.append(f"Fréquence: {freq_names.get(partial_params['frequency'], partial_params['frequency'])}")
                
                if partial_params.get('exact_date'):
                    if partial_params.get('frequency') == 'monthly':
                        details.append(f"Date: le {partial_params['exact_date']} de chaque mois")
                    else:
                        details.append(f"Date: le {partial_params['exact_date']}")
                
                confirmation_text = "Configuration du paiement récurrent:\n" + "\n".join(f"• {detail}" for detail in details)
                confirmation_text += "\n\nConfirmez-vous cette configuration ?"
                
                return {
                    "intent": current_intent,
                    "confidence": 0.95,
                    "response": confirmation_text,
                    "parameters": partial_params,
                    "requires_action": True,
                    "action_type": current_intent
                }
            else:
                # Il manque encore des infos
                missing_params = validation['missing_parameters']
                self.conversation_context['partial_parameters'] = partial_params
                
                missing_str = self.format_missing_recurring_parameters(missing_params)
                warnings_text = ""
                if validation['warnings']:
                    warnings_text = f"\n⚠️ {', '.join(validation['warnings'])}"
                
                return {
                    "intent": current_intent,
                    "confidence": 0.90,
                    "response": f"Merci ! Il me manque encore : {missing_str}.{warnings_text}",
                    "parameters": partial_params,
                    "requires_action": False,
                    "action_type": None
                }
        
        elif current_intent == 'transfer_money':
            # CORRECTION: Logique améliorée pour virements
            new_params = self.extract_parameters(user_input, current_intent)
            
            # Détection intelligente du type d'information fournie
            if self.is_likely_account_number(user_input_clean) and not partial_params.get('recipient_account'):
                new_params['recipient_account'] = user_input_clean.upper()
            elif self.is_likely_person_name(user_input_clean) and not partial_params.get('recipient_name'):
                new_params['recipient_name'] = user_input_clean.title()
            
            # Fusionner les paramètres
            partial_params.update(new_params)
            validation = self.validate_transaction_parameters(current_intent, partial_params)
            
            if validation['is_valid']:
                self.conversation_context['waiting_for_info'] = False
                self.conversation_context['current_intent'] = None
                self.conversation_context['partial_parameters'] = {}
                
                return {
                    "intent": current_intent,
                    "confidence": 0.95,
                    "response": f"Parfait ! Je procède maintenant à votre {self._get_action_french_name(current_intent)}...",
                    "parameters": partial_params,
                    "requires_action": True,
                    "action_type": current_intent
                }
            else:
                missing_params = validation['missing_parameters']
                self.conversation_context['partial_parameters'] = partial_params
                missing_str = self._format_missing_parameters(missing_params, current_intent)
                
                return {
                    "intent": current_intent,
                    "confidence": 0.90,
                    "response": f"Merci ! Il me manque encore : {missing_str}",
                    "parameters": partial_params,
                    "requires_action": False,
                    "action_type": None
                }
        
        elif current_intent == 'payment':
            # Logique existante pour paiements
            new_params = self.extract_parameters(user_input, current_intent)
            
            if re.match(r'[A-Z0-9\-]{6,20}', user_input_clean.upper()) and not partial_params.get('bill_number'):
                new_params['bill_number'] = user_input_clean.upper()
            elif self.is_likely_person_name(user_input_clean) and not partial_params.get('merchant'):
                new_params['merchant'] = user_input_clean.title()
            
            partial_params.update(new_params)
            validation = self.validate_transaction_parameters(current_intent, partial_params)
            
            if validation['is_valid']:
                self.conversation_context['waiting_for_info'] = False
                self.conversation_context['current_intent'] = None
                self.conversation_context['partial_parameters'] = {}
                
                return {
                    "intent": current_intent,
                    "confidence": 0.95,
                    "response": f"Parfait ! Je procède maintenant à votre {self._get_action_french_name(current_intent)}...",
                    "parameters": partial_params,
                    "requires_action": True,
                    "action_type": current_intent
                }
            else:
                missing_params = validation['missing_parameters']
                self.conversation_context['partial_parameters'] = partial_params
                missing_str = self._format_missing_parameters(missing_params, current_intent)
                
                return {
                    "intent": current_intent,
                    "confidence": 0.90,
                    "response": f"Merci ! Il me manque encore : {missing_str}",
                    "parameters": partial_params,
                    "requires_action": False,
                    "action_type": None
                }
        
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

    # MÉTHODES DE CORRECTION ORTHOGRAPHIQUE
    
    def normalize_text(self, text: str) -> str:
        """Normalise le texte en supprimant les accents et caractères spéciaux"""
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

    def correct_word(self, word: str, context_keywords: list = None) -> str:
        """Corrige un mot en utilisant plusieurs stratégies"""
        word_lower = word.lower().strip()
        
        # Stratégie 1: Correction directe depuis le dictionnaire
        if word_lower in self.spelling_corrections:
            correction = self.spelling_corrections[word_lower]
            if isinstance(correction, list):
                return correction[0]  # Prendre la première suggestion
            return correction
        
        # Stratégie 2: Recherche par similarité dans les mots-clés bancaires
        best_match = None
        best_score = 0
        min_score = 70  # Score minimum pour considérer une correction
        
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

    def correct_sentence(self, sentence: str) -> str:
        """Corrige une phrase entière en préservant la ponctuation"""
        # Séparer les mots tout en gardant la ponctuation
        words = re.findall(r'\b\w+\b|[.,!?;]', sentence)
        corrected_words = []
        
        # Détecter le contexte de la phrase pour améliorer les corrections
        sentence_lower = sentence.lower()
        context_keywords = []
        
        # Identifier le contexte
        if any(word in sentence_lower for word in ['solde', 'compte', 'consulter']):
            context_keywords = self.banking_keywords['comptes']
        elif any(word in sentence_lower for word in ['virement', 'paiement', 'facture']):
            context_keywords = self.banking_keywords['transactions']
        elif any(word in sentence_lower for word in ['bonjour', 'salut', 'hello']):
            context_keywords = self.banking_keywords['salutations']
        
        for word in words:
            if re.match(r'\w+', word):  # Si c'est un mot (pas de ponctuation)
                corrected_word = self.correct_word(word, context_keywords)
                corrected_words.append(corrected_word)
            else:
                corrected_words.append(word)  # Garder la ponctuation
        
        return ' '.join(corrected_words)

    def preprocess_user_input(self, user_input: str) -> tuple:
        """Préprocesse l'entrée utilisateur et retourne le texte original et corrigé"""
        original_input = user_input.strip()
        
        # Correction basique des espaces multiples
        cleaned_input = re.sub(r'\s+', ' ', original_input)
        
        # Correction orthographique
        corrected_input = self.correct_sentence(cleaned_input)
        
        # Déterminer si des corrections ont été faites
        has_corrections = original_input.lower() != corrected_input.lower()
        
        return corrected_input, has_corrections, original_input
    
    # MÉTHODES UTILITAIRES ET TESTS
    
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
    
    def get_conversation_history(self) -> List[BaseMessage]:
        """Retourne l'historique de conversation"""
        try:
            return self.memory.chat_memory.messages
        except Exception as e:
            logger.error(f"Erreur récupération historique: {str(e)}")
            return []

    # MÉTHODES DE TEST ET DEBUGGING
    
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
    
    def quick_intent_detection(self, user_input: str) -> Optional[Dict]:
        """CORRECTION: Détection rapide avec le nouveau système amélioré"""
        return self.detect_best_intent(user_input)
    
    def extract_parameters(self, user_input: str, intent: str) -> Dict:
        """CORRECTION: Extraction des paramètres avec meilleure précision"""
        user_lower = user_input.lower().strip()
        parameters = {}
        
        try:
            # Extraction du montant avec validation contextuelle
            for pattern in self.extraction_patterns['amount']:
                match = re.search(pattern, user_input, re.IGNORECASE)
                if match:
                    amount_str = match.group(1).replace(',', '.').replace(' ', '')
                    try:
                        amount = float(amount_str)
                        if amount > 0:  # Validation basique
                            parameters['amount'] = amount
                            break
                    except ValueError:
                        continue
            
            # Extraction selon l'intention spécifique
            if intent == 'transfer_money':
                # Amélioration: Extraction plus précise du compte destinataire
                account_patterns = [
                    # Patterns existants plus précis
                    r'(?:compte|rib|numéro)\s*[:\-]?\s*([A-Z]{2,4}[-]?\d{8,16})',
                    r'vers.*?(?:compte|numéro)\s*[:\-]?\s*([A-Z0-9-]{8,20})',
                    r'destinataire.*?([A-Z]{2,4}[-]?\d{8,16})',
                    # NOUVEAU: Pattern pour "avec le numéro de compte"
                    r'avec\s+le\s+numéro\s+(?:de\s+compte\s+)?([A-Z0-9-]{8,20})',
                    r'numéro\s+(?:de\s+compte\s+)?([A-Z0-9-]{8,20})',
                    # Pattern général pour compte isolé
                    r'\b([A-Z]{2,4}[-]?\d{8,16})\b',
                    r'\b(\d{10,16})\b(?=\s|$)'
                ]
                
                for pattern in account_patterns:
                    match = re.search(pattern, user_input, re.IGNORECASE)
                    if match:
                        account_candidate = match.group(1).strip().upper()
                        if self.is_likely_account_number(account_candidate):
                            parameters['recipient_account'] = account_candidate
                            break
                
                # Amélioration: Extraction plus précise du nom destinataire
                name_patterns = [
                    # Pattern pour "à [nom]"
                    r'\bà\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40}?)(?:\s+avec|\s+le|\s+numéro|$)',
                    # Patterns existants
                    r'(?:vers|pour|au nom de|destinataire)\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                    r'bénéficiaire\s*[:\-]?\s*([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                    r'nom\s*[:\-]?\s*([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                    # Titres de civilité
                    r'(?:monsieur|madame|m\.|mme|mr)\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                    # Contexte de virement
                    r'virement.*?(?:vers|à|pour)\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})',
                    r'envoyer.*?(?:vers|à|pour)\s+([A-Za-zÀ-ÿ\s\-\'\.]{2,40})'
                ]
                
                for pattern in name_patterns:
                    match = re.search(pattern, user_input, re.IGNORECASE)
                    if match:
                        name_candidate = match.group(1).strip()
                        # Nettoyer le nom (supprimer les mots parasites)
                        name_candidate = re.sub(r'\s+(?:avec|le|numéro|de|compte).*$', '', name_candidate, flags=re.IGNORECASE)
                        if self.is_likely_person_name(name_candidate):
                            parameters['recipient_name'] = name_candidate.title()
                            break
            
            elif intent == 'payment':
                # Code existant pour les paiements...
                for pattern in self.extraction_patterns['bill_number']:
                    match = re.search(pattern, user_input, re.IGNORECASE)
                    if match:
                        bill_num = match.group(1).strip().upper()
                        if len(bill_num) >= 4:
                            parameters['bill_number'] = bill_num
                            break
                
                for pattern in self.extraction_patterns['merchant']:
                    match = re.search(pattern, user_input, re.IGNORECASE)
                    if match:
                        merchant = match.group(1).strip()
                        if len(merchant) > 2:
                            parameters['merchant'] = merchant.title()
                            break
                
                special_services = {
                    'steg': 'STEG (Société Tunisienne de l\'Électricité et du Gaz)',
                    'sonede': 'SONEDE (Société Nationale d\'Exploitation et de Distribution des Eaux)',
                    'tunisie telecom': 'Tunisie Telecom',
                    'ooredoo': 'Ooredoo Tunisie',
                    'orange': 'Orange Tunisie'
                }
                
                for service, full_name in special_services.items():
                    if service in user_lower:
                        parameters['merchant'] = full_name
                        break
            
            elif intent == 'recurring_payment':
                # Code existant pour les paiements récurrents...
                for pattern in self.extraction_patterns['frequency']:
                    match = re.search(pattern, user_lower)
                    if match:
                        freq_text = match.group(1) if match.lastindex else match.group()
                        freq_mapping = {
                            'quotidien': 'daily', 'journalier': 'daily', 'jour': 'daily',
                            'hebdomadaire': 'weekly', 'semaine': 'weekly',
                            'mensuel': 'monthly', 'mois': 'monthly',
                            'trimestriel': 'quarterly', 'trimestre': 'quarterly',
                            'annuel': 'yearly', 'année': 'yearly'
                        }
                        parameters['frequency'] = freq_mapping.get(freq_text, 'monthly')
                        break
                
                for pattern in self.extraction_patterns['recipient_name']:
                    match = re.search(pattern, user_input, re.IGNORECASE)
                    if match:
                        name = match.group(1).strip()
                        if self.is_likely_person_name(name):
                            parameters['recipient_name'] = name.title()
                            break
                
                for pattern in self.extraction_patterns['account_number']:
                    match = re.search(pattern, user_input, re.IGNORECASE)
                    if match and self.is_likely_account_number(match.group(1)):
                        parameters['recipient_account'] = match.group(1).upper()
                        break
        
        except Exception as e:
            logger.error(f"Erreur extraction paramètres: {str(e)}")
        
        return parameters
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
    
    def process_message(self, user_input: str, user_context: Dict = None) -> Dict:
        
        try:
            # Preprocessing avec correction orthographique
            corrected_input, has_corrections, original_input = self.preprocess_user_input(user_input)
            
            # Vérifier d'abord les réponses ultra-rapides
            quick_response = self.get_quick_response(corrected_input)
            if quick_response:
                logger.info(f"Réponse ultra-rapide utilisée pour: {corrected_input}")
                if has_corrections:
                    quick_response['correction_applied'] = True
                    quick_response['original_input'] = original_input
                    quick_response['corrected_input'] = corrected_input
                self.add_context_to_memory(corrected_input, quick_response.get("response", ""))
                return quick_response
            
            # Essai de détection rapide avec nouveau système amélioré
            quick_response = self.quick_intent_detection(corrected_input)
            if quick_response and quick_response['confidence'] > 0.50:
                logger.info(f"Réponse rapide utilisée pour: {corrected_input} (Intent: {quick_response['intent']}, Confiance: {quick_response['confidence']:.2f})")
                
                # CORRECTION PRINCIPALE: Logique de virement complètement refactorisée
                if quick_response.get('action_type') == 'transfer_money':
                    validation = self.validate_transaction_parameters('transfer_money', quick_response['parameters'])
                    
                    # NOUVEAU: Si toutes les informations sont présentes, exécuter directement
                    if validation['is_valid']:
                        logger.info(f"Virement complet détecté - Paramètres: {quick_response['parameters']}")
                        
                        # Créer message de confirmation avec détails
                        amount = quick_response['parameters']['amount']
                        recipient_name = quick_response['parameters']['recipient_name']
                        recipient_account = quick_response['parameters']['recipient_account']
                        
                        confirmation_msg = f"Virement de {amount} DT vers {recipient_name} (compte {recipient_account}). Traitement en cours..."
                        
                        quick_response['response'] = confirmation_msg
                        quick_response['requires_action'] = True  # IMPORTANT: Forcer l'exécution
                        quick_response['confidence'] = 0.95
                        
                    else:
                        # Seulement si des infos manquent vraiment
                        missing_params = validation['missing_parameters']
                        
                        # Vérifier si on a au moins quelques paramètres
                        if quick_response['parameters']:
                            # Sauvegarder le contexte
                            self.conversation_context['waiting_for_info'] = True
                            self.conversation_context['current_intent'] = 'transfer_money'
                            self.conversation_context['partial_parameters'] = quick_response['parameters']
                            self.conversation_context['last_request_time'] = time.time()
                            
                            # Message personnalisé selon ce qui manque
                            if 'amount' in missing_params and 'recipient_name' in missing_params and 'recipient_account' in missing_params:
                                missing_msg = "le montant, le nom du bénéficiaire et son numéro de compte"
                            elif 'recipient_name' in missing_params and 'recipient_account' in missing_params:
                                missing_msg = "le nom complet du bénéficiaire et son numéro de compte"
                            elif 'amount' in missing_params:
                                missing_msg = "le montant exact à virer"
                            elif 'recipient_name' in missing_params:
                                missing_msg = "le nom complet du bénéficiaire"
                            elif 'recipient_account' in missing_params:
                                missing_msg = "le numéro de compte du destinataire"
                            else:
                                missing_msg = self._format_missing_parameters(missing_params, 'transfer_money')
                            
                            quick_response['response'] = f"Pour effectuer le virement, j'ai encore besoin de : {missing_msg}."
                            quick_response['requires_action'] = False
                            quick_response['confidence'] = 0.85
                        else:
                            # Aucun paramètre détecté
                            quick_response['response'] = "Pour effectuer le virement, j'ai besoin du montant, du nom complet du bénéficiaire et de son numéro de compte."
                            quick_response['requires_action'] = False
                            quick_response['confidence'] = 0.80
                
                # Logique existante pour autres intentions...
                elif quick_response.get('action_type') == 'recurring_payment':
                    specialized_response = self.handle_recurring_payment_intent(corrected_input)
                    if specialized_response:
                        if has_corrections:
                            specialized_response['correction_applied'] = True
                            specialized_response['original_input'] = original_input
                            specialized_response['corrected_input'] = corrected_input
                        self.add_context_to_memory(corrected_input, specialized_response.get("response", ""))
                        return specialized_response
                
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
                
                # Ajouter les informations de correction
                if has_corrections:
                    quick_response['correction_applied'] = True
                    quick_response['original_input'] = original_input
                    quick_response['corrected_input'] = corrected_input
                
                self.add_context_to_memory(corrected_input, quick_response.get("response", ""))
                return quick_response
            
            # Sinon traitement normal avec LLM
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
            
            # Ajouter les informations de correction
            if has_corrections:
                parsed_response['correction_applied'] = True
                parsed_response['original_input'] = original_input
                parsed_response['corrected_input'] = corrected_input
            
            # CORRECTION: Validation spécialisée avec logique de virement améliorée
            if parsed_response.get('requires_action'):
                action_type = parsed_response.get('action_type')
                
                if action_type == 'transfer_money':
                    validation = self.validate_transaction_parameters(action_type, parsed_response.get('parameters', {}))
                    
                    # NOUVEAU: Si complet, forcer l'exécution
                    if validation['is_valid']:
                        logger.info(f"Virement complet via LLM - Paramètres: {parsed_response.get('parameters', {})}")
                        parsed_response['requires_action'] = True  # S'assurer que l'action sera exécutée
                        
                        # Message de confirmation
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
                
                # Logique pour autres actions...
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
                            warnings_text = f"\n⚠️ Attention: {', '.join(validation['warnings'])}"
                        
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
            
            # Ajouter à la mémoire si succès
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
        