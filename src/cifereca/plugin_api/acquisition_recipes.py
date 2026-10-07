from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlsplit

from .bundles import BundleManifest
from .models import AcquisitionOffer


_AUDIO_FORMATS = {'mp3', 'm4a', 'ogg', 'opus', 'flac', 'wav'}
_AUDIO_MIME_FORMATS = {
    'audio/mpeg': 'mp3',
    'audio/mp4': 'm4a',
    'audio/x-m4a': 'm4a',
    'audio/ogg': 'ogg',
    'audio/opus': 'opus',
    'audio/flac': 'flac',
    'audio/x-flac': 'flac',
    'audio/wav': 'wav',
    'audio/x-wav': 'wav',
}


class BundleRecipeError(ValueError):
    """Invalid public bundle acquisition recipe or produced bundle."""


def _http_url(value: object, *, field_name: str) -> str:
    url = str(value or '').strip()
    parts = urlsplit(url)
    if parts.scheme not in {'http', 'https'} or not parts.netloc:
        raise BundleRecipeError(f'{field_name} must be an absolute HTTP(S) URL')
    return url


def normalize_audio_format(value: object) -> str:
    """Normalize only explicit audio formats; filenames and URLs are never inspected."""
    raw = str(value or '').strip().casefold()
    if raw in _AUDIO_FORMATS:
        return raw
    return _AUDIO_MIME_FORMATS.get(raw, '')


@dataclass(frozen=True, slots=True)
class BundleTrack:
    """One resolved media member; URL suffixes are never treated as format evidence."""

    url: str
    title: str = ''
    format: str = ''
    mime_type: str = ''
    headers: Mapping[str, str] = field(default_factory=dict)
    size_bytes: int | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def normalized_format(self) -> str:
        declared = normalize_audio_format(self.format)
        from_mime = normalize_audio_format(self.mime_type)
        if self.format and not declared:
            raise BundleRecipeError(f'unsupported audio format: {self.format}')
        if self.mime_type and not from_mime:
            raise BundleRecipeError(f'unsupported audio MIME type: {self.mime_type}')
        if declared and from_mime and declared != from_mime:
            raise BundleRecipeError('track format and MIME type disagree')
        return declared or from_mime

    def validate(self) -> 'BundleTrack':
        _http_url(self.url, field_name='track.url')
        if not self.normalized_format():
            raise BundleRecipeError('resolved track requires explicit format or MIME evidence')
        if self.size_bytes is not None and int(self.size_bytes) < 0:
            raise BundleRecipeError('track size_bytes must be non-negative')
        return self

    def as_source(self) -> dict[str, Any]:
        self.validate()
        result: dict[str, Any] = {'url': self.url, 'format': self.normalized_format()}
        if self.title:
            result['title'] = self.title
        if self.mime_type:
            result['mime_type'] = self.mime_type
        if self.headers:
            result['headers'] = {str(k): str(v) for k, v in self.headers.items()}
        if self.size_bytes is not None:
            result['size_bytes'] = int(self.size_bytes)
        if self.evidence:
            result['evidence'] = dict(self.evidence)
        return result


@dataclass(frozen=True, slots=True)
class BundleAcquisitionRecipe:
    """Provider-neutral recipe for a resource resolved into an autonomous bundle."""

    recipe_id: str
    resolver_url: str
    tracks: tuple[BundleTrack, ...] = ()
    media_type: str = 'audiobook'
    bundle_format: str = 'audiobook'
    playlist: str = ''
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def resolved(self) -> bool:
        return bool(self.tracks)

    def validate(self) -> 'BundleAcquisitionRecipe':
        if not str(self.recipe_id or '').strip():
            raise BundleRecipeError('recipe_id is required')
        _http_url(self.resolver_url, field_name='resolver_url')
        if self.media_type != 'audiobook':
            raise BundleRecipeError('audio bundle recipe media_type must be audiobook')
        if self.bundle_format != 'audiobook':
            raise BundleRecipeError('audio bundle recipe bundle_format must be audiobook')
        for track in self.tracks:
            if not isinstance(track, BundleTrack):
                raise BundleRecipeError('tracks must contain BundleTrack values')
            track.validate()
        return self

    def as_source(self) -> dict[str, Any]:
        self.validate()
        return {
            'recipe': 'bundle',
            'resolver_url': self.resolver_url,
            'resolved': self.resolved,
            'tracks': [track.as_source() for track in self.tracks],
            'bundle_format': self.bundle_format,
            'playlist': self.playlist,
        }

    def to_offer(self, *, offer_id: str | None = None, drm: str = 'none', priority: int = 0, metadata: Mapping[str, Any] | None = None) -> AcquisitionOffer:
        """Project to the public offer contract without inventing a file format."""
        self.validate()
        combined = dict(self.metadata)
        combined.update(dict(metadata or {}))
        combined.setdefault('bundle_format', self.bundle_format)
        combined.setdefault('bundle_resolved', self.resolved)
        if self.resolved:
            combined.setdefault('track_formats', tuple(track.normalized_format() for track in self.tracks))
        return AcquisitionOffer(
            offer_id=str(offer_id or self.recipe_id),
            media_type=self.media_type,
            format='',
            drm=str(drm or 'none'),
            source_kind='bundle',
            source=self.as_source(),
            priority=int(priority),
            metadata=combined,
        )


def validate_audio_bundle_manifest(manifest: BundleManifest, *, recipe: BundleAcquisitionRecipe | None = None) -> BundleManifest:
    """Validate a produced autonomous audiobook bundle against an optional recipe."""
    if not isinstance(manifest, BundleManifest):
        raise BundleRecipeError('expected BundleManifest')
    if str(manifest.media_format or '').casefold() != 'audiobook':
        raise BundleRecipeError('bundle media_format must be audiobook')
    if not manifest.members:
        raise BundleRecipeError('audio bundle has no members')

    playlist = str(manifest.playlist or '')
    audio_members: list[str] = []
    for member in manifest.members:
        if playlist and member.path == playlist:
            continue
        normalized = normalize_audio_format(member.format)
        if not normalized:
            raise BundleRecipeError(f'bundle member is not explicit audio: {member.path}')
        audio_members.append(normalized)
    if not audio_members:
        raise BundleRecipeError('audio bundle has no audio members')

    if recipe is not None:
        recipe.validate()
        if not recipe.resolved:
            raise BundleRecipeError('cannot validate final bundle against unresolved recipe')
        expected = Counter(track.normalized_format() for track in recipe.tracks)
        actual = Counter(audio_members)
        if actual != expected:
            raise BundleRecipeError(f'bundle tracks differ from recipe: expected {dict(expected)}, got {dict(actual)}')
    return manifest


__all__ = [
    'BundleRecipeError',
    'BundleTrack',
    'BundleAcquisitionRecipe',
    'normalize_audio_format',
    'validate_audio_bundle_manifest',
]
