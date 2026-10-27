"""Collection only. Atomic files, durable SQLite ledger, recoverable remote Batch jobs."""
from __future__ import annotations
import argparse,copy,csv,hashlib,io,json,math,os,re,sqlite3,sys,time,uuid
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from .utils import PROJECT_ROOT,load_config,load_env_file,stable_hash
from .study import read_study,validate_config,load_humans,split_pools,persist_pools,selected_fields,predictors
from .methods import prepare_replication
from .prompts import build_prompt
from .labels import field_map
from .schema import make_schema,normalise_generated_precision

DIRECTIVE=('\n\nCOLLECTION OUTPUT RULES: Use short named JSON fields as defined in the study specification. '
 'Return participant_id and only the unfixed variables required by the response schema. '
 'The software attaches original fixed values locally; never repeat, round, change or impute those fixed inputs. '
 'Return exactly 100 complete participant objects in ID order 1 through 100, with at most four decimal places '
 'for every generated number. Use compact JSON without commentary. '
 'Generate distinct complete profiles. For methods generating psychological variables, also ensure distinct '
 'combinations of baseline psychological and demographic variables after rounding; ignore participant_id. '
 'Do not recycle blocks or disguise duplicate profiles with superficial tiny changes. '
 'Retain all original method-specific information restrictions and statistical-generation instructions.')
OPERATIONAL={'batch_requests','max_active_batches','poll_seconds','max_batch_input_bytes','max_queued_input_tokens','request_timeout_seconds'}
TERMINAL={'completed','failed','expired','cancelled'}


def dump(v):return json.dumps(v,ensure_ascii=False,allow_nan=False,separators=(',',':'))
def atomic(path,data):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_name(path.name+'.tmp')
 with tmp.open('wb') as h:h.write(data.encode() if isinstance(data,str) else data);h.flush();os.fsync(h.fileno())
 os.replace(tmp,path)
def save(path,v):atomic(path,dump(v))
def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def retry_read(fn,*args,**kwargs):
 for attempt in range(5):
  try:return fn(*args,**kwargs)
  except Exception as exc:
   code=getattr(exc,'status_code',None)
   transient=code in (408,409,429,500,502,503,504) or type(exc).__name__ in ('APIConnectionError','APITimeoutError','ConnectionError','TimeoutError')
   if not transient or attempt==4:raise
   delay=min(60,5*2**attempt);print(f'Temporary read/download error; retrying in {delay}s.',flush=True);time.sleep(delay)

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

@contextmanager
def lock(root):
 # OS advisory lock releases automatically after crashes; the file itself may remain.
 f=(root/'collector.lock').open('a+b');f.seek(0);f.write(b'0');f.flush();f.seek(0)
 try:
  if os.name=='nt':
   import msvcrt;msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
  else:
   import fcntl;fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
 except OSError:
  f.close();raise RuntimeError('Another collector is running in this output folder.')
 try:yield
 finally:f.close()

class Collector:
 def __init__(self,full=True):
  self.cfg=load_config('config/collection.yaml');validate_config(self.cfg);self.study=read_study(self.cfg)
  if self.cfg['methods']!=list(range(1,9)) or self.cfg['participants_per_replication']!=100 or self.cfg['batch_size']!=100:raise ValueError('Expected M1-M8 and 100 participants in one request.')
  self.humans,self.qc=load_humans(self.cfg,self.study);self.cal,self.ev=split_pools(self.cfg,self.study,self.humans)
  self.full=True;self.limit=1000
  if self.cfg['reasoning_effort']!='medium' or self.cfg['max_output_tokens']!=128000:raise ValueError('This protocol requires medium reasoning and 128,000 max output tokens.')
  old_name=self.cfg['run_name']
  self.cfg['collection_stage']='full_medium'
  for key,value in self.cfg['paths'].items():
   self.cfg['paths'][key]=value.replace('outputs/'+old_name+'/', 'outputs/'+self.cfg['run_name']+'/')
  self.root=PROJECT_ROOT/'outputs'/self.cfg['run_name'];self.root.mkdir(parents=True,exist_ok=True)
  self.alias={'participant_id':'participant_id',**{f'psychological_{n:02d}_wave1':f'p{n:02d}' for n in range(1,39)},**{f'demographic_{n:02d}_wave1':f'd{n:02d}' for n in range(1,7)},'life_satisfaction_wave1':'y1','life_satisfaction_wave2':'y2'}
  self.map={f:self.alias[n] for f,n in field_map(self.study).items()}
  self.db=None
 def initialise(self):
  files=[p for d in ('src','config','prompts','data') for p in (PROJECT_ROOT/d).rglob('*') if p.is_file() and '__pycache__' not in str(p) and p != PROJECT_ROOT/'config/collection.yaml']
  manifest={'config':{k:v for k,v in self.cfg.items() if k!='project_root' and k not in OPERATIONAL},'files':{str(p.relative_to(PROJECT_ROOT)):sha(p) for p in sorted(files)},'protocol':'medium_full_v1','source_fingerprint':self.qc['fingerprint']}
  target=self.root/'collection_manifest.json'
  if target.exists() and read(target)!=manifest:raise ValueError('Source/configuration changed. Restore original files or choose a new run_name.')
  save(target,manifest);save(self.root/'operational_settings.json',{k:self.cfg[k] for k in OPERATIONAL if k in self.cfg});self.cal,self.ev=persist_pools(self.cfg,self.study,self.humans,self.qc)
  self.db=sqlite3.connect(self.root/'progress.sqlite3');self.db.row_factory=sqlite3.Row
  self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
  self.db.executescript('''CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,method INTEGER,rep INTEGER,status TEXT DEFAULT 'pending',attempts INTEGER DEFAULT 0,error TEXT,ceiling INTEGER DEFAULT 3);
  CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY,state TEXT,batch_id TEXT,file_id TEXT,members TEXT);
  CREATE TABLE IF NOT EXISTS processed(cid TEXT PRIMARY KEY);''')
  with self.db:self.db.executemany('INSERT OR IGNORE INTO jobs(id,method,rep) VALUES(?,?,?)',[(f'M{m}_R{r:04d}',m,r) for r in range(1,self.limit+1) for m in range(1,9)])
  # Accepted markers are durable commit records; regenerate missing CSVs from saved response if necessary.
  for j in self.db.execute("SELECT * FROM jobs WHERE status='complete'").fetchall():
   base=self.root/'accepted'/j['id'];marker=base.with_suffix('.json');csvpath=base.with_suffix('.csv')
   if not marker.exists():raise RuntimeError('Missing accepted record '+j['id'])
   v=read(marker)
   if not csvpath.exists() or sha(csvpath)!=v['csv_sha256']:
    ctx=self.context(j['method'],j['rep']);rows=self.decode(v['response'],ctx);self.write_accepted(j,rows,v['response'],v['custom_id'])
 @lru_cache(maxsize=16)
 def context(self,m,r):return prepare_replication(self.cfg,self.study,m,r,self.humans,self.cal,self.ev)
 def request(self,j):
  m,r=j['method'],j['rep'];ctx=self.context(m,r);ids=list(range(1,101))
  system,user=build_prompt(self.study,m,ids,ctx['fixed'],ctx['pilot'],ctx['summary'])
  for n in sorted(self.alias,key=len,reverse=True):user=user.replace(n,self.alias[n])
  schema=copy.deepcopy(make_schema(self.study).model_json_schema());schema['properties']['participants'].update(minItems=100,maxItems=100)
  node=schema['$defs']['Participant'];fixed=set(ctx['fixed'][0]) if ctx['fixed'] else set()
  props={self.alias[k]:v for k,v in node['properties'].items() if k=='participant_id' or k not in {field_map(self.study).get(f,f) for f in fixed}}
  node['properties']=props;node['required']=list(props);props['participant_id']['maximum']=100
  output_rule=('Return only {\"participants\":[...]}, exactly 100 rows in participant_id order 1 through 100. '
   'Each row must contain exactly these fields: '+dump(list(props))+'. '
   'Return only unfixed generated variables and participant_id. Fixed input columns are attached locally. '
   'No additional fields, source IDs, explanations or narrative.')
  user,count=re.subn(r'<OUTPUT_SCHEMA>.*?</OUTPUT_SCHEMA>','<OUTPUT_SCHEMA>\n'+output_rule+'\n</OUTPUT_SCHEMA>',user,flags=re.S)
  if count!=1:raise ValueError('Expected one output-schema block.')
  user,count=re.subn(r'Output format:.*?(?=\n\nInput blocks)','Output format: '+output_rule,user,flags=re.S)
  if count!=1:raise ValueError('Expected one output-format paragraph.')
  body={'model':self.cfg['model'],'instructions':system+DIRECTIVE,'input':user,'reasoning':{'effort':self.cfg['reasoning_effort']},'max_output_tokens':self.cfg['max_output_tokens'],'store':False,'text':{'format':{'type':'json_schema','name':'ParticipantBatch','strict':True,'schema':schema}}}
  if m!=7:
   body['instructions']+='\n\nPARTICIPANT DIVERSITY AND DUPLICATION CHECK: Generate 100 distinct participant profiles. Generate each participant individually; do not reuse an earlier participant profile or repeat a sequence of participants. Changing only the identifier does not create a new participant. Each participant must have a distinct combination of psychological scores and demographic values after rounding generated values to four decimal places. Differences must reflect plausible participant variation; do not introduce arbitrary tiny numerical changes solely to satisfy uniqueness. Before returning the dataset, check for repeated profiles and replace repeated profiles with newly generated participants. Return exactly 100 participants in the required JSON format. Preserve original fixed inputs and all method-specific information restrictions.'''
  attempt=j['attempts']+1 if 'attempts' in j.keys() else 1
  previous=self.root/'quality'/f"{j['id']}_A{attempt-1}.json"
  if attempt>1 and previous.exists():
   q=read(previous)
   if q.get('error','').startswith(('Repeated complete profiles','Repeated baseline profiles')):
    feedback=('Your previous response contained '+str(q.get('distinct_complete_profiles'))+' distinct complete profiles and '+str(q.get('distinct_baseline_profiles'))+' distinct baseline profiles among '+str(q.get('returned_participants'))+' participants. Generate a fresh dataset of 100 distinct participants. Do not repeat blocks. Use the same supplied reference data and retain all original method restrictions.')
    body['input']+='\n\nDUPLICATION FEEDBACK FROM PREVIOUS ATTEMPT:\n'+feedback
   elif 'reproduced a complete pilot' in q.get('error',''):
    body['input']+='\n\nPILOT-COPY FEEDBACK FROM PREVIOUS ATTEMPT: The previous response reproduced at least one complete pilot participant record under the rounding tolerance. Generate a fresh dataset of 100 new participants. Use the same pilot as reference, preserve all supplied statistical guidance, and do not copy complete pilot records. Check uniqueness of baseline and complete generated profiles. Do not disguise copying with arbitrary tiny numerical changes.'
  audit={'attempt':attempt,'protocol':'medium_full_v1','job_id':j['id'],'body':body,'request_sha256':stable_hash(body),'context':ctx,'seed':[self.cfg['random_seed'],m,r],'field_aliases':self.map,'source_fingerprint':self.qc['fingerprint']}
  path=self.root/'inputs'/f"{j['id']}_A{attempt}.json"
  if path.exists() and read(path)!=audit:raise RuntimeError('Request provenance changed '+j['id'])
  save(path,audit)
  if attempt==1:save(self.root/'inputs'/f"{j['id']}.json",audit)
  return body
 def decode(self,response,ctx):
  if not isinstance(response,dict):raise ValueError('Response body is missing or is not an object.')
  if response.get('status')!='completed':raise ValueError('Response incomplete: '+dump(response.get('incomplete_details')))
  texts=[c['text'] for o in response.get('output',[]) if o.get('type')=='message' for c in o.get('content',[]) if c.get('type')=='output_text']
  if not texts:raise ValueError('No output text (possibly a refusal).')
  parsed=json.loads(''.join(texts))
  if not isinstance(parsed,dict):raise ValueError('Expected a JSON object containing participants.')
  rows=parsed.get('participants')
  if set(parsed)!={'participants'} or not isinstance(rows,list) or len(rows)!=100:raise ValueError('Expected exactly 100 participants.')
  fixed=ctx['fixed'];excluded=set(fixed[0]) if fixed else set();needed={'participant_id'}|{short for f,short in self.map.items() if f not in excluded};reverse={v:k for k,v in self.map.items()}
  records=[]
  for i,row in enumerate(rows):
   if type(row) is not dict or set(row)!=needed:raise ValueError('Missing, extra or fixed output fields.')
   if type(row['participant_id']) is not int or row['participant_id']!=i+1:raise ValueError('Incorrect ID or order.')
   record={reverse.get(k,k):v for k,v in row.items()}
   if fixed:record.update(fixed[i])
   records.append(record)
  records=normalise_generated_precision(records,self.study,list(range(1,101)),fixed,ctx['pilot'])
  def unique(fields):return len({tuple(row[f] for f in fields) for row in records})==100
  if not unique(selected_fields(self.study)):raise ValueError('Repeated complete profiles after four-decimal rounding.')
  if ctx['pilot'] is not None or not fixed or len(fixed[0])<40:
   if not unique(predictors(self.study)):raise ValueError('Repeated baseline profiles after four-decimal rounding.')
  return records
 def write_accepted(self,j,rows,response,cid):
  path=self.root/'accepted'/j['id'];buffer=io.StringIO(newline='');w=csv.DictWriter(buffer,fieldnames=['participant_id']+selected_fields(self.study));w.writeheader();w.writerows(rows)
  atomic(path.with_suffix('.csv'),buffer.getvalue());save(path.with_suffix('.json'),{'job_id':j['id'],'custom_id':cid,'csv_sha256':sha(path.with_suffix('.csv')),'request_sha256':read(self.root/'inputs'/f'{cid}.json')['request_sha256'],'response':response})
 def ingest(self,line):
  cid=line['custom_id']
  if self.db.execute('SELECT 1 FROM processed WHERE cid=?',(cid,)).fetchone():return
  jid=cid.rsplit('_A',1)[0];j=self.db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
  if j is None:raise ValueError('Unexpected response ID '+cid)
  save(self.root/'responses'/f'{cid}.json',line)
  if j['status']=='complete':
   with self.db:self.db.execute('INSERT OR IGNORE INTO processed VALUES(?)',(cid,))
   return
  response=(line.get('response') or {}).get('body');error=None
  try:
   if line.get('error') or (line.get('response') or {}).get('status_code')!=200:raise ValueError(dump(line.get('error') or line.get('response')))
   rows=self.decode(response,self.context(j['method'],j['rep']));self.write_accepted(j,rows,response,cid);status='complete'
  except (ValueError,TypeError,KeyError) as exc:
   error=str(exc)[:3000];status='blocked' if j['attempts']>=j['ceiling'] else 'pending'
  quality=self.quality(response,self.context(j['method'],j['rep']))
  quality.update(custom_id=cid,method=j['method'],replication=j['rep'],attempt=int(cid.rsplit('_A',1)[1]),accepted=status=='complete',error=error or '')
  save(self.root/'quality'/f'{cid}.json',quality)
  with self.db:
   self.db.execute('UPDATE jobs SET status=?,error=? WHERE id=?',(status,error,jid));self.db.execute('INSERT OR IGNORE INTO processed VALUES(?)',(cid,))
  print(jid,status,('' if not error else error[:160]),flush=True)
 def quality(self,response,ctx):
  q={'returned_participants':None,'distinct_complete_profiles':None,'distinct_baseline_profiles':None,'usage':response.get('usage') if isinstance(response,dict) else None}
  try:
   text=''.join(c['text'] for o in response['output'] if o['type']=='message' for c in o['content'] if c['type']=='output_text')
   rows=json.loads(text)['participants'];q['returned_participants']=len(rows)
   full=[];baseline=[]
   for i,row in enumerate(rows):
    r={k:(round(v,4) if isinstance(v,float) else v) for k,v in row.items() if k!='participant_id'}
    if ctx['fixed']:r.update({self.map[k]:v for k,v in ctx['fixed'][i].items()})
    full.append(dump(dict(sorted(r.items()))));baseline.append(dump(dict(sorted((k,v) for k,v in r.items() if k not in ('y1','y2')))))
   q['distinct_complete_profiles']=len(set(full));q['distinct_baseline_profiles']=len(set(baseline))
  except (KeyError,TypeError,ValueError,IndexError):pass
  return q
 def report(self):
  attempts=[read(p) for p in sorted((self.root/'quality').glob('*.json'))] if (self.root/'quality').exists() else []
  methods=[]
  for m in range(1,9):
   a=[q for q in attempts if q['method']==m];first=[q for q in a if q['attempt']==1]
   methods.append({'method':m,'first_attempts_returned':len(first),'first_attempts_accepted':sum(q['accepted'] for q in first),'attempts_returned':len(a),'duplicate_rejections':sum(q.get('error','').startswith(('Repeated complete profiles','Repeated baseline profiles')) for q in a),'pilot_copy_rejections':sum('reproduced a complete pilot' in q.get('error','') for q in a)})
  report={'protocol':'medium_full_v1','stage':'full_medium','planned_datasets':self.limit*8,'returned_attempts':len(attempts),'methods':methods,'reasoning_effort':self.cfg['reasoning_effort'],'progress':{r[0]:r[1] for r in self.db.execute('SELECT status,COUNT(*) FROM jobs GROUP BY status')},'note':'Three initial attempts per dataset; duplicate and pilot-copy feedback applies to subsequent attempts. Every attempt is retained. Blocked jobs do not stop other jobs; the run pauses after all remaining work is exhausted if any dataset is still blocked. First-attempt pass rates must be reported separately from retry-selected results.'}
  save(self.root/'collection_report.json',report)
 def reconcile(self,client,chunk):
  # Never blindly resubmit a possibly accepted creation after timeout/crash.
  match=None
  for b in client.batches.list(limit=100):
   if (b.metadata or {}).get('collection_chunk')==chunk['id']:match=b;break
  if match:
   with self.db:self.db.execute("UPDATE chunks SET state='active',batch_id=? WHERE id=?",(match.id,chunk['id']))
   return True
  raise RuntimeError('Submission outcome uncertain for '+chunk['id']+'. No duplicate batch was submitted. Rerun later to reconcile; see README recovery section.')
 def submit(self,client):
  c=self.cfg;active=self.db.execute("SELECT COUNT(*) FROM chunks WHERE state IN ('active','intent')").fetchone()[0]
  if active>=c['max_active_batches']:return False
  canary=[]  # Full collection authorised after the separate medium-effort comparison.
  if any(r[0]=='blocked' for r in canary):raise RuntimeError('Initial eight-method trial has a blocked request; inspect response logs before wider collection.')
  trial=any(r[0]!='complete' for r in canary)
  if trial and active:return False
  queued=self.db.execute("SELECT COUNT(*) FROM jobs WHERE status='submitted'").fetchone()[0]
  # Conservative admission ceiling: bytes upper-bound the usual text token count.
  active_bytes=0
  for row in self.db.execute("SELECT id FROM chunks WHERE state='active'"):
   active_bytes+=(self.root/'batches'/f"{row['id']}.jsonl").stat().st_size
  allowance=c['max_queued_input_tokens']-active_bytes
  if allowance<=0:return False
  members=[];lines=[];size=0
  jobs=self.db.execute("SELECT * FROM jobs WHERE status='pending' AND attempts<ceiling ORDER BY rep,method LIMIT ?",(c['batch_requests'],)).fetchall()
  for j in jobs:
   if trial and j['rep']!=1:break
   body=self.request(j);cid=f"{j['id']}_A{j['attempts']+1}";line=dump({'custom_id':cid,'method':'POST','url':'/v1/responses','body':body})+'\n';n=len(line.encode())
   if size+n>min(c['max_batch_input_bytes'],allowance):
    if not members and active:return False
    if not members:raise RuntimeError('One request exceeds the configured queue allowance. Increase max_queued_input_tokens after checking account limits.')
    break
   lines.append(line);members.append(cid);size+=n
  if not members:return False
  chunkid=uuid.uuid4().hex;path=self.root/'batches'/f'{chunkid}.jsonl';atomic(path,''.join(lines))
  with self.db:
   self.db.execute('INSERT INTO chunks VALUES(?,?,?,?,?)',(chunkid,'prepared',None,None,dump(members)))
   for cid in members:self.db.execute("UPDATE jobs SET status='submitted',attempts=attempts+1 WHERE id=?",(cid.rsplit('_A',1)[0],))
  self.activate(client,self.db.execute('SELECT * FROM chunks WHERE id=?',(chunkid,)).fetchone());return True
 def activate(self,client,chunk):
  fileid=chunk['file_id']
  if not fileid:
   with (self.root/'batches'/f"{chunk['id']}.jsonl").open('rb') as f:fileid=client.files.create(file=f,purpose='batch').id
   with self.db:self.db.execute('UPDATE chunks SET file_id=? WHERE id=?',(fileid,chunk['id']))
  with self.db:self.db.execute("UPDATE chunks SET state='intent' WHERE id=?",(chunk['id'],))
  try:b=client.batches.create(input_file_id=fileid,endpoint='/v1/responses',completion_window='24h',metadata={'collection_chunk':chunk['id'],'run':self.cfg['run_name']})
  except Exception as exc:
   # Definitive HTTP rejection means no creation; network failures stay ambiguous.
   if getattr(exc,'status_code',None) in (400,401,403,404,422,429):
    with self.db:self.db.execute("UPDATE chunks SET state='prepared' WHERE id=?",(chunk['id'],))
   raise
  with self.db:self.db.execute("UPDATE chunks SET state='active',batch_id=? WHERE id=?",(b.id,chunk['id']))
  save(self.root/'batches'/f"{chunk['id']}.receipt.json",b.model_dump(mode='json'));print('Submitted',b.id,len(json.loads(chunk['members'])),'requests',flush=True)
 def harvest(self,client):
  for chunk in self.db.execute("SELECT * FROM chunks WHERE state='active'").fetchall():
   b=retry_read(client.batches.retrieve,chunk['batch_id']);save(self.root/'batches'/f"{chunk['id']}.status.json",b.model_dump(mode='json'))
   counts=b.request_counts.model_dump() if hasattr(b.request_counts,'model_dump') else b.request_counts
   print(time.strftime('%H:%M:%S'),'Batch',b.id,b.status,counts,flush=True)
   if b.status not in TERMINAL:continue
   if b.status=='failed':
    with self.db:self.db.execute("UPDATE chunks SET state='validation_failed' WHERE id=?",(chunk['id'],))
    print('Batch validation failed; details saved for '+chunk['id']+'. Retrieving other completed batches before pausing.',flush=True)
    continue
   for fileid in (b.output_file_id,b.error_file_id):
    if not fileid:continue
    path=self.root/'batches'/f'{fileid}.jsonl'
    if not path.exists():atomic(path,retry_read(client.files.content,fileid).content)
    with path.open(encoding='utf-8') as f:
     for line in f:
      if line.strip():
       result=json.loads(line)
       if result.get('custom_id') not in json.loads(chunk['members']):raise ValueError('Batch returned an unexpected custom_id.')
       self.ingest(result)
   # Terminal expired/cancelled batches can omit requests; retry only missing results.
   for cid in json.loads(chunk['members']):
    if not self.db.execute('SELECT 1 FROM processed WHERE cid=?',(cid,)).fetchone():self.ingest({'custom_id':cid,'error':{'code':'terminal_missing_result','batch_status':b.status}})
   with self.db:self.db.execute("UPDATE chunks SET state='done' WHERE id=?",(chunk['id'],))
 def status(self):
  counts={r[0]:r[1] for r in self.db.execute('SELECT status,COUNT(*) FROM jobs GROUP BY status')};save(self.root/'progress.json',counts);print('Progress:',counts,flush=True);return counts
 def run(self,client):
  for chunk in self.db.execute("SELECT * FROM chunks WHERE state='intent'").fetchall():self.reconcile(client,chunk)
  for chunk in self.db.execute("SELECT * FROM chunks WHERE state='prepared'").fetchall():self.activate(client,chunk)
  while True:
   self.harvest(client)
   if self.db.execute("SELECT 1 FROM chunks WHERE state='validation_failed'").fetchone():raise RuntimeError('Resolve failed batch validation before submitting additional work; see README.')
   while self.submit(client):pass
   counts=self.status()
   self.report()
   if self.db.execute("SELECT 1 FROM chunks WHERE state='validation_failed'").fetchone():raise RuntimeError('Batch validation failure remains. See --retry-failed-batches after resolving the cause.')
   if not counts.get('submitted') and not counts.get('pending'):
    if counts.get('blocked'):raise RuntimeError('Collection has blocked requests after bounded attempts. Valid datasets are saved. Inspect response logs; use --retry-blocked only after resolving the cause.')
    print('COLLECTION COMPLETE.',flush=True);return
   time.sleep(self.cfg['poll_seconds'])

def main():
 p=argparse.ArgumentParser();p.add_argument('--full',action='store_true');p.add_argument('--check-only',action='store_true');p.add_argument('--status',action='store_true');p.add_argument('--retry-failed-batches',action='store_true');p.add_argument('--retry-blocked',action='store_true');p.add_argument('--release-unsubmitted',metavar='CHUNK_ID');args=p.parse_args();print('Loading source data and medium-effort collector...',flush=True);c=Collector()
 with lock(c.root):
  print('Checking saved provenance and preparing/resuming the 8,000-dataset ledger...',flush=True)
  c.initialise()
  if args.check_only:
   for m in range(1,9):
    print(f'Checking M{m} request and input data...',flush=True);c.request({'method':m,'rep':1,'id':f'M{m}_R0001'})
   for r in (1,2,1000):
    a,b=c.context(5,r),c.context(6,r);assert a['pilot']!=b['pilot'] and b['summary'] is None and a['summary'] is not None
    a,b=c.context(7,r),c.context(8,r);assert a['truth']!=b['truth'];assert len(a['fixed'])==len(b['fixed'])==100
   assert c.context(5,1)['pilot']!=c.context(5,2)['pilot'];assert c.context(7,1)['fixed']!=c.context(7,2)['fixed']
   print(dump({'checks':'passed','api_calls':0,'model':c.cfg['model'],'model_batch_availability':'not checked','reasoning_effort':c.cfg['reasoning_effort'],'generation_attempts_per_dataset':3,'api_spending_cap':None,'datasets':c.limit*8,'participants_per_dataset':100,'repetitions_per_method':c.limit,'pilot_n':50,'fresh_independent_inputs':True,'matched_M5_M6_pilot':False,'matched_M7_M8_participants':False,'calibration_n':len(c.cal),'evaluation_n':len(c.ev),'mode':'Batch API','max_output_tokens':c.cfg['max_output_tokens']}));return
  if args.status:c.status();return
  if args.release_unsubmitted:
   # Explicit operator recovery only after checking dashboard that no remote batch exists.
   chunk=c.db.execute('SELECT * FROM chunks WHERE id=?',(args.release_unsubmitted,)).fetchone()
   if not chunk or chunk['state']!='intent':raise ValueError('Expected an uncertain submission chunk.')
   with c.db:c.db.execute("UPDATE chunks SET state='prepared' WHERE id=?",(chunk['id'],))
   print('Released uncertain chunk; next run will submit it.');return
  if args.retry_failed_batches:
   with c.db:
    for chunk in c.db.execute("SELECT * FROM chunks WHERE state='validation_failed'").fetchall():
     for cid in json.loads(chunk['members']):c.db.execute("UPDATE jobs SET status='pending',ceiling=MAX(ceiling,attempts+3) WHERE id=? AND status!='complete'",(cid.rsplit('_A',1)[0],))
     c.db.execute("UPDATE chunks SET state='done' WHERE id=?",(chunk['id'],))
   print('Failed batches released. Restart collection.');return
  if args.retry_blocked:
   with c.db:c.db.execute("UPDATE jobs SET status='pending',ceiling=attempts+3 WHERE status='blocked'")
   print('Blocked jobs released with three additional attempts; attempt numbers remain unique. Restart collection.');return
  load_env_file(PROJECT_ROOT/'.env')
  if not os.environ.get('OPENAI_API_KEY'):raise RuntimeError('Add OPENAI_API_KEY to .env first.')
  from openai import OpenAI
  client=OpenAI(timeout=c.cfg['request_timeout_seconds'],max_retries=0)
  try:c.run(client)
  finally:client.close();c.db.close()

if __name__=='__main__':
 try:main()
 except KeyboardInterrupt:print('\nStopped locally. Remote batches continue. Run the same command to resume.');sys.exit(130)
 except Exception as exc:print(f'PAUSED: {type(exc).__name__}: {exc}\nSaved work retained. Rerun the same command after resolving the issue.',file=sys.stderr);sys.exit(1)
