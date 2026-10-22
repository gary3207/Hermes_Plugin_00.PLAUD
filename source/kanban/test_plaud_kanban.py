"""Offline fixtures only: disposable native DB; never authenticated PLAUD."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
# Native delegated-child fence covers the Hermes home (including its cache).
# Test-only disposable native boards stay inside the explicitly approved source workspace.
SCRATCH = Path(__file__).parent/'test-sandboxes'
SCRATCH.mkdir(parents=True,exist_ok=True)

class NativeBoardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.home = Path(self.temp.name)
        self.env = patch.dict(os.environ, {
            'HERMES_HOME': str(self.home), 'HERMES_KANBAN_HOME': str(self.home),
            'HERMES_KANBAN_BOARD': 'default', 'HERMES_KANBAN_DB': '',
            'HERMES_KANBAN_WORKSPACES_ROOT': '', 'HERMES_KANBAN_ATTACHMENTS_ROOT': ''})
        self.env.start()
        from hermes_cli import kanban_db as db
        self.assertTrue(db.kanban_db_path(board='default').resolve().is_relative_to(self.home.resolve()))
        import plaud_kanban as job
        self.job = job
        self.state = self.home / 'private'
    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()
    def test_wait_is_scheduled_and_initialize_idempotent(self):
        from hermes_cli import kanban_db as db
        first = self.job.initialize('default', self.state)
        self.assertEqual(first, self.job.initialize('default', self.state))
        with self.job.connect_closing(board='default') as conn:
            db.recompute_ready(conn)
            from hermes_cli.kanban_db_dispatch import dispatch_once
            dispatch_once(conn, dry_run=True, board='default')
            card = db.get_task(conn, first)
            assert card is not None
            self.assertEqual('scheduled', card.status)
            self.assertEqual('plaud-job', card.assignee)
            self.assertEqual(1, len(db.list_tasks(conn)))
            self.assertTrue(card.title.startswith('[실행 대기]'))
            self.assertIsNone(card.current_run_id)
            self.assertEqual(0, conn.execute('SELECT count(*) FROM task_runs WHERE task_id=?', (first,)).fetchone()[0])
        with patch.object(self.job, 'launch_controller') as launch:
            self.assertFalse(self.job.dispatch_hook(board='default', state=self.state))
            launch.assert_not_called()

    def test_initialize_migrates_only_legacy_wait_without_execution(self):
        from hermes_cli import kanban_db as db
        with self.job.connect_closing(board='default') as conn:
            key = self.job.JOB + ':wait:legacy'
            first = db.create_task(conn, title='[실행 대기] PLAUD Import', body='legacy waiting card',
                initial_status='blocked', assignee=self.job.ASSIGNEE, created_by=self.job.CREATOR,
                tenant=self.job.TENANT, idempotency_key=key, board='default', workspace_kind='scratch')
            runs = conn.execute('SELECT count(*) FROM task_runs').fetchone()[0]
        self.job.save(self.state/'journal.json', dict(job=self.job.JOB, board='default', enabled=True,
            waiting=first, active=None, cards={first:dict(key=key, role='wait')}))
        self.assertEqual(first, self.job.initialize('default', self.state))
        with self.job.connect_closing(board='default') as conn:
            card = db.get_task(conn, first)
            assert card is not None
            self.assertEqual('scheduled', card.status)
            self.assertEqual(self.job.WAIT_BODY, card.body)
            self.assertEqual(runs, conn.execute('SELECT count(*) FROM task_runs').fetchone()[0])
            db.unblock_task(conn, first)
        self.assertEqual(first, self.job.initialize('default', self.state))
        with self.job.connect_closing(board='default') as conn:
            card = db.get_task(conn, first)
            assert card is not None
            self.assertEqual('ready', card.status)
        self.assertIsNone(self.job.read(self.state/'journal.json').get('active'))

    def test_atomic_journal_replace_retries_transient_windows_reader_lock(self):
        target=self.state/'fixture.json'
        self.job.save(target,{'before':True})
        original=os.replace
        calls=[]
        def transient(src,dst):
            calls.append(dst)
            if len(calls)==1: raise PermissionError('synthetic reader lock')
            return original(src,dst)
        with patch.object(os,'replace',side_effect=transient): self.job.save(target,{'after':True})
        self.assertEqual({'after':True},self.job.read(target))
        self.assertEqual(2,len(calls))

    def test_fixture_worker_passes_native_install_guard_without_override(self):
        import shutil
        from tools.plugin_guard import scan_plugin, should_allow_plugin_install
        payload = self.home/'scan-fixture'
        payload.mkdir()
        shutil.copy2(Path(__file__).with_name('fixture_worker.py'), payload/'fixture_worker.py')
        report = scan_plugin(payload)
        self.assertEqual('safe', report.verdict)
        self.assertIs(True, should_allow_plugin_install(report)[0])

    def test_manual_ready_runs_once_and_regenerates_one_wait(self):
        import sys
        from hermes_cli import kanban_db as db
        first = self.job.initialize('default', self.state)
        with self.job.connect_closing(board='default') as conn: db.unblock_task(conn, first)
        fixture = [sys.executable, str(Path(__file__).with_name('fixture_worker.py'))]
        self.job.supervise('default', self.state, fixture_command=fixture)
        journal = self.job.read(self.state/'journal.json')
        with self.job.connect_closing(board='default') as conn:
            self.assertEqual('done', db.get_task(conn, first).status, str([(p.name,p.read_text(encoding='utf-8')) for p in (self.state/'runs').glob('*/*.json')]))
            card=db.get_task(conn,first)
            assert card is not None
            self.assertRegex(card.title,r'\d{4}년 \d{1,2}월 \d{1,2}일 (오전|오후)')
            self.assertNotEqual(first, journal['waiting'])
            self.assertEqual('scheduled', db.get_task(conn, journal['waiting']).status)
            self.assertEqual(2, len(db.list_tasks(conn)))
            edits=conn.execute("SELECT count(*) FROM task_events WHERE task_id=? AND kind='edited'",(first,)).fetchone()[0]
            self.assertEqual(6,edits,'start + four phases + terminal; diagnostics are private only')
        run = list((self.state/'runs').glob('*/summary.json'))
        self.assertEqual(1, len(run))
        report = self.job.read(run[0])
        self.assertEqual(1,len(journal['history']))
        self.assertEqual(report['correlation'],journal['history'][0]['token'])
        self.assertEqual('success', report['outcome'])
        self.assertEqual(first, report['task'])
        for _ in range(3): self.job.supervise('default', self.state, fixture_command=fixture)
        self.assertEqual(journal['waiting'], self.job.read(self.state/'journal.json')['waiting'])

    def test_success_stores_report_only_in_description_and_recovers_same_run(self):
        from hermes_cli import kanban_db as db
        first=self.job.initialize('default',self.state)
        journal=self.job.read(self.state/'journal.json')
        directory=self.state/'runs'/('c'*32); directory.mkdir(parents=True)
        with self.job.connect_closing(board='default') as conn:
            db.unblock_task(conn,first); claimed=db.claim_task(conn,first)
            assert claimed is not None
            active=dict(task=first,run=claimed.current_run_id,token='c'*32,
                        started=self.job.now(),directory=str(directory))
            report=dict(outcome='success',seconds=1.25,verified=2,
                        counts=dict(records_total=3,records_scanned=3,created=2,unchanged=1))
            self.job.finish(conn,'default',self.state,journal,active,report)
            for recovery in (False,True):
                if recovery: self.job.finish(conn,'default',self.state,journal,active,report)
                card=db.get_task(conn,first)
                assert card is not None and card.body is not None and card.result is not None
                run=conn.execute('SELECT * FROM task_runs WHERE id=?',(active['run'],)).fetchone()
                self.assertEqual('done',card.status)
                self.assertEqual('completed',run['outcome'])
                self.assertEqual(first,run['task_id'])
                self.assertIn('| 단계 |',card.body)
                self.assertIn('신규 저장: 2',card.body)
                self.assertIn('산출물 검증 통과: 2',card.body)
                self.assertIn(active['token'],card.body)
                for text in (card.result,run['summary']):
                    self.assertTrue(text)
                    self.assertNotEqual(card.body,text)
                    self.assertNotIn('| 단계 |',text)
                    self.assertNotIn(active['token'],text)
                    self.assertIn('완료',text)
                    self.assertIn('Description',text)
                    self.assertLess(len(text),250)
                self.assertIn('산출물 검증 통과: 2',card.result)
                self.assertIn('0분 01.250초',card.result)
                self.assertEqual(1,conn.execute('SELECT count(*) FROM task_runs').fetchone()[0])
                self.assertEqual(1,len([e for e in db.list_events(conn,first) if e.kind=='completed']))

    def test_cancel_block_stops_owned_child_tree_and_prevents_late_done(self):
        self.cancel_run(False)
    def test_cancel_deletion_preserves_summary_and_regenerates_wait(self):
        self.cancel_run(True)
    def test_cancel_summary_preserves_counts_and_measures_active_phase(self):
        import time
        directory=self.state/'runs'/'fixture'; directory.mkdir(parents=True)
        self.job.save(directory/'counts.json',{'records_total':3,'records_scanned':1,'created':1})
        active={'started':self.job.now(),'started_monotonic':time.monotonic()-.2,'directory':str(directory)}
        report={'outcome':'cancelled','phase':3,'phase_started_monotonic':time.monotonic()-.1,
                'phases':[{'phase':1,'seconds':.02},{'phase':2,'seconds':.03},{'phase':3,'seconds':0}]}
        normalized=self.job.normalize_report(active,report)
        self.assertEqual(1,normalized['counts']['created'])
        self.assertEqual(4,len(normalized['phases']))
        self.assertGreaterEqual(normalized['phases'][2]['seconds'],.1)
        self.assertTrue(normalized['phases'][3]['skipped'])
        self.assertTrue(normalized['phases'][3]['unreported'])
        self.assertGreaterEqual(normalized['seconds'],.2)
        self.assertEqual(3,normalized['failure_phase'])
    def test_card_text_has_readable_phase_table_and_korean_counts(self):
        active={'run':1,'token':'fixture','started':'2026-10-10T10:00:00+09:00','directory':'fixture'}
        report={'outcome':'success','ended':'2026-10-10T10:01:00+09:00','seconds':60,
                'counts':{'records_total':3,'records_scanned':3,'created':2,'unchanged':1},
                'phases':[{'phase':1,'seconds':.2}]}
        body=self.job.card_body(active,report)
        self.assertIn('| 단계 |',body)
        self.assertIn('신규 저장: 2',body)
        self.assertIn('완료',body)
        for stage in ('1/4','2/4','3/4','4/4','3.1','3.2','3.3'):
            self.assertIn(f'| {stage} |',body)
        self.assertIn('총 소요: 1분 00.000초',body)
        self.assertIn('| 2/4 | 목록 조회 | 미보고 / 기록 없음 |',body)
        self.assertIn('합산하지 않음',body)
        self.assertEqual('0분 00.000초',self.job.format_duration(-1))
        self.assertEqual('1분 00.000초',self.job.format_duration(59.9999))
    def test_supervisor_crash_recovery_stops_tree_before_new_wait(self):
        self.cancel_run(False, crash=True)
    def cancel_run(self, delete, crash=False):
        import sys, subprocess, time
        from hermes_cli import kanban_db as db
        first=self.job.initialize('default',self.state)
        with self.job.connect_closing(board='default') as conn: db.unblock_task(conn,first)
        controller=subprocess.Popen([sys.executable,str(Path(__file__).with_name('fixture_controller.py')),'--state',str(self.state)])
        try:
            deadline=time.monotonic()+10
            child=None
            while time.monotonic()<deadline:
                journal=self.job.read(self.state/'journal.json')
                active=journal.get('active')
                if active: child=self.job.read(Path(active['directory'])/'fixture-child.json')
                if child: break
                time.sleep(.05)
            self.assertTrue(child,'synthetic child must actually start')
            worker=active['worker']
            if crash:
                import psutil
                psutil.Process(active['controller']['pid']).kill()
                controller.wait(timeout=10)
                self.job.supervise('default',self.state)
            else:
                with self.job.connect_closing(board='default') as conn:
                    if delete: db.delete_task(conn,first)
                    else: db.block_task(conn,first,reason='test operator cancellation')
                try: controller.wait(timeout=5)
                except subprocess.TimeoutExpired: self.fail('controller did not cancel promptly')
            self.assertFalse(self.job.alive(worker))
            self.assertFalse(self.job.alive(child))
            summary=self.job.read(Path(active['directory'])/'summary.json')
            self.assertEqual('cancelled',summary['outcome'])
            journal=self.job.read(self.state/'journal.json')
            with self.job.connect_closing(board='default') as conn:
                card=db.get_task(conn,first)
                if not delete:
                    self.assertEqual('blocked',card.status)
                    self.assertTrue(card.title.startswith('[취소]'))
                else: self.assertIsNone(card)
                self.assertEqual('scheduled',db.get_task(conn,journal['waiting']).status)
        finally:
            if controller.poll() is None:
                active=self.job.read(self.state/'journal.json').get('active')
                import psutil
                if active and self.job.alive(active['controller']): psutil.Process(active['controller']['pid']).kill()
                controller.wait(timeout=10)
            time.sleep(.2)

    def test_native_plugin_cli_hook_and_original_context_in_disposable_home(self):
        import shutil
        root=Path(__file__).parent
        destination=self.home/'plugins'/'hermes-plaud-kanban'; destination.mkdir(parents=True)
        for name in ('__init__.py','plugin.yaml','plaud_kanban.py'): shutil.copy2(root/name,destination/name)
        shutil.copytree(root/'skills',destination/'skills')
        original=self.home/'plugins'/'hermes-plaud'; original.mkdir()
        installed=Path(__file__).resolve().parent.parent
        for name in ('__init__.py','plugin.yaml'): shutil.copy2(installed/name,original/name)
        shutil.copytree(installed/'plaud_obsidian',original/'plaud_obsidian',ignore=shutil.ignore_patterns('__pycache__'))
        (self.home/'config.yaml').write_text('plugins:\n  enabled: [hermes-plaud, hermes-plaud-kanban]\n',encoding='utf-8')
        from hermes_cli.plugins import get_plugin_manager,discover_plugins,PluginContext
        discover_plugins(force=True)
        manager=get_plugin_manager()
        loaded=manager._plugins['hermes-plaud-kanban']
        self.assertTrue(loaded.enabled,loaded.error)
        self.assertIn('on_kanban_dispatch_tick',loaded.hooks_registered)
        skill=manager._plugin_skills['hermes-plaud-kanban:plaud-kanban-manual-jobs']
        self.assertEqual(destination/'skills/plaud-kanban-manual-jobs/SKILL.md',skill['path'])
        self.assertTrue(skill['path'].is_file())
        self.assertEqual('Use when operating PLAUD Kanban manual Import jobs.',skill['description'])
        plaud=manager._plugins['hermes-plaud']
        ctx=PluginContext(plaud.manifest,manager)
        vault=self.home/'vault'; vault.mkdir()
        ctx.set_config('vault',str(vault)); ctx.set_config('output','notes')
        engine,config,source,translator=self.job.original_runtime()
        self.assertEqual(str(vault),config['vault'])
        self.assertEqual('notes',config['output'])
        self.assertEqual('plaud_obsidian.engine',engine.__name__)
        self.assertEqual('NativePlaud',type(source).__name__)
        self.assertEqual('Translator',type(translator).__name__)
        self.job.initialize('default',self.state)
        import subprocess,sys,json
        cli=subprocess.run([str(Path(sys.executable).with_name('hermes.exe')),'plaud-kanban','status','--board','default','--state',str(self.state)],capture_output=True,text=True,timeout=30)
        self.assertEqual(0,cli.returncode,cli.stderr)
        self.assertEqual(self.job.JOB,json.loads(cli.stdout)['job'])
        with patch.object(self.job,'launch_controller') as launch:
            self.job.dispatch_hook(board='other',state=self.state)
            self.job.dispatch_hook(board='default',dry_run=True,state=self.state)
            self.job.dispatch_hook(board='default',state=self.state)
            launch.assert_not_called()
        from hermes_cli import kanban_db as db
        from hermes_cli.kanban_db_dispatch import dispatch_once
        hook_module=__import__('sys').modules[loaded.module.dispatch_hook.__module__]
        waiting=self.job.read(self.state/'journal.json')['waiting']
        with self.job.connect_closing(board='default') as conn:
            db.unblock_task(conn,waiting)
            with patch.object(hook_module,'private_state',return_value=self.state), patch.object(hook_module,'launch_controller',return_value=True) as launch:
                dispatch_once(conn,board='default',dry_run=False,spawn_fn=lambda *a,**k:self.fail('native must not spawn a profile worker for plaud-job'))
                self.assertEqual('ready',db.get_task(conn,waiting).status)
                launch.assert_called_once()
        manager.unload()

    def test_concurrent_initialize_has_exactly_one_card(self):
        import subprocess,sys,json
        command=[sys.executable,str(Path(__file__).with_name('plaud_kanban.py')),'initialize','--board','default','--state',str(self.state)]
        children=[subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(4)]
        outputs=[child.communicate(timeout=20) for child in children]
        ids=[]
        for child,(out,err) in zip(children,outputs):
            self.assertEqual(0,child.returncode,err)
            self.assertIn('waiting',json.loads(out),out)
            ids.append(json.loads(out)['waiting'])
        self.assertEqual(1,len(set(ids)))
        from hermes_cli import kanban_db as db
        with self.job.connect_closing(board='default') as conn: self.assertEqual(1,len(db.list_tasks(conn)))
    def test_failed_history_plus_new_ready_is_duplicate_not_two_runs(self):
        import sys
        from hermes_cli import kanban_db as db
        fixture=[sys.executable,str(Path(__file__).with_name('fixture_worker.py')),'--mode','fail']
        first=self.job.initialize('default',self.state)
        with self.job.connect_closing(board='default') as conn: db.unblock_task(conn,first)
        self.job.supervise('default',self.state,fixture_command=fixture)
        journal=self.job.read(self.state/'journal.json'); next_card=journal['waiting']
        with self.job.connect_closing(board='default') as conn:
            self.assertEqual('blocked',db.get_task(conn,first).status)
            self.assertTrue(db.get_task(conn,first).title.startswith('[실패]'))
            db.unblock_task(conn,first); db.unblock_task(conn,next_card)
        fixture=[sys.executable,str(Path(__file__).with_name('fixture_worker.py'))]
        self.job.supervise('default',self.state,fixture_command=fixture)
        with self.job.connect_closing(board='default') as conn:
            self.assertEqual('blocked',db.get_task(conn,first).status)
            self.assertIn('중복',db.get_task(conn,first).body)
            self.assertEqual('done',db.get_task(conn,next_card).status)
            self.assertEqual(3,len(db.list_tasks(conn)))
        self.assertEqual(2,len(list((self.state/'runs').glob('*/summary.json'))))
    def test_deleted_wait_regenerated_without_running_job(self):
        from hermes_cli import kanban_db as db
        first=self.job.initialize('default',self.state)
        with self.job.connect_closing(board='default') as conn: db.delete_task(conn,first)
        self.job.supervise('default',self.state)
        next_card=self.job.read(self.state/'journal.json')['waiting']
        self.assertNotEqual(first,next_card)
        with self.job.connect_closing(board='default') as conn:
            self.assertEqual('scheduled',db.get_task(conn,next_card).status)
            self.assertEqual(1,len(db.list_tasks(conn)))

    def test_history_card_never_executes_again_or_escalates_to_triage(self):
        import sys
        from hermes_cli import kanban_db as db
        first=self.job.initialize('default',self.state)
        with self.job.connect_closing(board='default') as conn: db.unblock_task(conn,first)
        fixture=[sys.executable,str(Path(__file__).with_name('fixture_worker.py')),'--mode','fail']
        self.job.supervise('default',self.state,fixture_command=fixture)
        waiting=self.job.read(self.state/'journal.json')['waiting']
        for _ in range(4):
            with self.job.connect_closing(board='default') as conn: db.unblock_task(conn,first)
            self.job.supervise('default',self.state,fixture_command=fixture)
            with self.job.connect_closing(board='default') as conn:
                db.recompute_ready(conn)
                self.assertEqual('blocked',db.get_task(conn,first).status)
        self.assertEqual(1,len(list((self.state/'runs').glob('*/summary.json'))))
        self.assertEqual(waiting,self.job.read(self.state/'journal.json')['waiting'])

    def test_initialize_does_not_regenerate_deleted_active_card(self):
        from hermes_cli import kanban_db as db
        first=self.job.initialize('default',self.state)
        journal=self.job.read(self.state/'journal.json')
        journal['active']={'task':first,'worker':self.job.identity(os.getpid())}
        self.job.save(self.state/'journal.json',journal)
        with self.job.connect_closing(board='default') as conn: db.delete_task(conn,first)
        with self.assertRaisesRegex(RuntimeError,'JOB_ACTIVE'):
            self.job.initialize('default',self.state)
        with self.job.connect_closing(board='default') as conn: self.assertEqual([],db.list_tasks(conn))

    def test_hook_recovers_claim_without_active_journal_and_missing_wait(self):
        from hermes_cli import kanban_db as db
        first=self.job.initialize('default',self.state)
        with self.job.connect_closing(board='default') as conn:
            db.unblock_task(conn,first); db.claim_task(conn,first)
        with patch.object(self.job,'launch_controller',return_value=True) as launch:
            self.assertTrue(self.job.dispatch_hook(board='default',state=self.state))
            launch.assert_called_once()
        journal=self.job.read(self.state/'journal.json'); journal['waiting']=None
        self.job.save(self.state/'journal.json',journal)
        with self.job.connect_closing(board='default') as conn:
            db.block_task(conn,first,reason='fixture missing wait')
        with patch.object(self.job,'launch_controller',return_value=True) as launch:
            self.assertTrue(self.job.dispatch_hook(board='default',state=self.state))
            launch.assert_called_once()

    def test_old_run_cannot_publish_progress_after_new_claim(self):
        from hermes_cli import kanban_db as db
        first=self.job.initialize('default',self.state)
        journal=self.job.read(self.state/'journal.json')
        with self.job.connect_closing(board='default') as conn:
            db.unblock_task(conn,first); original=db.claim_task(conn,first)
            active={'task':first,'run':original.current_run_id}
            db.block_task(conn,first,reason='synthetic cancellation',expected_run_id=original.current_run_id)
            db.unblock_task(conn,first); newer=db.claim_task(conn,first)
            db.edit_task(conn,first,body='new claim body')
            self.assertNotEqual(active['run'],newer.current_run_id)
            self.assertFalse(self.job.edit_run_card(conn,journal,active,body='stale body'))
            self.assertEqual('new claim body',db.get_task(conn,first).body)

    def test_recovery_provisional_success_is_cancelled_after_operator_block(self):
        self.recovery_terminal(False)

    def test_recovery_after_native_completion_publishes_history(self):
        self.recovery_terminal(True)

    def recovery_terminal(self, completed):
        from hermes_cli import kanban_db as db
        first=self.job.initialize('default',self.state)
        journal=self.job.read(self.state/'journal.json')
        token='b'*32; directory=self.state/'runs'/token; directory.mkdir(parents=True)
        with self.job.connect_closing(board='default') as conn:
            db.unblock_task(conn,first); claimed=db.claim_task(conn,first)
            active=dict(task=first,run=claimed.current_run_id,token=token,board='default',job=self.job.JOB,
                        started=self.job.now(),directory=str(directory),controller=None,worker=None)
            journal['active']=active; journal['cards'][first]['role']='active'
            self.job.save(self.state/'journal.json',journal)
            self.job.save(directory/'summary.json',dict(outcome='success',ended=self.job.now(),phases=[]))
            if completed: db.complete_task(conn,first,result='synthetic completed result',expected_run_id=claimed.current_run_id)
            else: db.block_task(conn,first,reason='synthetic operator cancellation',expected_run_id=claimed.current_run_id)
        self.job.supervise('default',self.state)
        summary=self.job.read(directory/'summary.json')
        self.assertEqual('success' if completed else 'cancelled',summary['outcome'])
        journal=self.job.read(self.state/'journal.json')
        self.assertEqual(1,len(journal['history']))
        with self.job.connect_closing(board='default') as conn:
            self.assertTrue(db.get_task(conn,first).title.startswith('[완료]' if completed else '[취소]'))
            self.assertEqual('scheduled',db.get_task(conn,journal['waiting']).status)

    def test_hook_detects_deleted_waiting_card(self):
        from hermes_cli import kanban_db as db
        first=self.job.initialize('default',self.state)
        with self.job.connect_closing(board='default') as conn: db.delete_task(conn,first)
        with patch.object(self.job,'launch_controller') as launch:
            self.job.dispatch_hook(board='default',state=self.state)
            launch.assert_called_once()

    def test_spawn_failure_is_logged_blocked_and_regenerated(self):
        from hermes_cli import kanban_db as db
        first=self.job.initialize('default',self.state)
        with self.job.connect_closing(board='default') as conn: db.unblock_task(conn,first)
        with patch('hermes_cli.local_runtime.processes.spawn_server',side_effect=OSError('DO_NOT_LOG_PRIVATE_DATA')):
            self.job.supervise('default',self.state)
        journal=self.job.read(self.state/'journal.json')
        self.assertIsNone(journal['active'])
        self.assertNotEqual(first,journal['waiting'])
        summary=next((self.state/'runs').glob('*/summary.json'))
        self.assertEqual('failed',self.job.read(summary)['outcome'])
        self.assertNotIn('DO_NOT_LOG',''.join(p.read_text(encoding='utf-8') for p in (self.state/'runs').glob('*/*') if p.is_file()))
    def test_private_retention_keeps_100_runs_without_deleting_native_history(self):
        self.job.initialize('default',self.state)
        journal=self.job.read(self.state/'journal.json')
        for i in range(105):
            token=f'{i:032x}'
            directory=self.state/'runs'/token; directory.mkdir(parents=True)
            self.job.save(directory/'summary.json',{'outcome':'success'})
            journal.setdefault('history',[]).append({'token':token,'task':f'fixture-{i}'})
        self.job.prune(self.state,journal)
        self.assertEqual(100,len(journal['history']))
        self.assertEqual(100,len(list((self.state/'runs').iterdir())))
        from hermes_cli import kanban_db as db
        with self.job.connect_closing(board='default') as conn: self.assertEqual(1,len(db.list_tasks(conn)))

    def test_fast_worker_drains_all_phase_transitions(self):
        import sys
        from hermes_cli import kanban_db as db
        first=self.job.initialize('default',self.state)
        with self.job.connect_closing(board='default') as conn: db.unblock_task(conn,first)
        self.job.supervise('default',self.state,fixture_command=[sys.executable,str(Path(__file__).with_name('fixture_worker.py')),'--mode','fast'])
        with self.job.connect_closing(board='default') as conn:
            self.assertEqual('done',db.get_task(conn,first).status)
            edits=conn.execute("SELECT count(*) FROM task_events WHERE task_id=? AND kind='edited'",(first,)).fetchone()[0]
            self.assertEqual(6,edits)

    def test_worker_startup_failure_gets_failure_summary_not_cancel(self):
        import sys
        from hermes_cli import kanban_db as db
        first=self.job.initialize('default',self.state)
        with self.job.connect_closing(board='default') as conn: db.unblock_task(conn,first)
        self.job.supervise('default',self.state,fixture_command=[sys.executable,str(Path(__file__).with_name('fixture_worker.py')),'--mode','startup-fail'])
        journal=self.job.read(self.state/'journal.json')
        self.assertIsNone(journal['active'])
        summary=self.job.read(next((self.state/'runs').glob('*/summary.json')))
        self.assertEqual('failed',summary['outcome'])
        self.assertEqual(7,summary['exit_code'])
    def test_nonzero_exit_cannot_be_done_even_with_success_result(self):
        import sys
        from hermes_cli import kanban_db as db
        first=self.job.initialize('default',self.state)
        with self.job.connect_closing(board='default') as conn: db.unblock_task(conn,first)
        self.job.supervise('default',self.state,fixture_command=[sys.executable,str(Path(__file__).with_name('fixture_worker.py')),'--mode','result-then-crash'])
        summary=self.job.read(next((self.state/'runs').glob('*/summary.json')))
        self.assertEqual('failed',summary['outcome'])
        self.assertEqual(7,summary['exit_code'])
        self.assertIn('WORKER_NONZERO_EXIT',summary['failed'])
        with self.job.connect_closing(board='default') as conn: self.assertEqual('blocked',db.get_task(conn,first).status)
    def test_diagnostic_log_is_bounded_but_phase_events_survive(self):
        directory=self.state/'fixture-log'; directory.mkdir(parents=True)
        for _ in range(1000): self.job.emit(directory,{'diagnostic':True,'component':'fixture','data':'x'*1000})
        self.job.emit(directory,{'phase':4,'phases':[]})
        path=directory/'events.jsonl'
        self.assertLess(path.stat().st_size,70*1024)
        self.assertTrue(path.read_text(encoding='utf-8').endswith('"phases": []}\n'))

    def test_startup_gate_rejects_cancelled_native_claim(self):
        first=self.job.initialize('default',self.state)
        token='a'*32; directory=self.state/'runs'/token; directory.mkdir(parents=True)
        gate=dict(task=first,run=123,token=token,board='default',job=self.job.JOB,directory=str(directory),worker=self.job.identity(os.getpid()),controller=self.job.identity(os.getpid()))
        journal=self.job.read(self.state/'journal.json'); journal['active']=gate
        self.job.save(self.state/'journal.json',journal); self.job.save(directory/'gate.json',gate)
        with self.assertRaisesRegex(RuntimeError,'STARTUP_CLAIM_LOST'): self.job.await_gate(directory)

class ImportTest(unittest.TestCase):
    def setUp(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from plaud_obsidian import engine
        import plaud_kanban
        self.engine, self.job = engine, plaud_kanban
        self.temp = tempfile.TemporaryDirectory(dir=SCRATCH)
        root = Path(self.temp.name)
        (root / 'vault').mkdir()
        self.config = dict(vault=str(root/'vault'), output='notes', state=str(root/'state'))
        self.source = FixtureSource()
    def tearDown(self): self.temp.cleanup()
    def test_missing_highlights_is_normal_and_counted_by_record(self):
        class NoHighlights(FixtureSource):
            def read_record(self,row):
                record=super().read_record(row)
                record['pending']=['Highlights: unavailable','Highlights: unavailable']
                return record
        report=self.job.run_import(self.engine,self.config,NoHighlights(),FixtureTranslator(),lambda e:None)
        self.assertEqual('success',report['outcome'])
        self.assertEqual([],report['failed'])
        self.assertEqual([],report['verification_errors'])
        self.assertEqual(1,report['highlights_unavailable'])
        self.assertEqual(0,report['other_unavailable'])

    def test_real_engine_success_no_change_verified_with_phase_only_updates(self):
        events = []
        report = self.job.run_import(self.engine, self.config, self.source, FixtureTranslator(), events.append)
        self.assertEqual('success', report['outcome'])
        self.assertEqual([1,2,3,4], [e['phase'] for e in events])
        self.assertEqual(1, report['verified'])
        again = self.job.run_import(self.engine, self.config, self.source, FixtureTranslator(), lambda e: None)
        self.assertEqual('success', again['outcome'])
        self.assertEqual(1, again['counts']['unchanged'])
        self.assertEqual(1, again['verified'])
        self.assertEqual(4, len(again['phases']))
        self.assertTrue(all(p['seconds'] >= 0 for p in again['phases']))
        operations=report['phases'][2]['operations']
        self.assertEqual(['원문 조회','번역'],[o['name'] for o in operations])
        self.assertTrue(all(o['seconds']>=0 and o['calls']>0 for o in operations))
        self.assertGreaterEqual(report['phases'][2]['seconds'],sum(o['seconds'] for o in operations))

    def test_partial_failure_retains_successful_note_and_is_failed(self):
        class Partial(FixtureSource):
            def list_records(self): return [{'id':'a'*32},{'id':'b'*32}]
            def read_record(self,row):
                if row['id']=='b'*32: raise RuntimeError('SYNTHETIC_READ_FAILED')
                return super().read_record(row)
        report=self.job.run_import(self.engine,self.config,Partial(),FixtureTranslator(),lambda e:None)
        self.assertEqual('failed',report['outcome'])
        self.assertEqual(['SYNTHETIC_READ_FAILED'],report['failed'])
        self.assertEqual(1,report['verified'])
        self.assertEqual(1,len(list((Path(self.config['vault'])/'notes').glob('*.md'))))
    def test_empty_inventory_marks_processing_not_run(self):
        class Empty(FixtureSource):
            def list_records(self): return []
        report=self.job.run_import(self.engine,self.config,Empty(),FixtureTranslator(),lambda e:None)
        self.assertEqual('success',report['outcome'])
        self.assertTrue(report['phases'][2]['skipped'])
        self.assertEqual(0,report['phases'][2]['seconds'])
    def test_artifact_body_hash_tamper_is_rejected(self):
        report=self.job.run_import(self.engine,self.config,self.source,FixtureTranslator(),lambda e:None)
        target=next((Path(self.config['vault'])/'notes').glob('*.md'))
        original=self.engine.sync
        def tampering(*args,**kwargs):
            result=original(*args,**kwargs)
            with target.open('ab') as stream: stream.write(b'changed after engine')
            return result
        with patch.object(self.engine,'sync',tampering):
            report=self.job.run_import(self.engine,self.config,self.source,FixtureTranslator(),lambda e:None)
        self.assertEqual('failed',report['outcome'])
        self.assertEqual(['ARTIFACT_INTEGRITY_FAILED'],report['verification_errors'])

    def test_worker_uses_engine_and_persists_sanitized_failure(self):
        from contextlib import nullcontext
        root=Path(self.temp.name)/'run'; root.mkdir()
        def runtime():
            return self.engine,self.config,nullcontext(self.source),FixtureTranslator()
        with patch.object(self.job,'await_gate',return_value={}):
            self.job.worker(root,runtime=runtime)
        self.assertEqual('success',self.job.read(root/'result.json')['outcome'])
        self.assertEqual(1,self.job.read(root/'counts.json')['created'])
        def broken(): raise ValueError('meeting body token=DO_NOT_LOG')
        with patch.object(self.job,'await_gate',return_value={}):
            self.job.worker(root,runtime=broken)
        result=self.job.read(root/'result.json')
        self.assertEqual('failed',result['outcome'])
        self.assertEqual(1,result['failure_phase'])
        self.assertNotIn('DO_NOT_LOG',''.join(p.read_text(encoding='utf-8') for p in root.glob('*') if p.is_file()))

    def test_private_counts_callback_does_not_rewrite_phase_events(self):
        events=[]; counts=[]
        self.job.run_import(self.engine,self.config,self.source,FixtureTranslator(),events.append,progress=counts.append)
        self.assertEqual(1,len(counts))
        self.assertEqual(1,counts[0]['created'])
        self.assertEqual([1,2,3,4],[e['phase'] for e in events])

class FixtureSource:
    def list_records(self): return [{'id': 'a'*32}]
    def read_record(self, row):
        return dict(id=row['id'], title='synthetic fixture', start_at='2026-10-10T10:00:00+09:00',
                    artifacts=[dict(key='Summary',kind='Summary',text='Synthetic text, not live PLAUD data.')],
                    pending=['Highlights'])
class FixtureTranslator:
    cache_identity = 'synthetic-offline-test'
    def translate(self, units): return [dict(id=u['id'], language='en', translation_ko='테스트 번역') for u in units]

if __name__ == '__main__': unittest.main(verbosity=2)
