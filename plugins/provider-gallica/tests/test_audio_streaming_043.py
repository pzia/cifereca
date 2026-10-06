from pathlib import Path

def test_audio_downloader_no_full_track_accumulator():
    source=(Path(__file__).resolve().parents[1]/'cifereca_gallica_audio.py').read_text(encoding='utf-8')
    assert 'chunks=[]' not in source
    assert ".track-{index:04d}.part" in source
    assert 'prefix=bytearray()' in source
