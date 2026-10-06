import unittest
import httpx

from cifereca.diagnostics import Diagnostics
from cifereca.paris.client import ParisClient


class LoginRetryTests(unittest.TestCase):
    def test_remote_protocol_error_recreates_session_and_retries_once(self):
        factory_calls = 0
        login_posts = 0

        def factory():
            nonlocal factory_calls
            factory_calls += 1
            generation = factory_calls

            def handler(request: httpx.Request):
                nonlocal login_posts
                if request.url.path.endswith('/numerique/'):
                    return httpx.Response(200, text='ok')
                if request.url.path.endswith('/logon.svc/logon'):
                    login_posts += 1
                    if generation == 1:
                        raise httpx.RemoteProtocolError(
                            'peer closed connection without sending complete message body'
                        )
                    return httpx.Response(
                        200,
                        json={'success': True, 'd': {}},
                        headers={'set-cookie': '_syrSessGuid=abc; Path=/'},
                    )
                raise AssertionError(request.url.path)

            return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)

        diag = Diagnostics(enabled=True, level='detailed')
        client = ParisClient('https://example.test', diagnostics=diag, client_factory=factory)
        try:
            result = client.login('user', 'password')
            self.assertTrue(result['success'])
            self.assertEqual(login_posts, 2)
            self.assertEqual(factory_calls, 2)
            retry = [x for x in diag.snapshot() if x['operation'] == 'login retry']
            self.assertEqual(len(retry), 1)
            self.assertEqual(retry[0]['detail']['attempt'], '1/2')
            self.assertEqual(retry[0]['detail']['action'], 'retry_compat_identity_close')
        finally:
            client.close()

    def test_remote_protocol_error_stops_after_second_login_attempt(self):
        factory_calls = 0
        login_posts = 0

        def factory():
            nonlocal factory_calls
            factory_calls += 1

            def handler(request: httpx.Request):
                nonlocal login_posts
                if request.url.path.endswith('/numerique/'):
                    return httpx.Response(200, text='ok')
                if request.url.path.endswith('/logon.svc/logon'):
                    login_posts += 1
                    raise httpx.RemoteProtocolError('truncated')
                raise AssertionError(request.url.path)

            return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)

        diag = Diagnostics(enabled=True, level='detailed')
        client = ParisClient('https://example.test', diagnostics=diag, client_factory=factory)
        try:
            with self.assertRaises(httpx.RemoteProtocolError):
                client.login('user', 'password')
            self.assertEqual(login_posts, 2)
            self.assertEqual(factory_calls, 2)
            exhausted = [x for x in diag.snapshot() if x['operation'] == 'login retry exhausted']
            self.assertEqual(len(exhausted), 1)
            self.assertEqual(exhausted[0]['detail']['attempt'], '2/2')
        finally:
            client.close()


if __name__ == '__main__':
    unittest.main()
