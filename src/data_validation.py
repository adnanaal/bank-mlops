"""Barrière de validation Great Expectations : suites versionnées, exécution, rapport et blocage.

Les suites formalisent des prérequis (contrat), pas une description des données du jour.
Toute exécution qui n'aboutit pas à un succès explicite de toutes les suites requises lève
ValidationBlockedError : un rapport rouge, une erreur GX ou un résultat absent bloquent la chaîne.
"""
import hashlib
import json
import logging
import math
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.contract import (
    BATCH_COLUMNS,
    CATEGORY_DOMAINS,
    ID_COLUMN,
    INTEGER_COLUMNS,
    LABELS,
    NUMERIC_BOUNDS,
    PERIOD_COLUMN,
    POSITIVE_LABEL,
    TARGET,
    DataValidationError,
)

SUITE_VERSION = '1.0.0'
INT_TYPES = ['int64', 'int32', 'Int64', 'int']
NUMERIC_TYPES = INT_TYPES + ['float64', 'float32', 'Float64', 'float']
MAX_EXAMPLES = 5

logging.getLogger('great_expectations').setLevel(logging.ERROR)


class ValidationBlockedError(DataValidationError):
    """Données non conformes ou validation impossible : aucun prétraitement ni entraînement."""

    def __init__(self, message, report_path=None, report=None):
        super().__init__(message)
        self.report_path = report_path
        self.report = report


def _e(cls_name, family, rule, **kwargs):
    import great_expectations as gx
    return getattr(gx.expectations, cls_name)(meta={'famille': family, 'regle': rule}, **kwargs)


def _suite(name, description, expectations):
    import great_expectations as gx
    suite = gx.ExpectationSuite(name=name, meta={'version': SUITE_VERSION, 'description': description})
    for expectation in expectations:
        suite.add_expectation(expectation)
    return suite


def _record_expectations(id_unique=True):
    """Règles ligne à ligne communes aux lots et au snapshot."""
    e = _e
    rules = [
        e('ExpectTableColumnsToMatchSet', 'Structure', 'Colonnes du contrat présentes',
          column_set=BATCH_COLUMNS, exact_match=False),
        e('ExpectColumnValuesToNotBeNull', 'Identité', 'client_id présent', column=ID_COLUMN),
        e('ExpectColumnValuesToBeInTypeList', 'Identité', 'client_id entier', column=ID_COLUMN,
          type_list=INT_TYPES),
        e('ExpectColumnValuesToBeBetween', 'Identité', 'client_id strictement positif', column=ID_COLUMN,
          min_value=1),
        e('ExpectColumnValuesToMatchRegex', 'Types et formats', 'period au format AAAA-MM',
          column=PERIOD_COLUMN, regex=r'^\d{4}-(?:0[1-9]|1[0-2])$'),
        e('ExpectColumnValuesToNotBeNull', 'Cible', 'y présent', column=TARGET),
        e('ExpectColumnValuesToBeInSet', 'Cible', 'y vaut yes ou no', column=TARGET, value_set=LABELS),
    ]
    if id_unique:
        rules.append(e('ExpectColumnValuesToBeUnique', 'Identité', 'client_id unique', column=ID_COLUMN))
    for column, (low, high) in NUMERIC_BOUNDS.items():
        types = INT_TYPES if column in INTEGER_COLUMNS else NUMERIC_TYPES
        rules += [
            e('ExpectColumnValuesToNotBeNull', 'Valeurs numériques', f'{column} renseigné', column=column),
            e('ExpectColumnValuesToBeInTypeList', 'Valeurs numériques', f'{column} numérique (pas de texte)',
              column=column, type_list=types),
            e('ExpectColumnValuesToBeBetween', 'Valeurs numériques', f'{column} dans [{low}, {high}]',
              column=column, min_value=low, max_value=high),
        ]
    for column, domain in CATEGORY_DOMAINS.items():
        rules += [
            e('ExpectColumnValuesToNotBeNull', 'Catégories', f'{column} renseigné', column=column),
            e('ExpectColumnValuesToBeInSet', 'Catégories', f'{column} dans le domaine UCI', column=column,
              value_set=domain),
        ]
    return rules


def build_batch_suite(quality):
    rules = _record_expectations() + [
        _e('ExpectColumnUniqueValueCountToBeBetween', 'Volumétrie', 'Un lot = un seul mois',
           column=PERIOD_COLUMN, min_value=1, max_value=1),
        _e('ExpectTableRowCountToBeBetween', 'Volumétrie', f"Au moins {quality['min_batch_rows']} lignes",
           min_value=int(quality['min_batch_rows'])),
    ]
    return _suite('bank_lot', 'Lot mensuel entrant : contrat strict ligne à ligne', rules)


def build_batch_quality_suite(quality):
    rules = [
        _e('ExpectTableRowCountToEqual', 'Qualité du lot', "Une ligne d'audit", value=1),
        _e('ExpectColumnValuesToBeBetween', 'Qualité du lot',
           f"Doublons de contenu <= {quality['max_duplicate_share']:.0%}", column='duplicate_share',
           min_value=0, max_value=float(quality['max_duplicate_share'])),
        _e('ExpectColumnValuesToBeBetween', 'Fuite', 'Aucun client_id du corpus de qualification',
           column='qualification_overlap', min_value=0, max_value=0),
        _e('ExpectColumnValuesToBeBetween', 'Identité', 'Aucun client_id déjà présent dans le corpus',
           column='already_known', min_value=0, max_value=0),
    ]
    for column, limit in quality['max_unknown_share'].items():
        rules.append(_e('ExpectColumnValuesToBeBetween', 'Qualité du lot',
                        f'Part de "unknown" pour {column} <= {limit:.0%}', column=f'unknown_share_{column}',
                        min_value=0, max_value=float(limit)))
    return _suite('bank_lot_qualite', "Indicateurs de qualité calculés sur le lot (table d'audit)", rules)


def build_snapshot_suite(quality):
    return _suite('bank_snapshot', "Snapshot d'entraînement complet (corpus initial + lots acceptés)",
                  _record_expectations())


def build_aptitude_suite(quality):
    minimum = int(quality['min_positive_in_snapshot'])
    return _suite('bank_aptitude', "Aptitude à entraîner (table d'audit du snapshot)", [
        _e('ExpectTableRowCountToEqual', 'Aptitude', "Une ligne d'audit", value=1),
        _e('ExpectColumnValuesToBeBetween', 'Aptitude', f'Au moins {minimum} souscripteurs',
           column='n_positive', min_value=minimum),
        _e('ExpectColumnValuesToBeBetween', 'Aptitude', f'Au moins {minimum} non-souscripteurs',
           column='n_negative', min_value=minimum),
        _e('ExpectColumnValuesToBeBetween', 'Aptitude', 'Au moins 5 souscripteurs dans le test stratifié',
           column='n_test_positive', min_value=5),
    ])


SUITE_BUILDERS = {
    'bank_lot': build_batch_suite,
    'bank_lot_qualite': build_batch_quality_suite,
    'bank_snapshot': build_snapshot_suite,
    'bank_aptitude': build_aptitude_suite,
}


# --- Tables d'audit : calculées sur une copie, jamais transmises au prétraitement ---

def batch_quality_table(batch, quality, known_ids, qualification_ids):
    ids = pd.to_numeric(batch.get(ID_COLUMN), errors='coerce')
    content = batch.drop(columns=[ID_COLUMN], errors='ignore')
    row = {
        'rows': int(len(batch)),
        'duplicate_share': float(content.duplicated().mean()) if len(batch) else 0.0,
        'qualification_overlap': int(ids.isin(qualification_ids).sum()),
        'already_known': int(ids.isin(known_ids).sum()),
    }
    for column in quality['max_unknown_share']:
        values = batch[column] if column in batch else pd.Series(dtype=object)
        row[f'unknown_share_{column}'] = float((values == 'unknown').mean()) if len(values) else 1.0
    return pd.DataFrame([row])


def aptitude_table(snapshot, test_size):
    target = snapshot[TARGET] if TARGET in snapshot else pd.Series(dtype=object)
    n_pos = int((target == POSITIVE_LABEL).sum())
    return pd.DataFrame([{
        'rows': int(len(snapshot)), 'n_positive': n_pos, 'n_negative': int((target != POSITIVE_LABEL).sum()),
        'n_test_positive': int(math.floor(n_pos * float(test_size))),
    }])


def frame_sha256(frame):
    return hashlib.sha256(to_csv_bytes(frame)).hexdigest()


def to_csv_bytes(frame):
    return frame.to_csv(index=False, lineterminator='\n').encode('utf-8')


# --- Exécution GX ---

class _GxRunner:
    """Data Context éphémère, source Pandas, un asset et une Batch Definition par suite."""

    def __init__(self, quality):
        import great_expectations as gx
        self.gx = gx
        self.quality = quality
        self.context = gx.get_context(mode='ephemeral')
        try:
            from great_expectations.data_context.types.base import ProgressBarsConfig
            self.context.variables.progress_bars = ProgressBarsConfig(globally=False)
        except Exception:  # noqa: BLE001 - option cosmétique
            pass
        self.source = self.context.data_sources.add_pandas('donnees_en_memoire')
        self.definitions = {}

    def definition(self, suite_name):
        if suite_name not in self.definitions:
            suite = self.context.suites.add(SUITE_BUILDERS[suite_name](self.quality))
            asset = self.source.add_dataframe_asset(name=f'asset_{suite_name}')
            batch = asset.add_batch_definition_whole_dataframe(f'batch_{suite_name}')
            self.definitions[suite_name] = self.context.validation_definitions.add(
                self.gx.ValidationDefinition(name=f'validation_{suite_name}', data=batch, suite=suite))
        return self.definitions[suite_name]

    def run(self, suite_name, frame):
        result_format = {'result_format': 'COMPLETE', 'partial_unexpected_count': MAX_EXAMPLES}
        if ID_COLUMN in frame.columns:
            result_format['unexpected_index_column_names'] = [ID_COLUMN]
        return self.definition(suite_name).run(batch_parameters={'dataframe': frame},
                                               result_format=result_format)


_RUNNERS = {}


def get_runner(quality):
    key = json.dumps(quality, sort_keys=True)
    if key not in _RUNNERS:
        _RUNNERS[key] = _GxRunner(quality)
    return _RUNNERS[key]


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, 'item'):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value if isinstance(value, (str, int, float, bool)) or value is None else str(value)


def summarize_result(result):
    failed, not_evaluated = [], []
    for item in result.results:
        if item.success:
            continue
        config = item.expectation_config
        exception = item.exception_info or {}
        raised = exception.get('raised_exception') or any(
            isinstance(v, dict) and v.get('raised_exception') for v in exception.values())
        entry = {'expectation': config.type, 'column': config.kwargs.get('column'),
                 'regle': (config.meta or {}).get('regle'), 'famille': (config.meta or {}).get('famille')}
        res = item.result or {}
        if 'unexpected_count' in res:
            entry['unexpected_count'] = res.get('unexpected_count')
            entry['examples'] = _jsonable(res.get('partial_unexpected_list', [])[:MAX_EXAMPLES])
            ids = [row.get(ID_COLUMN) for row in res.get('unexpected_index_list') or []
                   if isinstance(row, dict) and ID_COLUMN in row]
            entry['client_ids'] = _jsonable(list(dict.fromkeys(ids))[:MAX_EXAMPLES])
        if 'observed_value' in res:
            entry['observed_value'] = _jsonable(res['observed_value'])
        if raised:
            entry['erreur_evaluation'] = re.sub(r'\s+', ' ', str(_first_message(exception)))[:300]
            not_evaluated.append(entry)
        else:
            failed.append(entry)
    return failed, not_evaluated


def _first_message(exception):
    if exception.get('exception_message'):
        return exception['exception_message']
    for value in exception.values():
        if isinstance(value, dict) and value.get('exception_message'):
            return value['exception_message']
    return 'exception GX'


def run_plan(plan, quality, runner=None):
    """Exécuter chaque suite requise ; une erreur ou un résultat absent compte comme un échec."""
    entries = []
    setup_error = None
    try:
        runner = runner or get_runner(quality)
    except Exception as exc:  # noqa: BLE001 - toute erreur GX est bloquante
        runner, setup_error = None, f'{type(exc).__name__}: {exc}'
    for suite_name, scope, frame in plan:
        entry = {'suite': suite_name, 'version': SUITE_VERSION, 'scope': scope, 'rows': int(len(frame)),
                 'success': False, 'failed': [], 'not_evaluated': [], 'error': None}
        if runner is None:
            entry['error'] = f'Contexte GX indisponible : {setup_error}'
            entries.append(entry)
            continue
        try:
            result = runner.run(suite_name, frame)
        except Exception as exc:  # noqa: BLE001 - jamais un passage implicite
            entry['error'] = f'{type(exc).__name__}: {exc}'
            entries.append(entry)
            continue
        success = getattr(result, 'success', None)
        if not isinstance(success, bool) or not hasattr(result, 'results'):
            entry['error'] = 'Résultat GX absent ou invalide'
            entries.append(entry)
            continue
        entry['failed'], entry['not_evaluated'] = summarize_result(result)
        entry['statistics'] = _jsonable(dict(getattr(result, 'statistics', {}) or {}))
        # Examen explicite : seul un succès True de la suite autorise la suite du traitement.
        entry['success'] = success is True and not entry['failed'] and not entry['not_evaluated']
        entries.append(entry)
    return entries


def validate(plan, *, stage, source, snapshot_bytes, diagnostics_dir, quality, extra=None, runner=None):
    """Valider toutes les suites du plan, écrire le rapport et lever en cas d'échec.

    plan : liste (nom de suite, périmètre, DataFrame de contrôle). Retourne (chemin du rapport, rapport).
    """
    created = datetime.now(timezone.utc)
    snapshot_sha = hashlib.sha256(snapshot_bytes).hexdigest()
    entries = run_plan(plan, quality, runner=runner)
    success = bool(entries) and all(entry['success'] for entry in entries)
    report = {
        'created_at': created.isoformat(), 'stage': stage, 'source': source,
        'data_sha256': snapshot_sha, 'success': success, 'suites': entries, **(extra or {}),
        'avertissement': "Rapport de validation des données : ce n'est pas un entraînement.",
    }
    folder = Path(diagnostics_dir) / 'validation'
    folder.mkdir(parents=True, exist_ok=True)
    stem = f'{created:%Y%m%dT%H%M%S}_{stage}_{snapshot_sha[:12]}_{uuid.uuid4().hex[:6]}'
    if not success:
        rejected = Path(diagnostics_dir) / 'rejected'
        rejected.mkdir(parents=True, exist_ok=True)
        rejected_path = rejected / f'{stem}.csv'
        rejected_path.write_bytes(snapshot_bytes)
        report['rejected_data'] = str(rejected_path)
    report_path = folder / f"{stem}_{'ok' if success else 'rejet'}.json"
    report_path.write_text(json.dumps(_jsonable(report), ensure_ascii=False, indent=2), encoding='utf-8')
    if not success:
        raise ValidationBlockedError(_message(report, report_path), report_path=report_path, report=report)
    return report_path, report


def _message(report, report_path):
    parts = []
    for entry in report['suites']:
        if entry['success']:
            continue
        if entry['error']:
            parts.append(f"{entry['suite']} : erreur {entry['error']}")
        for item in entry['failed'] + entry['not_evaluated']:
            detail = item.get('examples', item.get('observed_value'))
            parts.append(f"{entry['suite']} : {item['regle']} (exemples {detail}, "
                         f"client_ids {item.get('client_ids', [])})")
    return (f"Validation GX en échec ({report['stage']}, source {report['source']}) ; "
            f"rapport : {report_path}\n- " + '\n- '.join(parts[:12]))


def export_suites(folder, quality):
    """Écrire les suites en JSON versionné (preuve des règles appliquées)."""
    get_runner(quality)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, builder in SUITE_BUILDERS.items():
        payload = builder(quality).to_json_dict()
        payload.pop('id', None)
        for expectation in payload['expectations']:
            expectation.pop('id', None)
        path = folder / f'{name}.v{SUITE_VERSION}.json'
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        paths.append(path)
    return paths
