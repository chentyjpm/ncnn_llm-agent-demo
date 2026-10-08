"""Public execution summaries, derived ONLY from observed application events.

Not model reasoning, not a fabricated plan, and never a second model/API call.
The raw Audit log stays separate. Call under Run.condition (one writer).
"""
from __future__ import annotations
import copy
import math
import re
import time

VERSION = 1
MAX_STEPS = 120
NOTE = '根据真实执行事件整理；不是模型内部思维链，也不是逐 token 输出。'
PENDING = {'prepared', 'running', 'waiting', 'stopping'}
SENSITIVE = re.compile(r'(?:password|passwd|secret|token|api.?key|authorization|cookie|credential|private.?key)', re.I)
PURPOSES = {
    'files.list': '查看工作区文件', 'files.read': '读取工作区文本',
    'files.write': '写入工作区文件', 'files.patch': '修改工作区文件',
    'documents.read': '提取文档正文与表格', 'documents.create': '生成新的工作文档',
    'images.generate': '调用 Qwen Image 生成图片', 'python.run': '执行已授权的 Python',
    'system.run': '执行管理员配置的固定命令',
}


def display_value(value):
    """Bounded display copy; never used for execution or permission decisions.

    Common secret keys/bearer strings are masked. Not a general DLP guarantee.
    All rendering must still use text nodes, never HTML.
    """
    budget = [10000]
    def walk(v, depth=0):
        if budget[0] <= 0: return '[已截断]'
        budget[0] -= 16
        if isinstance(v, dict):
            if depth >= 4: return '[嵌套内容已省略]'
            out = {}
            for k, x in list(v.items())[:32]:
                key = str(k)[:100]; budget[0] -= len(key)
                out[key] = '[已隐藏敏感字段]' if SENSITIVE.search(key) else walk(x, depth + 1)
            if len(v) > 32: out['…'] = '其余字段已省略'
            return out
        if isinstance(v, (list, tuple)):
            if depth >= 4: return '[嵌套内容已省略]'
            return [walk(x, depth + 1) for x in v[:32]] + (['[其余项已省略]'] if len(v) > 32 else [])
        if v is None or type(v) in (bool, int): return v
        if type(v) is float: return v if math.isfinite(v) else str(v)
        s = str(v)
        s = re.sub(r'(?i)\bBearer\s+[^\s\"\'<>]+', 'Bearer [已隐藏]', s)
        s = re.sub(r'(?i)([?&](?:token|api_key|key|secret|password)=)[^&\s]+', r'\1[已隐藏]', s)
        n = min(2048, max(0, budget[0])); budget[0] -= min(len(s), n)
        return s[:n] + ('…[已截断]' if len(s) > n else '')
    return walk(value)


def visible_answer(raw: str) -> tuple[str, bool]:
    """Separate leading exported <think> blocks from chat's final answer.

    Incomplete leading reasoning is NOT shown as a final answer. Literal think
    tags in code examples or later in an answer are not treated as reasoning.
    Nothing here generates a purported summary of the removed content.
    """
    text = raw.lstrip(); detected = False
    while text.startswith('<think>'):
        detected = True
        end = text.find('</think>', 7)
        if end < 0: return '', True
        text = text[end + 8:].lstrip()
    return text if detected else raw, detected


class Activity:
    def __init__(self, mode='chat', *, clock=time.monotonic):
        self.clock = clock; self.started = clock()
        self.data = {'version': VERSION, 'source': 'runtime_events', 'note': NOTE,
                     'revision': 0, 'mode': mode, 'state': 'running', 'stage': '准备任务',
                     'context': {}, 'steps': [], 'omitted_steps': 0, 'reasoning_detected': False}
        self.current_model = None; self.current_tool = None
        self.model_count = 0; self.tool_count = 0; self.finished = None; self.counted_tools = set()
        self.counts = {'completed': 0, 'failed': 0, 'denied': 0, 'expired': 0, 'cancelled': 0, 'interrupted': 0}

    def now(self): return max(0, int((self.clock() - self.started) * 1000))

    def row(self, row_id):
        return next((x for x in self.data['steps'] if x['id'] == row_id), None)

    def add(self, kind, title, status, **extra):
        row_id = f'{kind}-{self.data["revision"]}'
        row = {'id': row_id, 'kind': kind, 'title': title, 'status': status, 'started_ms': self.now(), **extra}
        if len(self.data['steps']) >= MAX_STEPS:
            self.data['steps'].pop(0)
            self.data['omitted_steps'] += 1
        self.data['steps'].append(row); return row

    def close(self, row_id, status):
        row = self.row(row_id)
        if row:
            row['status'] = status; row['duration_ms'] = max(0, self.now() - row['started_ms'])
            if row['kind'] == 'tool':
                if 'execution_started_ms' in row:
                    row['execution_ms'] = max(0, self.now() - row['execution_started_ms'])
                else: row['execution_ms'] = 0
                if 'wait_started_ms' in row and 'wait_ms' not in row:
                    row['wait_ms'] = max(0, self.now() - row['wait_started_ms'])
        return row

    def record(self, kind, data):
        if self.finished is not None: return False
        recognized = {'run_started','model_start','model_done','tool_start','tool_execute','tool_result',
                      'approval_required','approval_resolved','format_error','stopping','run_finished'}
        if kind not in recognized: return False
        self.data['revision'] += 1
        if kind == 'run_started':
            self.data['context'] = {k: display_value(data[k]) for k in ('model','history_turns','attachments') if k in data}
            labels = {'chat':'直接回答，不提供工具', 'agent':'由模型提出动作，工具按权限执行', 'image':'独立生图，不需要文字模型'}
            self.add('context', labels.get(self.data['mode'], '准备任务'), 'completed', duration_ms=0)
        elif kind == 'model_start':
            self.model_count += 1
            self.current_model = self.add('model', f'第 {self.model_count} 轮：模型生成回复', 'running')['id']
            self.data['stage'] = '模型正在生成回复'
        elif kind == 'model_done':
            self.close(self.current_model, 'completed')
            if self.row(self.current_model): self.row(self.current_model)['output_chars'] = data.get('output_chars', 0)
            self.data['reasoning_detected'] |= data.get('reasoning_detected') is True
            self.data['stage'] = '检查模型回复'
        elif kind == 'tool_start':
            self.tool_count += 1
            name = str(data.get('tool', 'unknown'))[:128]
            title = PURPOSES.get(name, '调用已注册的 MCP 工具' if name.startswith('mcp__') else '请求调用工具')
            self.current_tool = self.add('tool', title, 'prepared', tool=name, step=data.get('step'),
                                        arguments=display_value(data.get('arguments', {})), wait_ms=0)['id']
            self.data['stage'] = title + ' · 等待检查'
        elif kind == 'approval_required':
            row = self.row(self.current_tool)
            if row:
                row.update(status='waiting', approval_id=data.get('approval', {}).get('id'), wait_started_ms=self.now())
                row.pop('wait_ms', None)
            self.data['stage'] = '等待你的单次确认'
        elif kind == 'approval_resolved':
            row = self.row(self.current_tool)
            if row:
                row['wait_ms'] = max(0, self.now() - row.get('wait_started_ms', self.now()))
                row['approval'] = 'allowed' if data.get('allowed') else data.get('reason', 'denied')
                row['status'] = 'prepared' if data.get('allowed') else row['approval']
                if not data.get('allowed'): self.close(row['id'], row['status'])
            self.data['stage'] = '已确认，准备执行' if data.get('allowed') else '本次调用未获准执行'
        elif kind == 'tool_execute':
            row = self.row(self.current_tool)
            if row:
                row.update(status='running', execution_started_ms=self.now())
                self.data['stage'] = row['title']
        elif kind == 'tool_result':
            row = self.row(self.current_tool)
            if row:
                result = data.get('result', {})
                status = row['status'] if row['status'] in ('denied','expired','cancelled') else ('completed' if result.get('ok') is True else 'failed')
                self.close(row['id'], status); row['result'] = display_value(result)
                self.count_tool(row)
                self.current_tool = None
            self.data['stage'] = '工具结果已记录'
        elif kind == 'format_error':
            self.add('correction', '模型动作格式不合格，返回错误供下一轮修正', 'failed',
                     error=display_value(data.get('error', '动作格式错误')), duration_ms=0)
            self.data['stage'] = '模型动作需要修正'
        elif kind == 'stopping':
            self.data.update(state='stopping', stage='正在停止，等待当前操作结束')
        elif kind == 'run_finished':
            state = data.get('status', 'failed')
            labels = {'completed':'本轮已完成', 'failed':'本轮失败', 'cancelled':'本轮已停止'}
            for row in self.data['steps']:
                if row['status'] in PENDING:
                    final = 'cancelled' if state == 'cancelled' else 'interrupted'
                    self.close(row['id'], final)
                self.count_tool(row)
            self.data.update(state=state, stage=labels.get(state, '本轮结束'))
            if data.get('error'): self.data['error'] = display_value(data['error'])
            self.finished = self.now()
        return True

    def count_tool(self, row):
        if row['kind'] == 'tool' and row['status'] in self.counts and row['id'] not in self.counted_tools:
            self.counts[row['status']] += 1; self.counted_tools.add(row['id'])

    def snapshot(self):
        out = copy.deepcopy(self.data); now = self.now() if self.finished is None else self.finished
        out.update(elapsed_ms=now, model_calls=self.model_count, tool_calls=self.tool_count, tool_counts=dict(self.counts))
        for row in out['steps']:
            if row['status'] in PENDING:
                row['duration_ms'] = max(0, now - row['started_ms'])
                if row['status'] == 'waiting': row['wait_ms'] = max(0, now - row['wait_started_ms'])
                if row['status'] == 'running' and row['kind'] == 'tool': row['execution_ms'] = max(0, now - row['execution_started_ms'])
        return out


def interrupted(snapshot):
    """Recovery: freeze last observed durations, never re-execute a tool."""
    out = copy.deepcopy(snapshot)
    out.update(state='failed', stage='服务重启，任务已中断', error='已完成的操作不会回滚；未完成的操作不会自动重试。')
    for row in out.get('steps', []):
        if row.get('status') in PENDING:
            row['status'] = 'interrupted'
            if row.get('kind') == 'tool':
                counts = out.setdefault('tool_counts', {}); counts['interrupted'] = counts.get('interrupted', 0) + 1
    return out
