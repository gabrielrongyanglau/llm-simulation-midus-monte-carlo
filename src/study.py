from __future__ import annotations

import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
from .utils import load_yaml, resolve_path, stable_hash

METHODS = tuple(range(1, 9))
OUTCOMES = ('life_satisfaction_MIDUS2', 'life_satisfaction_MIDUS3')


def read_study(config):
    study = load_yaml(resolve_path(config, config['paths']['study']))
    fields = study['psychological_predictors'] + study['demographic_covariates']
    if len(fields) != len(set(fields)):
        raise ValueError('Predictor fields must be unique.')
    if not study['psychological_predictors'] or tuple(study['outcomes']) != OUTCOMES:
        raise ValueError('Select baseline psychological predictors and the two named outcomes.')
    if any(not f.startswith('B1') for f in fields):
        raise ValueError('Only MIDUS 2 predictors and demographic covariates are permitted.')
    if set(fields) & set(study['outcomes'].values()):
        raise ValueError('An outcome cannot also be a predictor.')
    if any(f in fields for f in ('B1SQ1','B1SSATIS','B1SSATIS2')):
        raise ValueError('MIDUS 2 life satisfaction is an outcome, never an added predictor.')
    for name in fields + list(study['outcomes'].values()):
        if name not in study['variables']:
            raise ValueError(f'No verified measure definition for {name}.')
    if not study['outcomes'][OUTCOMES[0]].startswith('B1') or not study['outcomes'][OUTCOMES[1]].startswith('C1'):
        raise ValueError('Outcome source waves must match their output labels.')
    pairs = [('B1SSATIS','C1SSATIS'),('B1SSATIS2','C1SSATIS2'),('B1SQ1','C1SQ1')]
    if tuple(study['outcomes'].values()) not in pairs:
        raise ValueError('Use matching life-satisfaction versions across the two waves.')
    if not 0 < study['calibration_fraction'] < 1:
        raise ValueError('Calibration fraction must lie strictly between zero and one.')
    return study


def validate_config(config):
    if config.get('variant') != 'representative' or config['replications'] != 1000:
        raise ValueError('This package requires one representative prompt and 1,000 replications per method.')
    methods = config['methods']
    if not methods or len(methods) != len(set(methods)) or any(m not in METHODS for m in methods):
        raise ValueError('Supported methods are 1 through 8.')
    for key in ('replications','participants_per_replication','pilot_size','batch_size',
                'max_attempts','max_output_tokens','batch_requests','max_active_batches','poll_seconds','max_batch_input_bytes','max_queued_input_tokens','request_timeout_seconds'):
        if isinstance(config[key],bool) or not isinstance(config[key],int) or config[key] <= 0:
            raise ValueError(f'{key} must be a positive integer.')

    if config['model']=='gpt-5.6-luna' and config['max_output_tokens']>128000:raise ValueError('GPT-5.6 Luna supports up to 128,000 output tokens.')
    if config['max_attempts'] != 3:raise ValueError('This protocol uses three initial attempts per job.')
    if config['max_batch_input_bytes'] > 180000000:raise ValueError('Batch input byte cap must not exceed 180 MB.')


def predictors(study):
    return study['psychological_predictors'] + study['demographic_covariates']


def selected_fields(study):
    return predictors(study) + list(OUTCOMES)


def json_records(frame):
    # Preserve round-trip float precision rather than pandas.to_json's 15-digit limit.
    rows=frame.astype(object).where(frame.notna(),None).to_dict(orient='records')
    return json.loads(json.dumps(rows,allow_nan=False,default=lambda v:v.item()))


def valid_mask(series, spec):
    valid = series.notna() & ~series.isin(spec['missing_codes'])
    if spec['kind'] == 'category':
        return valid & series.isin([int(x) for x in spec['categories']])
    valid &= series.between(spec['minimum'], spec['maximum'])
    if spec['kind'] == 'integer':
        valid &= series.mod(1).eq(0)
    return valid


def load_humans(config, study):
    source = resolve_path(config, config['paths']['midus_data'])
    original = pd.read_csv(source,float_precision='round_trip')
    required = [study['id_column'], study['family_column'],'B1STATUS','C1STATUS'] + predictors(study) + list(study['outcomes'].values())
    absent = sorted(set(required) - set(original.columns))
    if absent:
        raise ValueError(f'Missing source fields: {absent}')
    if original[study['id_column']].isna().any() or original[study['id_column']].duplicated().any():
        raise ValueError('The source must contain one row per non-missing M2ID.')
    if not (original['B1STATUS'].eq(2) & original['C1STATUS'].isin([4,5,6])).all():
        raise ValueError('The source contains records outside the full phone/SAQ linked cohort.')
    working = original[[study['id_column'],study['family_column']] + predictors(study)].copy()
    counts = {}
    for field in predictors(study) + list(study['outcomes'].values()):
        values = pd.to_numeric(original[field], errors='raise')
        spec = study['variables'][field]
        valid = valid_mask(values, spec)
        # Undeclared invalid values are a setup error, not silently discarded.
        bad = values.notna() & ~values.isin(spec['missing_codes']) & ~valid
        if bad.any():
            raise ValueError(f'Undeclared invalid values in {field}: {sorted(values[bad].unique())[:10]}')
        counts[field] = int((~valid).sum())
        if field in predictors(study): working[field] = values.where(valid)
    for output, field in study['outcomes'].items():
        working[output] = original[field].where(valid_mask(original[field],study['variables'][field]))
    # Valid outcomes are required, but no listwise exclusion on all predictors.
    working = working.dropna(subset=list(OUTCOMES)).reset_index(drop=True)
    if working.empty: raise ValueError('No linked participants have both selected outcomes.')
    fingerprint = stable_hash({'csv_sha256':__import__('hashlib').sha256(source.read_bytes()).hexdigest(),'study':study})
    qc = {'source_rows':len(original),'source_columns':len(original.columns),'eligible_outcome_rows':len(working),
          'excluded_for_selected_outcomes':len(original)-len(working),'missing_by_selected_source_field':counts,'fingerprint':fingerprint}
    return working, qc


def split_pools(config, study, humans):
    rng = np.random.default_rng(config['random_seed'])
    ids = rng.permutation(len(humans))
    n = int(round(len(humans)*study['calibration_fraction']))
    calibration = humans.iloc[ids[:n]].reset_index(drop=True)
    evaluation = humans.iloc[ids[n:]].reset_index(drop=True)
    if config['pilot_size'] > len(calibration): raise ValueError('Pilot size exceeds the calibration pool.')
    if set(config['methods']) & {7,8} and config['participants_per_replication'] > len(evaluation):
        raise ValueError('M7/M8 run size exceeds the evaluation pool (sampling is without replacement).')
    return calibration, evaluation


def persist_pools(config, study, humans, qc):
    calibration, evaluation = split_pools(config,study,humans)
    root = resolve_path(config,config['paths']['pool_output']);root.mkdir(parents=True,exist_ok=True)
    membership = pd.concat([calibration[[study['id_column'],study['family_column']]].assign(pool='calibration'),
                            evaluation[[study['id_column'],study['family_column']]].assign(pool='evaluation')],ignore_index=True)
    manifest = {'source':qc,'random_seed':config['random_seed'],'calibration_n':len(calibration),'evaluation_n':len(evaluation),
                'membership_hash':stable_hash(json_records(membership))}
    path = root/'manifest.json'
    if path.exists() and json.loads(path.read_text(encoding='utf-8')) != manifest:
        raise ValueError('Pool provenance changed. Use a new output directory for a new study/configuration.')
    from .collection import atomic
    atomic(path,json.dumps(manifest,indent=2))
    atomic(root/'membership.csv',membership.to_csv(index=False))
    return calibration, evaluation


def replication_rng(config, method, replication):
    return np.random.default_rng(np.random.SeedSequence([config['random_seed'],method,replication]))


def write_json(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if stable_hash(json.loads(path.read_text(encoding='utf-8'))) != stable_hash(value):
            raise ValueError(f'Existing saved data differ: {path}. Use a new output folder.')
        return
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    temp.replace(path)
