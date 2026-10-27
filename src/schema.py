from __future__ import annotations
from typing import Literal
import math
from decimal import Decimal, ROUND_HALF_EVEN
from .labels import field_map, neutral_text
from pydantic import BaseModel, ConfigDict, Field, create_model
from .study import OUTCOMES, predictors, selected_fields


def make_schema(study, model_facing=True):
    names=field_map(study) if model_facing else {}
    fields = {'participant_id':(int, Field(...,ge=1,strict=True))}
    for field in selected_fields(study):
        source=study['outcomes'].get(field,field);spec=study['variables'][source]
        annotation = Literal[tuple(spec['categories'])] if spec['kind']=='category' else int if spec['kind']=='integer' else float
        nullable = field not in OUTCOMES
        if nullable: annotation = annotation | None
        kwargs={'description':neutral_text(spec['label'])}
        if spec['kind'] != 'category':kwargs.update(ge=spec['minimum'],le=spec['maximum'])
        # Categories are checked exactly after parsing, including rejection of Boolean values.
        fields[names.get(field,field)]=(annotation,Field(...,**kwargs))
    record=create_model('Participant',__config__=ConfigDict(extra='forbid'),**fields)
    return create_model('ParticipantBatch',__config__=ConfigDict(extra='forbid'),participants=(list[record],...))


def validate_records(records, study, ids, fixed=None, pilot=None, enforce_precision=True):
    if len(records) != len(ids):raise ValueError('Incorrect participant count.')
    expected_fields={'participant_id',*selected_fields(study)}
    # Validate raw JSON before Pydantic can coerce strings, bools or fractional integers.
    for record,pid in zip(records,ids):
        if set(record)!=expected_fields:raise ValueError('Missing or additional output fields.')
        if type(record['participant_id']) is not int or record['participant_id']!=pid:
            raise ValueError('Participant identifiers were changed, duplicated or reordered.')
        for name in selected_fields(study):
            value=record[name];spec=study['variables'][study['outcomes'].get(name,name)]
            if value is None:
                if name in OUTCOMES:raise ValueError('Life satisfaction cannot be missing.')
                continue
            if type(value) in (int,float) and not math.isfinite(value):raise ValueError('Non-finite score.')
            if type(value) not in (int,float):raise ValueError(f'{name} must be numeric or null.')
            if value in spec['missing_codes']:raise ValueError(f'{name}: source missing code must be null.')
            if spec['kind'] in ('integer','category') and int(value)!=value:raise ValueError(f'{name} must be an integer.')
            if spec['kind']=='category' and int(value) not in spec['categories']:
                raise ValueError(f'{name}: invalid source category.')
    if enforce_precision:
        for index,record in enumerate(records):
            fixed_fields=set(fixed[index]) if fixed is not None else set()
            for name,value in record.items():
                if name not in fixed_fields and type(value) is float:
                    dec=Decimal(str(value))
                    if dec != dec.quantize(Decimal('0.0001')):
                        raise ValueError(f'{name}: generated values must have at most four decimal places.')
    make_schema(study,model_facing=False).model_validate({'participants':records})
    if fixed is not None:
        for record,profile in zip(records,fixed):
            if any(record.get(field)!=value for field,value in profile.items()):
                raise ValueError('A fixed demographic or predictor value was altered.')
    if pilot is not None:
        # Detect complete pilot copies, including tiny float-rounding changes.
        fields=predictors(study)+list(OUTCOMES)
        def matches(a,b):
            return all((a[f] is None and b[f] is None) or
                       (a[f] is not None and b[f] is not None and
                        math.isclose(a[f],b[f],rel_tol=0,abs_tol=0.000050001)) for f in fields)
        if any(matches(row,reference) for row in records for reference in pilot):
            raise ValueError('M5/M6 reproduced a complete pilot record.')
    return records


def normalise_generated_precision(records,study,ids,fixed=None,pilot=None):
    """Validate raw values first; round only generated values; validate again."""
    validate_records(records,study,ids,fixed,pilot,enforce_precision=False)
    result=[]
    for index,record in enumerate(records):
        fixed_fields=set(fixed[index]) if fixed is not None else set()
        row=dict(record)
        for field,value in row.items():
            if field not in fixed_fields and type(value) is float:
                row[field]=float(Decimal(str(value)).quantize(Decimal('0.0001'),rounding=ROUND_HALF_EVEN))
        result.append(row)
    return validate_records(result,study,ids,fixed,pilot)
