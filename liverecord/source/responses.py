from dataclasses import dataclass
import hashlib
import json


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def sha256(body):
    return hashlib.sha256(body).hexdigest()


@dataclass(frozen=True)
class ResponseEvidence:
    requested_url: str
    final_url: str
    request_range: str | None
    status: int
    headers: tuple
    length: int
    sha256: str

    def to_dict(self):
        return dict(requested_url=self.requested_url, final_url=self.final_url,
                    request_range=self.request_range, status=self.status,
                    headers=[list(x) for x in self.headers],
                    length=self.length, sha256=self.sha256)

    @classmethod
    def from_dict(cls, value):
        data = dict(value)
        data['headers'] = tuple(tuple(x) for x in data['headers'])
        return cls(**data)


@dataclass(frozen=True)
class FetchedResponse:
    body: bytes
    evidence: ResponseEvidence


def make_evidence(body, requested_url, final_url=None, request_range=None,
                  status=200, headers=()):
    return ResponseEvidence(requested_url, final_url or requested_url,
                            request_range, status, tuple(headers),
                            len(body), sha256(body))


def header_values(evidence, name):
    return [value for key, value in evidence.headers if key.lower() == name.lower()]
