"""Native resource console: consistent geometry, responsive panels, real telemetry.

Tk stays on the main thread. Only presentation changes here; device selection,
metrics providers, tool permissions and Windows background process flags do not.
"""
from __future__ import annotations
from collections import deque
import json
import os
from pathlib import Path
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from .monitor_widgets import COLORS as C, Theme, Button, MetricCard, memory, percent, ellipsis, rounded, draw_icon

STATES = {'running':'执行中', 'stopping':'正在停止', 'completed':'已完成', 'failed':'失败',
          'cancelled':'已取消', 'idle':'空闲', 'downloading':'下载中', 'verifying':'校验中',
          'converting':'转换中', 'activating':'启用中', 'interrupted':'已中断',
          'model_start':'模型推理', 'tool_start':'执行工具', 'tool_result':'工具已返回',
          'approval_required':'等待确认', 'starting':'正在启动', 'done':'结束'}


class Dashboard:
    def __init__(self, app, monitor, url, shutdown, service_alive, home, root=None):
        self.app, self.monitor, self.url = app, monitor, url
        self.shutdown, self.service_alive, self.home = shutdown, service_alive, Path(home)
        self.root = root if root is not None else tk.Tk()
        self.t = Theme(self.root)
        p = self.t.p
        self.root.title('Local Agent · 后台管理')
        sw,sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        width,height=min(p(1240),sw-50),min(p(870),sh-90)
        # Explicit initial placement keeps the capped window above the taskbar
        # on a 1024x768 runner as well as on larger end-user desktops.
        x,y=max(8,(sw-width)//2),max(8,(sh-height-60)//2)
        self.root.geometry(f'{width}x{height}+{x}+{y}')
        self.root.minsize(min(p(880),sw-50), min(p(650),sh-90))
        self.root.configure(bg=C['bg'])
        self.root.protocol('WM_DELETE_WINDOW', self.on_close)
        self.closing, self.snap, self.last_time = False, {}, None
        self.history = {k:deque(maxlen=90) for k in ('cpu','ram','app_cpu','app_ram')}
        self._after = None
        self._layout_pending = False
        self._card_columns = 0
        self._title_applied = False
        self.style = ttk.Style(self.root)
        self.style.theme_use('clam')
        self.style.configure('Monitor.Treeview', background=C['panel'],fieldbackground=C['panel'],
            foreground=C['text'], font=self.t.font(12), rowheight=p(38), borderwidth=0,
            lightcolor=C['panel'],darkcolor=C['panel'],bordercolor=C['panel'])
        self.style.map('Monitor.Treeview',background=[('selected',C['selected'])],foreground=[('selected',C['accent'])])
        self.style.configure('Monitor.Treeview.Heading',background=C['raised'],foreground=C['muted'],
            font=self.t.font(11),padding=(p(10),p(10)),relief='flat',borderwidth=0,
            bordercolor=C['raised'],lightcolor=C['raised'],darkcolor=C['raised'])
        self.style.map('Monitor.Treeview.Heading', background=[('active',C['raised'])], relief=[('active','flat')])
        for axis in ('Vertical','Horizontal'):
            self.style.configure('Monitor.'+axis+'.TScrollbar',background=C['line'],troughcolor=C['panel'],
                bordercolor=C['panel'],arrowcolor=C['muted'],lightcolor=C['line'],darkcolor=C['line'],arrowsize=p(10),borderwidth=0)
            self.style.map('Monitor.'+axis+'.TScrollbar',background=[('active',C['dim'])])
        # The complete surface can scroll on a small/high-DPI display. Nothing
        # is made inaccessible by a rigid fixed-height window.
        self.viewport=tk.Canvas(self.root,bg=C['bg'],bd=0,highlightthickness=0)
        self.scrollbar=ttk.Scrollbar(self.root,orient='vertical',command=self.viewport.yview,style='Monitor.Vertical.TScrollbar')
        self.viewport.configure(yscrollcommand=self.scrollbar.set)
        self.viewport.pack(side='left',fill='both',expand=True)
        self.shell=tk.Frame(self.viewport,bg=C['bg'],padx=p(24),pady=p(20))
        self.window=self.viewport.create_window(0,0,window=self.shell,anchor='nw')
        self.shell.columnconfigure(0,weight=1)
        self.shell.rowconfigure(4,weight=1)
        self.viewport.bind('<Configure>',self._schedule_layout)
        self.shell.bind('<Configure>',self._schedule_layout)
        self.root.bind('<MouseWheel>',self._wheel,add='+')
        self.root.bind('<Button-4>',self._wheel,add='+')
        self.root.bind('<Button-5>',self._wheel,add='+')
        self.root.bind('<Control-Tab>',lambda _:self._next_tab(1))
        self.root.bind('<Control-Shift-Tab>',lambda _:self._next_tab(-1))
        self._header()
        self.card_grid=tk.Frame(self.shell,bg=C['bg'])
        self.card_grid.grid(row=1,column=0,sticky='ew',pady=(p(21),p(16)))
        self.cards={}
        for key,title,caption,color,capacity in [
            ('cpu','整机 CPU','整机逻辑核归一化',C['cyan'],100),
            ('ram','整机内存','总量 − 可用量',C['purple'],100),
            ('app_cpu','Agent CPU','本程序与当前子进程',C['accent'],100),
            ('app_ram','Agent 内存','RSS 合计 · 共享页可能重复',C['blue'],None)]:
            self.cards[key]=MetricCard(self.card_grid,self.t,title,caption,color,capacity)
        self._runtime()
        self._toolbar()
        self._panels()
        self.footer=self.label(self.shell,'正在建立监控采样…',size=10,color='dim')
        self.footer.grid(row=5,column=0,sticky='ew',pady=(p(12),0))
        self.root.bind('<Destroy>',self._destroyed,add='+')
        self.refresh()

    def label(self,parent,text='',*,size=12,color='text',bold=False,**kw):
        return tk.Label(parent,text=text,bg=parent.cget('bg'),fg=C[color],font=self.t.font(size,bold),
                        anchor='w',bd=0,highlightthickness=0,**kw)

    def _header(self):
        p=self.t.p
        header=tk.Frame(self.shell,bg=C['bg']);header.grid(row=0,column=0,sticky='ew')
        header.columnconfigure(1,weight=1)
        mark=tk.Canvas(header,width=p(44),height=p(44),bg=C['bg'],highlightthickness=0)
        rounded(mark,(1,1,p(43),p(43)),p(12),fill=C['selected'],outline=C['line'])
        draw_icon(mark,'chip',p(11),p(11),p(22),C['accent'])
        mark.grid(row=0,column=0,rowspan=2,padx=(0,p(14)))
        self.label(header,'Local Agent',size=26,bold=True).grid(row=0,column=1,sticky='w')
        self.label(header,'本地运行控制台  /  RUNTIME CONTROL',size=10,color='dim').grid(row=1,column=1,sticky='w',pady=(p(2),0))
        self.service_label=self.label(header,'●  服务连接中',size=12,color='accent',bold=True)
        self.service_label.grid(row=0,column=2,sticky='e')
        self.caption=self.label(header,self.url,size=10,color='muted')
        self.caption.grid(row=1,column=2,sticky='e')

    def _runtime(self):
        p=self.t.p
        band=tk.Frame(self.shell,bg=C['panel'],padx=p(16),pady=p(12),highlightbackground=C['line'],highlightcolor=C['line'],highlightthickness=1)
        band.grid(row=2,column=0,sticky='ew')
        for col in range(3):band.columnconfigure(col,weight=1,uniform='runtime')
        self.model_text=self.label(band,'文本模型 · 等待采样',size=12)
        self.model_image=self.label(band,'生图模型 · 等待采样',size=12)
        self.activity=self.label(band,'等待任务状态',size=12,color='accent')
        for i,label in enumerate((self.model_text,self.model_image,self.activity)):
            label.grid(row=0,column=i,sticky='ew',padx=(0,p(12)))
        self.policy=self.label(band,'设备策略  AUTO · Vulkan 优先，不可用时 CPU',size=10,color='muted')
        self.policy.grid(row=1,column=0,columnspan=3,sticky='ew',pady=(p(6),0))

    def _toolbar(self):
        p=self.t.p
        self.toolbar=tk.Frame(self.shell,bg=C['bg']);self.toolbar.grid(row=3,column=0,sticky='ew',pady=(p(14),p(18)))
        self.action_buttons=[]
        for text,command,icon,kind in [
            ('打开工作台',self.open_browser,'launch','primary'),
            ('暂停新任务',self.toggle_pause,'pause','normal'),
            ('停止当前任务',self.cancel_task,'stop','normal'),
            ('导出诊断',self.export,'export','normal'),
            ('最小化',self.root.iconify,'minimize','normal'),
            ('退出服务',self.request_exit,'power','danger')]:
            button=Button(self.toolbar,self.t,text,command,icon=icon,kind=kind)
            self.action_buttons.append(button)
        self.pause_button,self.stop_button=self.action_buttons[1:3]

    def _panels(self):
        p=self.t.p
        self.panel=tk.Frame(self.shell,bg=C['panel'],highlightbackground=C['line'],highlightcolor=C['line'],highlightthickness=1)
        self.panel.grid(row=4,column=0,sticky='nsew')
        self.panel.columnconfigure(0,weight=1);self.panel.rowconfigure(2,weight=1)
        nav=tk.Frame(self.panel,bg=C['panel'],padx=p(12),pady=p(10));nav.grid(row=0,column=0,sticky='ew')
        self.tab_buttons=[]
        self.tab_names=('GPU / 显存','进程与资源','任务与诊断')
        for i,(name,icon) in enumerate(zip(self.tab_names,('chip','pulse','list'))):
            button=Button(nav,self.t,name,lambda i=i:self.select_tab(i),icon=icon,kind='tab')
            button.grid(row=0,column=i,sticky='ns',padx=(0,p(6)))
            button.bind('<Right>',lambda _,i=i:self._focus_tab((i+1)%3))
            button.bind('<Left>',lambda _,i=i:self._focus_tab((i-1)%3))
            button.bind('<Home>',lambda _:self._focus_tab(0))
            button.bind('<End>',lambda _:self._focus_tab(2))
            self.tab_buttons.append(button)
        nav.columnconfigure(3,weight=1)
        self.panel_count=self.label(nav,'',size=10,color='dim');self.panel_count.grid(row=0,column=3,sticky='e',padx=p(5))
        tk.Frame(self.panel,bg=C['line'],height=1).grid(row=1,column=0,sticky='ew')
        self.page_host=tk.Frame(self.panel,bg=C['panel'],height=p(230))
        self.page_host.grid(row=2,column=0,sticky='nsew')
        self.page_host.grid_propagate(False)
        self.page_host.columnconfigure(0,weight=1);self.page_host.rowconfigure(0,weight=1)
        self.pages=[]
        for _ in range(3):
            page=tk.Frame(self.page_host,bg=C['panel'],padx=p(12),pady=p(12))
            page.grid(row=0,column=0,sticky='nsew');page.columnconfigure(0,weight=1);page.rowconfigure(0,weight=1)
            self.pages.append(page)
        self.gpus=self.table(self.pages[0],[('name','适配器 / 采集标识',330),('util','GPU 利用率',120),
            ('vram','专用显存  已用 / 总量',210),('shared','共享内存',130),('app','本程序专用显存',160)],'未取得 GPU 指标 · 等待驱动计数器')
        self.gpu_note=self.label(self.pages[0],'GPU 指标与 Vulkan 预检独立；不可用的指标不会显示为 0。',size=10,color='dim',justify='left')
        self.gpu_note.grid(row=1,column=0,sticky='ew',pady=(p(12),0))
        self.processes=self.table(self.pages[1],[('pid','PID',90),('name','进程',290),('cpu','CPU / 整机占比',165),
            ('rss','驻留内存 RSS',155),('threads','线程',85),('status','状态',160)],'等待进程采样')
        self.proc_note=self.label(self.pages[1],'仅本程序及子进程 · RSS 合计可能重复计入共享页。',size=10,color='dim')
        self.proc_note.grid(row=1,column=0,sticky='ew',pady=(p(12),0))
        self.tasks=self.table(self.pages[2],[('id','任务 ID',155),('mode','模式',90),('status','状态',110),
            ('stage','阶段 / 工具',285),('device','后端设备记录',305)],'当前没有任务 · 从网页工作台发起一次对话')
        self.diag=self.label(self.pages[2],'原始审计保存在本机 state/runs。',size=10,color='dim',justify='left')
        self.diag.grid(row=1,column=0,sticky='ew',pady=(p(12),0))
        self.tables=(self.gpus,self.processes,self.tasks)
        self.active_tab=0
        self.select_tab(0)

    def table(self,parent,columns,empty_text):
        p=self.t.p
        frame=tk.Frame(parent,bg=C['panel']);frame.grid(row=0,column=0,sticky='nsew')
        frame.columnconfigure(0,weight=1);frame.rowconfigure(0,weight=1)
        tree=ttk.Treeview(frame,columns=[x[0] for x in columns],show='headings',height=4,style='Monitor.Treeview')
        vertical=ttk.Scrollbar(frame,orient='vertical',command=tree.yview,style='Monitor.Vertical.TScrollbar')
        horizontal=ttk.Scrollbar(frame,orient='horizontal',command=tree.xview,style='Monitor.Horizontal.TScrollbar')
        def scroll_state(bar, first, last):
            bar.set(first,last)
            if float(first)<=0 and float(last)>=1:bar.grid_remove()
            else:bar.grid()
        tree.configure(yscrollcommand=lambda a,b:scroll_state(vertical,a,b),
                       xscrollcommand=lambda a,b:scroll_state(horizontal,a,b))
        tree.grid(row=0,column=0,sticky='nsew');vertical.grid(row=0,column=1,sticky='ns')
        horizontal.grid(row=1,column=0,sticky='ew')
        for key,label,width in columns:
            tree.heading(key,text=label,anchor='w')
            tree.column(key,width=p(width),minwidth=p(width*.72),anchor='w',stretch=True)
        tree.tag_configure('odd',background=C['panel'])
        tree.tag_configure('even',background='#162334')
        tree.empty=self.label(frame,empty_text,size=12,color='muted')
        tree.empty.place(relx=.5,rely=.58,anchor='center')
        return tree

    @staticmethod
    def rows(tree,values):
        # Update by stable IDs instead of destroying every row each sample.
        # This preserves keyboard focus, selection and scroll position.
        values=list(values);keys={str(k) for k,_ in values}
        for key in tree.get_children():
            if key not in keys:tree.delete(key)
        for index,(key,row) in enumerate(values):
            key=str(key);tags=('even' if index%2 else 'odd',)
            if tree.exists(key):
                tree.item(key,values=row,tags=tags);tree.move(key,'',index)
            else:tree.insert('','end',iid=key,values=row,tags=tags)
        if values:tree.empty.place_forget()
        else:tree.empty.place(relx=.5,rely=.58,anchor='center')

    def select_tab(self,index):
        if index not in range(3):raise ValueError('Unknown monitoring panel')
        self.active_tab=index
        self.pages[index].tkraise()
        for i,button in enumerate(self.tab_buttons):button.set(selected=i==index)
        self._counts()

    def _focus_tab(self,index):
        self.select_tab(index);self.tab_buttons[index].focus_set();return 'break'

    def _next_tab(self,delta):
        return self._focus_tab((self.active_tab+delta)%3)

    def _counts(self):
        if hasattr(self,'tables'):
            count=len(self.tables[self.active_tab].get_children())
            self.panel_count.configure(text=f'{count} '+('个适配器','个进程','条任务')[self.active_tab])

    def _schedule_layout(self,_=None):
        if not self._layout_pending:
            self._layout_pending=True
            self.root.after_idle(self._layout)

    def _layout(self):
        self._layout_pending=False
        if not self.root.winfo_exists():return
        p=self.t.p
        width=max(1,self.viewport.winfo_width())
        self.viewport.itemconfigure(self.window,width=width)
        columns=4 if width/self.t.scale>=940 else 2
        if columns!=self._card_columns:
            self._card_columns=columns
            for i in range(4):self.card_grid.columnconfigure(i,weight=1 if i<columns else 0,uniform='cards' if i<columns else '')
            for i,card in enumerate(self.cards.values()):
                card.grid(row=i//columns,column=i%columns,sticky='ew',padx=(0,p(12) if i%columns<columns-1 else 0),pady=(0,p(12) if i//columns==0 and columns==2 else 0))
        available=width-2*p(24)
        total=sum(int(b.cget('width')) for b in self.action_buttons)+5*p(8)
        for i,b in enumerate(self.action_buttons):
            row,col=(0,i) if total<=available else (i//3,i%3)
            b.grid(row=row,column=col,sticky='w',padx=(0,p(8)),pady=(0,p(8) if row==0 and total>available else 0))
        self.footer.configure(wraplength=max(p(200),available))
        for label in (self.gpu_note,self.proc_note,self.diag):label.configure(wraplength=max(p(200),available-p(36)))
        # Geometry recalculates on the next idle pass; requested child height
        # is independent from allocated height (page_host has propagation off).
        needed=self.shell.winfo_reqheight()
        height=max(needed,self.viewport.winfo_height())
        self.viewport.itemconfigure(self.window,height=height)
        self.viewport.configure(scrollregion=(0,0,width,height))
        if needed>self.viewport.winfo_height()+2:
            if not self.scrollbar.winfo_manager():self.scrollbar.pack(side='right',fill='y')
        else:
            self.scrollbar.pack_forget();self.viewport.yview_moveto(0)
        if self.snap:self._runtime_labels(self.snap.get('runtime',{}))
        if not self._title_applied:
            self._title_applied=True;self._dark_titlebar()

    def _wheel(self,event):
        if event.widget.winfo_class() in ('Treeview','TScrollbar'):return
        if self.scrollbar.winfo_manager():
            delta=(-1 if getattr(event,'num',0)==4 else 1) if not getattr(event,'delta',0) else (-1 if event.delta>0 else 1)
            self.viewport.yview_scroll(delta*3,'units')
            return 'break'

    def _dark_titlebar(self):
        if os.name!='nt':return
        try:
            import ctypes as ct
            from ctypes import wintypes as wt
            user=ct.WinDLL('user32',use_last_error=True);dwm=ct.WinDLL('dwmapi',use_last_error=True)
            user.GetAncestor.argtypes=[wt.HWND,wt.UINT];user.GetAncestor.restype=wt.HWND
            dwm.DwmSetWindowAttribute.argtypes=[wt.HWND,wt.DWORD,ct.c_void_p,wt.DWORD]
            dwm.DwmSetWindowAttribute.restype=ct.c_long
            hwnd=user.GetAncestor(self.root.winfo_id(),2);on=wt.BOOL(True)
            dwm.DwmSetWindowAttribute(hwnd,20,ct.byref(on),ct.sizeof(on))
        except (OSError,AttributeError):
            pass  # Cosmetic OS support is optional; do not remove the native title bar.

    def _fit(self,label,text):
        width=max(self.t.p(100),label.winfo_width()-self.t.p(4))
        label.configure(text=ellipsis(text,self.t.font(12).measure,width))

    def _runtime_labels(self,runtime):
        models=runtime.get('models',{})
        active=next((x for x in runtime.get('tasks',[]) if x['id']==runtime.get('task')),None)
        self._fit(self.model_text,'文本模型  '+models.get('llm',{}).get('name','未配置'))
        image=models.get('image',{})
        self._fit(self.model_image,'生图模型  '+(image.get('name','未配置') if image.get('enabled') else '未启用'))
        status=(active or {}).get('status')
        self._fit(self.activity,('当前任务  '+STATES.get(status,status or '等待')) if active else '空闲 · 等待新任务')
        choice=(active or {}).get('device') or {}
        if active and choice:
            text='本次后端  '+choice.get('name','未记录')+'  ·  '+choice.get('reason','')
        else:
            policy=models.get('llm',{}).get('device_policy','auto')
            text='设备策略  AUTO · Vulkan 优先，不可用时 CPU' if policy=='auto' else '文本设备策略  '+str(policy)
        self.policy.configure(text=ellipsis(text,self.t.font(10).measure,max(self.t.p(250),self.policy.winfo_width())))

    def paint(self,snap):
        self.snap=snap
        runtime=snap.get('runtime',{})
        paused=bool(getattr(self.app,'paused',False))
        self.service_label.configure(text='●  正在退出' if self.closing else ('●  已暂停新任务' if paused else '●  本地服务运行中'),
            fg=C['amber'] if paused or self.closing else C['accent'])
        self.pause_button.set(text='恢复新任务' if paused else '暂停新任务',icon='play' if paused else 'pause')
        self.stop_button.set(enabled=bool(self.app.active) and not self.closing)
        system,application=snap.get('system',{}),snap.get('application',{})
        values={'cpu':percent(system.get('cpu_pct')),'ram':memory(system.get('memory_used_bytes')),
            'app_cpu':percent(application.get('cpu_pct')),'app_ram':memory(application.get('rss_bytes'))}
        series={'cpu':system.get('cpu_pct'),'ram':system.get('memory_pct'),'app_cpu':application.get('cpu_pct'),'app_ram':application.get('rss_bytes')}
        details={'cpu':f"{system.get('logical_cpus','—')} 个逻辑核心 · 100% 为整机满载",
            'ram':f"/ {memory(system.get('memory_total_bytes'))}  ·  {percent(system.get('memory_pct'))}",
            'app_cpu':f"{len(snap.get('processes',[]))} 个关联进程 · 整机占比",
            'app_ram':'驻留内存 · 非独占内存'}
        fresh=snap.get('sampled_at') is not None and self.last_time!=snap.get('sampled_at')
        for key,card in self.cards.items():
            if fresh:self.history[key].append(series[key])
            card.update_value(values[key],details[key],self.history[key])
        self.last_time=snap.get('sampled_at')
        self._runtime_labels(runtime)
        gpu=snap.get('gpu',{})
        self.rows(self.gpus,[(d['id'],(d['name'],percent(d.get('utilization_pct')),
            memory(d.get('dedicated_used_bytes'))+' / '+memory(d.get('dedicated_total_bytes')),
            memory(d.get('shared_used_bytes')),memory(d.get('app_dedicated_bytes')))) for d in gpu.get('devices',[])])
        self.gpu_note.configure(text=gpu.get('note') or '整卡利用率 / 显存，不等于模型独占。共享内存 ≠ 专用显存；没有数据时显示不可用。')
        self.rows(self.processes,[(v['pid'],(v['pid'],v['name'],percent(v['cpu_pct']),memory(v['rss_bytes']),v['threads'],v['status'])) for v in snap.get('processes',[])])
        self.rows(self.tasks,[(r['id'],(r['id'][:12],{'agent':'Agent','chat':'对话','image':'生图'}.get(r['mode'],r['mode']),STATES.get(r['status'],r['status']),
            r.get('tool') or STATES.get(r['stage'],r['stage']),(r.get('device') or {}).get('name','尚未记录'))) for r in reversed(runtime.get('tasks',[]))])
        job=runtime.get('model_job',{})
        self.diag.configure(text='模型安装：'+STATES.get(job.get('status','idle'),job.get('status','idle'))+'   ·   原始任务审计保存在本机 state/runs；监控不展示正文或工具参数。')
        self._counts()
        age=snap.get('age_seconds')
        if snap.get('status')!='ok':
            footer='采样不可用 / 初始化中  ·  '+str(snap.get('error','等待有效样本'))
        else:
            footer=f"数据年龄 {age:.1f} 秒" if age is not None else '已采样'
            footer+='    ·    CPU / RAM ≈ 1 秒    GPU ≈ 3 秒    ·    本机采集，不上传'
            if application.get('partial'):footer+='    ·    部分进程不可访问'
        self.footer.configure(text=footer,fg=C['amber'] if snap.get('status')!='ok' or (age or 0)>10 else C['dim'])

    def refresh(self):
        if not self.service_alive():
            self.root.destroy();return
        if self.monitor.focus.is_set():
            self.monitor.focus.clear();self.root.deiconify();self.root.lift()
        self.paint(self.monitor.snapshot())
        self._after=self.root.after(700,self.refresh)

    def _destroyed(self,event):
        if event.widget is self.root and self._after:
            try:self.root.after_cancel(self._after)
            except tk.TclError:pass
            self._after=None
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
            # This also runs INSIDE the frozen installation, not only source CI.
            baseline=None
            for index, button in enumerate(dashboard.tab_buttons):
                button.event_generate('<Button-1>',x=8,y=8)
                dashboard.root.update()
                positions=[(b.winfo_rooty(),b.winfo_height()) for b in dashboard.tab_buttons]
                assert len(set(positions))==1, positions
                assert dashboard.active_tab==index
                assert [b.selected for b in dashboard.tab_buttons]==[i==index for i in range(3)]
                assert baseline is None or positions==baseline
                baseline=positions
            dashboard.select_tab(0)
            dashboard.root.update()
            dashboard.toggle_pause(); assert app.paused
            dashboard.toggle_pause(); assert not app.paused
            dashboard.root.iconify(); dashboard.root.update()
            dashboard.root.deiconify(); dashboard.root.update()
            report.update(ok=True, checks=['Tk window visible', 'OS CPU/RAM sampled', 'own PID measured',
                'pause/resume controller', 'minimize/restore', 'equal tab height/baseline', 'all three panels switch without movement'], snapshot=sample, tk_version=str(tk.TkVersion))
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
