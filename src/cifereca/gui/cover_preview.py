"""Worker-only, bounded image previews; original cache files are preserved."""
import hashlib,uuid
from pathlib import Path

MAX_PIXELS=50_000_000
MAX_BYTES=24*1024*1024
SCHEMA='preview-v1'

def preview_path(source,root,width,height):
    info=source.stat()
    key=f'{source.resolve()}:{info.st_mtime_ns}:{info.st_size}:{width}:{height}:{SCHEMA}'
    return root/(hashlib.sha256(key.encode()).hexdigest()+'.png')

def create_preview(source,dest,width,height,backend=None):
    if not 1<=width<=2048 or not 1<=height<=2048:raise ValueError('preview_dimensions')
    if source.stat().st_size>MAX_BYTES:raise ValueError('preview_bytes_limit')
    if backend is None:
        import gi
        gi.require_version('GdkPixbuf','2.0')
        from gi.repository import GdkPixbuf
        backend=GdkPixbuf.Pixbuf
    info=backend.get_file_info(str(source))
    if not info or not info[0] or not 0<info[1]*info[2]<=MAX_PIXELS:raise ValueError('preview_pixels_limit')
    # Never enlarge an original; caller requests 3x logical dimensions for HiDPI.
    pixbuf=backend.new_from_file_at_scale(str(source),min(width,info[1]),min(height,info[2]),True)
    if pixbuf is None:raise ValueError('preview_decode_failed')
    temp=dest.with_name(dest.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        if pixbuf.savev(str(temp),'png',[],[]) is False:raise ValueError('preview_save_failed')
        temp.replace(dest)
    finally:temp.unlink(missing_ok=True)
    return dest
