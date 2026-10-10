"""SYNTHETIC disposable controller test driver. Never deployed."""
import argparse
from pathlib import Path
import sys
import plaud_kanban as job
p=argparse.ArgumentParser()
p.add_argument('--state',required=True)
a=p.parse_args()
job.supervise('default',Path(a.state), fixture_command=[sys.executable,str(Path(__file__).with_name('fixture_worker.py')),'--mode','hang'])
