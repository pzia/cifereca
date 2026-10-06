import json, unittest
import httpx
from cifereca.diagnostics import Diagnostics
from cifereca.paris.client import ParisClient

class ClientMockTests(unittest.TestCase):
    def test_login_and_loans(self):
        def handler(request: httpx.Request):
            p=request.url.path
            if p.endswith('/numerique/'):
                return httpx.Response(200, text='ok')
            if p.endswith('/logon.svc/logon'):
                return httpx.Response(200, json={'success':True,'d':{}}, headers={'set-cookie':'_syrSessGuid=abc; Path=/'})
            if p.endswith('/ListLoans'):
                provider=request.url.params.get('serviceCode')
                loans=[{'Id':'1','RecordId':'r','Title':provider+' title','AdditionalProperties':{'CanReturn':True}}] if provider=='DILICOM' else []
                return httpx.Response(200,json={'success':True,'d':{'Loans':loans}})
            raise AssertionError(p)
        c=ParisClient('https://example.test', diagnostics=Diagnostics())
        c.client.close(); c.client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        c.login('u','p')
        rows=c.all_loans()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0].provider,'DILICOM')
        c.close()

if __name__ == '__main__': unittest.main()
