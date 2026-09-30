#!/usr/bin/env python3
"""Real Tk interaction/geometry across simulated UI scales. Uses OS metrics.

No browser, model weights, GPU fixture or synthetic performance screenshots.
Tk scaling exercises font/layout behavior, not physical multi-monitor DPI moves.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from local_agent.monitor_ui import Dashboard
from local_agent.monitor import MonitorService
from local_agent.monitor_http import ManagedWebApp


def geometry(dashboard):
    root=dashboard.root
    root.update_idletasks();root.update();root.update_idletasks()
    tabs=[(b.winfo_rooty(),b.winfo_height()) for b in dashboard.tab_buttons]
    assert len(set(tabs))==1,('tab baselines/heights differ',tabs)
    host=dashboard.page_host
    for b in dashboard.tab_buttons:
        assert b.winfo_rooty()+b.winfo_height()<=host.winfo_rooty(), 'tab overlaps content'
    for card in dashboard.cards.values():
        assert card.winfo_width()>40
        assert card.winfo_x()>=0
        assert card.winfo_x()+card.winfo_width()<=dashboard.card_grid.winfo_width()+1
    return {'tabs':tabs,'page':(host.winfo_width(),host.winfo_height()),
            'columns':dashboard._card_columns,'scrollable':bool(dashboard.scrollbar.winfo_manager()),
            'window':(root.winfo_width(),root.winfo_height())}


def exercise(dashboard,output,stem):
    root=dashboard.root
    errors=[]
    root.report_callback_exception=lambda typ,value,tb:errors.append(f'{typ.__name__}: {value}')
    baseline=geometry(dashboard)
    records=[]
    for index,button in enumerate(dashboard.tab_buttons):
        button.event_generate('<Button-1>',x=8,y=8)
        current=geometry(dashboard)
        assert dashboard.active_tab==index
        assert [b.selected for b in dashboard.tab_buttons]==[i==index for i in range(3)]
        assert current['tabs']==baseline['tabs'] and current['page']==baseline['page'], 'switching panels shifted geometry'
        if index in (0,1):
            try:
                from PIL import ImageGrab
                x,y=root.winfo_rootx(),root.winfo_rooty()
                ImageGrab.grab(bbox=(x,y,x+root.winfo_width(),y+root.winfo_height())).save(output/f'{stem}-{index}.png')
            except OSError as exc:
                records.append({'screenshot_unavailable':str(exc)})
        records.append({'panel':index,'geometry':current})
    root.focus_force();root.update()
    dashboard.tab_buttons[2].focus_set();root.update()
    dashboard.tab_buttons[2].event_generate('<KeyPress-Home>');root.update()
    assert dashboard.active_tab==0,'Home navigation failed'
    dashboard.tab_buttons[0].event_generate('<KeyPress-Right>');root.update()
    assert dashboard.active_tab==1,'arrow navigation failed'
    root.event_generate('<Control-Tab>');root.update()
    assert dashboard.active_tab==2,'Ctrl+Tab failed'
    # Selection survives a real refresh without destroying tree items.
    tree=dashboard.processes
    items=tree.get_children()
    assert str(os.getpid()) in items
    tree.selection_set(str(os.getpid()));tree.focus(str(os.getpid()))
    dashboard.paint(dashboard.monitor.snapshot());root.update()
    assert tree.selection()==(str(os.getpid()),) and tree.focus()==str(os.getpid())
    oldsize=(root.winfo_width(),root.winfo_height())
    root.geometry(f'{min(dashboard.t.p(920),root.winfo_screenwidth()-50)}x{min(dashboard.t.p(660),root.winfo_screenheight()-90)}')
    small=geometry(dashboard)
    assert small['columns']==2,'narrow layout should have two card columns'
    if small['scrollable']:
        dashboard.viewport.yview_moveto(1);root.update()
        footer=dashboard.footer
        assert footer.winfo_rooty()+footer.winfo_height()<=dashboard.viewport.winfo_rooty()+dashboard.viewport.winfo_height()+2,'footer unreachable'
        dashboard.viewport.yview_moveto(0)
    root.geometry(f'{oldsize[0]}x{oldsize[1]}');geometry(dashboard)
    assert not errors,errors
    return {'panels':records,'resized':small,'keyboard':True,'selection_preserved':True,'tk_errors':errors}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    report={'ok':False,'scope':__doc__,'scales':[],'checks':[]}
    try:
        for scale in (1.,1.5,2.):
            with tempfile.TemporaryDirectory(prefix='dashboard-layout-') as tmp:
                home=Path(tmp);root=tk.Tk();root.tk.call('tk','scaling',(96/72)*scale)
                app=ManagedWebApp({'workspace':str(home/'workspace'),'llm':{'model':'unconfigured','device':'auto'},'image':{}},home/'state')
                monitor=MonitorService(app)
                dashboard=None
                try:
                    dashboard=Dashboard(app,monitor,'http://127.0.0.1:8765',lambda:None,lambda:True,home,root)
                    until=time.monotonic()+12
                    while time.monotonic()<until:
                        root.update()
                        sample=monitor.snapshot()
                        if sample.get('system',{}).get('cpu_pct') is not None:break
                        time.sleep(.05)
                    assert sample.get('status')=='ok',sample
                    dashboard.paint(sample)
                    result=exercise(dashboard,args.output,f'layout-{int(scale*100)}')
                    report['scales'].append({'requested_scale':scale,'effective_scale':dashboard.t.scale,
                        'result':result,'snapshot':sample})
                    print('PASS scale',scale,flush=True)
                finally:
                    root.destroy();monitor.close()
        report['checks']=['equal tab geometry','all three panels clickable','selected marker matches page',
            'arrow/Home/Ctrl+Tab navigation','selection/focus persist on refresh','responsive two-column cards',
            'high-DPI overflow can scroll to footer','real CPU/RAM/PID','no Tk callback errors']
        report['ok']=True
    except Exception as exc:
        import traceback
        traceback.print_exc()
        report['error']=f'{type(exc).__name__}: {exc}'
    (args.output/'layout-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 0 if report['ok'] else 1


if __name__=='__main__':raise SystemExit(main())
