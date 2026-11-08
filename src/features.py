"""Transformations déterministes, intégrées au Pipeline sérialisé (identiques à l'entraînement et à l'API)."""
import numpy as np
import pandas as pd

from src.contract import CATEGORICAL_FEATURES, FEATURES, NUMERIC_FEATURES, PDAYS_NEVER_CONTACTED


def add_derived_features(frame):
    """pdays = 999 signifie « jamais contacté » : indicateur explicite + valeur manquante (imputée ensuite)."""
    out = pd.DataFrame(frame, copy=True)[FEATURES]
    pdays = pd.to_numeric(out['pdays'], errors='coerce')
    out['never_contacted'] = (pdays == PDAYS_NEVER_CONTACTED).astype(float)
    out['pdays'] = pdays.where(pdays != PDAYS_NEVER_CONTACTED, np.nan)
    for column in NUMERIC_FEATURES:
        out[column] = pd.to_numeric(out[column], errors='coerce').astype(float)
    for column in CATEGORICAL_FEATURES:
        out[column] = out[column].astype(object)
    return out


DERIVED_NUMERIC = NUMERIC_FEATURES + ['never_contacted']
