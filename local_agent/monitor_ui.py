"""Native Tk desktop management window. No browser/WebView/HTTP rendering.

All Tk work runs on the main thread. Resource collection happens separately;
closing a browser does not close this window or stop the local service.
"""
from __future__ import annotations
from collections import deque
import json
import os
from pathlib import Path
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

BG = '#f3f6f5'
CARD = '#ffffff'
INK = '#20382e'
MUTED = '#62786d'
GREEN = '#227953'


def memory(value):
    if value is None:
        return '不可用'
    if value >= 1024 ** 3:
        return f'{value / 1024 ** 3:.2f} GiB'
    return f'{value / 1024 ** 2:.1f} MiB'


def percent(value):
    return '采样中 / 不可用' if value is None else f'{value:.1f}%'


class Dashboard:
    def __init__(self, app, monitor, url, shutdown, service_alive, home, root=None):
        self.app, self.monitor, self.url = app, monitor, url
        self.shutdown, self.service_alive, self.home = shutdown, service_alive, Path(home)
        self.root = root if root is not None else tk.Tk()
        self.root.title('Local Agent · 后台管理')
        self.root.geometry('1150x820')
        self.root.minsize(890, 690)
        self.root.configure(bg=BG)
        self.root.protocol('WM_DELETE_WINDOW', self.on_close)
        self.closing = False
        self.snap = {}
        self.history = {k: deque(maxlen=90) for k in ('cpu', 'ram', 'app_cpu', 'app_ram')}
        self.last_time = None
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', font=('Segoe UI', 10), background=BG, foreground=INK)
        style.configure('TButton', padding=(13, 8), background='#e2ebe6', borderwidth=0)
        style.map('TButton', background=[('active', '#d0e2d6')])
        style.configure('Accent.TButton', background=GREEN, foreground='white')
        style.map('Accent.TButton', background=[('active', '#165c3c')])
        style.configure('Treeview', background=CARD, fieldbackground=CARD, rowheight=29, borderwidth=0)
        style.configure('Treeview.Heading', background='#e5ece8', foreground=INK, padding=(8, 7))
        style.configure('TNotebook.Tab', padding=(18, 8))
        shell = ttk.Frame(self.root, padding=22)
        shell.pack(fill='both', expand=True)
        header = ttk.Frame(shell)
        header.pack(fill='x')
        tk.Label(header, text='Local Agent', bg=BG, fg=INK, font=('Segoe UI', 23, 'bold')).pack(side='left')
        tk.Label(header, text='  /  后台管理', bg=BG, fg=MUTED, font=('Segoe UI', 12)).pack(side='left', pady=(8, 0))
        self.service_label = tk.Label(header, text='● 服务启动中', bg=BG, fg=GREEN, font=('Segoe UI', 11, 'bold'))
        self.service_label.pack(side='right')
        self.caption = tk.Label(shell, anchor='w', text=url + '   ·   浏览器关闭后本窗口继续运行', bg=BG, fg=MUTED)
        self.caption.pack(fill='x', pady=(4, 17))
        grid = ttk.Frame(shell)
        grid.pack(fill='x')
        self.cards = {}
        for i, (key, title, note) in enumerate([
            ('cpu', '整机 CPU', '100% 表示全部逻辑核满载'),
            ('ram', '整机内存', '使用量 = 总量 − 可用量'),
            ('app_cpu', 'Local Agent CPU', '本程序 + 当前子进程'),
            ('app_ram', 'Local Agent 内存', 'RSS 合计；共享页可能重复')]):
            grid.columnconfigure(i, weight=1)
            f = tk.Frame(grid, bg=CARD, padx=16, pady=12, highlightbackground='#dde7df', highlightthickness=1)
            f.grid(row=0, column=i, sticky='nsew', padx=(0 if i == 0 else 6, 6 if i < 3 else 0))
            tk.Label(f, text=title, bg=CARD, fg=MUTED, anchor='w').pack(fill='x')
            value = tk.Label(f, text='—', bg=CARD, fg=INK, anchor='w', font=('Segoe UI', 21, 'bold'))
            value.pack(fill='x', pady=(5, 0))
            detail = tk.Label(f, text=note, bg=CARD, fg=MUTED, anchor='w', font=('Segoe UI', 9))
            detail.pack(fill='x')
            canvas = tk.Canvas(f, height=36, bg=CARD, highlightthickness=0)
            canvas.pack(fill='x', pady=(8, 0))
            self.cards[key] = (value, detail, canvas)
        self.runtime_text = tk.StringVar(value='模型状态：等待采集')
        tk.Label(shell, textvariable=self.runtime_text, justify='left', anchor='w', wraplength=1070,
                 bg='#e8f0eb', fg=INK, padx=13, pady=12).pack(fill='x', pady=(16, 13))
        buttons = ttk.Frame(shell)
        buttons.pack(fill='x', pady=(0, 15))
        for title, command, style_name in [
            ('打开网页工作台', self.open_browser, 'Accent.TButton'),
            ('暂停新任务', self.toggle_pause, 'TButton'),
            ('停止当前任务', self.cancel_task, 'TButton'),
            ('导出诊断', self.export, 'TButton'),
            ('最小化', self.root.iconify, 'TButton'),
            ('退出服务', self.request_exit, 'TButton')]:
            b = ttk.Button(buttons, text=title, command=command, style=style_name)
            b.pack(side='left', padx=(0, 8))
            if title == '暂停新任务':
                self.pause_button = b
            if title == '停止当前任务':
                self.stop_button = b
        tabs = ttk.Notebook(shell)
        tabs.pack(fill='both', expand=True)
        gpu_tab = ttk.Frame(tabs, padding=12)
        proc_tab = ttk.Frame(tabs, padding=12)
        task_tab = ttk.Frame(tabs, padding=12)
        tabs.add(gpu_tab, text='GPU / 显存')
        tabs.add(proc_tab, text='进程与资源')
        tabs.add(task_tab, text='任务状态与诊断')
        self.gpus = self.table(gpu_tab, [('name', '适配器 / 采集标识', 320), ('util', 'GPU 利用率', 130),
            ('vram', '专用显存 已用 / 总量', 185), ('shared', '共享内存', 130), ('app', '本程序专用显存', 150)])
        self.gpu_note = tk.Label(gpu_tab, text='GPU 指标与 Vulkan 预检独立；等待驱动计数器。', anchor='w',
                                 justify='left', wraplength=1030, bg=BG, fg=MUTED)
        self.gpu_note.pack(fill='x', pady=(12, 0))
        self.processes = self.table(proc_tab, [('pid', 'PID', 85), ('name', '进程', 310),
            ('cpu', 'CPU（整机归一化）', 170), ('rss', '驻留内存 RSS', 145), ('threads', '线程', 80), ('status', '状态', 140)])
        self.tasks = self.table(task_tab, [('id', '任务 ID', 160), ('mode', '模式', 95), ('status', '状态', 110),
            ('stage', '阶段 / 工具', 260), ('device', '实际后端选择', 290)])
        self.diag = tk.Label(task_tab, text='没有任务。', anchor='w', justify='left', wraplength=1030, bg=BG, fg=MUTED)
        self.diag.pack(fill='x', pady=12)
        self.footer = tk.Label(shell, text='CPU/内存约每秒采集；GPU 约每 3 秒采集。数据均来自本机，不上传。',
                               anchor='w', bg=BG, fg=MUTED, font=('Segoe UI', 9))
        self.footer.pack(fill='x', pady=(12, 0))
        self.refresh()

    @staticmethod
    def table(parent, columns):
        frame = ttk.Frame(parent)
        frame.pack(fill='both', expand=True)
        tree = ttk.Treeview(frame, columns=[x[0] for x in columns], show='headings', height=7)
        bar = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=bar.set)
        tree.pack(side='left', fill='both', expand=True)
        bar.pack(side='right', fill='y')
        for key, label, width in columns:
            tree.heading(key, text=label)
            tree.column(key, width=width, minwidth=60, anchor='w')
        return tree

    @staticmethod
    def rows(tree, values):
        # Small local tables; preserve selection when possible.
        selected = tree.selection()
        tree.delete(*tree.get_children())
        for key, row in values:
            tree.insert('', 'end', iid=str(key), values=row)
        for key in selected:
            if tree.exists(key):
                tree.selection_add(key)

    def paint(self, snap):
        self.snap = snap
        runtime = snap.get('runtime', {})
        paused = bool(getattr(self.app, 'paused', False))
        self.service_label['text'] = '● 正在退出…' if self.closing else ('● 已暂停接收任务' if paused else '● 本地服务运行中')
        self.pause_button['text'] = '恢复新任务' if paused else '暂停新任务'
        self.stop_button['state'] = 'normal' if self.app.active else 'disabled'
        system, application = snap.get('system', {}), snap.get('application', {})
        values = {'cpu': percent(system.get('cpu_pct')), 'ram': memory(system.get('memory_used_bytes')),
                  'app_cpu': percent(application.get('cpu_pct')), 'app_ram': memory(application.get('rss_bytes'))}
        chart_values = {'cpu': system.get('cpu_pct'), 'ram': system.get('memory_pct'),
                        'app_cpu': application.get('cpu_pct'), 'app_ram': application.get('rss_bytes')}
        for key, (label, detail, canvas) in self.cards.items():
            label['text'] = values[key]
            if key == 'ram':
                detail['text'] = f"总计 {memory(system.get('memory_total_bytes'))}  ·  {percent(system.get('memory_pct'))}"
            if self.last_time != snap.get('sampled_at'):
                self.history[key].append(chart_values[key])
            points = list(self.history[key])
            ceiling = max([v for v in points if v is not None] + [1]) if key == 'app_ram' else 100
            width = max(canvas.winfo_width(), 10)
            canvas.delete('all')
            # Unknown samples break the line, never become a fabricated zero.
            for i in range(1, len(points)):
                if points[i-1] is not None and points[i] is not None:
                    x1, x2 = (i-1)*width/89, i*width/89
                    canvas.create_line(x1, 34-min(1, points[i-1]/ceiling)*30,
                                       x2, 34-min(1, points[i]/ceiling)*30, fill=GREEN, width=2)
        self.last_time = snap.get('sampled_at')
        models = runtime.get('models', {})
        active = next((x for x in runtime.get('tasks', []) if x['id'] == runtime.get('task')), None)
        choice = (active or {}).get('device') or {}
        choice_text = choice.get('name', '等待本次设备选择') if active else '空闲（未执行推理）'
        self.runtime_text.set(f"文本模型：{models.get('llm', {}).get('name', '未配置')}    ·    生图模型：{models.get('image', {}).get('name', '未配置')}\n"
            f"执行状态：{choice_text}    |    默认策略：Vulkan 优先，无可用硬件时 CPU。预检选择不等于每层均在 GPU 执行。")
        gpu = snap.get('gpu', {})
        self.rows(self.gpus, [(d['id'], (d['name'], percent(d.get('utilization_pct')),
            memory(d.get('dedicated_used_bytes')) + ' / ' + memory(d.get('dedicated_total_bytes')),
            memory(d.get('shared_used_bytes')), memory(d.get('app_dedicated_bytes')))) for d in gpu.get('devices', [])])
        sources = ', '.join(dict.fromkeys(d['source'] for d in gpu.get('devices', [])))
        self.gpu_note['text'] = (sources + '\n' if sources else '') + (gpu.get('note') or
            '显卡行的利用率/显存是整张卡，不等于本模型独占。WDDM 利用率为最忙引擎，共享内存不等于独立显存。')
        self.rows(self.processes, [(p['pid'], (p['pid'], p['name'], percent(p['cpu_pct']), memory(p['rss_bytes']),
            p['threads'], p['status'])) for p in snap.get('processes', [])])
        self.rows(self.tasks, [(r['id'], (r['id'][:12], r['mode'], r['status'], r.get('tool') or r['stage'],
            (r.get('device') or {}).get('name', '尚未记录'))) for r in reversed(runtime.get('tasks', []))])
        job = runtime.get('model_job', {})
        self.diag['text'] = ('模型安装：' + str(job.get('status', 'idle')) + (' / ' + str(job.get('model')) if job.get('model') else '') +
            '\n监控不展示用户提示词、Cookie、命令参数或文件正文。原始任务审计保留在本机 state/runs。')
        age = snap.get('age_seconds')
        self.footer['text'] = f"采样状态：{snap.get('status', 'starting')}  ·  数据年龄：{age:.1f} 秒" if age is not None else '正在建立监控采样…'
        if snap.get('error'):
            self.footer['text'] += '  |  ' + snap['error']
        elif application.get('partial'):
            self.footer['text'] += '  |  部分进程已退出或不可访问，汇总可能不完整。'

    def refresh(self):
        if not self.service_alive():
            self.root.destroy()
            return
        if self.monitor.focus.is_set():
            self.monitor.focus.clear()
            self.root.deiconify(); self.root.lift()
        self.paint(self.monitor.snapshot())
        self.root.after(700, self.refresh)

    def open_browser(self):
        import webbrowser
        webbrowser.open(self.url)

    def toggle_pause(self):
        self.monitor.pause(not getattr(self.app, 'paused', False))
        self.paint(self.monitor.snapshot())

    def cancel_task(self):
        self.monitor.cancel_task()

    def export(self):
        target = filedialog.asksaveasfilename(parent=self.root, title='保存资源诊断', defaultextension='.json',
            initialfile='local-agent-monitor.json', filetypes=[('JSON', '*.json')])
        if target:
            try:
                Path(target).write_text(json.dumps(self.monitor.snapshot(), ensure_ascii=False, indent=2), encoding='utf-8')
            except OSError as e:
                messagebox.showerror('保存失败', str(e), parent=self.root)

    def request_exit(self):
        if self.closing:
            return
        if not messagebox.askyesno('退出 Local Agent', '停止服务和当前任务？\n已完成的文件写入不会回滚。正在执行的工具需要等待结束或超时。', parent=self.root):
            return
        self.begin_exit()

    def begin_exit(self):
        self.closing = True
        self.monitor.pause(True)
        self.monitor.cancel_task()
        hub = getattr(self.app, 'model_hub', None)
        if hub:
            hub.stop()
        self._wait_exit()

    def _wait_exit(self):
        hub = getattr(self.app, 'model_hub', None)
        if hub:
            with hub.lock:
                installing = hub.job.get('status') in ('downloading', 'verifying', 'converting', 'activating')
        else:
            installing = False
        if self.app.active or installing:
            self.root.after(500, self._wait_exit)
        else:
            import threading
            threading.Thread(target=self.shutdown, daemon=True).start()

    def on_close(self):
        answer = messagebox.askyesnocancel('关闭管理窗口', '是否退出整个本地服务？\n“是”停止服务；“否”最小化并继续运行。', parent=self.root)
        if answer is True:
            self.begin_exit()
        elif answer is False:
            self.root.iconify()

    def run(self):
        self.root.mainloop()


def native_self_test(output):
    """Real native window + real CPU/RAM sampling; no model or GPU fixture."""
    import tempfile
    from .monitor import MonitorService
    from .monitor_http import ManagedWebApp as WebApp
    report = {'ok': False, 'scope': 'Actual Tk native window and OS CPU/RAM metrics. No model inference or fabricated GPU metrics.'}
    dashboard = monitor = None
    with tempfile.TemporaryDirectory(prefix='monitor-ui-') as tmp:
        home = Path(tmp)
        app = WebApp({'workspace': str(home/'workspace'), 'llm': {'model': 'unconfigured', 'device': 'auto'}, 'image': {}}, home/'state')
        try:
            monitor = MonitorService(app)
            dashboard = Dashboard(app, monitor, 'http://127.0.0.1:8765', lambda: None, lambda: True, home)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                dashboard.root.update()
                sample = monitor.snapshot()
                if sample.get('system', {}).get('cpu_pct') is not None:
                    break
                time.sleep(.05)
            dashboard.paint(sample)
            dashboard.root.update()
            assert sample['status'] == 'ok' and sample['system']['memory_total_bytes'] > 0, sample
            assert any(p['pid'] == os.getpid() for p in sample['processes'])
            assert dashboard.root.winfo_viewable()
            dashboard.toggle_pause(); assert app.paused
            dashboard.toggle_pause(); assert not app.paused
            dashboard.root.iconify(); dashboard.root.update()
            dashboard.root.deiconify(); dashboard.root.update()
            report.update(ok=True, checks=['Tk window visible', 'OS CPU/RAM sampled', 'own PID measured',
                'pause/resume controller', 'minimize/restore'], snapshot=sample, tk_version=str(tk.TkVersion))
            try:
                from PIL import ImageGrab
                x, y = dashboard.root.winfo_rootx(), dashboard.root.winfo_rooty()
                ImageGrab.grab(bbox=(x, y, x+dashboard.root.winfo_width(), y+dashboard.root.winfo_height())).save(Path(output).with_suffix('.png'))
                report['screenshot'] = Path(output).with_suffix('.png').name
            except Exception as e:
                report['screenshot_unavailable'] = str(e)
        except Exception as e:
            report['error'] = f'{type(e).__name__}: {e}'
        finally:
            if dashboard:
                dashboard.root.destroy()
            if monitor:
                monitor.close()
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--self-test', type=Path, required=True)
    a = ap.parse_args()
    a.self_test.parent.mkdir(parents=True, exist_ok=True)
    raise SystemExit(native_self_test(a.self_test))
