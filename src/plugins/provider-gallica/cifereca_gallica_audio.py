"""Validated, bounded audio bundles; publish without replacing a destination."""
import ctypes
import errno
import os
from pathlib import Path
import shutil
import tempfile
from urllib.parse import urlsplit
from cifereca.plugin_api import (
    ProviderError,
    BundleAcquisitionRecipe,
    BundleTrack,
    normalize_audio_format,
    validate_audio_bundle_manifest,
    write_bundle_manifest,
)

EXTENSIONS = {'audio/mpeg':'.mp3','audio/mp3':'.mp3','audio/mp4':'.m4a','audio/x-m4a':'.m4a',
              'audio/ogg':'.ogg','audio/opus':'.opus','audio/flac':'.flac','audio/wav':'.wav','audio/x-wav':'.wav'}

def validated_extension(content, mime):
    if content.startswith((b'ID3',)) or (len(content)>1 and content[0]==255 and content[1]&224==224):
        detected='.mp3'
    elif content.startswith(b'OggS'):detected='.opus' if b'OpusHead' in content[:128] else '.ogg'
    elif content.startswith(b'fLaC'):detected='.flac'
    elif content.startswith(b'RIFF') and content[8:12]==b'WAVE':detected='.wav'
    elif len(content)>12 and content[4:8]==b'ftyp':detected='.m4a'
    else:raise ProviderError('Gallica : contenu de piste audio non reconnu')
    declared=EXTENSIONS.get(mime)
    if declared and declared != detected and {declared,detected}!={'.ogg','.opus'}:
        raise ProviderError('Gallica : format audio différent de celui annoncé')
    return detected

def rename_exclusive(source, destination):
    """Linux desktop atomic directory publication with RENAME_NOREPLACE."""
    libc=ctypes.CDLL(None,use_errno=True)
    rename=getattr(libc,'renameat2',None)
    if rename is None:raise ProviderError('Gallica : publication atomique de dossier indisponible sur ce système')
    rename.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
    rename.restype=ctypes.c_int
    if rename(-100,os.fsencode(source),-100,os.fsencode(destination),1)!=0:
        code=ctypes.get_errno()
        if code==errno.EEXIST:raise FileExistsError(code,'bundle destination exists')
        raise OSError(code,os.strerror(code))

def identical_bundle(a,b):
    if b.is_symlink() or not b.is_dir():return False
    first={p.name for p in a.iterdir()};second={p.name for p in b.iterdir()}
    return first==second and all((b/name).is_file() and not (b/name).is_symlink() and (a/name).read_bytes()==(b/name).read_bytes() for name in first)

def download_bundle(session,item,target_dir,*,recipe: BundleAcquisitionRecipe):
    ark=session._extract_ark(item.record_id,item.friendly_url,item.download_url)
    if not ark:raise ProviderError('Gallica : ARK absent pour le document sonore')
    tracks=session._audio_tracks(ark)
    if not tracks or len(tracks)>1000:raise ProviderError('Gallica : nombre de pistes audio invalide')
    target_dir=Path(target_dir);target_dir.mkdir(parents=True,exist_ok=True)
    name=session._safe_name(item.title)[:120]+'_'+session._safe_name(ark.split('/')[-1],'audio')[:24]
    staged=Path(tempfile.mkdtemp(prefix='.gallica-audio-',dir=target_dir))
    total=0;names=[];formats=[];resolved_tracks=[];member_metadata={}
    try:
        for index,(title,url) in enumerate(tracks,1):
            if urlsplit(url).scheme not in {'http','https'}:raise ProviderError('Gallica : lien audio invalide')
            with session.client.stream('GET',url,headers={'Accept':'audio/*'}) as response:
                response.raise_for_status();mime=response.headers.get('content-type','').split(';',1)[0].strip().lower()
                if mime=='text/html':raise ProviderError('Gallica : piste HTML refusée')
                size=0; prefix=bytearray(); raw=staged/f'.track-{index:04d}.part'
                with raw.open('wb') as handle:
                    for chunk in response.iter_bytes():
                        size+=len(chunk);total+=len(chunk)
                        if size>session.max_audio_track_bytes or total>session.max_audio_bundle_bytes:
                            raise ProviderError('Gallica : téléchargement audio trop volumineux')
                        if len(prefix)<512: prefix.extend(chunk[:512-len(prefix)])
                        handle.write(chunk)
                    handle.flush();os.fsync(handle.fileno())
            ext=validated_extension(bytes(prefix),mime)
            filename=f'{index:02d} - {session._safe_name(title,"Plage")[:100]}{ext}'
            raw.replace(staged/filename)
            normalized_format=ext[1:].casefold()
            names.append(filename);formats.append(normalized_format.upper())
            member_metadata[filename]={"format":normalized_format,"title":title}
            declared_format=normalize_audio_format(mime)
            resolved_tracks.append(BundleTrack(
                url=url,
                title=title,
                format=normalized_format,
                mime_type=mime if declared_format == normalized_format else "",
                size_bytes=size,
                evidence={"source":"gallica-oai","declared_mime":mime},
            ))
        playlist='00 - playlist.m3u8'
        with (staged/playlist).open('w',encoding='utf-8') as handle:
            handle.write('#EXTM3U\n'+'\n'.join(names)+'\n');handle.flush();os.fsync(handle.fileno())
        resolved_recipe=BundleAcquisitionRecipe(
            recipe_id=recipe.recipe_id,
            resolver_url=recipe.resolver_url,
            tracks=tuple(resolved_tracks),
            media_type=recipe.media_type,
            bundle_format=recipe.bundle_format,
            playlist=playlist,
            metadata=recipe.metadata,
        ).validate()
        manifest=write_bundle_manifest(
            staged,
            media_format="audiobook",
            title=str(item.title or ""),
            author=str(item.author or ""),
            playlist=playlist,
            member_metadata=member_metadata,
            metadata={"provider_id":"gallica","recipe_id":recipe.recipe_id},
        )
        validate_audio_bundle_manifest(manifest,recipe=resolved_recipe)
        folder=target_dir/name
        for serial in range(1000):
            candidate=folder if serial==0 else folder.with_name(folder.name+f'_{serial}')
            try:
                rename_exclusive(staged,candidate)
            except FileExistsError:
                if identical_bundle(staged,candidate):break
                continue
            break
        else:raise ProviderError('Gallica : trop de collisions de dossiers audio')
        item.metadata['audio_formats']=list(dict.fromkeys(formats))
        item.metadata['audio_tracks']=len(names)
        item.format=formats[0] if len(set(formats))==1 else None
        session._diagnostic('GALLICA','audio download','ok',detail={'tracks':len(names),'bytes':total,'formats':item.metadata['audio_formats']},level='detailed')
        return candidate/playlist,str(item.friendly_url or f'https://gallica.bnf.fr/{ark}')
    finally:
        if staged.exists():shutil.rmtree(staged)
