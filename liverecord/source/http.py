import http.client
import urllib.error
import urllib.request
from liverecord.source.errors import FetchError
from liverecord.source.registry import validate_url
from liverecord.source.responses import FetchedResponse, make_evidence


class HttpClient:
    def __init__(self, timeout=5):
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def fetch(self, url, range_request=None):
        validate_url(url)
        header = range_request.header if range_request is not None else None
        headers = {'Accept-Encoding': 'identity'}
        if header is not None:
            headers['Range'] = header
        request = urllib.request.Request(url, headers=headers)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                validate_url(response.geturl())
                body = response.read()
                raw_headers = tuple(response.headers.raw_items())
                lengths = response.headers.get_all('Content-Length') or []
                if lengths and (len(lengths) != 1 or not lengths[0].isdigit()
                                or int(lengths[0]) != len(body)):
                    raise FetchError('HTTP Content-Length mismatch')
                if not 200 <= response.status < 300:
                    raise FetchError('HTTP status ' + str(response.status))
                evidence = make_evidence(body, url, response.geturl(), header,
                                         response.status, raw_headers)
                return FetchedResponse(body, evidence)
        except FetchError:
            raise
        except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException) as exc:
            raise FetchError(str(exc)) from exc
