from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class SourceHostPolicy:
    allowed_hosts: frozenset[str]
    require_allowlist: bool

    @classmethod
    def from_csv(cls, value: str, *, require_allowlist: bool) -> "SourceHostPolicy":
        hosts = frozenset(item.strip().lower() for item in value.split(",") if item.strip())
        return cls(allowed_hosts=hosts, require_allowlist=require_allowlist)

    def enforce(self, url: str) -> None:
        hostname = urlsplit(url).hostname
        if hostname is None:
            raise ValueError("source URL has no hostname")
        if not self.allowed_hosts and not self.require_allowlist:
            return
        if hostname.lower() not in self.allowed_hosts:
            raise ValueError("source host is not in the deployment allowlist")
