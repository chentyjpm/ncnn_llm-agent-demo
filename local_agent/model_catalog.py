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
