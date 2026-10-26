"""Public-peer pinning and the real HTTPX/HTTPcore transport boundary, offline."""
import io
import socket
import ssl

import httpcore
import httpx
import pytest

import cifereca.public_httpx_transport as public
import cifereca.http_trace as trace_module
from cifereca.http_trace import HTTPTraceCapture
from cifereca.plugin_api import HTTPX_TRANSPORT
from cifereca.logging_utils import diagnostics_level, set_diagnostics_level


PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:4700:4700::1111"


def _answer(ip):
    if ":" in ip:
        return (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, 443, 0, 0))
    return (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))


class FakeSocket:
    def __init__(self, peer, calls):
        self.peer, self.calls, self.closed = peer, calls, False

    def settimeout(self, timeout):
        self.calls.append(("timeout", timeout))

    def connect(self, sockaddr):
        self.calls.append(("connect", sockaddr))

    def getpeername(self):
        return (self.peer, 443)

    def close(self):
        self.closed = True


@pytest.mark.parametrize("blocked", [
    "127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.3.2",
    "169.254.169.254", "::1", "fd00::1", "fe80::1", "0.0.0.0",
])
def test_dns_private_targets_fail_before_a_socket_is_created(blocked):
    made = []
    with pytest.raises(ValueError, match="non publique"):
        public._public_socket(
            "news.example", 443, timeout=1,
            resolve=lambda *_args, **_kwargs: [_answer(blocked)],
            socket_factory=lambda *_args: made.append(True),
        )
    assert made == []


def test_mixed_public_private_dns_answers_fail_closed():
    created = []
    with pytest.raises(ValueError, match="non publique"):
        public._public_socket(
            "news.example", 443, timeout=1,
            resolve=lambda *_args, **_kwargs: [_answer(PUBLIC_V4), _answer("10.1.2.3")],
            socket_factory=lambda *_args: created.append(True),
        )
    assert created == []


@pytest.mark.parametrize("ip", [PUBLIC_V4, PUBLIC_V6])
def test_global_dns_address_connects_using_numeric_sockaddr_only(ip):
    calls, addresses, created = [], [], []
    def resolve(host, port, **kwargs):
        addresses.append((host, port))
        return [_answer(ip)]
    def make_socket(*args):
        sock = FakeSocket(ip, calls)
        created.append(sock)
        return sock
    sock = public._public_socket(
        "news.example", 443, timeout=1.5,
        resolve=resolve, socket_factory=make_socket,
    )
    assert sock is created[0]
    assert addresses == [("news.example", 443)]
    assert ("connect", _answer(ip)[4]) in calls
    assert all("news.example" not in str(call) for call in calls)


def test_dns_rebinding_cannot_change_the_actual_peer(monkeypatch):
    # A peer address different from the vetted IP is never returned to HTTP.
    sock = FakeSocket("127.0.0.1", [])
    with pytest.raises(ValueError, match="Socket connecté"):
        public._public_socket(
            "news.example", 443, timeout=1,
            resolve=lambda *_args, **_kwargs: [_answer(PUBLIC_V4)],
            socket_factory=lambda *_args: sock,
        )
    assert sock.closed


class WireSocket(FakeSocket):
    def __init__(self, peer, calls):
        super().__init__(peer,calls)
        self.incoming=io.BytesIO(b'HTTP/1.1 200 OK\r\nContent-Length: 4\r\nConnection: close\r\n\r\nbody')
    def setsockopt(self,*args):
        pass
    def send(self,data):
        self.calls.append(('http',bytes(data)))
        return len(data)
    def recv(self,maximum):
        return self.incoming.read(maximum)
    def fileno(self):
        return -1
    def getsockname(self):
        return ('192.0.2.1',54321)


@pytest.mark.parametrize('scheme',['http','https'])
def test_core_public_client_uses_pinned_backend_and_trace_with_original_origin(monkeypatch,scheme):
    previous=diagnostics_level()
    set_diagnostics_level('trace')
    capture=HTTPTraceCapture()
    monkeypatch.setattr(trace_module,'HTTP_TRACE',capture)
    calls=[]
    sock=WireSocket(PUBLIC_V4,calls)

    def dial(host,port,**kwargs):
        calls.append(('dial',host,port))
        return sock

    monkeypatch.setattr(public,'_public_socket',dial)
    client=HTTPX_TRANSPORT.public_client(producer='public.test',timeout=2)
    ctx=client._transport._pool._ssl_context
    assert ctx.check_hostname and ctx.verify_mode==ssl.CERT_REQUIRED

    class Context:
        def set_alpn_protocols(self,protocols):
            pass
        def wrap_socket(self,raw,*,server_hostname):
            calls.append(('tls',raw,server_hostname))
            return raw

    client._transport._pool._ssl_context=Context()
    try:
        with client:
            response=client.get(scheme+'://news.example/feed?synthetic=1')
            assert response.content==b'body'
        assert ('dial','news.example',443 if scheme=='https' else 80) in calls
        wire=b''.join(value for kind,*values in calls if kind=='http' for value in values)
        assert b'GET /feed?synthetic=1 HTTP/1.1' in wire
        assert b'Host: news.example\r\n' in wire
        if scheme=='https':
            assert ('tls',sock,'news.example') in calls
        assert sock.closed
        entries=capture.snapshot()['exchanges']
        assert len(entries)==1 and entries[0]['response_body']['data']==b'body'
        assert entries[0]['response_body']['state']=='complete'
        assert entries[0]['request']['url'].startswith(scheme+'://news.example/')
    finally:
        client.close()
        set_diagnostics_level(previous)


def test_core_public_client_blocks_private_dns_before_socket(monkeypatch):
    created=[]
    monkeypatch.setattr(public.socket,'getaddrinfo',lambda *a,**kw:[_answer('10.0.0.1')])
    monkeypatch.setattr(public.socket,'socket',lambda *a:created.append(a))
    with HTTPX_TRANSPORT.public_client(producer='public.test') as client:
        with pytest.raises(ValueError,match='non publique'):
            client.get('https://news.example/feed')
    assert not created


def test_tls_failure_closes_socket_without_sending_http(monkeypatch):
    calls=[]
    sock=WireSocket(PUBLIC_V4,calls)
    monkeypatch.setattr(public,'_public_socket',lambda *a,**kw:sock)
    class Context:
        def set_alpn_protocols(self,protocols):pass
        def wrap_socket(self,raw,*,server_hostname):
            raise ssl.SSLError('synthetic TLS failure')
    with HTTPX_TRANSPORT.public_client(producer='public.test') as client:
        client._transport._pool._ssl_context=Context()
        with pytest.raises(httpx.ConnectError):
            client.get('https://news.example/feed')
    assert sock.closed and not any(row[0]=='http' for row in calls)


@pytest.mark.parametrize('timeout',[None,True,0,-1,float('inf'),float('nan'),10**400])
def test_public_client_requires_finite_bounded_wait(timeout):
    with pytest.raises(ValueError):
        HTTPX_TRANSPORT.public_client(producer='test',timeout=timeout)


@pytest.mark.parametrize('key',['proxy','transport','mounts','trust_env','verify'])
def test_public_service_disallows_transport_policy_overrides(key):
    with pytest.raises(TypeError):
        HTTPX_TRANSPORT.public_client(producer='test',**{key:None})
