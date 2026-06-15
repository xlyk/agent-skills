"""Deterministic URL / git-remote canonicalization for skill-source-discovery.

The whole dedup guarantee rests on a `canonical_key` that is byte-identical for
every equivalent form of a source. This is the only code in the skill; the rest
is SQL the agent runs directly. Standard library only.

CLI:
    python3 normalize.py <url-or-git-remote>
    -> prints JSON {canonical_key, canonical_url, platform, host, owner, repo, aliases}

Rules:
    * scheme dropped from the key; canonical_url is always https
    * host lowercased; for git repos owner/repo lowercased too (hosts treat them
      case-insensitively and redirect); website paths keep their case
    * `.git`, trailing slashes, fragments, and utm_* params stripped
    * git repo identity is host/owner/repo only — deep links (tree/blob, GitLab
      `/-/`) are cut
    * websites keep non-utm query params, sorted, so distinct pages stay distinct
"""

import dataclasses
import json
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit

_GIT_HOSTS = {
    "github.com": "github",
    "gitlab.com": "gitlab",
    "bitbucket.org": "bitbucket",
    "codeberg.org": "forgejo",
}
_ALLOWED_SCHEMES = {"http", "https", "ssh", "git"}


@dataclasses.dataclass
class Normalized:
    canonical_key: str
    canonical_url: str
    platform: str
    host: str
    owner: str | None
    repo: str | None
    aliases: list[str]


def normalize(raw: str) -> Normalized:
    """Canonicalize a URL or git remote into a stable identity."""
    if not raw or not raw.strip():
        raise ValueError("empty input")
    s = raw.strip()

    scheme, host, path, query = _split(s)
    host = host.lower()
    platform = _platform_for(host, scheme, path)

    if platform == "website":
        return _website(host, path, query)
    return _git(platform, host, path)


def _split(s: str) -> tuple[str, str, str, str]:
    """Return (scheme, host, path, query). Raises ValueError on unparseable input."""
    # scp-like git remote: git@host:owner/repo.git  (no scheme, has user@host:path)
    if "://" not in s and "@" in s:
        userhost, sep, path = s.partition(":")
        host = userhost.split("@", 1)[1]
        if not sep or not host or not path:
            raise ValueError(f"cannot parse git remote: {s!r}")
        return "ssh", host, path, ""

    parts = urlsplit(s)
    scheme = parts.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"unsupported or missing scheme: {s!r}")
    host = parts.hostname or ""
    if not host:
        raise ValueError(f"no host in {s!r}")
    return scheme, host, parts.path, parts.query


def _platform_for(host: str, scheme: str, path: str) -> str:
    if host in _GIT_HOSTS:
        return _GIT_HOSTS[host]
    if host == "git.sr.ht" or host.endswith(".sr.ht"):
        return "sourcehut"
    if scheme in ("ssh", "git"):
        return "git"
    if path.rstrip("/").endswith(".git"):
        return "git"
    return "website"


def _git(platform: str, host: str, path: str) -> Normalized:
    p = path.strip("/")
    if p.endswith(".git"):
        p = p[:-4]

    if platform == "gitlab":
        p = p.split("/-/", 1)[0].strip("/")  # GitLab deep-link separator
        segs = [x for x in p.split("/") if x]
        if not segs:
            raise ValueError(f"no project path: {path!r}")
        owner = "/".join(segs[:-1]) if len(segs) > 1 else segs[0]
        repo = segs[-1] if len(segs) > 1 else None
    else:
        segs = [x for x in p.split("/") if x]
        if not segs:
            raise ValueError(f"no owner in path: {path!r}")
        owner = segs[0]
        repo = segs[1] if len(segs) > 1 else None

    host = host.lower()
    owner = owner.lower()
    if repo:
        repo = repo.lower()
        key = f"{host}/{owner}/{repo}"
        url = f"https://{host}/{owner}/{repo}"
        aliases = [f"git@{host}:{owner}/{repo}.git"]
    else:
        key = f"{host}/{owner}"
        url = f"https://{host}/{owner}"
        aliases = []
    return Normalized(key, url, platform, host, owner, repo, aliases)


def _website(host: str, path: str, query: str) -> Normalized:
    p = path.rstrip("/")
    pairs = sorted(
        (k, v) for k, v in parse_qsl(query, keep_blank_values=True)
        if not k.lower().startswith("utm_")
    )
    q = urlencode(pairs)

    key = f"{host}{p}" if p else host
    url = f"https://{host}{p}" if p else f"https://{host}"
    if q:
        key = f"{key}?{q}"
        url = f"{url}?{q}"
    return Normalized(key, url, "website", host, None, None, [])


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: normalize.py <url-or-git-remote>", file=sys.stderr)
        return 2
    try:
        result = normalize(argv[1])
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(dataclasses.asdict(result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
