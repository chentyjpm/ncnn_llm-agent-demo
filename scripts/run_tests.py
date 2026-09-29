#!/usr/bin/env python3
"""Run stdlib tests and persist genuine results, not a hand-authored test count."""
import argparse
import io
import json
from pathlib import Path
import platform
import sys
import time
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
class Result(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs); self.records=[]
    def addSuccess(self,test):
        super().addSuccess(test); self.records.append({"test":test.id(),"status":"passed"})
    def addFailure(self,test,err):
        super().addFailure(test,err); self.records.append({"test":test.id(),"status":"failed","detail":self._exc_info_to_string(err,test)})
    def addError(self,test,err):
        super().addError(test,err); self.records.append({"test":test.id(),"status":"error","detail":self._exc_info_to_string(err,test)})
    def addSkip(self,test,reason):
        super().addSkip(test,reason); self.records.append({"test":test.id(),"status":"skipped","reason":reason})
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output-dir', default=str(ROOT/'reports'), help='Directory for this run only')
args=parser.parse_args()
output=Path(args.output_dir).resolve()
output.mkdir(parents=True,exist_ok=True)
stream=io.StringIO(); start=time.monotonic()
suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_*.py',top_level_dir=str(ROOT))
result=unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=Result).run(suite)
report={"tests_run":result.testsRun,"passed":sum(r['status']=='passed' for r in result.records),
 "failures":len(result.failures),"errors":len(result.errors),"skipped":len(result.skipped),
 "elapsed_seconds":round(time.monotonic()-start,3),"python":platform.python_version(),"platform":platform.platform(),
 "scope":"Real file/Python/MCP subprocess tests; model/image processes in adapter tests are explicitly fake fixtures. No ncnn model inference, C++ compilation, GPU or Docker runtime validation.","tests":result.records}
(output/'tests.log').write_text(stream.getvalue(),encoding='utf-8')
(output/'test-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(stream.getvalue()); print(json.dumps({k:v for k,v in report.items() if k!='tests'},ensure_ascii=False,indent=2))
raise SystemExit(0 if result.wasSuccessful() else 1)
