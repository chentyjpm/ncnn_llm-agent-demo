"""Curated compatible model profiles; not an arbitrary remote model browser.
Pins/geometry obtained from live official-provider metadata, CI 36661994229.
Provider repositories have INDEPENDENT commit histories; never reuse HF SHA on MS.
"""
PROVIDERS = {"modelscope": "ModelScope 魔搭", "huggingface": "Hugging Face"}

_GEOMETRY_05 = {
    'model_type': 'qwen2', 'hidden_size': 896, 'intermediate_size': 4864,
    'num_hidden_layers': 24, 'num_attention_heads': 14, 'num_key_value_heads': 2,
    'vocab_size': 151936, 'tie_word_embeddings': True, 'use_sliding_window': False,
    'rms_norm_eps': 1e-6, 'rope_theta': 1000000.0,
}
PROFILES = {
    'qwen05': {
        'name': 'Qwen2.5 0.5B Instruct', 'description': '轻量入门：短问答、简单文档整理。',
        'kind': 'llm', 'format': 'safetensors → ncnn FP32', 'profile': dict(_GEOMETRY_05),
        'weight_sha256': 'fdf756fa7fcbe7404d5c60e26bff1a0c8b8aa1f72ced49e7dd0210fe288fb7fe',
        'revision': '7ae557604adf67be50417f59c2c2f167def9a775',
        'repository': 'Qwen/Qwen2.5-0.5B-Instruct', 'estimated_download_bytes': 995148776,
        'validation': '已验证 CPU 基础推理',
        'sources': {
            'huggingface': {'repository': 'Qwen/Qwen2.5-0.5B-Instruct', 'revision': '7ae557604adf67be50417f59c2c2f167def9a775'},
            'modelscope': {'repository': 'Qwen/Qwen2.5-0.5B-Instruct', 'revision': '186d8559ad54c32cf47dc3a8225f993742c507b8'},
        },
    },
    'qwen_coder05': {
        'name': 'Qwen2.5 Coder 0.5B Instruct', 'description': '代码方向：代码解释、短脚本与简单编程任务。',
        'kind': 'llm', 'format': 'safetensors → ncnn FP32', 'profile': dict(_GEOMETRY_05),
        'weight_sha256': 'f9523886352217ded3aeeef552b381af79d568c6d49a4b9e423288cea56b0a44',
        'revision': 'ea3f2471cf1b1f0db85067f1ef93848e38e88c25',
        'repository': 'Qwen/Qwen2.5-Coder-0.5B-Instruct', 'estimated_download_bytes': 995148776,
        'validation': '新增适配，待完整模型实跑',
        'sources': {
            'huggingface': {'repository': 'Qwen/Qwen2.5-Coder-0.5B-Instruct', 'revision': 'ea3f2471cf1b1f0db85067f1ef93848e38e88c25'},
            'modelscope': {'repository': 'Qwen/Qwen2.5-Coder-0.5B-Instruct', 'revision': '62789576f47d1d43f192fe05a95773ab3ab9e9c6'},
        },
    },
    'qwen15': {
        'name': 'Qwen2.5 1.5B Instruct', 'description': '更大文本模型：文档整理、写作和一般问答。',
        'kind': 'llm', 'format': 'safetensors → ncnn FP32',
        'profile': dict(_GEOMETRY_05, hidden_size=1536, intermediate_size=8960,
                        num_hidden_layers=28, num_attention_heads=12),
        'weight_sha256': 'dd924a11b4c220f385b51ffa522daea7c9f3d850e31b162bb5661df483c6d3ee',
        'revision': '989aa7980e4cf806f80c7fef2b1adb7bc71aa306',
        'repository': 'Qwen/Qwen2.5-1.5B-Instruct', 'estimated_download_bytes': 3094518097,
        'validation': '新增适配，待完整模型实跑',
        'sources': {
            'huggingface': {'repository': 'Qwen/Qwen2.5-1.5B-Instruct', 'revision': '989aa7980e4cf806f80c7fef2b1adb7bc71aa306'},
            'modelscope': {'repository': 'Qwen/Qwen2.5-1.5B-Instruct', 'revision': '3c3787b7c81927cc64ad45dc32ff1c9ce2a5de34'},
        },
    },
}
CATALOG = {key: {k: v for k, v in value.items() if k not in ('profile', 'weight_sha256')} for key, value in PROFILES.items()}
CATALOG['qwenimage21'] = {
    'name': 'Qwen Image 2.1', 'kind': 'image', 'format': 'ncnn 原生权重',
    'description': '可选生图大模型；请先确认文件大小和磁盘空间。',
    'validation': '引擎已接入，完整生图未验收',
    'repository': 'nihui-szyl/qwen-image-ncnn', 'revision': 'main',
    'sources': {'huggingface': {'repository': 'nihui-szyl/qwen-image-ncnn', 'revision': 'main'}},
    'unavailable_sources': {'modelscope': '尚未核实同版本 ncnn 权重镜像；请手动选择 Hugging Face，不会下载原始 PyTorch 权重冒充。'},
}

# Native converted bundles keep their upstream tokenizer, graph and template.
# Do not attempt to run OCR/embedding/discriminator models through a chat ABI.
from .native_catalog import NATIVE_MODELS
PROVIDERS['sdu'] = 'ncnn 上游镜像（SDU）'
for _id, _item in CATALOG.items():
    _item.update(installable=True, category='code' if 'coder' in _id else _item['kind'],
                 family='Qwen Image' if _item['kind']=='image' else 'Qwen2.5',
                 capabilities=['image'] if _item['kind']=='image' else ['chat','documents','agent-experimental'],
                 light=_id in ('qwen05','qwen_coder05'), install_kind='converted' if _id in PROFILES else 'image')
    _item.setdefault('unavailable_sources', {})['sdu'] = '此模型保留官方 Hugging Face / ModelScope 来源，请手动选择。'
for _id, _native in NATIVE_MODELS.items():
    _family = {'qwen3':'Qwen3','minicpm4':'MiniCPM4','youtu_llm':'YoutuLLM','qwen3.5':'Qwen3.5','qwen2.5_vl':'Qwen2.5 VL','qwen2-vl':'Qwen2.5 VL'}.get(_native['expected_type'],_native['expected_type'])
    _label = _native['repository'].replace('qwen3.5_0.8b','Qwen3.5 0.8B').replace('qwen2.5_vl_3b','Qwen2.5 VL 3B').replace('qwen3_0.6b','Qwen3 0.6B').replace('minicpm4_0.5b','MiniCPM4 0.5B').replace('youtu_llm','YoutuLLM').replace('_int8',' INT8')
    _quantized = _native['precision']=='int8'
    CATALOG[_id] = {
        'name':_label + (' · 文本入口' if _native['text_only'] else ''), 'kind':'llm',
        'family':_family, 'category':'llm', 'light':_family in ('Qwen3','Qwen3.5','MiniCPM4'),
        'precision':'INT8 decoder' if _quantized else '上游原始包',
        'format':'ncnn 原生权重 · 无需转换', 'install_kind':'ncnn', 'installable':True,
        'description':'保留上游模型配置、分词器和聊天模板。' + ('当前仅接入文字，不把图片上传当作视觉输入。' if _native['text_only'] else ''),
        'capabilities':['chat','documents','agent-experimental'],
        'runtime_note':'INT8 解码器由上游切换到 CPU；Vulkan 预检成功不表示量化解码在 GPU。' if _quantized else '可用硬件 Vulkan 优先；完整模型运行结果以实测为准。',
        'validation':'文件全量 SHA-256 已核对；本项目推理验收待运行',
        'estimated_download_bytes':_native['download_bytes'], 'repository':_native['repository'], 'revision':_native['revision'],
        'sources':{'sdu':{'repository':_native['repository'],'revision':_native['revision']}},
        'unavailable_sources':{'huggingface':'尚未核实相同 ncnn 字节的 HF 镜像。请手动选择 ncnn 上游镜像（SDU）。',
                               'modelscope':'尚未核实相同 ncnn 字节的魔搭镜像。请手动选择 ncnn 上游镜像（SDU）。'},
    }

# Visible roadmap, deliberately not downloadable/activatable through the chat engine.
for _id, _name, _family, _why in (
    ('pending_glm_ocr','GLM-OCR / INT8','OCR','需要图像 prefill 和 OCR 专用结果接口'),
    ('pending_hunyuan_ocr','HunyuanOCR / INT8','OCR','需要专用图像预处理与 OCR 输出接口'),
    ('pending_liteocr','LiteOCR','OCR','需要独立 OCR 适配'),
    ('pending_qwen_asr','Qwen3 ASR 0.6B / 1.7B / INT8','ASR','需要音频输入和语音识别适配'),
    ('pending_laya','Laya / Laya Multilingual / INT8','判别器','判别器输出分类分数，不是聊天生成模型'),
    ('pending_jina_text','Jina Embeddings v5 Text Nano / INT8','嵌入','输出向量，需要嵌入或检索接口'),
    ('pending_jina_clip','Jina CLIP v2 / INT8','多模态嵌入','输出图文向量，不是对话或生图模型'),
):
    CATALOG[_id]={'name':_name,'kind':'pending','category':'pending','family':_family,'installable':False,
        'sources':{},'description':_why,'validation':'上游有支持，本应用尚未接通专用调用接口',
        'format':'专用模型 · 不是聊天后端','capabilities':[], 'light':False}
