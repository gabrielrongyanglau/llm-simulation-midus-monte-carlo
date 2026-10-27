"""Demographic-only marginal quotas; no real joint profiles or outcomes."""
from __future__ import annotations
import numpy as np
import pandas as pd
from .study import json_records


def apportion(counts, size):
    keys=list(counts);weights=np.array([counts[k] for k in keys],dtype=float)
    target=size*weights/weights.sum();allocated=np.floor(target).astype(int)
    for i in np.argsort(-(target-allocated),kind='stable')[:size-int(allocated.sum())]:allocated[i]+=1
    return {k:int(n) for k,n in zip(keys,allocated)}


def demographic_profiles(humans,study,size,rng):
    columns={};audit={}
    for field in study['demographic_covariates']:
        source=humans[field];spec=study['variables'][field]
        if field=='B1PRAGE_2019':
            bins=study['age_quota_bins'];counts={str(i):int(source.between(a,b).sum()) for i,(a,b) in enumerate(bins)}
            counts['missing']=int(source.isna().sum())
            if sum(counts.values())!=len(source):raise ValueError('Age quota bins do not cover the source population.')
            allocated=apportion(counts,size)
            values=[]
            for key,n in allocated.items():
                if key=='missing':values.extend([np.nan]*n)
                else:
                    lo,hi=bins[int(key)];values.extend(rng.integers(lo,hi+1,size=n).tolist())
            audit[field]={'age_bins':bins,'source_counts':counts,'allocated_counts':allocated}
        else:
            counts={str(k):int(source.eq(k).sum()) for k in spec['categories']}
            counts['missing']=int(source.isna().sum());allocated=apportion(counts,size)
            values=[np.nan if key=='missing' else int(key) for key,n in allocated.items() for _ in range(n)]
            audit[field]={'source_counts':counts,'allocated_counts':allocated}
        rng.shuffle(values);columns[field]=values
    # Independent permutation preserves each marginal, without copying real joint rows.
    return pd.DataFrame(columns),audit
