from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.plugin_api import (
    BundleAcquisitionRecipe,
    BundleRecipeError,
    BundleTrack,
    normalize_audio_format,
    validate_audio_bundle_manifest,
    write_bundle_manifest,
)


def test_unresolved_bundle_offer_keeps_product_format_unknown_and_separates_resolver_url():
    recipe = BundleAcquisitionRecipe(
        recipe_id='gallica:ark123',
        resolver_url='https://gallica.bnf.fr/ark:/12148/ark123',
    )

    offer = recipe.to_offer()

    assert offer.media_type == 'audiobook'
    assert offer.format == ''
    assert offer.source_kind == 'bundle'
    assert offer.source['resolver_url'].endswith('/ark123')
    assert offer.source['tracks'] == []
    assert offer.source['resolved'] is False
    assert 'url' not in offer.source
    assert offer.metadata['bundle_resolved'] is False


def test_track_format_is_never_inferred_from_url_suffix():
    track = BundleTrack(url='https://media.example.test/chapter-01.mp3')

    with pytest.raises(BundleRecipeError, match='explicit format or MIME evidence'):
        track.validate()


def test_explicit_audio_mime_is_valid_format_evidence():
    track = BundleTrack(
        url='https://media.example.test/resource?id=1',
        mime_type='audio/mpeg',
        evidence={'source': 'resolver-response'},
    )

    assert track.normalized_format() == 'mp3'
    assert track.as_source()['format'] == 'mp3'


def test_format_and_mime_must_not_contradict():
    track = BundleTrack(
        url='https://media.example.test/track',
        format='flac',
        mime_type='audio/mpeg',
    )

    with pytest.raises(BundleRecipeError, match='disagree'):
        track.validate()


@pytest.mark.parametrize('value', ['html', 'epub', 'pdf', 'MONE', 'application/octet-stream'])
def test_non_audio_values_are_not_promoted_to_audio_formats(value):
    assert normalize_audio_format(value) == ''


def test_resolved_recipe_projects_tracks_without_using_resolver_as_media():
    recipe = BundleAcquisitionRecipe(
        recipe_id='gallica:audio:1',
        resolver_url='https://gallica.bnf.fr/services/audio/1',
        tracks=(
            BundleTrack(url='https://media.example.test/a', format='mp3', title='Piste 1'),
            BundleTrack(url='https://media.example.test/b', mime_type='audio/flac', title='Piste 2'),
        ),
        playlist='playlist.m3u8',
    )

    offer = recipe.to_offer(priority=10)

    assert offer.format == ''
    assert offer.priority == 10
    assert offer.source_kind == 'bundle'
    assert offer.source['resolved'] is True
    assert [row['format'] for row in offer.source['tracks']] == ['mp3', 'flac']
    assert offer.metadata['track_formats'] == ('mp3', 'flac')
    assert offer.source['resolver_url'] not in {row['url'] for row in offer.source['tracks']}


def test_recipe_rejects_non_http_track_and_resolver_urls():
    with pytest.raises(BundleRecipeError, match='resolver_url'):
        BundleAcquisitionRecipe(recipe_id='x', resolver_url='file:///tmp/page').validate()

    recipe = BundleAcquisitionRecipe(
        recipe_id='x',
        resolver_url='https://example.test/page',
        tracks=(BundleTrack(url='file:///tmp/track.mp3', format='mp3'),),
    )
    with pytest.raises(BundleRecipeError, match='track.url'):
        recipe.validate()


def test_final_bundle_manifest_matches_resolved_recipe(tmp_path: Path):
    (tmp_path / '01.bin').write_bytes(b'one')
    (tmp_path / '02.bin').write_bytes(b'two')
    (tmp_path / 'playlist.m3u8').write_text('#EXTM3U\n01.bin\n02.bin\n', encoding='utf-8')
    manifest = write_bundle_manifest(
        tmp_path,
        media_format='audiobook',
        playlist='playlist.m3u8',
        member_metadata={
            '01.bin': {'format': 'mp3'},
            '02.bin': {'format': 'flac'},
            'playlist.m3u8': {'format': 'm3u8'},
        },
    )
    recipe = BundleAcquisitionRecipe(
        recipe_id='x',
        resolver_url='https://example.test/resolve',
        tracks=(
            BundleTrack(url='https://media.example.test/a', format='mp3'),
            BundleTrack(url='https://media.example.test/b', format='flac'),
        ),
        playlist='playlist.m3u8',
    )

    assert validate_audio_bundle_manifest(manifest, recipe=recipe) is manifest


def test_final_bundle_manifest_rejects_missing_or_different_tracks(tmp_path: Path):
    (tmp_path / '01.bin').write_bytes(b'one')
    manifest = write_bundle_manifest(
        tmp_path,
        media_format='audiobook',
        member_metadata={'01.bin': {'format': 'mp3'}},
    )
    recipe = BundleAcquisitionRecipe(
        recipe_id='x',
        resolver_url='https://example.test/resolve',
        tracks=(
            BundleTrack(url='https://media.example.test/a', format='mp3'),
            BundleTrack(url='https://media.example.test/b', format='flac'),
        ),
    )

    with pytest.raises(BundleRecipeError, match='tracks differ'):
        validate_audio_bundle_manifest(manifest, recipe=recipe)


def test_unresolved_recipe_cannot_claim_validation_of_final_bundle(tmp_path: Path):
    (tmp_path / '01.bin').write_bytes(b'one')
    manifest = write_bundle_manifest(
        tmp_path,
        media_format='audiobook',
        member_metadata={'01.bin': {'format': 'mp3'}},
    )
    recipe = BundleAcquisitionRecipe(
        recipe_id='x',
        resolver_url='https://example.test/resolve',
    )

    with pytest.raises(BundleRecipeError, match='unresolved recipe'):
        validate_audio_bundle_manifest(manifest, recipe=recipe)


def test_bundle_manifest_never_enters_derived_private_stage(tmp_path, monkeypatch):
    import cifereca.plugin_api.bundles as bundle_api
    from cifereca.core.derived_variant_region_paths import PRIVATE_DERIVED_REGION

    assert bundle_api._PRIVATE_DERIVED_REGION == PRIVATE_DERIVED_REGION
    root = tmp_path / "audiobook"
    private = root / "disc" / PRIVATE_DERIVED_REGION
    private.mkdir(parents=True)
    stage = private / "secret.mp3"
    stage.write_bytes(b"private unpublished track")
    (root / "track.mp3").write_bytes(b"public track")
    actual_scandir = bundle_api.os.scandir

    def guard(path):
        if not isinstance(path, int):
            assert PRIVATE_DERIVED_REGION not in Path(path).parts
        return actual_scandir(path)

    monkeypatch.setattr(bundle_api.os, "scandir", guard)
    with pytest.raises(bundle_api.BundleError, match="reserved Core derived region"):
        write_bundle_manifest(root, title="Synthetic audio")
    assert stage.read_bytes() == b"private unpublished track"
    assert (root / "track.mp3").read_bytes() == b"public track"
    assert not (root / bundle_api.BUNDLE_MANIFEST_NAME).exists()


def test_bundle_manifest_preserves_near_named_ordinary_directory(tmp_path):
    import cifereca.plugin_api.bundles as bundle_api

    root = tmp_path / "audiobook"
    folder = root / (bundle_api._PRIVATE_DERIVED_REGION + "-backup")
    folder.mkdir(parents=True)
    (folder / "track.mp3").write_bytes(b"ordinary")
    manifest = write_bundle_manifest(root, title="Synthetic audio")
    assert [member.path for member in manifest.members] == [
        bundle_api._PRIVATE_DERIVED_REGION + "-backup/track.mp3",
    ]
    assert (folder / "track.mp3").read_bytes() == b"ordinary"


def test_bundle_manifest_rejects_symlink_and_scan_failure(tmp_path, monkeypatch):
    import cifereca.plugin_api.bundles as bundle_api

    root = tmp_path / "audiobook"
    root.mkdir()
    foreign = tmp_path / "foreign.mp3"
    foreign.write_bytes(b"foreign data")
    (root / "link.mp3").symlink_to(foreign)
    with pytest.raises(bundle_api.BundleError, match="symlink"):
        write_bundle_manifest(root)
    assert foreign.read_bytes() == b"foreign data"
    (root / "link.mp3").unlink()

    def denied(_root, **kwargs):
        kwargs["onerror"](PermissionError("synthetic denied"))
        yield

    monkeypatch.setattr(bundle_api.os, "walk", denied)
    with pytest.raises(bundle_api.BundleError, match="scan incomplete"):
        write_bundle_manifest(root)
    assert not (root / bundle_api.BUNDLE_MANIFEST_NAME).exists()
