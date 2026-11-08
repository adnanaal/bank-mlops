"""Contrat de données du projet : colonnes, domaines autorisés et exceptions métier.

Source : UCI Bank Marketing, fichier bank-additional-full.csv (Moro, Rita & Cortez, 2014, CC BY 4.0).
"""

ID_COLUMN = 'client_id'          # identifiant de traçabilité (numéro de ligne du fichier source), jamais une feature
PERIOD_COLUMN = 'period'         # mois reconstitué AAAA-MM, sert au découpage en lots, jamais une feature
TARGET = 'y'                     # souscription au dépôt à terme : 'yes' / 'no'
POSITIVE_LABEL = 'yes'
LABELS = ['no', 'yes']

NUMERIC_FEATURES = [
    'age', 'campaign', 'pdays', 'previous',
    'emp.var.rate', 'cons.price.idx', 'cons.conf.idx', 'euribor3m', 'nr.employed',
]
CATEGORICAL_FEATURES = [
    'job', 'marital', 'education', 'default', 'housing', 'loan',
    'contact', 'month', 'day_of_week', 'poutcome',
]
FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES

# duration n'est connue qu'après l'appel (et y est alors connu) : fuite de données, exclue des features.
EXCLUDED_COLUMNS = ['duration']

SOURCE_COLUMNS = FEATURES + EXCLUDED_COLUMNS + [TARGET]
BATCH_COLUMNS = [ID_COLUMN, PERIOD_COLUMN] + SOURCE_COLUMNS

CATEGORY_DOMAINS = {
    'job': ['admin.', 'blue-collar', 'entrepreneur', 'housemaid', 'management', 'retired',
            'self-employed', 'services', 'student', 'technician', 'unemployed', 'unknown'],
    'marital': ['divorced', 'married', 'single', 'unknown'],
    'education': ['basic.4y', 'basic.6y', 'basic.9y', 'high.school', 'illiterate',
                  'professional.course', 'university.degree', 'unknown'],
    'default': ['no', 'yes', 'unknown'],
    'housing': ['no', 'yes', 'unknown'],
    'loan': ['no', 'yes', 'unknown'],
    'contact': ['cellular', 'telephone'],
    'month': ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'],
    'day_of_week': ['mon', 'tue', 'wed', 'thu', 'fri'],
    'poutcome': ['failure', 'nonexistent', 'success'],
}

# Bornes « physiques » du contrat (pas les min/max observés du jour).
NUMERIC_BOUNDS = {
    'age': (17, 100),
    'campaign': (1, 100),
    'pdays': (0, 999),
    'previous': (0, 50),
    'duration': (0, 10_000),
    'emp.var.rate': (-5.0, 5.0),
    'cons.price.idx': (85.0, 100.0),
    'cons.conf.idx': (-70.0, 0.0),
    'euribor3m': (0.0, 10.0),
    'nr.employed': (4500.0, 5500.0),
}
INTEGER_COLUMNS = ['age', 'campaign', 'pdays', 'previous', 'duration']

PDAYS_NEVER_CONTACTED = 999       # valeur sentinelle documentée par UCI : client jamais contacté
MONTHS = CATEGORY_DOMAINS['month']


class DataValidationError(Exception):
    """Données non conformes au contrat : bloque le prétraitement et l'entraînement."""


class RunDirectoryExistsError(Exception):
    """Le dossier de run existe déjà : refus pour ne jamais écraser un run publié."""


class NoPendingBatchError(Exception):
    """Aucun lot en attente dans la zone d'arrivée."""
