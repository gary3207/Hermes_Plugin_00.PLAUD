"""SYNTHETIC offline subprocess; never invokes PLAUD/auth/LLM/network."""
import argparse
import os
from pathlib import Path
import sys
import time
import subprocess
import plaud_kanban as job

p=argparse.ArgumentParser()
p.add_argument('--run-dir', required=True)
p.add_argument('--mode', default='success')
a=p.parse_args()
run=Path(a.run_dir)
if a.mode=='startup-fail': sys.exit(7)
job.save(run/'fixture-debug.json',dict(worker=job.identity(os.getpid()),gate=job.read(run/'gate.json')))
job.await_gate(run)
for phase in range(1,5):
    job.emit(run, dict(phase=phase, phases=[]))
    if phase==2: job.emit(run,dict(diagnostic=True,component='synthetic-fixture',causes=[]))
    time.sleep(0 if a.mode=='fast' else .08)
if a.mode == 'hang':
    child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
    job.save(run/'fixture-child.json',job.identity(child.pid))
    time.sleep(120)
job.save(run/'result.json', dict(outcome='failed' if a.mode=='fail' else 'success', failed=['SYNTHETIC_PARTIAL_FAILURE'] if a.mode=='fail' else [], ended=job.now(), phases=[], seconds=.32, verified=0))
if a.mode=='result-then-crash': sys.exit(7)
