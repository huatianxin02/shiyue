"""Bounded, offline document extraction; complex formats run in a disposable process."""
from __future__ import annotations
import json, os, re, subprocess, sys, time, zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'vendor'))
from safe_content import parse_html, decode_text, sanitize_svg

TEXT_EXT = {'.txt','.md','.markdown','.csv','.tsv','.json','.yaml','.yml','.log','.html','.htm','.xml','.py','.js','.css','.sql','.rtf'}
IMAGE_EXT = {'.jpg','.jpeg','.png','.gif','.webp','.heic','.heif','.tif','.tiff','.bmp','.svg'}
VIDEO_EXT = {'.mp4','.mov','.m4v','.avi','.mkv','.webm'}
MAX_TEXT = 2_000_000

EXTRACTION_TIMEOUT = 35
COMPLEX_EXT = {'.pdf','.docx','.pptx','.xlsx','.html','.htm','.svg'}

def kind_for(path):
    ext=Path(path).suffix.lower()
    return 'image' if ext in IMAGE_EXT else 'video' if ext in VIDEO_EXT else 'document'

def office_xml(raw):
    if re.search(br'<!\s*(DOCTYPE|ENTITY)',raw.replace(b'\x00',b''),re.I):raise ValueError('文档 XML 含有不支持的实体')
    return ET.fromstring(raw)

def _extract_inline(path, filename):
    ext = Path(filename).suffix.lower(); chunks = []; warning = ''; total = 0
    def add(text, label, page=None):
        nonlocal total
        text = text.strip().replace('\x00','')
        if not text or total >= MAX_TEXT: return
        text = text[:MAX_TEXT-total]; total += len(text)
        for pos in range(0, len(text), 1800): chunks.append({'text':text[pos:pos+1800], 'label':label, 'page':page, 'offset':pos})
    try:
        if ext in TEXT_EXT:
            with Path(path).open('rb') as f: raw = f.read(MAX_TEXT*4)
            text = decode_text(raw)
            if ext in {'.html','.htm'}: text = parse_html(text).text()
            add(text, '正文')
        elif ext == '.svg':
            with Path(path).open('rb') as f: raw=f.read(8_000_001)
            _,_,text=sanitize_svg(raw);add(text,'图中文字')
        elif ext == '.pdf':
            try: from pypdf import PdfReader
            except ImportError: return [], 'PDF 文本组件未安装；原件已保存，可按文件名搜索'
            reader = PdfReader(str(path))
            if reader.is_encrypted: return [], '加密 PDF；原件已保存，暂未抽取正文'
            for n, page in enumerate(reader.pages):
                if total >= MAX_TEXT: break
                add(page.extract_text() or '', f'第 {n+1} 页', n+1)
            if not chunks: warning = '未发现可提取文字，可能是扫描件；可在资料详情运行本地 OCR'
        elif ext in {'.docx','.pptx','.xlsx'}:
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                if len(names)>20000 or len(names)!=len(set(names)):raise ValueError('文档条目过多或重复')
                targets = ['word/document.xml'] if ext == '.docx' else sorted((n for n in names if re.fullmatch(r'ppt/slides/slide\d+\.xml',n)), key=lambda n:int(re.search(r'(\d+)\.xml',n).group(1))) if ext == '.pptx' else [n for n in names if n.startswith('xl/worksheets/sheet') and n.endswith('.xml')]
                strings = []
                if ext == '.xlsx' and 'xl/sharedStrings.xml' in names:
                    if z.getinfo('xl/sharedStrings.xml').file_size > 32_000_000: raise ValueError('共享字符串过大，已跳过文本提取')
                    root = office_xml(z.read('xl/sharedStrings.xml'))
                    strings = [''.join(n.itertext()) for n in root]
                for i,n in enumerate(targets):
                    if total >= MAX_TEXT: break
                    if z.getinfo(n).file_size > 32_000_000: raise ValueError('文档 XML 过大，已跳过文本提取')
                    root = office_xml(z.read(n))
                    if ext == '.xlsx':
                        rows=[]
                        for row in root.iter():
                            if row.tag.split('}')[-1] != 'row': continue
                            cells=[]
                            for cell in row:
                                value=''.join(e.text or '' for e in cell.iter() if e.tag.split('}')[-1] in ('v','t'))
                                if cell.attrib.get('t') == 's' and value.isdigit(): value = strings[int(value)]
                                cells.append(value)
                            rows.append(' | '.join(cells))
                        text='\n'.join(rows)
                    else:
                        text='\n'.join(''.join(e.text or '' for e in p.iter() if e.tag.split('}')[-1]=='t') for p in root.iter() if p.tag.split('}')[-1]=='p')
                    add(text, '正文' if ext=='.docx' else f'第 {i+1} 张幻灯片' if ext=='.pptx' else f'工作表 {i+1}', i+1 if ext=='.pptx' else None)
        else: warning = '已登记原件；此格式暂不提取正文' if kind_for(filename)=='document' else ''
    except Exception as e: warning = f'原件已保存，正文提取失败：{str(e)[:180]}'
    if total >= MAX_TEXT: warning = '正文索引已达到 200 万字符上限，原件完整保留'
    return chunks, warning


def extract(path, filename, cancel=None):
    if Path(filename).suffix.lower() not in COMPLEX_EXT:
        return _extract_inline(path, filename)
    request=json.dumps({'path':str(Path(path).resolve()),'filename':filename}).encode()
    process=subprocess.Popen([sys.executable,'-I','-B',str(Path(__file__).resolve()),'--worker'],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    deadline=time.monotonic()+EXTRACTION_TIMEOUT
    first=True
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise InterruptedError('任务将在下次启动后继续')
            remaining=deadline-time.monotonic()
            if remaining<=0:
                return [], '文档解析超过时间限制；原件已保留，可按文件名搜索'
            try:
                output,_=process.communicate(input=request if first else None,timeout=min(.2,remaining))
                break
            except subprocess.TimeoutExpired:
                first=False
        if process.returncode or len(output)>16_000_000:
            return [], '文档解析达到资源限制或异常退出；原件已保留'
        chunks,warning=json.loads(output)
        return chunks,warning
    except InterruptedError:
        raise
    except (OSError,ValueError) as error:
        return [], '正文提取失败；原件已保留：'+str(error)[:160]
    finally:
        if process.poll() is None:process.kill()
        process.communicate()


def _worker():
    # CPU is bounded on macOS and Linux. Address-space limits are best effort on
    # platforms that do not implement RLIMIT_AS; the parent always enforces time.
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU,(30,31))
        resource.setrlimit(resource.RLIMIT_AS,(1024**3,1024**3))
    except (ImportError,OSError,ValueError):
        pass
    request=json.loads(sys.stdin.buffer.read(16384))
    result=_extract_inline(Path(request['path']),request['filename'])
    sys.stdout.write(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':
    if sys.argv[1:]!=['--worker']:raise SystemExit('Only --worker is supported')
    _worker()
