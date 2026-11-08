"""Contrats HTTP (OpenAPI). Les entrées sont les features du modèle, sans la cible ni duration."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

EXAMPLE_CLIENT = {
    'client_ref': 'crm-000123', 'age': 41, 'job': 'technician', 'marital': 'married',
    'education': 'university.degree', 'default': 'no', 'housing': 'yes', 'loan': 'no', 'contact': 'cellular',
    'month': 'may', 'day_of_week': 'thu', 'campaign': 2, 'pdays': 999, 'previous': 0, 'poutcome': 'nonexistent',
    'emp_var_rate': -1.8, 'cons_price_idx': 92.893, 'cons_conf_idx': -46.2, 'euribor3m': 1.313,
    'nr_employed': 5099.1,
}
Category = Field(min_length=1, max_length=40)


class Client(BaseModel):
    """Un client à scorer. Les catégories inconnues du modèle sont acceptées (encodées comme rares)."""
    model_config = ConfigDict(extra='forbid', json_schema_extra={'example': EXAMPLE_CLIENT})

    client_ref: str | None = Field(None, max_length=64, description='Référence de traçabilité, pas une feature')
    age: int | None = Field(..., ge=17, le=100, description='Âge ; null accepté (imputé)')
    job: str = Category
    marital: str = Category
    education: str = Category
    default: str = Category
    housing: str = Category
    loan: str = Category
    contact: str = Category
    month: Literal['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
    day_of_week: Literal['mon', 'tue', 'wed', 'thu', 'fri']
    campaign: int = Field(..., ge=1, le=100, description='Contacts pendant la campagne en cours')
    pdays: int = Field(..., ge=0, le=999, description='Jours depuis le dernier contact ; 999 = jamais')
    previous: int = Field(..., ge=0, le=50)
    poutcome: str = Category
    emp_var_rate: float = Field(..., ge=-5, le=5)
    cons_price_idx: float = Field(..., ge=85, le=100)
    cons_conf_idx: float = Field(..., ge=-70, le=0)
    euribor3m: float = Field(..., ge=0, le=10)
    nr_employed: float = Field(..., ge=4500, le=5500)

    def features(self):
        data = self.model_dump(exclude={'client_ref'})
        return {
            'age': data['age'], 'campaign': data['campaign'], 'pdays': data['pdays'], 'previous': data['previous'],
            'emp.var.rate': data['emp_var_rate'], 'cons.price.idx': data['cons_price_idx'],
            'cons.conf.idx': data['cons_conf_idx'], 'euribor3m': data['euribor3m'],
            'nr.employed': data['nr_employed'], **{k: data[k] for k in (
                'job', 'marital', 'education', 'default', 'housing', 'loan', 'contact', 'month', 'day_of_week',
                'poutcome')},
        }


class PredictRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', json_schema_extra={'example': {'client': EXAMPLE_CLIENT}})
    client: Client


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', json_schema_extra={'example': {'clients': [EXAMPLE_CLIENT]}})
    clients: list[Client] = Field(..., min_length=1, max_length=100)


class ModelIdentity(BaseModel):
    registered_name: str
    version: int
    mlflow_run_id: str


class Prediction(BaseModel):
    prediction_id: UUID
    item_index: int
    client_ref: str | None
    probability: float = Field(description='Probabilité estimée de souscription')
    label: Literal['yes', 'no']
    decision_threshold: float


class PredictResponse(BaseModel):
    request_id: UUID
    model: ModelIdentity
    prediction: Prediction


class BatchResponse(BaseModel):
    request_id: UUID
    model: ModelIdentity
    predictions: list[Prediction]


class FeedbackRequest(BaseModel):
    """Un retour immuable par prédiction ; feedback_id stable fourni par le client pour ses retries."""
    model_config = ConfigDict(extra='forbid', json_schema_extra={'example': {
        'feedback_id': '7b0f6d5e-9a3e-4f1e-9c69-2d3a0e3f8b11', 'prediction_id': '00000000-0000-0000-0000-000000000000',
        'useful': True, 'observed_label': 'yes', 'observed_at': '2026-11-07T12:00:00Z', 'label_source': 'crm'}})

    feedback_id: UUID
    prediction_id: UUID
    useful: bool | None = None
    observed_label: Literal['yes', 'no'] | None = None
    observed_at: datetime | None = None
    label_source: str | None = Field(None, min_length=1, max_length=64)

    @model_validator(mode='after')
    def coherence(self):
        if self.useful is None and self.observed_label is None:
            raise ValueError('Fournir au moins useful ou observed_label')
        has_observation = self.observed_label is not None
        if has_observation and (self.observed_at is None or self.label_source is None):
            raise ValueError('observed_label exige observed_at et label_source')
        if not has_observation and (self.observed_at is not None or self.label_source is not None):
            raise ValueError('observed_at et label_source seulement avec observed_label')
        if self.observed_at is not None and self.observed_at.tzinfo is None:
            raise ValueError('observed_at doit porter un fuseau horaire')
        return self


class FeedbackResponse(BaseModel):
    request_id: UUID
    feedback_id: UUID
    status: Literal['created', 'already_recorded']


class ErrorResponse(BaseModel):
    request_id: UUID
    error_code: str
    message: str
