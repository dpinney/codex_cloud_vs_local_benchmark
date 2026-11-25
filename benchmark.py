#!/usr/bin/env python3
"""Paired Codex local/cloud benchmark. Python 3.9+, standard library only.
Run once, then paste one cloud prompt and one final result line. No API key needed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

VERSION = 2
DEFAULT_MODEL = 'gpt-6.2-sol'
# This exact driver is supplied to both agents. It contains no expected answers.
DRIVER = r'''import hashlib, json, random

QUERIES = [
    "SELECT id, region, amount FROM orders ORDER BY id",
    "SELECT id, amount * 2 + 3 AS v FROM orders WHERE amount >= 10 AND amount < 70 ORDER BY id",
    "SELECT id FROM orders WHERE NOT (amount < 20 OR region = 'west') ORDER BY id",
    "SELECT id, note FROM orders WHERE note IS NULL OR note = 'rush' ORDER BY id",
    "SELECT id FROM orders WHERE amount IS NOT NULL AND (region = 'east' OR region = 'north') ORDER BY id",
    "SELECT DISTINCT region FROM orders ORDER BY region",
    "SELECT DISTINCT region, note FROM orders ORDER BY region, note",
    "SELECT id, amount FROM orders ORDER BY amount DESC, id ASC LIMIT 7 OFFSET 2",
    "SELECT id, amount FROM orders ORDER BY amount ASC, id DESC LIMIT 9",
    "SELECT o.id, c.name, o.amount FROM orders AS o INNER JOIN customers AS c ON o.customer_id = c.id ORDER BY o.id",
    "SELECT o.id, c.name FROM orders o LEFT JOIN customers c ON o.customer_id = c.id ORDER BY o.id",
    "SELECT o.id FROM orders o LEFT JOIN customers c ON o.customer_id = c.id WHERE c.id IS NULL ORDER BY o.id",
    "SELECT o.id, c.name FROM orders o JOIN customers c ON o.customer_id = c.id WHERE c.active = 1 AND o.amount >= 30 ORDER BY o.id",
    "SELECT region, COUNT(*) AS n, COUNT(amount) AS paid, SUM(amount) AS total, MIN(amount) AS lo, MAX(amount) AS hi FROM orders GROUP BY region ORDER BY region",
    "SELECT region, COUNT(*) AS n FROM orders GROUP BY region HAVING COUNT(*) >= 4 ORDER BY n DESC, region",
    "SELECT c.name, COUNT(*) AS n, SUM(o.amount) AS total FROM orders o JOIN customers c ON o.customer_id = c.id GROUP BY c.name ORDER BY c.name",
    "SELECT c.id, COUNT(o.id) AS n FROM customers c LEFT JOIN orders o ON c.id = o.customer_id GROUP BY c.id ORDER BY c.id",
    "SELECT COUNT(*), COUNT(amount), SUM(amount), MIN(amount), MAX(amount), AVG(amount) FROM orders",
    "SELECT COUNT(*), SUM(amount), AVG(amount) FROM orders WHERE id < 0",
    "SELECT region, AVG(amount) AS avg_amount FROM orders GROUP BY region ORDER BY region",
    "SELECT id, COALESCE(amount, 0) AS paid, LOWER(region) AS r FROM orders ORDER BY id",
    "SELECT id, ABS(amount - 50) AS distance FROM orders WHERE amount IS NOT NULL ORDER BY distance, id",
    "SELECT id FROM orders WHERE region IN ('east', 'north') AND amount BETWEEN 20 AND 80 ORDER BY id",
    "SELECT id FROM orders WHERE note LIKE 'r%' OR note LIKE '_old' ORDER BY id",
    "SELECT id FROM orders WHERE note NOT IN ('rush', NULL) ORDER BY id",
    "SELECT id FROM orders WHERE region <> 'west' AND (amount + 2) * 3 > 90 ORDER BY id",
    "SELECT id, -amount AS neg FROM orders WHERE amount IS NOT NULL ORDER BY id",
    "SELECT region, COUNT(DISTINCT customer_id) AS n FROM orders GROUP BY region ORDER BY region",
    "SELECT id, CASE WHEN amount IS NULL THEN 'unpaid' WHEN amount >= 60 THEN 'large' ELSE 'small' END AS bucket FROM orders ORDER BY id",
    "SELECT id FROM orders WHERE note = 'O''Brien' ORDER BY id",
    "sElEcT ID, REGION FrOm ORDERS WhErE ID <= 3 OrDeR By ID;",
    "SELECT id FROM orders ORDER BY id LIMIT 0",
    "SELECT id, region FROM orders WHERE id < 0 ORDER BY id",
    "SELECT region, note, COUNT(*) AS n FROM orders GROUP BY region, note ORDER BY region, note",
    "SELECT COUNT(*), COUNT(note) FROM orders WHERE note IS NULL",
    "SELECT o.id, c.name FROM orders o LEFT JOIN customers c ON o.customer_id = c.id AND c.active = 1 ORDER BY o.id",
    "SELECT id FROM orders WHERE amount IN (NULL, 20, 40) ORDER BY id",
    "SELECT id, CASE WHEN amount > 25 AND amount < 60 THEN 1 ELSE 0 END AS flag FROM orders ORDER BY id",
    "SELECT id, COALESCE(note, region, 'none') AS label FROM orders ORDER BY id",
    "SELECT region, SUM(amount) AS total FROM orders GROUP BY region HAVING SUM(amount) > 150 ORDER BY total DESC, region LIMIT 3",
]

def datasets():
    for seed in range(16):
        r = random.Random(412000 + seed)
        customers = [[i, 'customer_%02d' % i, r.randrange(2)] for i in range(1, 13)]
        orders = []
        for i in range(1, 81):
            orders.append([i, r.choice([None]+list(range(1,16))),
                r.choice(['east','west','north','south',None]),
                r.choice([None, 20, 40]+list(range(101))),
                r.choice([None,'rush','hold','normal',"O'Brien",''])])
        if seed == 0: orders = []
        if seed == 1:
            for row in orders: row[3] = None
        if seed == 2:
            customers = []
        yield {
            'customers': {'columns': ['id','name','active'], 'rows': customers},
            'orders': {'columns': ['id','customer_id','region','amount','note'], 'rows': orders},
        }

def digest(query):
    # Normalize floats so equivalent AVG implementations do not differ by rounding noise.
    def normalized(v):
        if isinstance(v, float): return round(v, 9)
        if isinstance(v, (str,int)) or v is None: return v
        raise TypeError('Invalid cell: %r' % (v,))
    results = []
    for tables in datasets():
        for sql in QUERIES:
            rows = query(tables, sql)
            results.append([[normalized(v) for v in row] for row in rows])
    payload = json.dumps(results, ensure_ascii=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(payload.encode('ascii')).hexdigest(), len(results)

if __name__ == '__main__':
    from miniql import query
    h, n = digest(query)
    print('BENCHMARK_RESULT', h, n)
'''

SPEC = '''Build a small but production-quality Python 3.9+ standard-library-only relational query engine called miniql in a NEW temporary directory. Do not edit the existing repository or its files. Do not use sqlite3, another SQL engine, external packages, eval/exec for parsing, or subprocesses in the implementation. Do not use external services, web search, or subagents. SQLite is permitted ONLY in your test code as an independent differential oracle.

Required deliverables:
1. miniql package with separate lexer, recursive-descent or Pratt parser, relational execution engine, and public API. Implement general semantics, not special cases for the provided queries. Public API: miniql.query(tables, sql) -> list of row lists. Input tables are dictionaries of {table_name: {"columns": [column names], "rows": [row lists]}}. Values are integers, strings, or None. Do not mutate inputs. Case-insensitive identifiers and keywords; case-sensitive strings. Report malformed/unsupported SQL, unknown table/column, or ambiguous unqualified column using clear ValueError messages. No need for DDL, subqueries, window functions or SQL beyond the features listed.
2. Support SELECT expressions and aliases (AS and bare table aliases), FROM, INNER/JOIN and LEFT JOIN with ON, WHERE, DISTINCT, GROUP BY, HAVING, ORDER BY (alias or expression, ASC/DESC, multiple keys), LIMIT and OFFSET. Support parentheses, integer/string/NULL literals, qualified/unqualified identifiers, unary NOT and minus, arithmetic + - *, comparisons = != <> < <= > >=, AND/OR, IS [NOT] NULL, [NOT] IN lists, [NOT] BETWEEN, [NOT] LIKE (% and _; ASCII case-insensitive like SQLite), COALESCE, ABS, LOWER, searched CASE WHEN ... THEN ... ELSE ... END, COUNT(*) / COUNT(expr) / COUNT(DISTINCT expr), SUM/MIN/MAX/AVG. Implement SQL three-valued logic, null propagation, left-join null extension, aggregate behavior on empty inputs, stable multi-key ordering with NULL first in ascending order / last in descending order. Follow SQLite semantics for these features. Grouped queries will select only grouping keys and aggregate expressions. ORDER BY ties in benchmark queries are unambiguous or indistinguishable. COUNT returns int; AVG returns float or None; other aggregate types follow inputs. A trailing semicolon is optional. Do not hardcode query strings or outputs.
3. Add a command line interface `python -m miniql tables.json "SELECT ..."` producing JSON rows, and a concise README with API usage, supported syntax, limitations, architecture, and how to run tests.
4. Write a real unittest suite with at least 25 explicit tests, including malformed SQL, ambiguous columns, empty tables, null logic, joins with duplicate matches, grouping, ordering, and CLI behavior. Also write a differential test against the Python sqlite3 reference engine using the supplied workload. Use sqlite3 only in test files. Run and fix failures in your code. Check that the query API does not mutate inputs. Do not fabricate results.
5. Save the following exact driver as benchmark_driver.py, then run `python benchmark_driver.py` against your actual implementation. All queries must succeed; compare all results with the independent SQLite oracle in your own test code. Stop once the deliverables and tests are complete. In your final response return ONLY the line printed by benchmark_driver.py (BENCHMARK_RESULT <sha256> <query_count>). Do not include markdown, test logs, or other text.

Exact benchmark_driver.py:
'''
PROMPT = SPEC + '\n```python\n' + DRIVER + '\n```\n'


def driver_namespace():
    namespace = {'__name__': 'benchmark_driver'}
    exec(compile(DRIVER, 'benchmark_driver.py', 'exec'), namespace)
    return namespace


def oracle(tables, sql):
    with sqlite3.connect(':memory:') as db:
        for name, table in tables.items():
            types = {'customers': ['INTEGER','TEXT','INTEGER'], 'orders': ['INTEGER','INTEGER','TEXT','INTEGER','TEXT']}[name]
            cols = ','.join('"%s" %s' % (c,t) for c,t in zip(table['columns'], types))
            db.execute('CREATE TABLE "%s" (%s)' % (name,cols))
            db.executemany('INSERT INTO "%s" VALUES (%s)' % (name,','.join('?' for _ in types)), table['rows'])
        return db.execute(sql).fetchall()


def expected():
    return driver_namespace()['digest'](oracle)


def quota(codex):
    # Proven working with the user's Codex CLI. Save only quota fields, not account IDs.
    p = subprocess.Popen([codex,'app-server'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, bufsize=1)
    q = queue.Queue()
    def reader():
        for line in p.stdout:
            try: q.put(json.loads(line))
            except ValueError: pass
        q.put(None)
    threading.Thread(target=reader, daemon=True).start()
    def send(obj):
        p.stdin.write(json.dumps(obj)+'\n'); p.stdin.flush()
    def receive(i):
        deadline = time.monotonic()+30
        while time.monotonic()<deadline:
            try: x = q.get(timeout=max(.01,deadline-time.monotonic()))
            except queue.Empty: break
            if x is None: raise RuntimeError('Codex app-server exited')
            if x.get('id') == i:
                if 'error' in x: raise RuntimeError(str(x['error']))
                return x['result']
        raise RuntimeError('Timed out reading quota')
    try:
        send({'id':1,'method':'initialize','params':{'clientInfo':{'name':'quota_benchmark','title':'Quota benchmark','version':'2.0'}}}); receive(1)
        send({'method':'initialized'}); send({'id':2,'method':'account/rateLimits/read'})
        raw = receive(2)
        sets = raw.get('rateLimitsByLimitId') or {'codex':raw.get('rateLimits')}
        windows = {}
        for name, limits in sets.items():
            if not isinstance(limits,dict): continue
            for slot in ('primary','secondary'):
                w = limits.get(slot)
                if w and w.get('usedPercent') is not None:
                    windows['%s/%s/%s' % (name,w.get('windowDurationMins'),slot)] = {
                        k:w.get(k) for k in ('usedPercent','windowDurationMins','resetsAt')}
        if not windows: raise RuntimeError('No quota data; check ChatGPT sign-in.')
        limit = raw.get('rateLimits') or {}
        return {'time':time.time(),'windows':windows,'plan':limit.get('planType'),
                'ordinary_usage_allowed':raw.get('ordinaryUsageAllowed'),
                'credit_balance':(limit.get('credits') or {}).get('balance')}
    finally:
        p.terminate()
        try: p.wait(timeout=3)
        except subprocess.TimeoutExpired: p.kill(); p.wait()


def delta(before, after):
    results = {}
    for key, x in before['windows'].items():
        y = after['windows'].get(key)
        invalid = None
        if not y: invalid = 'missing window'
        elif x.get('resetsAt') is None or y.get('resetsAt') is None: invalid = 'unknown reset time'
        # The pilot showed a one-second reset timestamp fluctuation in the same window.
        elif abs(y['resetsAt']-x['resetsAt'])>2 or before['time']<=x['resetsAt']<=after['time']+2: invalid = 'reset boundary'
        elif y['usedPercent']<x['usedPercent']: invalid = 'quota decreased'
        if invalid:
            results[key]={'delta_pp':None,'reason':invalid}; continue
        d = round(y['usedPercent']-x['usedPercent'],6)
        # Each difference of integer readings has <1 pp rounding error (consistent rounding).
        results[key]={'delta_pp':d,'lower_pp':max(0,d-1),'upper_pp':d+1}
    return results


def ratios(local, cloud):
    out = {}
    for k,l in local.items():
        c=cloud.get(k,{})
        if l.get('delta_pp') is None or c.get('delta_pp') is None:
            out[k]={'estimate':None,'reason':'invalid or missing window'}; continue
        out[k]={'estimate':round(c['delta_pp']/l['delta_pp'],3) if l['delta_pp']>0 and c['delta_pp']>0 else None,
                'lower':round(c['lower_pp']/l['upper_pp'],3),
                'upper':round(c['upper_pp']/l['lower_pp'],3) if l['lower_pp']>0 else None,
                'adequate_resolution':min(l['delta_pp'],c['delta_pp'])>=5}
    return out


def validate_answer(answer, target):
    m=re.search(r'BENCHMARK_RESULT\s+([a-fA-F0-9]{64})\s+(\d+)',answer)
    return {'digest_correct':bool(m and m.group(1).lower()==target[0]),
            'query_count_correct':bool(m and int(m.group(2))==target[1])}


def run_local(codex, args, target):
    # The agent works in a temporary directory and writes its own project in a new subdirectory.
    # TMPDIR is pointed inside the sandbox so the implementation can be inspected afterward.
    with tempfile.TemporaryDirectory(prefix='codex-bench-') as d:
        cmd=[codex,'exec','--json','--skip-git-repo-check','--sandbox','workspace-write',
             '--model',args.model,'-c',f'model_reasoning_effort="{args.effort}"',PROMPT]
        env=os.environ.copy(); env['TMPDIR']=d
        usage=[]; messages=[]; errors=[]; calls=0; started=time.monotonic(); last_update=started
        p=subprocess.Popen(cmd,cwd=d,stdout=subprocess.PIPE,text=True,env=env)
        try:
            for line in p.stdout:
                try: x=json.loads(line)
                except ValueError: continue
                if x.get('type')=='turn.completed': usage.append(x.get('usage',{}))
                if x.get('type') in ('error','turn.failed'): errors.append(str(x.get('message',x.get('error','')))[:500])
                if x.get('type')=='item.completed':
                    item=x.get('item',{}); t=item.get('type')
                    if t=='agent_message': messages.append(item.get('text',''))
                    elif t in ('command_execution','file_change','mcp_tool_call','web_search'): calls+=1
                if time.monotonic()-last_update>=30:
                    print('  Local running: %ds, %d tool events' % (time.monotonic()-started,calls),flush=True); last_update=time.monotonic()
            code=p.wait()
        except BaseException:
            p.terminate()
            try: p.wait(timeout=5)
            except subprocess.TimeoutExpired: p.kill(); p.wait()
            raise
        elapsed=round(time.monotonic()-started,1)
        answer=messages[-1] if messages else ''
        # Independent re-run of the original driver against the implementation is intentionally
        # NOT done on the host: executing agent-written code outside the sandbox is unsafe.
        # The result is checked against a separately computed oracle instead, for both arms.
        return {'returncode':code,'seconds':elapsed,'usage':usage,'tool_events':calls,
                'answer':answer,'errors':errors,**validate_answer(answer,target),
                'model_requested':args.model,'effort_requested':args.effort}


def settle(codex, seconds, save_sample):
    start=time.monotonic(); latest=None
    while True:
        latest=quota(codex); save_sample(latest)
        if time.monotonic()-start>=seconds: return latest
        time.sleep(min(15,max(0,seconds-(time.monotonic()-start))))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model',default=DEFAULT_MODEL,help='Exact local Codex model ID (default %(default)s).')
    p.add_argument('--effort',default='high',choices=['low','medium','high','xhigh'])
    p.add_argument('--settle',type=int,default=45,help='Post-run quota sampling seconds (default %(default)s).')
    p.add_argument('--output',default='codex_benchmark_v2_results.json')
    p.add_argument('--cloud-first',action='store_true')
    p.add_argument('--resume',action='store_true',help='Resume an unfinished pair; no completed arm is rerun.')
    p.add_argument('--prompt',action='store_true',help='Print exact prompt only.')
    p.add_argument('--self-test',action='store_true',help='Test workload oracle and quota math without calling Codex.')
    a=p.parse_args()
    if a.prompt: print(PROMPT); return
    if a.settle<0: p.error('--settle must be nonnegative')
    target=expected()
    if a.self_test:
        self_test(target); return
    codex=shutil.which('codex')
    if not codex:
        path='/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex'
        if os.path.isfile(path): codex=path
        else: p.error('Codex CLI not found. Install it and sign in with ChatGPT first.')
    for key in ('OPENAI_API_KEY','CODEX_API_KEY'): os.environ.pop(key,None)
    dest=Path(a.output).expanduser().resolve()
    fingerprint=hashlib.sha256(PROMPT.encode()).hexdigest()
    if a.resume:
        if not dest.exists(): p.error('No results file to resume.')
        result=json.loads(dest.read_text())
        if (result.get('version'),result.get('prompt_sha256'),result.get('model'),result.get('effort'))!=(VERSION,fingerprint,a.model,a.effort):
            p.error('Resume requires the original version, model and effort.')
        if any('measurements' not in r for r in result['runs'].values()):
            p.error('A previous arm was interrupted after its starting quota was read. Cannot attribute further usage safely; use a new --output file.')
    else:
        if dest.exists(): p.error('Results already exist. Use --resume or a new --output filename.')
        result={'version':VERSION,'model':a.model,'effort':a.effort,'prompt_sha256':fingerprint,
                'expected_digest':target[0],'expected_query_count':target[1],
                'codex_version':subprocess.check_output([codex,'--version'],text=True).strip(),
                'order':['cloud','local'] if a.cloud_first else ['local','cloud'],'runs':{}}
    def save():
        tmp=dest.with_suffix(dest.suffix+'.tmp'); tmp.write_text(json.dumps(result,indent=2)+'\n'); tmp.replace(dest)
    print('Model:',a.model,'| Effort:',a.effort,'| Reference cases:',target[1])
    print('Do not run unrelated Codex/Work tasks during the pair. Do not reset quota.')
    save()
    for kind in result['order']:
        if kind in result['runs']:
            if not result['runs'][kind].get('valid_task') or result['runs'][kind].get('credits_changed'): print('Previous arm failed or spent credits; no comparable included-quota pair.'); break
            continue
        if kind=='cloud':
            print('\nOpen a NEW Codex Cloud task. Set model to %s, effort %s, standard speed, one attempt.' % (a.model,a.effort))
            if shutil.which('pbcopy'):
                subprocess.run(['pbcopy'],input=PROMPT,text=True,check=True); print('Prompt copied to clipboard.')
            else: print(PROMPT)
            confirm=input('Press Enter when configured (do NOT submit yet); type cancel to stop: ').strip()
            if confirm: print('Stopped before cloud submission. Resume with --resume.'); return
        before=quota(codex)
        print('Starting quota:',{k:v['usedPercent'] for k,v in before['windows'].items()})
        if before.get('ordinary_usage_allowed') is False:
            print('Included quota unavailable; stopping rather than spending credits.'); return
        five=[w for w in before['windows'].values() if w['windowDurationMins']==300]
        weekly=[w for w in before['windows'].values() if w['windowDurationMins']==10080]
        if (five and max(w['usedPercent'] for w in five)>=85) or (weekly and max(w['usedPercent'] for w in weekly)>=90):
            print('Quota headroom is low; stopping. Resume after reset, but cross-window comparability may be weaker.'); return
        r=result['runs'][kind]={'before':before,'after_samples':[]}; save()
        if kind=='local':
            print('Running local Codex...',flush=True); task=run_local(codex,a,target)
        else:
            print('Submit now. When finished, paste the single BENCHMARK_RESULT line.')
            start=time.monotonic(); answer=input('Result: ').strip()
            task={'seconds_including_human_delay':round(time.monotonic()-start,1),'answer':answer,
                  'model_effort_user_confirmed':True,**validate_answer(answer,target)}
        r['task']=task; save()
        print('Sampling quota after completion...',flush=True)
        def record(sample): r['after_samples'].append(sample); save()
        after=settle(codex,a.settle,record)
        r['measurements']=delta(before,after)
        r['valid_task']=task['digest_correct'] and task['query_count_correct'] and task.get('returncode',0)==0
        r['credits_changed']=before.get('credit_balance')!=after.get('credit_balance')
        save(); print(kind, 'quota:',{k:v['delta_pp'] for k,v in r['measurements'].items()},'correct:',r['valid_task'])
        if len(r['after_samples'])>1 and r['after_samples'][-1]['windows']!=r['after_samples'][-2]['windows']:
            print('Note: quota readings changed during the final sample interval; delayed reporting may affect the estimate.')
        if kind=='local' and not any((v.get('delta_pp') or 0)>=5 for k,v in r['measurements'].items() if '/300/' in k):
            print('Local usage is below the 5-point target. The cloud comparison may still be informative, but rounding bounds will be wide.')
        if not r['valid_task']:
            print('Task did not return the correct digest and count. Do not interpret its consumption as equivalent successful work.'); break
        if r['credits_changed']:
            print('Purchased credit balance changed; included-quota comparison may be invalid. Stopping.'); break
    if all(k in result['runs'] and result['runs'][k].get('valid_task') for k in ('local','cloud')):
        l,c=result['runs']['local'],result['runs']['cloud']
        if not l['credits_changed'] and not c['credits_changed']:
            result['cloud_local_quota_ratio']=ratios(l['measurements'],c['measurements'])
            print('\nCloud/local ratios (upper null = unbounded; bounds reflect integer rounding only):')
            for k,v in result['cloud_local_quota_ratio'].items(): print(k,v)
            if not any(v.get('adequate_resolution') for v in result['cloud_local_quota_ratio'].values()): print('Warning: small quota deltas; ratio is imprecise. No automatic retries.')
        else: print('No included-quota ratio: credit balance changed.')
        save()
    print('Results:',dest)


def self_test(target):
    assert target[1]==640 and re.fullmatch('[a-f0-9]{64}',target[0])
    assert expected()==target
    assert validate_answer('BENCHMARK_RESULT %s %d'%target,target)=={'digest_correct':True,'query_count_correct':True}
    assert not validate_answer('BENCHMARK_RESULT %s 1'%target[0],target)['query_count_correct']
    key='codex/300/primary'
    def snap(p,t,reset=100): return {'time':t,'windows':{key:{'usedPercent':p,'resetsAt':reset}}}
    l=delta(snap(10,1),snap(16,2,101)); c=delta(snap(16,3,101),snap(28,4,100))
    assert ratios(l,c)[key]=={'estimate':2.0,'lower':1.571,'upper':2.6,'adequate_resolution':True}
    assert delta(snap(10,1),snap(12,2,200))[key]['delta_pp'] is None
    assert delta(snap(10,99),snap(12,101))[key]['delta_pp'] is None
    assert ratios(delta(snap(10,1),snap(10,2)),c)[key]['upper'] is None
    print('Self-test passed. SQLite reference:',target[1],'queries; SHA-256:',target[0])

if __name__=='__main__':
    try: main()
    except (RuntimeError, OSError) as e: sys.exit('Benchmark error: '+str(e))
