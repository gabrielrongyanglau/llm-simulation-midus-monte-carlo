"""Fresh within-replication linked pilots; pairwise summaries retain missingness."""
from __future__ import annotations
import numpy as np
from .study import OUTCOMES,predictors,json_records


def summarise_pilot(pilot,study):
    psychological=study['psychological_predictors'];numeric=psychological+list(OUTCOMES)
    distribution={}
    for field in numeric:
        values=pilot[field].dropna()
        distribution[field]={'n_valid':len(values),'n_missing':int(pilot[field].isna().sum()),
                             'mean':float(values.mean()) if len(values) else None,
                             'sd':float(values.std()) if len(values)>1 else None,
                             'minimum':float(values.min()) if len(values) else None,
                             'maximum':float(values.max()) if len(values) else None}
    relationships=[]
    # All predictor pairs, each predictor with each outcome, and the outcome pair.
    for i,first in enumerate(numeric):
        for second in numeric[i+1:]:
            pair=pilot[[first,second]].dropna();n=len(pair)
            covariance=correlation=None
            if n>1:
                covariance=float(pair.cov().iloc[0,1])
                if pair[first].std()>0 and pair[second].std()>0:correlation=float(pair.corr().iloc[0,1])
            relationships.append({'a':first,'b':second,'n':n,'covariance':covariance,'r':correlation})
    demos={}
    for field in study['demographic_covariates']:
        values=pilot[field]
        if study['variables'][field]['kind']=='category':
            demos[field]={'counts':{str(k):int(v) for k,v in values.value_counts().sort_index().items()},'n_missing':int(values.isna().sum())}
        else:
            demos[field]={'mean':float(values.mean()) if values.notna().any() else None,
                          'sd':float(values.std()) if values.notna().sum()>1 else None,'n_missing':int(values.isna().sum())}
    return {'pilot_n':len(pilot),'demographics':demos,'distributions':distribution,'pairwise_relationships':relationships,
            'missing_rule':'Pairwise valid observations; n shown separately. No imputation or new composite scores.'}
