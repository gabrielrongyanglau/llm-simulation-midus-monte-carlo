from __future__ import annotations
from .study import OUTCOMES,predictors,json_records,replication_rng,write_json
from .quotas import demographic_profiles
from .random_pilots import summarise_pilot
from .utils import resolve_path

METHOD_NAMES={1:'Persona-prompted synthetic sampling',2:'Ungrounded random silicon sampling',3:'Population-guided silicon sampling',4:'Quota-matched population silicon sampling',5:'Small-pilot-calibrated silicon sampling',6:'Small-pilot-analysis-guided silicon sampling',7:'Demographic and Psychological Predictors microdata-anchored silicon sampling',8:'Demographic-only microdata-anchored silicon sampling'}

def prepare_replication(config,study,method,replication,humans,calibration,evaluation,persist=False):
    # Separate method-specific random streams; fresh samples in every repetition.
    rng=replication_rng(config,method,replication);n=config['participants_per_replication']
    context={'fixed':None,'pilot':None,'summary':None,'truth':None,'quota_audit':None}
    if method==4:
        profiles,audit=demographic_profiles(humans,study,n,rng)
        context.update(fixed=[{'participant_id':i+1,**r} for i,r in enumerate(json_records(profiles))],quota_audit=audit)
        if persist:write_json(resolve_path(config,config['paths']['anchor_output'])/'method_4'/f'rep_{replication:04d}.json',{'fixed_profiles':context['fixed'],'quota_audit':audit})
    elif method in (5,6):
        chosen=calibration.iloc[rng.choice(len(calibration),size=config['pilot_size'],replace=False)]
        context['source_ids']=chosen[study['id_column']].astype(int).tolist()
        context['pilot']=json_records(chosen[predictors(study)+list(OUTCOMES)])
        if method==5:context['summary']=summarise_pilot(chosen,study)
        if persist:write_json(resolve_path(config,config['paths']['pilot_audit_output'])/f'method_{method}'/f'rep_{replication:04d}.json',{'source_ids':chosen[study['id_column']].astype(int).tolist(),**context})
    elif method in (7,8):
        chosen=evaluation.iloc[rng.choice(len(evaluation),size=n,replace=False)]
        context['source_ids']=chosen[study['id_column']].astype(int).tolist()
        fields=predictors(study) if method==7 else study['demographic_covariates']
        context['fixed']=[{'participant_id':i+1,**r} for i,r in enumerate(json_records(chosen[fields]))]
        context['truth']=[{'participant_id':i+1,**r} for i,r in enumerate(json_records(chosen[[study['id_column'],study['family_column']]+list(OUTCOMES)]))]
        if persist:write_json(resolve_path(config,config['paths']['anchor_output'])/f'method_{method}'/f'rep_{replication:04d}.json',{'fixed_profiles':context['fixed'],'withheld_truth':context['truth']})
    elif method not in (1,2,3):raise ValueError('Unsupported method.')
    return context
