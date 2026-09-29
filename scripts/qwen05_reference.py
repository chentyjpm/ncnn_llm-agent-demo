#!/usr/bin/env python3
"""CPU reference vectors from official weights; never used as inference fallback."""
import argparse
import json
from pathlib import Path
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    torch.set_num_threads(4)
    tokenizer = AutoTokenizer.from_pretrained(a.source, local_files_only=True, trust_remote_code=False)
    model = AutoModelForCausalLM.from_pretrained(a.source, local_files_only=True, trust_remote_code=False,
                                               torch_dtype=torch.float32, attn_implementation='eager').eval().to('cpu')
    records = []
    for question in ['What is 2 + 2? Reply with only the number.',
                     'What is the capital of France? Reply with one word.',
                     '中国的首都是哪里？只回答城市名称。']:
        msgs = [{'role': 'system', 'content': 'You are a helpful assistant.'}, {'role': 'user', 'content': question}]
        ids = tokenizer.apply_chat_template(msgs, tokenize=True, add_generation_prompt=True, return_tensors='pt')
        seq = ids.clone()
        steps = []
        with torch.inference_mode():
            for _ in range(4):
                logits = model(seq, use_cache=False).logits[0, -1].float()
                top = torch.topk(logits, 8)
                next_id = int(logits.argmax())
                steps.append({'next_id': next_id, 'indices': top.indices.tolist(), 'logits': top.values.tolist()})
                seq = torch.cat([seq, torch.tensor([[next_id]], dtype=torch.long)], dim=1)
                if next_id == tokenizer.eos_token_id: break
        records.append({'question': question, 'input_ids': ids[0].tolist(), 'steps': steps,
                        'reference_text': tokenizer.decode(seq[0, ids.shape[1]:], skip_special_tokens=True)})
    result = {'backend': 'transformers CPU float32 reference only', 'torch': torch.__version__, 'vectors': records}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
