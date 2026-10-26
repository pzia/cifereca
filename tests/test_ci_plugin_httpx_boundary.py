"""The gate rejects autonomous stacks, preserves models/tests and one exception."""
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.ci_plugin_httpx_boundary import AUTONOMOUS_PACKAGES, ROOT, check_repository, violations_in_source


@pytest.mark.parametrize('source', [
    'import httpx; httpx.Client()',
    'import httpx as h; h.AsyncClient()',
    'from httpx import Client as C; C()',
    'import httpx; factory=httpx.Client; factory()',
    'import httpx; getattr(httpx,"Client")()',
    'import httpx; httpx.get("https://example.test")',
    'import httpx; httpx.HTTPTransport()',
    'import httpx\nclass OwnClient(httpx.Client): pass',
    'from urllib.request import build_opener',
    'from urllib import request; request.urlopen("https://example.test")',
    'import requests',
    'from aiohttp import ClientSession',
    'import urllib3',
    'import http.client',
    'from cifereca.plugin_api import TraceHTTPClient; TraceHTTPClient()',
])
def test_autonomous_stacks_are_rejected(source):
    assert violations_in_source(source, Path('provider/example.py'))


@pytest.mark.parametrize('source', [
    'from urllib.parse import urlsplit; urlsplit("https://example.test")',
    'import httpx\ndef f(c: httpx.Client) -> httpx.Response: raise httpx.ReadTimeout("example")',
    'from cifereca.plugin_api import HTTPX_TRANSPORT\nc=HTTPX_TRANSPORT.client(producer="test")',
    'from cifereca.plugin_api import HTTPX_TRANSPORT as H\nc=H.public_client(producer="test")',
    'x="httpx.Client()"',
])
def test_core_service_types_errors_and_url_parsing_are_allowed(source):
    assert violations_in_source(source,Path('provider/example.py'))==[]


def test_tests_are_excluded_and_only_exact_recorder_package_is_excepted(tmp_path):
    files={'provider/good.py':'from cifereca.plugin_api import HTTPX_TRANSPORT',
           'provider/tests/mock.py':'import httpx; httpx.Client()',
           'browser-recorder/native.py':'import requests',
           'browser-recorder-other/bad.py':'import requests'}
    for relative,content in files.items():
        path=tmp_path/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)
    failures,checked,excepted=check_repository(tmp_path)
    assert checked==2 and excepted==1
    assert {str(row.path) for row in failures}=={'browser-recorder-other/bad.py'}
    assert set(AUTONOMOUS_PACKAGES)=={'browser-recorder'}
    assert 'Browser Recorder' in (ROOT/AUTONOMOUS_PACKAGES['browser-recorder']).read_text()


def test_unparseable_production_file_is_not_silently_skipped():
    rows=violations_in_source('def broken(',Path('provider/bad.py'))
    assert len(rows)==1 and rows[0].target=='unparseable_python'


def test_cli_is_a_failing_gate_for_a_new_direct_client(tmp_path):
    path=tmp_path/'provider.py';path.write_text('from httpx import Client\nClient()')
    result=subprocess.run([sys.executable,str(ROOT/'scripts/ci_plugin_httpx_boundary.py'),
                           '--root',str(tmp_path)],capture_output=True,text=True)
    assert result.returncode==1 and 'Core HTTPX_TRANSPORT' in result.stdout
