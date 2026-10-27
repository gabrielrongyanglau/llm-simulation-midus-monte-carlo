"""Neutral model-facing labels; source column names stay in local output CSVs."""
import re

def field_map(study):
    return {**{f:f'psychological_{i:02d}_wave1' for i,f in enumerate(study['psychological_predictors'],1)},
            **{f:f'demographic_{i:02d}_wave1' for i,f in enumerate(study['demographic_covariates'],1)},
            'life_satisfaction_MIDUS2':'life_satisfaction_wave1','life_satisfaction_MIDUS3':'life_satisfaction_wave2'}

def neutral_text(text):
    text=re.sub(r'\s*\([^)]*(?:MIDUS|Parallel M1|M1 items)[^)]*\)', '', text, flags=re.I)
    text=text.replace('AF versions use the MIDUS 1 parallel items; PA versions use the newer PANAS items.','AF versions use the earlier item set; PA versions use the newer PANAS items.')
    for a,b in [('MIDUS 2','Wave 1'),('MIDUS 3','Wave 2'),('MIDUS 1','an earlier assessment')]:text=text.replace(a,b)
    return re.sub('MIDUS','the study',text,flags=re.I)

def to_model(value,study):
    mapping=field_map(study)
    if isinstance(value,dict):return {mapping.get(k,k):to_model(v,study) for k,v in value.items()}
    if isinstance(value,list):return [to_model(v,study) for v in value]
    if isinstance(value,str):return mapping.get(value,neutral_text(value))
    return value

def from_model_records(records,study):
    reverse={v:k for k,v in field_map(study).items()}
    for row in records:
        if set(row)!={'participant_id',*reverse}:raise ValueError('Missing or extra model-facing output fields.')
    return [{reverse.get(k,k):v for k,v in row.items()} for row in records]
