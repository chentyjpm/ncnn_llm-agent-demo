#!/usr/bin/env python3
"""Real Turbo checkpoint smoke: shared weights + six Turbo files, eight steps.
No synthetic weights or images. The application's ImageRunner executes inference.
Decoding proves execution integrity, NOT semantic quality or a speedup benchmark.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from local_agent.image_profiles import BASE_IMAGE,TURBO_IMAGE,TURBO_FILES,shared_components
from local_agent.images import ImageRunner
from local_agent.paths import Workspace
from local_agent.model_sources import resolve_manifest,file_url,open_https,verify_mirror_manifest,response_host
from scripts.qwenimage_real_test import (Samples,validate_image,check_device,verify_native,
                                        identity,save,resource_report,model_file,verified_file)


def prepare_models(root: Path, route: str, output: Path, *, open_file=None):
    """Keep all shared components and Turbo; omit only unused base Transformer."""
    root.mkdir(parents=True,exist_ok=True)
    manifests={mid:resolve_manifest(mid,'huggingface',download_route_id=route) for mid in (BASE_IMAGE,TURBO_IMAGE)}
    for mid,m in manifests.items():verify_mirror_manifest(mid,m['repository'],m['revision'],m['files'])
    selected={mid:[f for f in m['files'] if mid==TURBO_IMAGE or not f['name'].startswith('transformer/')]
              for mid,m in manifests.items()}
    if {f['name'] for f in selected[TURBO_IMAGE]}!=TURBO_FILES:raise ValueError('Missing Turbo files')
    total=sum(f['bytes'] for fs in selected.values() for f in fs)
    save(output/'model-manifests.json',manifests)
    resources=resource_report(root);resources.update(required_download_bytes=total,route=route)
    save(output/'resources-before.json',resources)
    if resources['disk_free']<total+2*1024**3:raise RuntimeError('Insufficient disk for reviewed Turbo/shared weights')
    if total>60*1024**3:raise ValueError('Weight size exceeds test download budget')
    opener=open_file or open_https
    started=time.monotonic();records=[];count=0
    for mid,fs in selected.items():
        for f in fs:
            p=model_file(root/mid,f['name']);p.parent.mkdir(parents=True,exist_ok=True)
            final_host=None;cached=verified_file(p,f)
            if not cached:
                tmp=model_file(root/mid,f['name']+'.part');n=0;url=file_url(manifests[mid],f)
                try:
                    with opener(url,timeout=90) as response,tmp.open('wb') as stream:
                        final_host=response_host(response,url)
                        print(f'Download {mid}/{f["name"]}: {f["bytes"]} bytes from {final_host}',flush=True)
                        while block:=response.read(4*1024**2):
                            if time.monotonic()-started>2700:raise TimeoutError('Turbo weight download exceeded 45 minutes')
                            n+=len(block)
                            if n>f['bytes']:raise ValueError('Download exceeds manifest size')
                            stream.write(block)
                            if n//(256*1024**2)!=(n-len(block))//(256*1024**2):print(f'{count+n}/{total} bytes received',flush=True)
                    if not verified_file(tmp,f):raise ValueError('Weight checksum/length mismatch: '+mid+'/'+f['name'])
                    os.replace(tmp,p)
                finally:tmp.unlink(missing_ok=True)
            count+=f['bytes'];records.append(dict(model=mid,name=f['name'],bytes=f['bytes'],digest=f['digest'],verified=True,cached=cached,final_host=final_host))
            save(output/'download-progress.json',{'verified_bytes':count,'total_bytes':total,'files':records})
    dependency=shared_components(root/BASE_IMAGE,verify_hashes=True)
    save(output/'download-result.json',{**identity(),'ok':True,'files':records,'dependency':dependency,
        'download_bytes':total,'seconds':round(time.monotonic()-started,3),
        'scope':'Full Turbo and all shared components. Unused base Transformer omitted; not a base-model installation test.'})
    return manifests


def run(args):
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    report={**identity(),'ok':False,'kind':'REAL_QWEN_IMAGE_TURBO','device_requested':args.device,
            'route':args.route,'width':256,'height':256,'steps':8,'quality_test':'not_run'}
    (out/'generated.png').unlink(missing_ok=True)
    save(out/'result.json',report)
    try:
        report['engine']=verify_native(args.binary.resolve(strict=True),args.native_status)
        manifests=prepare_models(args.models.resolve(),args.route,out)
        report['model_revisions']={k:v['revision'] for k,v in manifests.items()}
        ws=Workspace(out/'workspace');name='turbo-'+str(time.time_ns())+'.png'
        cfg={'enabled':True,'command':[str(args.binary.resolve())], 'model':str(args.models.resolve()/TURBO_IMAGE),
             'device':args.device,'timeout':args.timeout}
        engine=ImageRunner(ws,cfg)
        params={'prompt':'A red wooden cube on a white table, studio photograph.',
                'output':name,'width':256,'height':256,'seed':42,'steps':8}
        report['argv']=engine.command(**params);check_device(engine.device_selection,args.device)
        report['device_selection']=engine.device_selection
        save(out/'result.json',report)
        start=time.monotonic()
        with Samples(out/'resources.jsonl') as samples:result=engine.run(**params)
        report['generation_seconds']=round(time.monotonic()-start,3)
        report['sampled_peak_rss_bytes']=samples.peak_rss;report['sampling_error']=samples.error
        report['process']={k:v for k,v in result.items() if k not in ('stdout','stderr')}
        for stream in ('stdout','stderr'):(out/f'engine.{stream}.log').write_text(result.get(stream,''),encoding='utf-8')
        if result['returncode']!=0 or result['timed_out'] or not result['file_created']:raise RuntimeError('Real Turbo process failed, timed out or produced no file')
        check_device(result['device_selection'],args.device)
        combined=result['stdout']+'\n'+result['stderr']
        for marker in ('model-type = Turbo','step 8/8 done','vae done'):
            if marker not in combined:raise ValueError('Missing actual Turbo execution evidence: '+marker)
        if result['steps']!=8 or result['image_profile']['variant']!='turbo':raise ValueError('Wrong application image profile')
        report['image']=validate_image(ws.path(name),256,256)
        shutil.copyfile(ws.path(name),out/'generated.png')
        report.update(ok=True,status='passed',scope='Real 8-step Turbo execution; no quality rating or speedup comparison.')
    except Exception as e:report.update(status='failed',error=type(e).__name__+': '+str(e))
    finally:
        save(out/'result.json',report);print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
        if os.getenv('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as s:
                s.write(f"## Real Qwen Image Turbo\n\nStatus: **{report.get('status')}**. Device: {args.device}; fixed 8 steps.\n\nImage quality and performance improvement are not certified by this smoke test.\n")
    return 0 if report['ok'] else 1


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary',type=Path,required=True)
    p.add_argument('--native-status',type=Path,default=ROOT/'reports/ci/image/status.json')
    p.add_argument('--models',type=Path,default=ROOT/'models/turbo-ci')
    p.add_argument('--output',type=Path,default=ROOT/'reports/turbo-real')
    p.add_argument('--route',choices=('direct','hf_mirror'),default='direct')
    p.add_argument('--device',choices=('cpu','vulkan'),default='cpu')
    p.add_argument('--timeout',type=int,default=3600)
    args=p.parse_args()
    if not 1<=args.timeout<=5400:p.error('timeout must be 1..5400')
    raise SystemExit(run(args))
