"""Offline media derivatives via the macOS frameworks bundled helper."""
import json, math, os, re, subprocess, tempfile
from datetime import datetime
from pathlib import Path
from safe_content import sanitize_svg

MEDIA_VERSION=3
HELPER=Path(__file__).resolve().parent/'bin'/'MediaProbe'
MEDIA_COLUMNS=('width','height','duration','captured_at','captured_ts','captured_source','camera','make','gps_lat','gps_lon','codec','thumb_path','media_error','version')

def normalize_date(value):
    if not isinstance(value,str):return None
    value=value.strip()
    value=re.sub(r'^(\d{4}):(\d{2}):(\d{2})[ T]',r'\1-\2-\3T',value)
    try: parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:return None
    if not 1800<=parsed.year<=2200:return None
    return parsed.isoformat(timespec='seconds')

def capture_epoch(value):
    """Absolute sort key for the timeline.

    EXIF often carries a local wall clock with an offset; QuickTime container dates
    arrive in UTC. Sorting the raw strings would interleave the two, so every
    captured_at is converted to epoch seconds here. A timestamp without an offset
    is read as this machine's local time, which is what the photographer saw.
    """
    if not isinstance(value,str):return None
    try: parsed=datetime.fromisoformat(value.strip().replace('Z','+00:00'))
    except ValueError:return None
    if not 1800<=parsed.year<=2200:return None
    if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    try: return round(parsed.timestamp(),3)
    except (OverflowError,OSError,ValueError):return None


def build_media(path, filename, sha, root):
    result={key:None for key in MEDIA_COLUMNS};result.update(media_error='',version=MEDIA_VERSION)
    ext=Path(filename).suffix.lower();kind='video' if ext in {'.mp4','.mov','.m4v','.avi','.mkv','.webm'} else 'image'
    folder=Path(root)/'thumbs'/sha[:2];folder.mkdir(parents=True,exist_ok=True)
    dest=folder/(sha+('.svg' if ext=='.svg' else '.jpg'))
    fd,tmp=tempfile.mkstemp(dir=folder,prefix='.preview-');os.close(fd)
    try:
        if ext=='.svg':
            with Path(path).open('rb') as f:raw=f.read(8_000_001)
            data,metadata,_=sanitize_svg(raw);Path(tmp).write_bytes(data)
        else:
            if not HELPER.is_file():raise ValueError('媒体组件未构建，请运行 build-app.sh')
            # Managed objects are named only by SHA. AVFoundation on supported
            # macOS versions may require a container suffix to open them. A
            # temporary hard link supplies it without copying large videos or
            # changing the original object; both paths stay in the same vault.
            with tempfile.TemporaryDirectory(dir=Path(root)/'tmp',prefix='media-input-') as stage:
                probe_path=Path(path)
                if kind=='video' and probe_path.suffix.lower()!=ext:
                    probe_path=Path(stage)/('input'+ext)
                    os.link(path,probe_path)
                proc=subprocess.run([str(HELPER),kind,str(probe_path),tmp],capture_output=True,text=True,timeout=45,check=False)
            if proc.returncode!=0:raise ValueError('系统媒体组件未能完成处理')
            metadata=json.loads(proc.stdout)
            if metadata.get('error'):raise ValueError(metadata['error'])
        for key in ('width','height'):
            val=metadata.get(key)
            if isinstance(val,(float,int)) and 0<val<=1_000_000:result[key]=round(val)
        duration=metadata.get('duration')
        if isinstance(duration,(int,float)) and math.isfinite(duration) and duration>=0:result['duration']=round(duration,3)
        for key in ('camera','make','codec'):
            if isinstance(metadata.get(key),str):result[key]=metadata[key].strip()[:300] or None
        result['captured_at']=normalize_date(metadata.get('captured_at'))
        result['captured_ts']=capture_epoch(result['captured_at'])
        if result['captured_at']:result['captured_source']=metadata.get('captured_source')
        for key,bound in (('gps_lat',90),('gps_lon',180)):
            val=metadata.get(key)
            if isinstance(val,(float,int)) and math.isfinite(val) and abs(val)<=bound:result[key]=val
        if Path(tmp).stat().st_size:
            os.replace(tmp,dest);result['thumb_path']=str(dest.relative_to(root))
        if metadata.get('thumbnail_error'):result['media_error']=str(metadata['thumbnail_error'])[:400]
    except Exception as e:result['media_error']='媒体元数据或预览读取失败：'+str(e)[:300]
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    return result
