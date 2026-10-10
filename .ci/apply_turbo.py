"""Temporary, reviewed source assembly on the Turbo feature branch only.
Assertions prevent applying changes to unexpected source. Excluded from main.
"""
from pathlib import Path
import json
r=Path.cwd()
def edit(rel,old,new):
    p=r/rel;s=p.read_text(encoding='utf-8')
    assert s.count(old)==1,(rel,s.count(old),old[:100])
    p.write_text(s.replace(old,new),encoding='utf-8')

p=r/'ci/dependencies.json';v=json.loads(p.read_text());v['qwenimage']['commit']='14953559f39c7658e5a6cc117481e1922660cc9e';p.write_text(json.dumps(v,indent=2)+'\n')
p=r/'upstream-lock.json';v=json.loads(p.read_text());v['reviewed_on']='2026-10-10'
for x in v['repositories']:
    if x['name']=='nihui/qwenimage-ncnn-vulkan':x['commit']='14953559f39c7658e5a6cc117481e1922660cc9e'
v['ncnn_library']='Native builds pin Tencent/ncnn in ci/dependencies.json and verify the matching Qwen Image submodule.'
p.write_text(json.dumps(v,indent=2)+'\n')
pin=json.loads((r/'.ci/turbo-pin.json').read_text())
p=r/'local_agent/hf_download_pins.py'
p.write_text(p.read_text()+"\n# Turbo identities resolved independently from HF origin (2026-10-10).\nPINNED_HF['qwenimage21-turbo'] = "+repr(pin)+'\n')
p=r/'local_agent/model_catalog.py';s=p.read_text();at=s.index('\n# Native converted bundles')
s=s[:at]+'''
# Distinct Turbo checkpoint with explicit dependency and no hidden download.
from .image_profiles import TURBO_IMAGE, BASE_IMAGE
from .hf_download_pins import PINNED_HF
CATALOG[TURBO_IMAGE] = {
    'name': 'Qwen Image 2.1 Turbo', 'kind': 'image', 'format': 'ncnn Turbo Transformer · 8 步',
    'description': '固定 8 步生图。先安装基础模型，再仅下载 Turbo Transformer；共享组件不重复复制。',
    'validation': '新增适配；真实生成结果见 Turbo CI，不承诺固定加速倍数',
    'repository': PINNED_HF[TURBO_IMAGE]['repository'], 'revision': PINNED_HF[TURBO_IMAGE]['revision'],
    'estimated_download_bytes': sum(f['bytes'] for f in PINNED_HF[TURBO_IMAGE]['files']),
    'depends_on': [BASE_IMAGE], 'default_steps': 8, 'fixed_steps': 8,
    'sources': {'huggingface': {'repository': PINNED_HF[TURBO_IMAGE]['repository'],
                              'revision': PINNED_HF[TURBO_IMAGE]['revision']}},
    'unavailable_sources': {'modelscope': '尚未核实同版本 ncnn Turbo 权重；请选 Hugging Face 原站或 HF-Mirror。'},
}
'''+s[at:];p.write_text(s)
edit('local_agent/model_sources.py','from .hf_download_pins import PINNED_HF','from .hf_download_pins import PINNED_HF\nfrom .image_profiles import TURBO_IMAGE, TURBO_FILES')
edit('local_agent/model_sources.py',"model_id == 'qwenimage21':","model_id in ('qwenimage21', TURBO_IMAGE):")
edit('local_agent/model_sources.py',"            if not isinstance(remote, str) or not remote.startswith('qwenimage21/'): continue\n            name = remote[len('qwenimage21/'):]","            prefix = (TURBO_IMAGE if model_id == TURBO_IMAGE else 'qwenimage21') + '/'\n            if not isinstance(remote, str) or not remote.startswith(prefix): continue\n            name = remote[len(prefix):]")
edit('local_agent/model_sources.py','required = QWEN_FILES if model_id in PROFILES else IMAGE_REQUIRED','required = QWEN_FILES if model_id in PROFILES else TURBO_FILES if model_id == TURBO_IMAGE else IMAGE_REQUIRED')
edit('local_agent/model_sources.py',"    if download_route_id == 'hf_mirror':\n        verify_mirror_manifest","    if download_route_id == 'hf_mirror' or model_id == TURBO_IMAGE:\n        verify_mirror_manifest")
edit('local_agent/model_sources.py',"    if route_id == 'hf_mirror':\n        verify_mirror_manifest","    if route_id == 'hf_mirror' or manifest['id'] == TURBO_IMAGE:\n        verify_mirror_manifest")
edit('local_agent/images.py','from .device import select, engine_env','from .device import select, engine_env\nfrom .image_profiles import image_profile, image_steps, shared_components')
edit('local_agent/images.py','height: int = 512, steps: int = 40, seed: int = 42,','height: int = 512, steps: int | None = None, seed: int = 42,')
edit('local_agent/images.py','        if type(steps) is not int or not 1 <= steps <= 100:\n            raise PolicyError("steps must be in 1..100; four steps alone is not a LoRA accelerator")','        steps = image_steps(self.config, steps)')
edit('local_agent/images.py','        self.device_selection = select(self.config, "image")','        if image_profile(self.config)["variant"] == "turbo":\n            shared_components(model.resolve().parent / "qwenimage21")\n        self.device_selection = select(self.config, "image")')
edit('local_agent/images.py','"device_selection": self.device_selection})','"device_selection": self.device_selection,\n                       "image_profile": image_profile(self.config),\n                       "steps": image_steps(self.config, arguments.get("steps"))})')
edit('local_agent/image_tasks.py','from .paths import Workspace, PolicyError','from .paths import Workspace, PolicyError\nfrom .image_profiles import image_profile, image_steps')
edit('local_agent/image_tasks.py','def plan_image(workspace: Workspace, prompt: str, options: dict | None, references: list[str]) -> dict:','def plan_image(workspace: Workspace, prompt: str, options: dict | None, references: list[str], *, image_config: dict | None = None) -> dict:')
edit('local_agent/image_tasks.py',"    params = {'width': 512, 'height': 512, 'steps': 40, 'seed': secrets.randbelow(2147483648), **options}","    params = {'width': 512, 'height': 512, 'steps': image_profile(image_config)['default_steps'], 'seed': secrets.randbelow(2147483648), **options}\n    params['steps'] = image_steps(image_config, params['steps'])")
edit('local_agent/image_tasks.py',"            'Use a unique relative output path. Default steps=40; steps=4 is NOT a LoRA accelerator. '","            'Use a unique relative output path. Follow the registered steps schema: base defaults to steps=40; '\n            'Turbo requires exactly 8. Omitting steps selects the active model default. '\n            'Setting base steps=8 does NOT select Turbo; steps=4 is NOT a LoRA accelerator. '")
edit('local_agent/image_tasks.py','\'"output":"images/cat-example.png","width":512,"height":512,"steps":40,"seed":42}}. \'','\'"output":"images/cat-example.png","width":512,"height":512,"seed":42}}. \'')
edit('local_agent/tools.py','    if image_runner and image_runner.config.get("enabled", False):\n        r.add(Tool("images.generate", "Create an actual picture/illustration/poster using Qwen Image (生图/画图). Not for text-only image advice. references edits existing images. Default 40 steps, no overwrite.",','''    if image_runner and image_runner.config.get("enabled", False):
        from .image_profiles import image_profile
        profile = image_profile(image_runner.config)
        step_schema = {"type": "integer", "minimum": 1, "maximum": 100, "default": profile['default_steps']}
        if profile['fixed_steps'] is not None:
            step_schema['enum'] = [profile['fixed_steps']]
        description = ("Create an actual picture/illustration/poster using Qwen Image (生图/画图). "
                       "Not for text-only advice. references edits existing images. No overwrite. "
                       + ("Active Turbo model: exactly 8 steps, fixed scheduler." if profile['fixed_steps'] else "Base model: default 40 steps."))
        r.add(Tool("images.generate", description,''')
edit('local_agent/tools.py','"steps": {"type": "integer", "minimum": 1, "maximum": 100},','"steps": step_schema,')
edit('local_agent/web.py','from .image_tasks import route_request, image_ready, plan_image, image_artifacts','from .image_tasks import route_request, image_ready, plan_image, image_artifacts\nfrom .image_profiles import image_profile')
edit('local_agent/web.py',"        result['image_model'] = Path(self.config.get('image', {}).get('model', '')).name or '未配置'","        result['image_model'] = Path(self.config.get('image', {}).get('model', '')).name or '未配置'\n        result['image_profile'] = image_profile(self.config.get('image', {}))")
edit('local_agent/web.py',"plan_image(ws, image_prompt, payload.get('image_options'), attachments)","plan_image(ws, image_prompt, payload.get('image_options'), attachments, image_config=self.config.get('image', {}))")
edit('local_agent/model_hub.py','from .model_catalog import CATALOG, PROFILES, PROVIDERS','from .model_catalog import CATALOG, PROFILES, PROVIDERS\nfrom .image_profiles import BASE_IMAGE, TURBO_IMAGE, TURBO_FILES, shared_components')
edit('local_agent/model_hub.py','HF_DOWNLOAD_ROUTES, response_host','HF_DOWNLOAD_ROUTES, response_host, verify_mirror_manifest')
edit('local_agent/model_hub.py',"        folder = self.models / model_id\n        try:\n            m = json.loads","        folder = self.models / model_id\n        try:\n            if model_id == TURBO_IMAGE:\n                if not self.installed(BASE_IMAGE): return False\n                shared_components(self.models / BASE_IMAGE)\n            m = json.loads")
edit('local_agent/model_hub.py',"m = json.loads((folder / 'READY.json').read_text(encoding='utf-8'))\n            return","m = json.loads((folder / 'READY.json').read_text(encoding='utf-8'))\n            if model_id == TURBO_IMAGE and {f['name'] for f in m['files']} != TURBO_FILES: return False\n            return")
edit('local_agent/model_hub.py',"                'models':[dict(id=k, **v, installed=self.installed(k), active=active.get(v['kind']) == k)","                'models':[dict(id=k, **v, installed=self.installed(k), active=active.get(v['kind']) == k,\n                               missing_dependencies=[d for d in v.get('depends_on', []) if not self.installed(d)])")
edit('local_agent/model_hub.py',"        route = download_route(provider, download_route_id)\n        with self.lock:","        route = download_route(provider, download_route_id)\n        if model_id == TURBO_IMAGE:\n            if not self.installed(BASE_IMAGE):\n                raise ValueError('请先安装 Qwen Image 2.1 基础模型；Turbo 复用它的分词器、文本编码器和 VAE，不重复复制共享权重')\n            shared_components(self.models / BASE_IMAGE)\n        with self.lock:")
edit('local_agent/model_hub.py',"        quote.update(download_route=download_route_id,","        if model_id == TURBO_IMAGE:\n            quote['base_dependency'] = shared_components(self.models / BASE_IMAGE)\n        quote.update(download_route=download_route_id,")
edit('local_agent/model_hub.py',"quote = entry[1]\n            if self.installed","quote = entry[1]\n            if quote['id'] == TURBO_IMAGE:\n                if not self.installed(BASE_IMAGE): raise ValueError('基础模型已不可用，请先恢复基础模型再安装 Turbo')\n                shared_components(self.models / BASE_IMAGE)\n            if self.installed")
edit('local_agent/model_hub.py',"            kind = CATALOG[model_id]['kind']\n            if not Path(self.engines[kind]).is_file():","            kind = CATALOG[model_id]['kind']\n            if model_id == TURBO_IMAGE and not _installation:\n                shared_components(self.models / BASE_IMAGE, verify_hashes=True)\n            if not Path(self.engines[kind]).is_file():")
edit('local_agent/model_hub.py',"        try:\n            cache.mkdir(parents=True, exist_ok=True)","        try:\n            if model_id == TURBO_IMAGE:\n                if not self.installed(BASE_IMAGE): raise ValueError('基础模型未完整安装，不能安装 Turbo')\n                shared_components(self.models / BASE_IMAGE)\n                verify_mirror_manifest(model_id, quote['repository'], quote['revision'], quote['files'])\n            cache.mkdir(parents=True, exist_ok=True)")
edit('local_agent/model_hub.py',"            save_json(stage/'READY.json', {'id':model_id,'provider':provider,'download_route':route_id,'revision':quote['revision'],'files':files})","            dependency = {}\n            if model_id == TURBO_IMAGE:\n                self._update(message='正在核对基础模型共享组件；不会重复下载或复制')\n                dependency = shared_components(self.models / BASE_IMAGE, verify_hashes=True, cancelled=self.cancelled.is_set)\n            save_json(stage/'READY.json', {'id':model_id,'provider':provider,'download_route':route_id,'revision':quote['revision'],'files':files,\n                                         **({'base_dependency':dependency} if dependency else {})})")
edit('local_agent/webui/workbench.js',"  const activeJob=busy&&job.model===model.id;","  const dependency=(model.missing_dependencies||[])[0];\n  if(dependency)card.append(el('p','source-unavailable','先安装 Qwen Image 2.1 基础模型，再安装 Turbo 独立权重。共享组件不重复下载或复制。'));\n  const activeJob=busy&&job.model===model.id;")
edit('local_agent/webui/workbench.js',"activeJob?'正在安装…':'准备下载',null,()=>chooseModel(model)","activeJob?'正在安装…':dependency?'先准备基础模型':'准备下载',null,()=>chooseModel(dependency?data.models.find(m=>m.id===dependency):model)")
edit('local_agent/webui/workbench.js',"const q=setup.quote,box=el('div','download-confirmation');box.append(el('strong','','确认下载信息'));","const q=setup.quote,box=el('div','download-confirmation');box.append(el('strong','','确认下载信息'));\n   if(q.base_dependency)box.append(el('p','setting-note','复用已安装基础组件 '+amount(q.base_dependency.shared_bytes)+'；本次只下载 Turbo Transformer。不要删除 qwenimage21 基础目录。'));")
edit('local_agent/webui/app.js',"for(const id of ['image-width','image-height','image-steps','image-seed'])$(id).disabled=busy;","for(const id of ['image-width','image-height','image-steps','image-seed'])$(id).disabled=busy;syncImageProfile(busy);")
edit('local_agent/webui/app.js',"state.runtime?.image_ready?'Qwen Image 独立生图 · 不需要文字模型 · 提交后仍需逐次确认'","state.runtime?.image_ready?(state.runtime?.image_profile?.fixed_steps?'Qwen Image Turbo · 固定 8 步 · 复用基础组件 · 提交后仍需确认':'Qwen Image 标准版 · 建议 40 步 · 不需要文字模型 · 提交后仍需确认')")
edit('local_agent/webui/app.js','function controls(){','''function syncImageProfile(busy){
 const input=$('image-steps'),profile=state.runtime?.image_profile||{default_steps:40,fixed_steps:null};
 const key=state.runtime?.image_model||'';
 if(input.dataset.model!==key){input.value=String(profile.default_steps||40);input.dataset.model=key;}
 if(profile.fixed_steps){input.value=String(profile.fixed_steps);input.disabled=true;input.setAttribute('aria-description','Turbo 固定使用 8 步');}
 else{input.disabled=busy;input.removeAttribute('aria-description');}
}
function controls(){''')
edit('scripts/hf_mirror_smoke.py',"wanted=('vae/decoder.ncnn.param','transformer/output.ncnn.bin') if model=='qwenimage21' else ('config.json',)","wanted=(('transformer/output.ncnn.param','transformer/output.ncnn.bin') if model=='qwenimage21-turbo' else\n                        ('vae/decoder.ncnn.param','transformer/output.ncnn.bin') if model=='qwenimage21' else ('config.json',))")
edit('scripts/hf_mirror_smoke.py',"len(report['cases'])==8","len(report['cases'])==2*len(PINNED_HF)")
p=r/'scripts/native_regression.py';s=p.read_text();at=s.index("            ('nonfinite_lora_scale'");end=s.index('\n',at)+1
s=s[:end]+'''            ('turbo_reject_2_steps', ['-m', str(scratch / 'qwenimage21-turbo'), '-l', '2'], 2, 'Turbo models require exactly 8 steps', False),
            ('turbo_reject_40_steps', ['-m', str(scratch / 'qwenimage21-turbo'), '-l', '40'], 2, 'Turbo models require exactly 8 steps', False),
            ('turbo_case_insensitive', ['-m', str(scratch / 'QWENIMAGE21-TURBO'), '-l', '4'], 2, 'Turbo models require exactly 8 steps', False),
            ('turbo_trailing_separator', ['-m', str(scratch / 'qwenimage21-turbo') + '/', '-l', '4'], 2, 'Turbo models require exactly 8 steps', False),
'''+s[end:];p.write_text(s)
edit('tests/test_ci_regressions.py',"('image', 9)","('image', 13)")
p=r/'README.md';s=p.read_text();at=s.index('## 选择模型，也可以直接生图');pos=s.index('\n\n',at)+2
s=s[:pos]+'> 新增 **[Qwen Image 2.1 Turbo](docs/QWEN_IMAGE_TURBO.md)**：固定 8 步，按需下载增量权重并复用基础组件。Hugging Face 支持显式选择 **[HF-Mirror 下载线路](docs/HF_MIRROR.md)**；两条线路按相同可信清单校验。\n\n'+s[pos:];p.write_text(s)
p=r/'docs/HF_MIRROR.md';p.write_text(p.read_text()+'\n## Turbo 模型\n\n[Qwen Image 2.1 Turbo](QWEN_IMAGE_TURBO.md) 同样支持原站与镜像。六个 Transformer 文件增量安装；必须先安装基础模型，复用共享组件。原站和镜像都校验独立可信哈希，不把镜像元数据本身作为唯一依据。\n')
print('Core Turbo source assembled; no real-model success asserted.')
