"""Operational launcher for the existing MIDUS_Monte_Carlo_Medium package.
Does not edit source, prompts, configuration, validation or attempt limits.
"""
from __future__ import annotations
import argparse,collections,datetime,json,os,sqlite3,subprocess,sys,time
from pathlib import Path

BASE=Path(__file__).resolve().parent
ROOT=BASE/'outputs/monte_carlo_medium_v1'

def classify(lines):
    # Inspect only the final PAUSED message, not earlier model-output errors.
    messages=[s for s in lines if s.startswith('PAUSED:')]
    if not messages:return 'review'
    message=messages[-1].lower()
    billing=('insufficient_quota','credit_balance_exhausted','organization_usage_limit_exceeded','organization_spend_limit_exceeded','project_spend_limit_exceeded','billing_hard_limit','exceeded your current quota')
    if any(word in message for word in billing):return 'billing'
    if 'submission outcome uncertain' in message:return 'reconcile'
    transient=('paused: apiconnectionerror:','paused: apitimeouterror:','paused: internalservererror:','paused: connectionerror:','paused: timeouterror:')
    if message.startswith(transient):return 'network'
    if message.startswith('paused: ratelimiterror:') and ('rate_limit_exceeded' in message or 'rate limit reached' in message):return 'network'
    return 'review'

def ledger_summary():
    path=ROOT/'progress.sqlite3'
    if not path.exists():return {'ledger':'not created'}
    try:
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5) as db:
            counts=dict(db.execute('SELECT status,COUNT(*) FROM jobs GROUP BY status'))
            blocked=[{'job_id':row[0],'attempts':row[1],'error':row[2]} for row in db.execute("SELECT id,attempts,error FROM jobs WHERE status='blocked' ORDER BY method,rep")]
            return {'counts':counts,'blocked_datasets':blocked,'note':'Snapshot only. No statuses or attempt limits were changed.'}
    except sqlite3.Error as exc:return {'ledger_read_error':str(exc)}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--check-only',action='store_true')
    p.add_argument('--max-restarts',type=int,default=10)
    p.add_argument('--delay',type=int,default=60)
    args=p.parse_args()
    if args.max_restarts<0 or args.delay<1:raise SystemExit('Use non-negative max-restarts and a positive delay.')
    if not (BASE/'src/collection.py').exists() or not (BASE/'config/collection.yaml').exists():raise SystemExit('Copy these launcher files into the EXISTING MIDUS_Monte_Carlo_Medium folder, beside setup.ps1.')
    os.chdir(BASE)
    if args.check_only:
        print('Launcher check passed. Zero API calls. Collector files unchanged.')
        print('Transient failures: up to',args.max_restarts,'automatic restarts,',args.delay,'seconds apart.')
        print('Billing, validation, exhausted generation attempts and unknown errors pause for review.')
        return
    ROOT.mkdir(parents=True,exist_ok=True)
    # Independent lock prevents multiple recovery launchers. The collector also
    # keeps its own existing lock, so a direct collector cannot run alongside it.
    from src.collection import lock,save
    guard=ROOT/'recovery_launcher';guard.mkdir(exist_ok=True)
    with lock(guard):
        restarts=0
        while True:
            print('Starting/resuming saved medium collection. Automatic restarts used:',restarts,flush=True)
            tail=collections.deque(maxlen=300)
            env=os.environ.copy();env['PYTHONUNBUFFERED']='1';env['PYTHONIOENCODING']='utf-8'
            child=subprocess.Popen([sys.executable,'-u','-m','src.collection'],cwd=BASE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',bufsize=1,env=env)
            try:
                for line in child.stdout:
                    print(line,end='',flush=True);tail.append(line.strip())
                code=child.wait()
            except KeyboardInterrupt:
                # Windows console normally delivers Ctrl+C to both processes;
                # allow the collector time to exit cleanly before terminating.
                if child.poll() is None:
                    try:child.wait(timeout=10)
                    except subprocess.TimeoutExpired:child.terminate();child.wait()
                print('\nStopped by user. Remote batches continue. Rerun run_with_recovery.ps1 to resume.',flush=True)
                return 130
            kind=classify(tail)
            snapshot={'time_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'collector_exit_code':code,'classification':kind,'automatic_restarts_used':restarts,'ledger':ledger_summary(),'last_messages':list(tail)[-25:]}
            save(guard/'last_exit.json',snapshot)
            if code==0:print('Collection completed. No automatic restart needed.');return 0
            if code==130:print('Collector stopped by user. No automatic restart.');return 130
            if kind not in ('network','reconcile'):
                print('PAUSED FOR REVIEW. Saved work retained. No automatic retries for billing, invalid configuration/batches, blocked datasets or unknown errors.')
                print('Recovery details:',guard/'last_exit.json');return code or 1
            if restarts>=args.max_restarts:
                print('PAUSED: automatic reconnect allowance reached. Restore connectivity, then rerun this same launcher. No dataset attempt limits were increased.')
                print('Recovery details:',guard/'last_exit.json');return code or 1
            restarts+=1
            print(f'Temporary connection/submission-reconciliation issue. Resuming in {args.delay} seconds. Existing batch IDs and dataset attempt limits are retained.',flush=True)
            try:time.sleep(args.delay)
            except KeyboardInterrupt:print('Stopped. Rerun this launcher to resume.');return 130

if __name__=='__main__':sys.exit(main())
