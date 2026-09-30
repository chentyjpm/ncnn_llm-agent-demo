"""Native dashboard presentation primitives. No sampling, model or network code.

Geometry uses one density factor; fonts are measured in pixels to avoid double
scaling. Unknown samples split sparklines and never turn into zero readings.
"""
from __future__ import annotations
import math
import tkinter as tk
from tkinter import font as tkfont

COLORS = {
    'bg': '#0c121d', 'panel': '#131e2d', 'raised': '#1b293b',
    'line': '#28374c', 'grid': '#203047', 'text': '#edf3fc',
    'muted': '#a0b0c6', 'dim': '#7e91ac', 'accent': '#5de0bd',
    'cyan': '#68caff', 'purple': '#b7a4ff', 'blue': '#85adff',
    'amber': '#f3c579', 'red': '#f79b9b', 'selected': '#223d4b',
}


def metric(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def memory(value):
    value = metric(value)
    if value is None:
        return '不可用'
    return f'{value / 1024 ** 3:.2f} GiB' if value >= 1024 ** 3 else f'{value / 1024 ** 2:.1f} MiB'


def percent(value):
    value = metric(value)
    return '不可用' if value is None else f'{value:.1f}%'


def ellipsis(text, measure, width):
    """Elide display text only; underlying snapshots/diagnostics remain intact."""
    text = str(text)
    if measure(text) <= width:
        return text
    if width < measure('…'):
        return ''
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if measure(text[:mid] + '…') <= width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + '…'


def chart_segments(values, width, height, capacity=None):
    points = [metric(v) for v in list(values)[-90:]]
    ceiling = metric(capacity) or max([v for v in points if v is not None] + [1]) * 1.15
    segments, current = [], []
    for i, value in enumerate(points):
        if value is None:
            if current:
                segments.append(current)
                current = []
            continue
        # Keep fixed sample spacing and the newest sample at the right edge.
        x = width * (90 - len(points) + i) / 89
        current.append((x, height * (1 - min(1, value / ceiling))))
    if current:
        segments.append(current)
    return segments


class Theme:
    def __init__(self, root):
        self.colors = COLORS
        self.scale = max(.75, min(3., float(root.tk.call('tk', 'scaling')) / (96 / 72)))
        families = set(tkfont.families(root))
        self.family = next((f for f in ('Microsoft YaHei UI', 'PingFang SC', 'Noto Sans CJK SC', 'Segoe UI', 'DejaVu Sans') if f in families), 'TkDefaultFont')
        self.numeric = next((f for f in ('Segoe UI Variable Display', 'Segoe UI', 'SF Pro Display', 'DejaVu Sans') if f in families), self.family)
        self.fonts = {}
        self.root = root

    def p(self, value):
        return max(1, round(value * self.scale))

    def font(self, size=12, bold=False, numeric=False):
        key = size, bold, numeric
        if key not in self.fonts:
            self.fonts[key] = tkfont.Font(self.root, family=self.numeric if numeric else self.family,
                size=-self.p(size), weight='bold' if bold else 'normal')
        return self.fonts[key]


def rounded(canvas, box, radius, **kw):
    x, y, r, b = box
    radius = min(radius, (r - x) / 2, (b - y) / 2)
    pts = [x+radius,y, r-radius,y, r,y, r,y+radius, r,b-radius, r,b,
           r-radius,b, x+radius,b, x,b, x,b-radius, x,y+radius, x,y]
    return canvas.create_polygon(pts, smooth=True, splinesteps=18, **kw)


def draw_icon(canvas, name, x, y, size, color):
    """Small vector icons; no emoji/font downloads or platform glyph dependency."""
    s = size / 20
    def line(*coords):
        return canvas.create_line(*[x+c*s if i % 2 == 0 else y+c*s for i,c in enumerate(coords)], fill=color, width=max(1.3, 1.5*s), capstyle='round', joinstyle='round')
    if name == 'launch':
        line(5, 4, 3, 4, 3, 17, 16, 17, 16, 14); line(9, 3, 17, 3, 17, 11); line(17,3,8,12)
    elif name == 'pause':
        line(7,4,7,16); line(13,4,13,16)
    elif name == 'play':
        line(6,3,16,10,6,17,6,3)
    elif name == 'stop':
        line(5,5,15,5,15,15,5,15,5,5)
    elif name == 'export':
        line(10,2,10,12);line(6,8,10,12,14,8);line(3,13,3,17,17,17,17,13)
    elif name == 'minimize':
        line(4,14,16,14)
    elif name == 'power':
        canvas.create_arc(x+3*s,y+3*s,x+17*s,y+17*s,start=135,extent=270,style='arc',outline=color,width=max(1.3,1.5*s));line(10,1,10,9)
    elif name == 'chip':
        line(5,5,15,5,15,15,5,15,5,5)
        for pos in (7,13):
            line(pos,2,pos,5);line(pos,15,pos,18);line(2,pos,5,pos);line(15,pos,18,pos)
    elif name == 'pulse':
        line(1,11,5,11,8,4,12,16,15,9,19,9)
    elif name == 'list':
        for pos in (5,10,15):
            line(3,pos,4,pos);line(8,pos,17,pos)


class Button(tk.Canvas):
    """Constant-height keyboard-operable button; no native raised-tab geometry."""
    def __init__(self, parent, theme, text, command, *, icon=None, kind='normal', width=None):
        self.t, self.text, self.command = theme, text, command
        self.kind, self.icon, self.enabled, self.hover, self.selected = kind, icon, True, False, False
        self.height = theme.p(38)
        width = width or theme.font(12).measure(text) + theme.p(32 + (22 if icon else 0))
        super().__init__(parent, width=width, height=self.height, bg=parent.cget('bg'),
                         bd=0, highlightthickness=0, takefocus=True, cursor='hand2')
        self.bind('<Configure>', lambda _: self.draw())
        self.bind('<Enter>', lambda _: self._hover(True))
        self.bind('<Leave>', lambda _: self._hover(False))
        self.bind('<FocusIn>', lambda _: self.draw())
        self.bind('<FocusOut>', lambda _: self.draw())
        self.bind('<Button-1>', self._click)
        self.bind('<Return>', self._key)
        self.bind('<space>', self._key)

    def _hover(self, on):
        self.hover = on
        self.draw()

    def _click(self, _):
        if self.enabled:
            self.focus_set()
            self.invoke()

    def _key(self, _):
        self.invoke()
        return 'break'

    def invoke(self):
        if self.enabled:
            return self.command()

    def set(self, *, text=None, enabled=None, selected=None, icon=None):
        if text is not None:
            self.text = text
        if enabled is not None:
            self.enabled = bool(enabled)
            self.configure(takefocus=self.enabled, cursor='hand2' if self.enabled else 'arrow')
        if selected is not None:
            self.selected = bool(selected)
        if icon is not None:
            self.icon = icon
        self.draw()

    def draw(self):
        self.delete('all')
        t, c = self.t, self.t.colors
        w, h = self.winfo_width(), self.height
        fill, ink, border = c['raised'], c['text'], c['line']
        if self.kind == 'primary':
            fill, ink, border = c['accent'], c['bg'], c['accent']
        elif self.kind == 'danger':
            fill, ink = c['panel'], c['red']
        elif self.kind == 'tab':
            fill, ink, border = (c['selected'], c['accent'], c['selected']) if self.selected else (c['panel'], c['muted'], c['panel'])
        if self.hover and self.enabled:
            border = c['accent'] if self.kind in ('primary', 'tab') else c['muted']
        if not self.enabled:
            fill, ink, border = c['panel'], c['dim'], c['line']
        if self.focus_get() is self:
            border = c['accent']
        rounded(self, (1,1,w-1,h-1), t.p(7), fill=fill, outline=border, width=1)
        x = t.p(15)
        if self.icon:
            draw_icon(self, self.icon, x, (h-t.p(16))/2, t.p(16), ink)
            x += t.p(25)
        self.create_text(x, h/2, anchor='w', text=ellipsis(self.text,t.font(12).measure,max(0,w-x-t.p(12))),
                         font=t.font(12, self.kind == 'primary'), fill=ink)
        if self.kind == 'tab' and self.selected:
            self.create_line(t.p(16),h-2,w-t.p(16),h-2,fill=c['accent'],width=t.p(2))


class MetricCard(tk.Canvas):
    def __init__(self, parent, theme, title, caption, color, capacity=None):
        self.t, self.title, self.caption, self.color = theme, title, caption, color
        self.capacity = capacity
        self.value, self.detail, self.points = '—', '等待有效采样', []
        super().__init__(parent, bg=COLORS['bg'], width=1, height=theme.p(184), highlightthickness=0, bd=0)
        self.bind('<Configure>', lambda _: self.draw())

    def update_value(self, value, detail, points):
        self.value, self.detail, self.points = value, detail, list(points)
        self.draw()

    def draw(self):
        self.delete('all')
        t, c = self.t, COLORS
        w, h = self.winfo_width(), self.winfo_height()
        if w < 30:
            return
        rounded(self, (1,1,w-1,h-1),t.p(12),fill=c['panel'],outline=c['line'],width=1)
        pad = t.p(20)
        self.create_line(pad,t.p(21),pad+t.p(16),t.p(21),fill=self.color,width=t.p(3))
        self.create_text(pad+t.p(25),t.p(21),anchor='w',text=self.title,font=t.font(12),fill=c['muted'])
        value_font = t.font(29,True,True)
        if value_font.measure(self.value)>w-2*pad:
            value_font=t.font(23,True,True)
        self.create_text(pad,t.p(58),anchor='w',text=ellipsis(self.value,value_font.measure,w-2*pad),font=value_font,fill=c['text'])
        self.create_text(pad,t.p(88),anchor='w',text=ellipsis(self.detail,t.font(10).measure,w-2*pad),font=t.font(10),fill=c['muted'])
        top, ch, cw = t.p(111),t.p(34),w-2*pad
        for y in (top,top+ch):
            self.create_line(pad,y,w-pad,y,fill=c['grid'])
        segments=chart_segments(self.points,cw,ch,self.capacity)
        for part in segments:
            coords=[n for x,y in part for n in (pad+x,top+y)]
            if len(part)>1:
                self.create_line(*coords,fill=self.color,width=t.p(2),joinstyle='round')
        if self.points and metric(self.points[-1]) is not None and segments:
            x,y=segments[-1][-1];r=t.p(2)
            self.create_oval(pad+x-r,top+y-r,pad+x+r,top+y+r,fill=self.color,outline='')
        self.create_text(pad,h-t.p(19),anchor='w',text=self.caption,font=t.font(9),fill=c['dim'])
        count=sum(metric(x) is not None for x in self.points)
        self.create_text(w-pad,h-t.p(19),anchor='e',text=f'{count} 个样本',font=t.font(9),fill=c['dim'])
