#!/usr/bin/env python3
"""Live provider adapter acceptance: manifests and a checksummed config download.
No large weights. Six model/source pairs must actually succeed, no fake fallback.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from local_agent.model_catalog import PROFILES
from local_agent.model_sources import resolve_manifest,file_url,open_https,digest


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=ROOT/'reports/provider-catalog');a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    results=[]
    for provider in ('huggingface','modelscope'):
        for model,profile in PROFILES.items():
            record={'provider':provider,'model':model,'ok':False}
            try:
                q=resolve_manifest(model,provider)
                record['manifest']=q
                entry=next(f for f in q['files'] if f['name']=='config.json')
                with open_https(file_url(q,entry)) as response:raw=response.read(65537)
                if len(raw)!=entry['bytes']:raise ValueError('Downloaded config size mismatch')
                with tempfile.TemporaryDirectory() as tmp:
                    f=Path(tmp)/'config.json';f.write_bytes(raw)
                    if digest(f,entry['algorithm'])!=entry['digest']:raise ValueError('Downloaded config checksum mismatch')
                config=json.loads(raw)
                for key,value in profile['profile'].items():
                    if config.get(key)!=value:raise ValueError('Profile geometry differs: '+key)
                record.update(ok=True,config=config)
            except Exception as e:record['error']=f'{type(e).__name__}: {e}'
            results.append(record);print(provider,model,record['ok'],record.get('error',''),flush=True)
    report={'scope':__doc__,'ok':all(r['ok'] for r in results),'results':results}
    (a.output/'metadata.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if report['ok'] else 1


if __name__=='__main__':raise SystemExit(main())
