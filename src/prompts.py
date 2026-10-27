from __future__ import annotations
import json,re
from .study import selected_fields
from .labels import field_map,to_model,neutral_text
from .utils import PROJECT_ROOT

def compact(value):return json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))

def describe(study,fields):
    names=field_map(study);lines=[]
    for name in fields:
        spec=study['variables'][name]
        allowed='Categories: '+compact(spec['categories']) if spec['kind']=='category' else f"Valid range {spec['minimum']} to {spec['maximum']} inclusive; {'integer' if spec['kind']=='integer' else 'decimal scores permitted'}"
        lines.append(f"{names[name]}: {neutral_text(spec['label'])}. {neutral_text(spec['scoring'])} {allowed}. Missing codes {spec['missing_codes']} are not valid scores; represent them as null.")
    return '\n'.join(lines)

def build_prompt(study,method,ids,fixed=None,pilot=None,summary=None,variant=None):
    if method not in range(1,9):raise ValueError('Unsupported method.')
    names=field_map(study);outcomes=[]
    for output,source in study['outcomes'].items():
        spec=study['variables'][source]
        outcomes.append(f"{names[output]}: {neutral_text(spec['scoring'])} Range 0 to 10. Both outcomes are required and cannot be null. They refer to the same individual.")
    specs=[
        'Eligibility: participants in a US longitudinal adult cohort who completed the phone interview and full self-administered questionnaires at both waves. The baseline cohort was recruited through random-digit dialling, twin and sibling recruitment and city oversamples; it is not a new cross-sectional sample of current US adults. Both selected life-satisfaction outcomes must be valid. Missing individual predictors do not exclude an otherwise eligible participant. Do not assume complete item response, employment or partnership.',
        describe(study,study['psychological_predictors']),describe(study,study['demographic_covariates']),'\n'.join(outcomes),
        'Wave 1 was fielded in 2004-2006; Wave 2 in 2013-2014, approximately nine years later. Wave 2 includes later refielding completions, so this is not an exact individual interval. Both outcomes are current ratings at their own wave. Psychological scores and demographics refer only to Wave 1. No participant dates or calculated individual wave intervals are supplied.',
        'Numerical precision: use at most four decimal places for every newly generated numerical value, with no unnecessary trailing zeros. Return compact JSON. Copy fixed supplied values exactly, retaining their original precision and nulls; the four-decimal rule applies only to generated values. Do not round, modify or impute fixed input values.',
        'Keep all required keys. Use JSON null for missing or not-applicable predictors and demographics; apply the missing codes in each field definition. Do not impute fixed null values. Do not substitute zero for missingness. Partnership and work measures can be inapplicable. Existing source composites may be fractional because of available-item scoring; do not force composite scores to integers.']
    schema=f'Return {{"participants":[...]}}, exactly {len(ids)} rows. Each row has these fields in this order: {compact(["participant_id"]+[names[f] for f in selected_fields(study)])}. participant_id is an integer; use identifiers in this exact order: {compact(ids)}. Predictors are numbers or null; categories use exact integer codes or null. Both outcomes are numeric 0-10. No additional fields, source IDs or narrative.'
    replacements={'N':str(len(ids)),'STUDY_SPECIFICATIONS':'\n'.join(specs),'PARTICIPANT_IDS':compact(ids),'OUTPUT_SCHEMA':schema,
        'Wave_1_demographic_profiles':compact(to_model(fixed or [],study)),
        'Wave_1_actual_demographic_profiles':compact(to_model(fixed or [],study)),
        'Wave_1_predictor_profiles':compact(to_model(fixed or [],study)),
        'pilot_data':compact(to_model(pilot or [],study)),'calibration_summary':compact(to_model(summary or {},study))}
    bank=json.loads((PROJECT_ROOT/'prompts/methods.json').read_text(encoding='utf-8'))
    matches=[p for p in bank['prompts'] if p['method']==method]
    if len(matches)!=1:raise ValueError('Expected exactly one prompt per method.')
    user=matches[0]['user']
    for k,v in replacements.items():user=user.replace('{'+k+'}',v)
    if re.findall(r'\{[A-Za-z_][A-Za-z_0-9]*\}',user):raise ValueError('Unresolved prompt placeholders.')
    return bank['system'],user
