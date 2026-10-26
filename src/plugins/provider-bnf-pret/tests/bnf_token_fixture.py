"""Synthetic BnF reader account and loan-token transport; never reaches BnF."""
from pathlib import Path
import sys

import httpx

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "tests", ROOT.parents[1]):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from test_http_provider import HTTPProviderTests

TOKEN = (b'<fulfillmentToken xmlns="http://ns.adobe.com/adept" '
         b'fulfillmentType="loan" auth="user"><resourceItemInfo/></fulfillmentToken>')


def connected(*, payload=TOKEN, status=200, headers=None):
    session, calls, state = HTTPProviderTests().make()
    original = session.client.http._transport.transport.handler
    downloaded = []

    def handler(request):
        if request.url.path.startswith("/loans/"):
            downloaded.append(request)
            return httpx.Response(
                status, content=payload,
                headers=headers if headers is not None
                else {"content-type": "application/octet-stream"},
            )
        return original(request)

    session.client.http._transport.transport.handler = handler
    account, = session.connect()
    account.identity_id = "profile-a"
    loan, = session.loans(account)
    return session, account, loan, state, calls, downloaded
