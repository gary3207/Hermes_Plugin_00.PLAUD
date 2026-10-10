"""One explicitly scoped, manual recurring PLAUD job. No business-engine fork."""
from contextlib import contextmanager, closing
from hermes_cli.kanban_db_connect import connect_closing
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import time
import uuid
from typing import Any

JOB = 'plaud-import-v1'
CREATOR = 'hermes-plaud-kanban'
TENANT = 'plaud-manual'
ASSIGNEE = 'plaud-job'
WAIT_BODY = 'Scheduled 수동 실행 대기 — Ready로 이동하면 실행. 날짜·자동 타이머 없음. 성공한 노트는 실패·취소 시에도 보존됩니다.'


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.journal-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(20):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt==19: raise
                time.sleep(.025)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def read(path, default=None) -> Any:
    for attempt in range(20):
        try:
            return json.loads(Path(path).read_text(encoding='utf-8'))
        except FileNotFoundError: return default
        except PermissionError:
            if attempt == 19: raise
            time.sleep(.025)  # Windows replacement briefly refuses shared readers.


@contextmanager
def lock(path, blocking=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b'0'); stream.flush()
        while True:
            try:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if not blocking: raise RuntimeError('JOB_BUSY') from None
                time.sleep(.05)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt': msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else: fcntl.flock(stream, fcntl.LOCK_UN)


def owned(card, journal):
    return bool(card and card.created_by == CREATOR and card.tenant == TENANT
                and card.assignee == ASSIGNEE and card.id in journal['cards']
                and card.idempotency_key == journal['cards'][card.id]['key'])


def ensure_wait(conn, board, state, journal):
    from hermes_cli import kanban_db as db
    wait = journal.get('waiting')
    if wait and owned(db.get_task(conn, wait), journal):
        if journal['cards'][wait]['role'] == 'wait':
            # Fence legacy migration against a concurrent manual start; never park a claim.
            with db.write_txn(conn):
                changed = conn.execute("UPDATE tasks SET status='scheduled', body=? "
                    "WHERE id=? AND status='blocked' AND current_run_id IS NULL "
                    "AND created_by=? AND tenant=? AND assignee=? AND idempotency_key=?",
                    (WAIT_BODY, wait, CREATOR, TENANT, ASSIGNEE, journal['cards'][wait]['key'])).rowcount
                if changed:
                    db._append_event(conn, wait, 'scheduled', {'reason': None, 'actor': CREATOR})
                    db._append_event(conn, wait, 'edited', {'fields': ['body'], 'actor': CREATOR})
        return wait
    key = journal.setdefault('pending_wait_key', JOB + ':wait:' + uuid.uuid4().hex)
    save(state / 'journal.json', journal)  # persist create identity BEFORE native mutation
    card = db.create_task(conn, title='[실행 대기] PLAUD Import',
                          body=WAIT_BODY,
                          initial_status='blocked', assignee=ASSIGNEE, created_by=CREATOR,
                          tenant=TENANT, idempotency_key=key, board=board, workspace_kind='scratch')
    # Native creation cannot start Scheduled; Blocked is safe if this step crashes.
    created = db.get_task(conn, card)
    if not created or created.status not in ('blocked', 'scheduled'):
        raise RuntimeError('WAIT_SCHEDULE_FAILED')
    if created.status == 'blocked' and not db.schedule_task(conn, card):
        raise RuntimeError('WAIT_SCHEDULE_FAILED')
    journal['cards'][card] = {'key': key, 'role': 'wait'}
    journal['waiting'] = card
    journal.pop('pending_wait_key', None)
    save(state / 'journal.json', journal)
    assert owned(db.get_task(conn, card), journal)
    return card


def initialize(board='default', state=None):
    from hermes_cli import kanban_db as db
    state = Path(state) if state is not None else private_state()
    with lock(state / 'state.lock'):
        journal = read(state / 'journal.json', {'job': JOB, 'board': board, 'cards': {}, 'enabled': True})
        if journal['board'] != board or journal['job'] != JOB: raise RuntimeError('SCOPE_MISMATCH')
        if journal.get('active'): raise RuntimeError('JOB_ACTIVE')
        with connect_closing(board=board) as conn:
            return ensure_wait(conn, board, state, journal)


def run_import(engine, config, source, translator, transition, progress=None):
    started = now()
    clock = time.monotonic()
    phases, expected = {}, set()
    current = 0
    began = clock
    def phase(number):
        nonlocal current, began
        if current == number: return
        stamp = now()
        if current:
            phases[current].update(ended=stamp, seconds=time.monotonic()-began)
        current, began = number, time.monotonic()
        phases[number] = dict(phase=number, started=stamp, ended=None, seconds=0)
        transition(dict(phase=number, started=started, phase_started_monotonic=began,
                        phases=[dict(p) for p in phases.values()]))
    class Source:
        def list_records(self):
            phase(2)
            return source.list_records()
        def read_record(self, row):
            phase(3)
            record = source.read_record(row)
            expected.update((record['id'], a['key']) for a in record['artifacts'])
            return record
    phase(1)
    output, _ = engine.paths(config)
    report = engine.sync(config, Source(), translator, progress=progress or (lambda r: None))
    if 3 not in phases:
        phases[3] = dict(phase=3, started=None, ended=None, seconds=0, skipped=True)
    phase(4)
    index = engine.existing_targets(output)
    verified = 0
    failures = []
    for rid, key in sorted(expected):
        targets = index.get((rid, key), [])
        if len(targets) != 1:
            failures.append('ARTIFACT_MISSING_OR_DUPLICATE'); continue
        target = targets[0]
        if target.is_symlink() or not target.resolve().is_relative_to(output.resolve()):
            failures.append('ARTIFACT_OUTSIDE_OUTPUT'); continue
        meta, body = engine.read_note(target.read_bytes())
        if ((meta.get('plaud_plugin'), meta.get('plaud_record_id'), meta.get('plaud_artifact_key'))
                != ('hermes-plaud', rid, key)
                or meta.get('plaud_render_sha256') != engine.digest(body.encode('utf-8'))):
            failures.append('ARTIFACT_INTEGRITY_FAILED'); continue
        verified += 1
    phases[current].update(ended=now(), seconds=time.monotonic()-began)
    return dict(outcome='failed' if report['failed'] or failures else 'success',
                started=started, ended=now(), seconds=time.monotonic()-clock,
                phases=[phases[k] for k in sorted(phases)], verified=verified,
                verification_errors=failures,
                counts={k:report.get(k,0) for k in ('records_total','records_scanned','created','updated','unchanged','restored','renamed')},
                failed=[r['error'] for r in report['failed']], pending=len(report['pending']))


def identity(pid):
    import psutil
    try:
        process = psutil.Process(int(pid))
        return {'pid': process.pid, 'birth': process.create_time()}
    except psutil.NoSuchProcess: return None


def alive(fingerprint):
    return bool(fingerprint and identity(fingerprint['pid']) == fingerprint)


def await_gate(run_dir):
    """No imports/providers/child processes until durable ownership exists."""
    run_dir = Path(run_dir)
    save(run_dir/'hello.json', identity(os.getpid()))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        gate = read(run_dir/'gate.json')
        if gate:
            if gate['worker'] != identity(os.getpid()) or not alive(gate['controller']):
                raise RuntimeError('STARTUP_OWNERSHIP_LOST')
            journal=read(run_dir.parent.parent/'journal.json')
            if (not journal or journal.get('job')!=JOB or journal.get('board')!=gate.get('board')
                    or not journal.get('active') or journal['active']['token']!=gate['token']
                    or Path(gate['directory']).resolve()!=run_dir.resolve()):
                raise RuntimeError('STARTUP_CLAIM_LOST')
            with connect_closing(board=gate['board']) as conn:
                if not active_card(conn,journal,gate): raise RuntimeError('STARTUP_CLAIM_LOST')
            return gate
        time.sleep(.05)
    raise RuntimeError('STARTUP_GATE_TIMEOUT')


def emit(run_dir, event):
    path=Path(run_dir)/'events.jsonl'
    data=(json.dumps(event, ensure_ascii=False)+'\n').encode('utf-8')
    # ponytail: 64 KiB diagnostic budget per run; stage/final evidence is never dropped.
    if event.get('diagnostic') and (path.stat().st_size if path.exists() else 0)+len(data)>64*1024: return
    with path.open('ab') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())


def active_card(conn, journal, active):
    from hermes_cli import kanban_db as db
    card = db.get_task(conn, active['task'])
    return card if owned(card, journal) and card.status == 'running' and card.current_run_id == active['run'] else None


def card_body(active, report):
    names = {1:'준비', 2:'목록 조회', 3:'원문 조회·번역·저장 (레코드별)', 4:'산출물 검증'}
    phase=report.get('phase',report.get('failure_phase',4))
    outcome={'success':'정상 완료','failed':'실패','cancelled':'사용자 취소 / 실행 중단'}.get(report.get('outcome'),'실행 중')
    lines = [f"**{outcome} · {phase}/4 단계: {names[phase]}**",
             f"시작: {active['started']}"]
    if report.get('ended'):
        lines.append(f"종료: {report['ended']} · 총 소요: {report.get('seconds',0):.3f}초")
    rows=['| 단계 | 작업 | 소요시간 |','|---|---|---|']
    for p in report.get('phases',[]):
        duration='미실행' if p.get('skipped') else ('진행 중' if not report.get('ended') and p['phase']==phase else f"{p.get('seconds',0):.3f}초")
        rows.append(f"| {p['phase']}/4 | {names[p['phase']]} | {duration} |")
    lines.append('\n'.join(rows))
    counts=report.get('counts',{})
    if counts:
        lines += [f"처리 기록: {counts.get('records_scanned',0)} / {counts.get('records_total',0)}",
                  f"신규 저장: {counts.get('created',0)} · 갱신: {counts.get('updated',0)} · 변경 없음: {counts.get('unchanged',0)} · 이름 변경: {counts.get('renamed',0)}"]
    if report.get('ended'):
        lines += [f"산출물 검증 통과: {report.get('verified',0)}",
                  f"실패: {', '.join(report.get('failed',[])+report.get('verification_errors',[])) or '없음'} · 미제공 자료: {report.get('pending',0)}",
                  '성공한 노트는 유지됩니다.']
        if report['outcome']=='cancelled': lines.append('집계는 마지막으로 완료한 기록 기준이며, 중단 당시 진행 중이던 기록의 저장분은 일부 미집계일 수 있습니다.')
    lines += [f"로그/독립 요약: `{active['directory']}`",
              f"JOB: {JOB} · run: {active['run']} · correlation: {active['token']}"]
    return '\n\n'.join(lines)


def normalize_report(active, report):
    """Finalize interrupted phase timings and persisted, not guessed, progress."""
    report=dict(report)
    directory=Path(active['directory'])
    events=directory/'events.jsonl'
    if events.exists():
        snapshots=[json.loads(line) for line in events.read_text(encoding='utf-8').splitlines(keepends=True)
                   if line.endswith('\n')]
        snapshots=[e for e in snapshots if e.get('phase') in (1,2,3,4) and not e.get('diagnostic')]
        if snapshots:
            snapshot=snapshots[-1]
            for key in ('phase','phase_started_monotonic'):
                if key in snapshot: report.setdefault(key,snapshot[key])
            if not report.get('phases'): report['phases']=snapshot.get('phases',[])
    counts=read(directory/'counts.json',{})
    report['counts']=(dict(counts,**report.get('counts',{})) if report['outcome']=='success'
                      else dict(report.get('counts',{}),**counts))
    ended=report.setdefault('ended',now())
    current=report.get('phase',report.get('failure_phase',1))
    phases={p['phase']:dict(p) for p in report.get('phases',[])}
    if report['outcome']!='success':
        report.setdefault('failure_phase',current)
        if current in phases and report.get('phase_started_monotonic') is not None:
            phases[current].update(ended=ended,seconds=max(0,time.monotonic()-report['phase_started_monotonic']))
    for number in range(1,5):
        phases.setdefault(number,dict(phase=number,started=None,ended=None,seconds=0,skipped=True))
    report['phases']=[phases[n] for n in range(1,5)]
    report.setdefault('seconds',max(0,time.monotonic()-active.get('started_monotonic',time.monotonic())))
    report.setdefault('started',active['started'])
    return report


def edit_run_card(conn,journal,active,*,body,title=None,terminal=False):
    """Check ownership and mutate under ONE write transaction, not check/edit."""
    from hermes_cli import kanban_db as db
    with db.write_txn(conn):
        card=db.get_task(conn,active['task'])
        if not owned(card,journal): return False
        if terminal:
            latest=conn.execute('SELECT MAX(id) FROM task_runs WHERE task_id=?',(card.id,)).fetchone()[0]
            valid=card.status in ('done','blocked') and card.current_run_id is None and latest==active['run']
        else:
            valid=card.status=='running' and card.current_run_id==active['run']
        if not valid: return False
        if title is None: conn.execute('UPDATE tasks SET body=? WHERE id=?',(body,card.id))
        else: conn.execute('UPDATE tasks SET title=?,body=? WHERE id=?',(title,body,card.id))
        db._append_event(conn,card.id,'edited',{'fields':['body'] if title is None else ['title','body'],
                                             'actor':CREATOR,'expected_run_id':active['run']})
        return True


def finish(conn, board, state, journal, active, report):
    from hermes_cli import kanban_db as db
    report=normalize_report(active,report)
    report.update(task=active['task'], run=active['run'], correlation=active['token'])
    save(Path(active['directory'])/'summary.json', report)
    card = active_card(conn, journal, active)
    if card:
        if report['outcome'] == 'success':
            ok = db.complete_task(conn, card.id, result=card_body(active, report), expected_run_id=active['run'])
        else:
            ok = sticky_block(conn, card.id, 'PLAUD_'+report['outcome'].upper(), expected_run_id=active['run'])
        if not ok: report['outcome'] = 'cancelled'
        else: report['native_finalized']=True
    else:
        terminal=conn.execute('SELECT task_id,outcome FROM task_runs WHERE id=?',(active['run'],)).fetchone()
        old=db.get_task(conn,active['task'])
        completed=bool(terminal and terminal['task_id']==active['task'] and terminal['outcome']=='completed'
                       and old and old.status=='done' and report['outcome']=='success')
        blocked=bool(terminal and terminal['task_id']==active['task'] and terminal['outcome']=='blocked'
                     and old and old.status=='blocked' and report.get('native_finalized'))
        if not completed and not blocked: report['outcome']='cancelled'
    prefix = {'success':'완료','failed':'실패','cancelled':'취소'}[report['outcome']]
    edit_run_card(conn,journal,active,title=f"[{prefix}] PLAUD Import · {active['started']}",
                  body=card_body(active,report),terminal=True)
    save(Path(active['directory'])/'summary.json', report)
    journal['cards'][active['task']]['role'] = 'history'
    journal['cards'][active['task']]['outcome'] = report['outcome']
    history=journal.setdefault('history',[])
    if not any(h['token']==active['token'] for h in history):
        history.append({'token':active['token'],'task':active['task']})
    journal['active'] = None
    if journal.get('waiting') == active['task']: journal['waiting'] = None
    save(state/'journal.json', journal)
    ensure_wait(conn, board, state, journal)
    prune(state,journal)


def supervise(board='default', state=None, fixture_command=None):
    """One controller owns the job lock; the native worker PID is separate."""
    import subprocess
    import sys
    from hermes_cli import kanban_db as db
    from hermes_cli.kanban_db_dispatch import adopt_worker_pid, heartbeat_worker
    from hermes_cli.local_runtime.processes import spawn_server
    state = Path(state) if state is not None else private_state()
    with lock(state/'controller.lock', blocking=False):
        with lock(state/'state.lock'):
            journal = read(state/'journal.json')
            if not journal or not journal.get('enabled'): return
            if journal['board'] != board or journal['job'] != JOB: raise RuntimeError('SCOPE_MISMATCH')
            with connect_closing(board=board) as conn:
                if journal.get('active'):
                    active=journal['active']
                    if alive(active.get('controller')) and active['controller'] != identity(os.getpid()):
                        raise RuntimeError('CONTROLLER_STILL_ALIVE')
                    stop_owned(active)
                    summary=read(Path(active['directory'])/'summary.json')
                    report=summary or dict(outcome='cancelled', ended=now(), failed=['SUPERVISOR_LOST'], phases=[])
                    finish(conn,board,state,journal,active,report)
                    return
                # A controller can die between native claim and journal persistence.
                leaked=[c for c in db.list_tasks(conn,status='running',tenant=TENANT) if owned(c,journal)]
                for card in leaked:
                    sticky_block(conn,card.id,'SUPERVISOR_LOST',expected_run_id=card.current_run_id)
                    db.edit_task(conn,card.id,title='[취소] PLAUD Import · recovery',board=board)
                    journal['cards'][card.id]['role']='history'
                    if journal.get('waiting')==card.id: journal['waiting']=None
                if leaked:
                    save(state/'journal.json',journal)
                    ensure_wait(conn,board,state,journal)
                    return
                block_duplicates(conn,board,journal,journal.get('waiting'))
                cards = [c for c in db.list_tasks(conn, status='ready', tenant=TENANT)
                         if owned(c,journal) and journal['cards'][c.id]['role']=='wait']
                if not cards:
                    ensure_wait(conn,board,state,journal)
                    return
                task = next((c for c in cards if c.id==journal.get('waiting')),cards[0])
                block_duplicates(conn,board,journal,task.id)
                claimed = db.claim_task(conn, task.id, ttl_seconds=86400)
                if not claimed: return
                token = uuid.uuid4().hex
                directory = state/'runs'/token
                directory.mkdir(parents=True)
                active: dict[str,Any] = dict(task=task.id, run=claimed.current_run_id, token=token, board=board, job=JOB, started=now(), started_monotonic=time.monotonic(), directory=str(directory),
                              controller=identity(os.getpid()), worker=None)
                journal['active'] = active
                journal['cards'][task.id]['role'] = 'active'
                save(state/'journal.json', journal)
        command = fixture_command or [sys.executable, str(Path(__file__).resolve()), 'worker']
        try:
            if os.name!='nt': raise RuntimeError('WINDOWS_CONTAINMENT_REQUIRED')
            proc, scope = spawn_server(command+['--run-dir', str(directory)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, env=dict(os.environ, HERMES_KANBAN_BOARD=board))
        except Exception as exc:
            diagnostic(directory,'worker-spawn',exc)
            report=dict(outcome='failed',ended=now(),failed=[type(exc).__name__],failure_phase=1,phases=[])
            with lock(state/'state.lock'):
                with connect_closing(board=board) as conn: finish(conn,board,state,journal,active,report)
            return
        import psutil
        active['launcher'] = identity(proc.pid)
        deadline = time.monotonic()+30
        hello = None
        while proc.poll() is None and time.monotonic()<deadline:
            hello = read(directory/'hello.json')
            if hello: break
            time.sleep(.05)
        candidates = [proc.pid]+[p.pid for p in psutil.Process(proc.pid).children(recursive=True)] if proc.poll() is None else []
        if not hello or hello['pid'] not in candidates or not alive(hello):
            if scope: scope.close()
            if proc.poll() is None: proc.kill()
            proc.wait(timeout=10)
            report=dict(outcome='failed',ended=now(),failed=['WORKER_HANDSHAKE_FAILED'],failure_phase=1,phases=[],exit_code=proc.returncode)
            emit(directory,dict(timestamp=now(),component='worker-startup',diagnostic=True,exit_code=proc.returncode,error_class='StartupOwnershipLoss'))
            with lock(state/'state.lock'):
                with connect_closing(board=board) as conn: finish(conn,board,state,journal,active,report)
            return
        active['worker'] = hello
        with lock(state/'state.lock'):
            save(state/'journal.json', journal)
            save(directory/'gate.json', active)
        offset = 0
        heartbeat = 0
        cancelled = False
        clock = time.monotonic()
        last_event = {'phase':1,'phases':[]}
        with connect_closing(board=board) as conn:
            adopt_worker_pid(conn, task.id, active['run'], active['worker']['pid'])
            edit_run_card(conn,journal,active,title=f"[실행 중] PLAUD Import · {active['started']}",
                          body=card_body(active,{'phase':1}))
            try:
                while proc.poll() is None:
                    block_duplicates(conn,board,journal,task.id)
                    children = tree_identities(active['launcher'])
                    if children != active.get('children'):
                        active['children'] = children
                        with lock(state/'state.lock'): save(state/'journal.json',journal)
                    if not active_card(conn,journal,active):
                        cancelled = True
                        stop_owned(active,scope,proc)
                        break
                    offset,last_event=consume_events(conn,board,journal,active,offset,last_event)
                    if time.monotonic()-heartbeat > 30:
                        heartbeat_worker(conn,task.id,expected_run_id=active['run']); heartbeat=time.monotonic()
                    time.sleep(.05)
                stop_owned(active,scope,proc)
                offset,last_event=consume_events(conn,board,journal,active,offset,last_event)
                report = read(directory/'result.json', {'outcome':'failed', 'failed':['WORKER_NO_RESULT'], 'ended':now()})
                report['exit_code']=proc.returncode
                if proc.returncode!=0:
                    report['outcome']='failed'
                    report['failed']=report.get('failed',[])+['WORKER_NONZERO_EXIT']
                    emit(directory,dict(timestamp=now(),component='worker-exit',diagnostic=True,exit_code=proc.returncode))
                if cancelled or not active_card(conn,journal,active):
                    report = dict(last_event, outcome='cancelled', ended=now(), failed=['OPERATOR_CANCELLED'])
                with lock(state/'state.lock'): finish(conn,board,state,journal,active,report)
            finally:
                if scope: scope.close()
                if proc.poll() is None: proc.kill(); proc.wait(timeout=10)


def tree_identities(root):
    import psutil
    if not alive(root): return []
    try:
        return [root]+[f for p in psutil.Process(root['pid']).children(recursive=True) if (f := identity(p.pid))]
    except psutil.NoSuchProcess: return []


def stop_owned(active, scope=None, proc=None):
    """Close the contained tree, then verify identities, not bare reused PIDs."""
    import psutil
    known = [f for f in [active.get('worker'),active.get('launcher')]+active.get('children',[]) if f]
    if scope: scope.close()
    else:
        # Recovery: Windows job already died with controller; fingerprint checks fence fallback.
        for f in reversed(known):
            if alive(f): psutil.Process(f['pid']).kill()
    if proc: proc.wait(timeout=10)
    deadline=time.monotonic()+10
    while any(alive(f) for f in known):
        if time.monotonic()>deadline: raise RuntimeError('OWNED_TREE_STILL_ALIVE')
        time.sleep(.05)


def diagnostic(run_dir, component, exc):
    import traceback
    causes=[]
    seen=set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        causes.append(dict(error_class=type(exc).__name__,errno=getattr(exc,'errno',None),
                           frames=[dict(file=Path(f.filename).name,function=f.name,line=f.lineno)
                                   for f in traceback.extract_tb(exc.__traceback__)]))
        exc=exc.__cause__ or exc.__context__
    emit(run_dir,dict(timestamp=now(),component=component,causes=causes,diagnostic=True))


def safe_code(exc):
    import re
    code=str(exc)
    return code if re.fullmatch(r'[A-Z][A-Z0-9_]{2,100}',code) else type(exc).__name__


def original_runtime():
    """Reconstruct the ORIGINAL plugin's native context; no guessed config schema."""
    from hermes_cli.plugins import discover_plugins, get_plugin_manager, PluginContext
    discover_plugins()
    manager=get_plugin_manager()
    # Native manager exposes manifest/module only through its loaded registry in this release.
    loaded=manager._plugins.get('hermes-plaud')
    if not loaded or not loaded.enabled or loaded.error or not loaded.module:
        raise RuntimeError('PLAUD_PLUGIN_NOT_LOADED')
    module=loaded.module
    ctx=PluginContext(loaded.manifest,manager)
    config=module.settings(ctx)
    from importlib import import_module
    engine=import_module(module.sync.__module__)
    engine.paths(config)
    return engine,config,module.NativePlaud(config),module.Translator(ctx.llm.complete_structured,config['state'])


def worker(run_dir, runtime=None):
    run_dir=Path(run_dir)
    await_gate(run_dir)
    started,clock=now(),time.monotonic()
    last={'phase':1,'phase_started_monotonic':clock,
          'phases':[dict(phase=1,started=started,ended=None,seconds=0)]}
    emit(run_dir,dict(last,started=started))
    def transition(event):
        nonlocal last
        last=dict(event)
        last['phases']=[dict(p) for p in event['phases']]
        if last['phases']:
            preparation=last['phases'][0]
            preparation['started']=started
            if event['phase']==1:
                last['phase_started_monotonic']=clock
            else:
                preparation['seconds']=max(0,event['phase_started_monotonic']-clock-
                    sum(p['seconds'] for p in last['phases'][1:] if p['phase']<event['phase']))
        if event['phase']!=1: emit(run_dir,last)
    try:
        engine,config,source_context,translator=(runtime or original_runtime)()
        save(run_dir/'engine-state.json',{'state':config['state']})
        with source_context as source:
            class Source:
                def list_records(self):
                    try: return source.list_records()
                    except Exception as exc: diagnostic(run_dir,'inventory',exc); raise
                def read_record(self,row):
                    try: return source.read_record(row)
                    except Exception as exc: diagnostic(run_dir,'record-read',exc); raise
            class Translation:
                cache_identity=translator.cache_identity
                def translate(self,units):
                    try: return translator.translate(units)
                    except Exception as exc: diagnostic(run_dir,'translation',exc); raise
            def counts(report):
                save(run_dir/'counts.json',{k:report.get(k,0) for k in ('records_total','records_scanned','created','updated','unchanged','restored','renamed')})
            counts({})
            report=run_import(engine,config,Source(),Translation(),transition,progress=counts)
        report['started']=started
        report['seconds']=time.monotonic()-clock
        preparation=time.monotonic()-clock-sum(p['seconds'] for p in report['phases'])
        report['phases'][0]['seconds']+=max(0,preparation)
        report['phases'][0]['started']=started
        if report['outcome']=='failed': report['failure_phase']=3 if report['failed'] else 4
    except Exception as exc:
        diagnostic(run_dir,'worker',exc)
        report=dict(last,outcome='failed',started=started,ended=now(),seconds=time.monotonic()-clock,
                    failed=[safe_code(exc)],failure_phase=last['phase'])
    save(run_dir/'result.json',report)
    return report


def launch_controller(board, state):
    import subprocess,sys
    previous=read(Path(state)/'launch.json')
    if alive(previous): return False
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    proc=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'supervise','--board',board,'--state',str(state)],
                          stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                          creationflags=flags,env=dict(os.environ,HERMES_KANBAN_BOARD=board))
    save(Path(state)/'launch.json',identity(proc.pid))
    return True


def dispatch_hook(*, board=None, dry_run=False, state=None, **_):
    """Read-only fast probe; startup is delegated to a deterministic subprocess."""
    import sqlite3
    from hermes_cli import kanban_db as db
    state=Path(state) if state is not None else private_state()
    if dry_run: return False
    journal=read(state/'journal.json')
    if not journal or not journal.get('enabled') or journal['board']!=board: return False
    active=journal.get('active')
    if active and alive(active.get('controller')): return False
    needed=bool(active or journal.get('pending_wait_key') or not journal.get('waiting'))
    path=db.kanban_db_path(board=board)
    if not path.exists(): return False
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.05)) as conn:
        for card,info in journal['cards'].items():
            row=conn.execute('SELECT status,created_by,tenant,assignee,idempotency_key FROM tasks WHERE id=?',(card,)).fetchone()
            if card==journal.get('waiting') and (not row or row[1:]!=(CREATOR,TENANT,ASSIGNEE,info['key'])):
                needed=True
            if row and row[0] in ('ready','running') and row[1:]==(CREATOR,TENANT,ASSIGNEE,info['key']): needed=True
        if journal.get('waiting') and not conn.execute('SELECT id FROM tasks WHERE id=?',(journal['waiting'],)).fetchone(): needed=True
    if not needed: return False
    try:
        with lock(state/'spawn.lock',blocking=False): return launch_controller(board,state)
    except RuntimeError: return False


def parser(existing=None):
    import argparse
    p=existing or argparse.ArgumentParser(prog='plaud-kanban')
    commands=p.add_subparsers(dest='action',required=True)
    for action in ('initialize','status','tick','reconcile','supervise'):
        sub=commands.add_parser(action)
        sub.add_argument('--board',default='default')
        sub.add_argument('--state',type=Path,help=argparse.SUPPRESS)
    sub=commands.add_parser('worker')
    sub.add_argument('--run-dir',type=Path,required=True)
    return p


def execute(args):
    try:
        if args.action=='worker': worker(args.run_dir); return json.dumps({'worker_finished':True})
        state=args.state or private_state()
        if args.action=='initialize': result={'waiting':initialize(args.board,state)}
        elif args.action=='status':
            journal=read(state/'journal.json',{})
            if journal and journal['board']!=args.board: raise RuntimeError('SCOPE_MISMATCH')
            result=journal
        elif args.action=='tick': result={'spawned':dispatch_hook(board=args.board,state=state)}
        else:
            supervise(args.board,state)
            result={'reconciled':True}
        return json.dumps(result,ensure_ascii=False)
    except Exception as exc:
        # Never interpolate arbitrary exception messages into CLI/gateway output.
        return json.dumps({'success':False,'error':safe_code(exc)})


def sticky_block(conn,task_id,reason,expected_run_id=None):
    from hermes_cli import kanban_db as db
    return db.block_task(conn,task_id,reason=reason,kind='transient',expected_run_id=expected_run_id)


def block_duplicates(conn,board,journal,except_task):
    from hermes_cli import kanban_db as db
    for card in db.list_tasks(conn,status='ready',tenant=TENANT):
        if card.id!=except_task and owned(card,journal):
            # A history card is not a new run. Record an actual manual-lane
            # transition without inventing a capability failure or retry attempt.
            with db.write_txn(conn):
                restored='done' if journal['cards'][card.id].get('outcome')=='success' else 'blocked'
                changed=conn.execute("UPDATE tasks SET status=? WHERE id=? AND status='ready' "
                                     "AND current_run_id IS NULL",(restored,card.id)).rowcount
                if changed:
                    db._append_event(conn,card.id,'blocked' if restored=='blocked' else 'status',
                        {'status':restored,'reason':'HISTORY_CARD_IS_NOT_RUNNABLE','actor':CREATOR})
            if changed:
                message='중복/이력 실행 요청 거절: 새 [실행 대기] 카드만 Ready로 이동하세요.'
                if message not in (card.body or ''):
                    db.edit_task(conn,card.id,body=(card.body or '')+'\n\n'+message,board=board)


def prune(state,journal):
    import re,shutil
    history=journal.setdefault('history',[])
    for old in history[:-100]:
        token=old['token']
        if not re.fullmatch('[a-f0-9]{32}',token): raise RuntimeError('UNSAFE_HISTORY_TOKEN')
        directory=state/'runs'/token
        if directory.is_symlink() or not directory.resolve().is_relative_to((state/'runs').resolve()):
            raise RuntimeError('UNSAFE_HISTORY_PATH')
        if directory.exists(): shutil.rmtree(directory)
        if old['task']!=journal.get('waiting'): journal['cards'].pop(old['task'],None)
    journal['history']=history[-100:]
    save(state/'journal.json',journal)


def consume_events(conn,board,journal,active,offset,last):
    from hermes_cli import kanban_db as db
    path=Path(active['directory'])/'events.jsonl'
    if not path.exists(): return offset,last
    lines=path.read_text(encoding='utf-8').splitlines(keepends=True)
    while offset<len(lines) and lines[offset].endswith('\n'):
        event=json.loads(lines[offset]); offset+=1
        if event.get('diagnostic'): continue
        if event.get('phase') not in (1,2,3,4): raise RuntimeError('INVALID_PHASE_EVENT')
        last=event
        event['counts']=read(Path(active['directory'])/'counts.json',{})
        edit_run_card(conn,journal,active,body=card_body(active,event))
    return offset,last


def private_state():
    from hermes_constants import get_hermes_home
    return get_hermes_home() / 'plugin-data' / CREATOR


if __name__=='__main__':
    print(execute(parser().parse_args()))
