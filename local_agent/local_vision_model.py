"""Explicit local-only MiniCPM registration; no download or model execution."""
import json
from pathlib import Path
from .process import run_process
from .device import engine_env

MODEL_ID = 'minicpm_v47_local'
PROFILE = {'name':'MiniCPM-V-4.7 · 本地单图问答', 'kind':'llm', 'family':'MiniCPM-V-4.7',
    'category':'llm', 'install_kind':'local', 'installable':True, 'import_only':True,
    'format':'已转换 ncnn FP32 本地目录', 'precision':'CPU FP32', 'light':False,
    'description':'导入已有转换目录，不下载、不复制权重。支持文字和单张图片问答；不支持 Agent / 工具。',
    'capabilities':['chat','single-image-experimental'], 'sources':{},
    'runtime_note':'CPU FP32；单图缩略图。需要切片的图片会明确拒绝；建议先使用 448×448 PNG。',
    'validation':'实验性能力；导入校验不等于当前机器真实推理验收。'}


def validate_package(directory):
    if not isinstance(directory,str) or not directory or '\x00' in directory or directory.startswith(('\\\\','//')):
        raise ValueError('请选择可信的本地转换模型绝对目录')
    root=Path(directory).expanduser()
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError('模型必须是本地绝对目录，不能是符号链接')
    root=root.resolve()
    files={}
    def file(name,limit):
        if not isinstance(name,str) or not name or '\\' in name or ':' in name or '\x00' in name:
            raise ValueError('模型文件名无效')
        rel=Path(name)
        if rel.is_absolute() or any(p in ('.','..') for p in rel.parts): raise ValueError('模型文件不能越出导入目录')
        p=root
        for part in rel.parts:
            p=p/part
            if p.is_symlink(): raise ValueError('模型文件不能是符号链接')
        if not p.is_file() or not 0<p.stat().st_size<=limit: raise ValueError('模型文件缺失或大小无效: '+name)
        files[name]=p.stat().st_size
        return p
    def read(name):
        value=json.loads(file(name,1024**2).read_text(encoding='utf-8-sig'))
        if not isinstance(value,dict): raise ValueError('模型 JSON 必须是对象')
        return value
    try:
        config=read('model.json'); vision=config['setting']['vision']; tokenizer=config['tokenizer']
        if vision['type']!='minicpm_v4_7' or tokenizer['type']!='bbpe': raise ValueError('仅支持 MiniCPM-V-4.7 转换模型')
        pre=tokenizer['pretokenizer']
        if pre['type']!='minicpm_v47': raise ValueError('缺少 MiniCPM 精确分词器')
        artifact=file(pre['file'],1024**2)
        with artifact.open(encoding='utf-8') as f:
            if f.readline().strip()!='minicpm_v47_tokenizer_v1': raise ValueError('分词器格式不受支持')
        for key in ('vocab_file','merges_file'): file(tokenizer[key],64*1024**2)
        for component in ('embed_token','decoder','proj_out'):
            file(config['params'][component+'_param'],16*1024**2)
            file(config['params'][component+'_bin'],8*1024**3)
        manifest=read(vision['manifest'])
        if any(manifest.get(k)!=v for k,v in {'type':'minicpm_v47','format_version':1,'weight_dtype':'float32','scope':'single_image_thumbnail','downsample_mode':'16x','patch_size':14,'hidden_size':1152,'output_hidden_size':1024}.items()):
            raise ValueError('视觉图配置不受支持')
        for component in ('patch_embed','encoder_pre','window_attn','window_mlp','encoder_post','projector'):
            entry=manifest['components'][component];file(entry['param'],16*1024**2);file(entry['bin'],8*1024**3)
        pos=manifest['position_embeddings']; p=file(pos['file'],64*1024**2)
        if pos['rows']!=4900 or pos['cols']!=1152 or p.stat().st_size!=4900*1152*4: raise ValueError('视觉位置权重大小不匹配')
    except (KeyError,TypeError) as error:
        raise ValueError('MiniCPM 转换目录不完整') from error
    return {'path':str(root),'files':files,'capabilities':PROFILE['capabilities']}


def check_bridge(executable,home):
    if not Path(executable).is_file(): raise ValueError('缺少本地 ncnn bridge')
    result=run_process([str(executable),'--capabilities'],cwd=Path(home),timeout=10,max_output=8192,env=engine_env([str(executable)]))
    try: capabilities=json.loads(result['stdout'])
    except (ValueError,TypeError): capabilities={}
    if result['returncode']!=0 or result['timed_out'] or result.get('output_truncated',False) or not isinstance(capabilities,dict) or type(capabilities.get('minicpm_v4_7_runtime')) is not int or capabilities['minicpm_v4_7_runtime']<1 or capabilities.get('single_image_protocol') is not True:
        raise ValueError('当前引擎未编译 MiniCPM 单图接口；请使用支持该功能的构建')
    return capabilities
