"""A looser query cannot repair transport, authentication or malformed XML."""
import httpx
from xml.etree.ElementTree import ParseError

def is_technical_search_error(error):
    seen=set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if getattr(error,'code',None)=='search_temporarily_unavailable':return True
        if isinstance(error,(httpx.TransportError,ParseError)):return True
        if isinstance(error,httpx.HTTPStatusError):
            status=error.response.status_code
            if status in {401,403,429} or status>=500:return True
        error=error.__cause__ or error.__context__
    return False
