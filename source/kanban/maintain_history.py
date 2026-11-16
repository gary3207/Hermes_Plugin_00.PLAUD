"""Scoped display deployment; never executes Import or completion recovery."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import re
import time

import plaud_kanban as job
from hermes_cli import kanban_db as db


def durable_backup(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def refresh(conn, journal, task_id, body, backup, *, waiting=False):
    with db.write_txn(conn):
        card = db.get_task(conn, task_id)
        if card is None or not job.owned(card, journal) or card.current_run_id is not None:
            raise RuntimeError('HISTORY_NOT_IDLE_OR_OWNED')
        if waiting:
            if (task_id != journal.get('waiting') or card.status != 'scheduled'
                    or journal['cards'][task_id]['role'] != 'wait'):
                raise RuntimeError('WAIT_NOT_IDLE_OR_OWNED')
        else:
            if card.status not in ('done', 'blocked') or journal['cards'][task_id]['role'] == 'wait':
                raise RuntimeError('HISTORY_NOT_IDLE_OR_OWNED')
            latest = conn.execute('SELECT * FROM task_runs WHERE task_id=? ORDER BY id DESC LIMIT 1',
                                  (task_id,)).fetchone()
            if latest is None or not latest['ended_at']:
                raise RuntimeError('HISTORY_RUN_NOT_ENDED')
        if card.body == body:
            return False
        durable_backup(backup, dict(task=dict(conn.execute('SELECT * FROM tasks WHERE id=?',
                       (task_id,)).fetchone()), runs=[dict(r) for r in conn.execute(
                       'SELECT * FROM task_runs WHERE task_id=?', (task_id,))]))
        conn.execute('UPDATE tasks SET body=? WHERE id=?', (body, task_id))
        db._append_event(conn, task_id, 'edited', {'fields': ['body'], 'actor': job.CREATOR,
                                                 'reason': 'Detailed conditional plan; measured evidence preserved'})
    return True


def main():
    from hermes_constants import get_hermes_home
    from gateway.control_socket import reload_gateway_plugins
    home = get_hermes_home()
    installed = home / 'plugins/hermes-plaud-kanban'
    spec = importlib.util.spec_from_file_location('installed_history_baseline', installed/'plaud_kanban.py')
    if spec is None or spec.loader is None:
        raise RuntimeError('CONNECTOR_IMPORT_UNAVAILABLE')
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    state = old.private_state()
    with ExitStack() as stack:
        for name in ('spawn.lock', 'controller.lock', 'state.lock'):
            stack.enter_context(old.lock(state/name, blocking=False))
        journal_bytes = (state/'journal.json').read_bytes()
        journal = json.loads(journal_bytes)
        if journal.get('active') or old.alive(old.read(state/'launch.json')):
            raise RuntimeError('JOB_ACTIVE')
        selected = [journal['history'][-1]]
        failed = next((item for item in reversed(journal['history'])
                       if journal['cards'][item['task']].get('outcome') == 'failed'), None)
        if failed is not None and failed not in selected:
            selected.append(failed)
        with old.connect_closing(board=journal['board']) as conn:
            updates = []
            for item in selected:
                task_id, token = item['task'], item['token']
                if not re.fullmatch('[0-9a-f]{32}', token):
                    raise RuntimeError('INVALID_RUN_TOKEN')
                directory = state/'runs'/token
                report = old.read(directory/'summary.json')
                if report['task'] != task_id or report['correlation'] != token or not report.get('native_finalized'):
                    raise RuntimeError('HISTORY_SUMMARY_MISMATCH')
                card = db.get_task(conn, task_id)
                if card is None or not card.body:
                    raise RuntimeError('HISTORY_BODY_MISSING')
                # Preserve the controller's recorded start, not the later engine timestamp.
                started = next(line[4:] for line in card.body.splitlines() if line.startswith('시작: '))
                active = dict(started=started, directory=str(directory), run=report['run'], token=token)
                if old.card_body(active, report) != card.body:
                    raise RuntimeError('HISTORY_BASELINE_CHANGED')
                updates.append((task_id, job.card_body(active, report), False))
            waiting = db.get_task(conn, journal['waiting'])
            if waiting is None or waiting.body != old.WAIT_BODY:
                raise RuntimeError('WAITING_BASELINE_CHANGED')
            updates.append((waiting.id, job.WAIT_BODY, True))
            before_tasks = [dict(r) for r in conn.execute('SELECT * FROM tasks ORDER BY id')]
            before_runs = [dict(r) for r in conn.execute('SELECT * FROM task_runs ORDER BY id')]
            backup = state/'backups'/f'history-{time.time_ns()}'
            backup.mkdir(parents=True)
            sources = [Path(__file__).with_name('plaud_kanban.py'),
                       Path(__file__).parent/'skills/plaud-kanban-manual-jobs/SKILL.md']
            for source in sources:
                relative = source.relative_to(Path(__file__).parent)
                target = installed/relative
                previous = target.read_bytes()
                saved = backup/relative
                saved.parent.mkdir(parents=True, exist_ok=True)
                with saved.open('xb') as stream:
                    stream.write(previous); stream.flush(); os.fsync(stream.fileno())
                with target.with_suffix(target.suffix+'.history-tmp').open('xb') as stream:
                    stream.write(source.read_bytes()); stream.flush(); os.fsync(stream.fileno())
                os.replace(stream.name, target)
                assert target.read_bytes() == source.read_bytes()
            changed = []
            for task_id, body, is_waiting in updates:
                if refresh(conn, journal, task_id, body, backup/(task_id+'.json'), waiting=is_waiting):
                    changed.append(task_id)
            after_tasks = [dict(r) for r in conn.execute('SELECT * FROM tasks ORDER BY id')]
            bodies = {task_id: body for task_id, body, _ in updates}
            for row in before_tasks:
                if row['id'] in bodies:
                    row['body'] = bodies[row['id']]
            assert before_tasks == after_tasks
            assert before_runs == [dict(r) for r in conn.execute('SELECT * FROM task_runs ORDER BY id')]
            assert (state/'journal.json').read_bytes() == journal_bytes
        with old.connect_closing(board=journal['board']) as conn:
            for task_id, body, _ in updates:
                saved_card = db.get_task(conn, task_id)
                assert saved_card is not None and saved_card.body == body
        result: dict = dict(installed=True, updated_cards=changed, backup=str(backup),
                      task_fields_preserved=True, runs_preserved=True, journal_preserved=True,
                      import_executed=False)
    result['reload'] = reload_gateway_plugins(home, profile_home=home)
    job.save(Path(__file__).parent.parent/'detailed-history-deployment.json', result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
