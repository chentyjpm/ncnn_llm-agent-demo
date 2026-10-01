from pathlib import Path
from html import escape
import sys
SVG_ONLY = "--svg-only" in sys.argv
if not SVG_ONLY:
 import cairosvg

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/images'
OUT.mkdir(parents=True,exist_ok=True)
W,H=1600,1000
INK='#22304d';MUTED='#64728b';PINK='#d368a4';BLUE='#488bca';TEAL='#238f91';PURPLE='#8068bd'
class Drawing:
 def __init__(self,number,title,subtitle):
  self.parts=[f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-labelledby="title desc">
<title id="title">{escape(title)}</title><desc id="desc">{escape(subtitle)}。工程示意图，不是程序截图或性能结果。</desc>
<defs>
<linearGradient id="bg" x2="1" y2="1"><stop stop-color="#f9f2fc"/><stop offset=".6" stop-color="#f5faff"/><stop offset="1" stop-color="#fff3f6"/></linearGradient>
<linearGradient id="ribbon"><stop stop-color="#745ba8"/><stop offset="1" stop-color="#508eb5"/></linearGradient>
<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" orient="auto"><path d="M1 1 L9 5 L1 9" fill="none" stroke="{TEAL}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></marker>
<marker id="arrow-back" viewBox="0 0 10 10" refX="1" refY="5" markerWidth="9" markerHeight="9" orient="auto"><path d="M9 1 L1 5 L9 9" fill="none" stroke="{TEAL}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></marker>
<filter id="shadow" x="-10%" y="-10%" width="120%" height="140%"><feDropShadow dx="0" dy="5" stdDeviation="7" flood-color="#63779a" flood-opacity=".09"/></filter>
<style>text{{font-family:'Noto Sans CJK SC','Microsoft YaHei','PingFang SC',sans-serif;fill:{INK}}} .muted{{fill:{MUTED}}} .small{{font-size:18px}} .label{{font-size:23px;font-weight:700}} .body{{font-size:20px}}</style>
</defs><rect width="1600" height="1000" rx="30" fill="url(#bg)"/>
<circle cx="1540" cy="40" r="180" fill="#f7d6e8" opacity=".4"/>
<circle cx="0" cy="920" r="160" fill="#dbeaf9" opacity=".4"/>
<rect x="56" y="44" width="216" height="31" rx="15" fill="#eee5f7"/>
<text x="74" y="66" font-size="15" font-weight="700" fill="{PURPLE}">LOCAL AGENT / GUIDE {number}</text>
<text x="56" y="132" font-size="45" font-weight="800">{escape(title)}</text>
<text x="58" y="170" font-size="21" class="muted">{escape(subtitle)}</text>
''']
  self.mascot(1475,89)
 def text(self,x,y,s,size=21,color=INK,weight=400,anchor='start'):
  self.parts.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}" style="fill:{color}">{escape(s)}</text>')
 def rect(self,x,y,w,h,fill='#ffffff',stroke='#dce3ed',r=20,shadow=False):
  self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="{stroke}" stroke-width="1.5"'+(' filter="url(#shadow)"' if shadow else '')+'/>')
 def card(self,x,y,w,h,title,lines=(),accent=BLUE,tag=None):
  self.rect(x,y,w,h,shadow=True)
  self.parts.append(f'<rect x="{x}" y="{y+22}" width="5" height="{min(55,h-44)}" rx="2" fill="{accent}"/>')
  self.text(x+23,y+39,title,24,INK,700)
  for i,line in enumerate(lines):self.text(x+23,y+76+29*i,line,20,MUTED)
  if tag:self.text(x+w-20,y+h-19,tag,15,accent,600,'end')
 def arrow(self,points,label=None,lx=None,ly=None,dashed=False,both=False):
  self.parts.append(f'<path d="{points}" fill="none" stroke="{TEAL}" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round" marker-end="url(#arrow)"'+(' marker-start="url(#arrow-back)"' if both else '')+(' stroke-dasharray="6 8"' if dashed else '')+'/>')
  if label:self.text(lx,ly,label,17,TEAL,500,'middle')
 def pill(self,x,y,w,label,fill='#ecf5f7',color=TEAL):
  self.rect(x,y,w,32,fill,fill,16);self.text(x+w/2,y+23,label,17,color,600,'middle')
 def mascot(self,x,y):
  # Original compact vector guide mascot; no embedded font or external image.
  self.parts.append(f'''<g transform="translate({x},{y})">
<ellipse cx="0" cy="59" rx="43" ry="8" fill="#d5d1e4" opacity=".4"/>
<path d="M-37 16 Q-50-36-16-46 Q8-61 33-38 Q48-24 38 19 L46 36 L-45 36Z" fill="#c99bbb" stroke="#af829f" stroke-width="2"/>
<ellipse cy="-8" rx="33" ry="35" fill="#ffe8dc"/>
<path d="M-32-22 Q-25-47 5-41 Q30-39 33-8 Q14-9 0-27 Q-12-4-34-1Z" fill="#e2b8d2" stroke="#bf93b0" stroke-width="1.5"/>
<ellipse cx="-12" cy="1" rx="4" ry="7" fill="#746899"/><ellipse cx="13" cy="1" rx="4" ry="7" fill="#746899"/>
<circle cx="-13" cy="-2" r="1.4" fill="white"/><circle cx="12" cy="-2" r="1.4" fill="white"/>
<ellipse cx="-22" cy="12" rx="6" ry="3" fill="#e99cac" opacity=".6"/><ellipse cx="23" cy="12" rx="6" ry="3" fill="#e99cac" opacity=".6"/>
<path d="M-5 14 Q1 20 6 14" fill="none" stroke="#b36982" stroke-width="2" stroke-linecap="round"/>
<path d="M-20 28 Q0 39 21 28 L31 53 L-32 53Z" fill="#bbb6e1" stroke="#948bbc" stroke-width="2"/>
<rect x="-34" y="36" width="70" height="27" rx="5" fill="#edf6fb" stroke="#98b0c7" stroke-width="2"/>
<text x="1" y="55" text-anchor="middle" font-size="14" font-weight="700" style="fill:#62799f">ncnn</text>
<g transform="translate(33,-36)"><ellipse rx="7" ry="14" fill="#f3aaca" transform="rotate(-35)"/><ellipse rx="7" ry="14" fill="#f3aaca" transform="rotate(35)"/><circle r="4" fill="#ffe2a3"/></g></g>''')
 def footer(self,note):
  self.parts.append('<path d="M56 935 H1544" stroke="#dce3ed"/>')
  self.text(58,964,note,16,MUTED)
  self.text(1540,964,'代码基线 cc0b067 · 架构示意，非运行截图',15,MUTED,400,'end')
 def save(self,name):
  svg=''.join(self.parts)+'</svg>'
  (OUT/f'{name}.svg').write_text(svg,encoding='utf-8')
  if not SVG_ONLY:
   cairosvg.svg2png(bytestring=svg.encode(),write_to=str(OUT/f'{name}.png'))
  print(name, 'SVG',len(svg.encode()),'bytes')

# 01 — topology: host tools are not inference layers.
d=Drawing('01','模块分工：谁负责什么？','界面交互、任务编排、工具执行与模型计算，各自负责一层。')
d.card(60,218,790,115,'H5 工作台',['聊天 · 文档 · 模型中心 · 执行确认'],PINK,'webui / HTTP')
d.card(1030,218,510,115,'原生后台管理',['CPU · 内存 · GPU · 任务状态'],PURPLE,'monitor_ui / monitor')
d.rect(60,395,790,192,fill='#f3f6fe',stroke='#cad6ee',shadow=True)
d.text(85,436,'Python 服务与编排',27,INK,700)
for x,title,sub in [(85,'WebApp','会话与 Run'),(335,'Agent','模型动作循环'),(585,'Registry','工具校验与调用')]:
 d.rect(x,457,240,96,fill='#ffffff',stroke='#d6deed',r=12);d.text(x+17,493,title,23,BLUE,700);d.text(x+17,527,sub,18,MUTED)
d.arrow('M455 336 V390','本机 HTTP · 任务事件',580,370,both=True)
d.arrow('M1285 336 V364 H826 V390','采样与管理',1050,357,dashed=True,both=True)
d.card(1030,402,510,179,'模型管理 ModelHub',['下载源 → 校验 → 按格式准备','文字与生图模型分别启用'],PURPLE,'model_catalog / model_sources / model_hub')
d.arrow('M1025 493 H855','配置与状态',940,478,both=True)
d.card(60,660,440,179,'文件与文档工具',['Workspace · DOCX / XLSX / PPTX','授权后可用 Python / 固定命令 / MCP'],PINK,'本地工具，不经过 ncnn')
d.card(555,660,450,179,'ncnn_llm：文字推理',['Backend → ncnn_agent_bridge','JSON-RPC / stdio · 独立进程'],BLUE,'backends.py / rpc.py')
d.card(1060,660,480,179,'Qwen Image：生图',['images.generate → ImageRunner','命令参数 / 输出文件 · 独立进程'],PURPLE,'images.py')
d.arrow('M205 590 V623 H280 V654','工具调用',343,621)
d.arrow('M455 590 V618 H780 V654','messages / 回答',700,610,both=True)
d.arrow('M855 555 H900 V632 H1300 V654','调用图像工具',1170,622)
d.rect(555,874,985,42,fill='#e7f2f5',stroke='#c7e1e5',r=13)
d.text(1047,903,'两个推理引擎分别采用 ncnn · Vulkan / CPU',22,TEAL,700,'middle')
d.arrow('M780 842 V870');d.arrow('M1300 842 V870')
d.text(280,896,'当前会话工作区 / 输出文档',20,PINK,600,'middle')
d.footer('实线：调用或数据交换；虚线：观察与管理。ncnn_llm 与 Qwen Image 不串联。')
d.save('guide-modules')

# 02 — explicit mode split; no automatic side effects from keywords.
d=Drawing('02','工作流程：同一句请求，不同执行路径','对话只回答，Agent 调用工具，独立生图不需要文字模型。')
d.rect(60,212,1480,78,fill='#ffffff',shadow=True)
d.pill(85,234,132,'用户请求',fill='#f7e8f0',color=PINK)
d.text(248,261,'选择「生图」或使用顶层 /image 指令；普通对话不靠关键词自动生图。',23,INK,600)
# Chat lane
d.rect(60,320,1480,137,fill='#eff6fc',stroke='#d7e7f4')
d.pill(84,342,140,'01  对话',fill='#dbeafb',color=BLUE)
d.text(84,409,'不提供工具',19,MUTED)
d.card(290,340,330,92,'读取消息与历史',[],BLUE)
d.card(715,340,330,92,'文字模型推理',[],BLUE)
d.card(1140,340,350,92,'显示文字回答',[],BLUE)
d.arrow('M237 383 H284');d.arrow('M625 383 H709');d.arrow('M1050 383 H1134')
# Agent lane
d.rect(60,492,1480,223,fill='#f2effb',stroke='#dfd9ee')
d.pill(84,513,140,'02  Agent',fill='#e7dff6',color=PURPLE)
d.text(84,585,'模型选择动作',18,MUTED)
d.card(290,518,265,110,'模型提出动作',['完整 JSON / final'],PURPLE)
d.card(610,518,265,110,'校验与按需确认',['有副作用先确认'],PURPLE)
d.card(930,518,265,110,'工具真实执行',['文档 / MCP / 生图等'],PURPLE)
d.card(1250,518,240,110,'记录实际结果',['文件、图片或错误'],PURPLE)
d.arrow('M237 568 H284');d.arrow('M560 568 H604');d.arrow('M880 568 H924');d.arrow('M1200 568 H1244')
d.arrow('M1370 633 V679 H422 V634','工具结果回传模型；final 直接返回回答，不再调用工具',925,671)
# Image lane
d.rect(60,750,1480,157,fill='#fff1f6',stroke='#efd9e6')
d.pill(84,771,165,'03  生图 /image',fill='#f5deeb',color=PINK)
d.text(84,854,'无需文字模型',19,MUTED)
d.card(290,774,265,108,'准备画面参数',['提示词 / 尺寸 / 步数'],PINK)
d.card(610,774,265,108,'用户逐次确认',['允许后才开始执行'],PINK)
d.card(930,774,265,108,'Qwen Image',['实际生成图片文件'],PINK)
d.card(1250,774,240,108,'校验与展示',['完整解码 / 预览下载'],PINK)
d.arrow('M254 825 H284');d.arrow('M560 825 H604');d.arrow('M880 825 H924');d.arrow('M1200 825 H1244')
d.footer('Agent 使用 images.generate；拒绝或失败不冒充成功，模型文字不能伪造图片卡片。')
d.save('guide-workflows')

# 03 — installation vs device probing, not GPU guarantees.
d=Drawing('03','模型就绪：先安装，再选择计算设备','下载来源与推理设备是两件事；文字引擎和生图引擎各自预检。')
steps=[('01  选择来源',['ModelScope / Hugging Face','SDU 原生镜像（按模型）']),('02  清单与确认',['查询版本、大小、所需空间','用户确认后开始下载']),('03  下载与校验',['真实字节进度 / 文件校验','失败不会启用半成品']),('04  按格式准备',['受支持 Qwen2.5：转换','原生 ncnn 包：校验部署']),('05  分别启用',['当前文字模型 / 生图模型','READY 发布后才可使用'])]
for i,(title,lines) in enumerate(steps):
 x=60+i*300
 d.card(x,234,280,192,title,lines,[BLUE,PINK,TEAL,PURPLE,BLUE][i])
 if i<4:d.arrow(f'M{x+282} 330 H{x+296}')
d.text(60,465,'生图模型目前使用已核实的 Hugging Face ncnn 来源；不支持的来源不自动替换。',20,MUTED)
d.parts.append('<path d="M60 499 H1540" stroke="#dce3ed" stroke-dasharray="5 8"/>')
d.pill(60,523,215,'运行时：device = auto',fill='#e9e5f6',color=PURPLE)
d.card(60,597,385,182,'按需启动推理引擎',['文字：ncnn_llm / bridge','生图：Qwen Image'],BLUE,'分别使用匹配的设备探测器')
# Diamond with centered short, high contrast labels.
d.parts.append('<path d="M725 561 L925 697 L725 833 L525 697 Z" fill="#ffffff" stroke="#c5c9e8" stroke-width="2" filter="url(#shadow)"/>')
d.text(725,663,'硬件 Vulkan',26,PURPLE,700,'middle');d.text(725,704,'是否可用？',29,INK,700,'middle');d.text(725,743,'设备枚举 + 小型计算预检',18,MUTED,400,'middle')
d.arrow('M450 695 H518')
d.card(1120,568,420,128,'优先 Vulkan',['采用预检通过的硬件设备'],TEAL)
d.card(1120,747,420,128,'无可用硬件时 CPU',['记录回退原因，不伪装 GPU'],PINK)
d.arrow('M828 625 H992 V625 H1114','是',1007,608)
d.arrow('M828 771 H995 V811 H1114','否',1010,792)
d.text(60,882,'预检通过 ≠ 模型每一层都在 GPU 上；INT8 等路径可能包含 CPU 算子。',20,MUTED)
d.footer('权重损坏、内存不足、超时等运行错误，不应被无条件 CPU 重试掩盖。')
d.save('guide-model-lifecycle')
