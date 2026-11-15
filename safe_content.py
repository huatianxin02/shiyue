"""Text extraction and inert previews. Never fetch resources referenced by a file."""
from html import escape
from html.parser import HTMLParser
import re
from xml.etree import ElementTree as ET

class ReadableHTML(HTMLParser):
    BLOCKED={'script','style','template','noscript','iframe','object','embed','svg','math','head'}
    ALLOWED={'article','section','main','header','footer','div','p','br','hr','h1','h2','h3','h4','h5','h6','ul','ol','li','table','thead','tbody','tfoot','tr','td','th','caption','blockquote','pre','code','strong','b','em','i','u','s','small','sup','sub','span','dl','dt','dd','figure','figcaption'}
    BLOCKS={'article','section','main','header','footer','div','p','br','hr','h1','h2','h3','h4','h5','h6','li','tr','td','th','blockquote','pre','dt','dd','figcaption'}
    VOID={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
    def __init__(self):
        super().__init__(convert_charrefs=True); self.stack=[]; self.parts=[]; self.rendered=[]
    @property
    def blocked(self): return any(hidden for _,hidden in self.stack)
    def handle_starttag(self,tag,attrs):
        values=dict(attrs); style=(values.get('style') or '').lower().replace(' ','')
        hidden=tag in self.BLOCKED or 'hidden' in values or values.get('aria-hidden')=='true' or 'display:none' in style or 'visibility:hidden' in style
        if tag not in self.VOID: self.stack.append((tag,hidden))
        if self.blocked or hidden:return
        if tag in self.BLOCKS:self.parts.append('\n')
        if tag in self.ALLOWED:
            safe=''
            if tag in {'td','th'}:
                for key in ('colspan','rowspan'):
                    value=values.get(key,'') or ''
                    if value.isdigit() and 0<int(value)<100:safe+=f' {key}="{value}"'
            self.rendered.append('<'+tag+safe+'>')
        elif tag=='img' and values.get('alt'):
            alt=values['alt'];self.parts.append(' '+alt+' ');self.rendered.append('<span>［'+escape(alt)+'］</span>')
    def handle_endtag(self,tag):
        was_blocked=self.blocked
        if tag in self.VOID:return
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:
                del self.stack[i:];break
        if was_blocked:return
        if tag in self.ALLOWED:self.rendered.append('</'+tag+'>')
        if tag in self.BLOCKS:self.parts.append('\n')
    def handle_data(self,data):
        if not self.blocked:self.parts.append(data);self.rendered.append(escape(data))
    def text(self):return re.sub(r'\n[ \t\n]+','\n',''.join(self.parts)).strip()

def decode_text(raw):
    encodings=['utf-8-sig']
    if raw[:2] in (b'\xff\xfe',b'\xfe\xff'):encodings.insert(0,'utf-16')
    charset=re.search(br'charset\s*=\s*["\x27]?([a-zA-Z0-9_-]+)',raw[:4096])
    if charset:encodings.append(charset.group(1).decode('ascii'))
    encodings.append('gb18030')
    for encoding in encodings:
        try:return raw.decode(encoding)
        except (UnicodeError,LookupError):pass
    return raw.decode('utf-8',errors='replace')

def parse_html(text):
    parser=ReadableHTML();parser.feed(text);parser.close();return parser

def html_preview(raw,name):
    p=parse_html(decode_text(raw))
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
      '<title>'+escape(name)+'</title><style>body{font:15px/1.9 -apple-system,sans-serif;color:#314438;max-width:900px;padding:32px;margin:auto;background:#fff}'
      'table{border-collapse:collapse;max-width:100%;display:block;overflow:auto}td,th{border:1px solid #d9e3dc;padding:8px 12px}pre{white-space:pre-wrap;background:#f4f7f4;padding:16px}'
      'blockquote{border-left:3px solid #bad2c5;margin-left:0;padding-left:20px;color:#718477}.notice{font-size:12px;color:#899d8d;border-bottom:1px solid #e4ebe6;padding-bottom:16px;margin-bottom:28px}*{overflow-wrap:anywhere}</style></head><body>'
      '<div class="notice">本地安全阅读 · 脚本、外部资源和交互表单已移除，原文件保持完整。</div>'+''.join(p.rendered)+'</body></html>').encode()

SVG_NS='http://www.w3.org/2000/svg'
SVG_TAGS={'svg','g','path','rect','circle','ellipse','line','polyline','polygon','text','tspan','title','desc','defs','linearGradient','radialGradient','stop','clipPath','mask','marker'}
SVG_ATTRS={'id','viewBox','width','height','x','y','x1','y1','x2','y2','cx','cy','r','rx','ry','d','points','transform','fill','fill-opacity','fill-rule','stroke','stroke-width','stroke-opacity','stroke-linecap','stroke-linejoin','stroke-dasharray','stroke-dashoffset','opacity','font-size','font-family','font-weight','text-anchor','dominant-baseline','dx','dy','offset','stop-color','stop-opacity','gradientUnits','gradientTransform','spreadMethod','fx','fy','clip-path','clipPathUnits','mask','maskUnits','marker-start','marker-mid','marker-end','markerWidth','markerHeight','refX','refY','orient','preserveAspectRatio'}
SVG_STYLES={'fill','stroke','stroke-width','stroke-opacity','fill-opacity','opacity','font-size','font-weight','text-anchor','stroke-linecap','stroke-linejoin'}

def sanitize_svg(raw):
    if len(raw)>8_000_000 or re.search(br'<!\s*(DOCTYPE|ENTITY)',raw,re.I):raise ValueError('SVG 含有不支持的实体或体积过大')
    root=ET.fromstring(raw)
    if root.tag not in ('svg','{'+SVG_NS+'}svg'):raise ValueError('不是 SVG 图像')
    def safe_value(v):
        if len(v)>500_000 or re.search(r'[<>\\]|(?:javascript|data|https?|file)\s*:|expression\s*\(|@',v,re.I):return False
        for match in re.findall(r'url\s*\([^)]*\)',v,re.I):
            if not re.fullmatch(r'url\(#[A-Za-z_][\w.-]*\)',match):return False
        return True
    def clean(el):
        for child in list(el):
            if child.tag.split('}')[-1] not in SVG_TAGS or (child.tag.startswith('{') and not child.tag.startswith('{'+SVG_NS+'}')):el.remove(child)
            else:clean(child)
        tag=el.tag.split('}')[-1];el.tag='{'+SVG_NS+'}'+tag
        attrs={}
        for k,v in el.attrib.items():
            if k in SVG_ATTRS and safe_value(v):attrs[k]=v
            if k=='style':
                for declaration in v.split(';'):
                    prop,sep,value=declaration.partition(':');prop=prop.strip();value=value.strip()
                    if sep and prop in SVG_STYLES and safe_value(value):attrs[prop]=value
        el.attrib.clear();el.attrib.update(attrs)
    clean(root)
    def number(v):
        m=re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(?:px)?\s*',v or '')
        return float(m.group(1)) if m else None
    width=number(root.get('width'));height=number(root.get('height'))
    box=re.split(r'[ ,]+',(root.get('viewBox') or '').strip())
    if len(box)==4:
        try:width=width or float(box[2]);height=height or float(box[3])
        except ValueError:pass
    width=width or 300;height=height or 150
    if not 0<width<=1_000_000 or not 0<height<=1_000_000:raise ValueError('SVG 尺寸无效')
    if not root.get('viewBox'):root.set('viewBox',f'0 0 {width:g} {height:g}')
    ratio=min(1,512/max(width,height));root.set('width',str(round(width*ratio,2)));root.set('height',str(round(height*ratio,2)))
    ET.register_namespace('',SVG_NS)
    text=' '.join(''.join(e.itertext()) for e in root.iter() if e.tag.split('}')[-1] in ('text','title','desc'))
    return ET.tostring(root,encoding='utf-8',xml_declaration=True),{'width':round(width),'height':round(height)},text
