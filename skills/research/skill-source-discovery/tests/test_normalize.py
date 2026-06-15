"""Tests for scripts/normalize.py — the canonical-key helper.

Covers §19 items 1-3 (URL canonicalization, git-remote normalization, alias
detection) plus idempotency. normalize() must map every equivalent URL form of
a source to one byte-identical canonical_key — the whole dedup guarantee rests
on it.
"""

import json
import pathlib
import subprocess
import sys

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import normalize as N  # noqa: E402


# --- basic git repo parsing ------------------------------------------------

def test_https_github_repo_basic():
    r = N.normalize("https://github.com/anthropics/skills")
    assert r.platform == "github"
    assert r.host == "github.com"
    assert r.owner == "anthropics"
    assert r.repo == "skills"
    assert r.canonical_url == "https://github.com/anthropics/skills"
    assert r.canonical_key == "github.com/anthropics/skills"


def test_strips_trailing_slash():
    assert N.normalize("https://github.com/anthropics/skills/").canonical_key == "github.com/anthropics/skills"


def test_strips_dot_git_suffix():
    assert N.normalize("https://github.com/anthropics/skills.git").canonical_key == "github.com/anthropics/skills"


def test_lowercases_host():
    assert N.normalize("https://GitHub.com/anthropics/skills").host == "github.com"


def test_lowercases_owner_and_repo():
    assert N.normalize("https://github.com/Anthropics/Skills").canonical_key == "github.com/anthropics/skills"


def test_strips_fragment():
    assert N.normalize("https://github.com/anthropics/skills#readme").canonical_key == "github.com/anthropics/skills"


def test_strips_utm_params():
    assert N.normalize("https://github.com/anthropics/skills?utm_source=newsletter").canonical_key == "github.com/anthropics/skills"


def test_strips_deep_tree_path():
    assert N.normalize("https://github.com/anthropics/skills/tree/main/doc").canonical_key == "github.com/anthropics/skills"


# --- every git remote form collapses to one identity -----------------------

GIT_FORMS = [
    "https://github.com/anthropics/skills",
    "https://github.com/anthropics/skills/",
    "https://github.com/anthropics/skills.git",
    "http://github.com/anthropics/skills",
    "git@github.com:anthropics/skills.git",
    "ssh://git@github.com/anthropics/skills.git",
    "git://github.com/anthropics/skills.git",
    "https://github.com/Anthropics/Skills",
]


@pytest.mark.parametrize("url", GIT_FORMS)
def test_all_git_forms_same_key(url):
    assert N.normalize(url).canonical_key == "github.com/anthropics/skills"


def test_ssh_scp_form_parsed():
    r = N.normalize("git@github.com:anthropics/skills.git")
    assert r.platform == "github"
    assert r.owner == "anthropics"
    assert r.repo == "skills"
    assert r.canonical_url == "https://github.com/anthropics/skills"


# --- aliases ---------------------------------------------------------------

def test_aliases_include_ssh_form():
    r = N.normalize("https://github.com/anthropics/skills")
    assert "git@github.com:anthropics/skills.git" in r.aliases


def test_website_has_no_alt_aliases():
    assert N.normalize("https://example.com/agent-skills").aliases == []


# --- platform / host specifics ---------------------------------------------

def test_gitlab_nested_group():
    r = N.normalize("https://gitlab.com/group/subgroup/repo")
    assert r.platform == "gitlab"
    assert r.owner == "group/subgroup"
    assert r.repo == "repo"
    assert r.canonical_key == "gitlab.com/group/subgroup/repo"


def test_codeberg_is_forgejo():
    assert N.normalize("https://codeberg.org/user/skills").platform == "forgejo"


def test_sourcehut_tilde_owner():
    r = N.normalize("https://git.sr.ht/~user/skills")
    assert r.platform == "sourcehut"
    assert r.owner == "~user"
    assert r.repo == "skills"


def test_bitbucket():
    assert N.normalize("https://bitbucket.org/team/skills").platform == "bitbucket"


def test_unknown_host_with_dotgit_is_git():
    r = N.normalize("https://git.example.com/foo/bar.git")
    assert r.platform == "git"
    assert r.canonical_key == "git.example.com/foo/bar"


# --- websites --------------------------------------------------------------

def test_generic_website():
    r = N.normalize("https://mcpservers.org/agent-skills")
    assert r.platform == "website"
    assert r.host == "mcpservers.org"
    assert r.canonical_url == "https://mcpservers.org/agent-skills"
    assert r.canonical_key == "mcpservers.org/agent-skills"


def test_website_strips_trailing_slash_preserves_path_case():
    assert N.normalize("https://example.com/Agent-Skills/").canonical_key == "example.com/Agent-Skills"


def test_website_strips_utm_keeps_and_sorts_other_query():
    r = N.normalize("https://example.com/list?utm_source=x&page=2&a=1")
    assert "utm_source" not in r.canonical_url
    assert r.canonical_url == "https://example.com/list?a=1&page=2"


def test_website_root_path():
    r = N.normalize("https://example.com/")
    assert r.canonical_key == "example.com"
    assert r.canonical_url == "https://example.com"


# --- idempotency -----------------------------------------------------------

@pytest.mark.parametrize("url", GIT_FORMS + [
    "https://mcpservers.org/agent-skills/",
    "https://example.com/list?utm_source=x&page=2",
])
def test_idempotent(url):
    once = N.normalize(url)
    twice = N.normalize(once.canonical_url)
    assert once.canonical_key == twice.canonical_key
    assert N.normalize(twice.canonical_url).canonical_url == twice.canonical_url


# --- errors ----------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "   ", "not a url", "ftp://x/y"])
def test_invalid_inputs_raise(bad):
    with pytest.raises(ValueError):
        N.normalize(bad)


# --- property: all git forms of any owner/repo collapse to one key ---------

try:
    from hypothesis import given, strategies as st

    _names = st.from_regex(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,30}", fullmatch=True)

    @given(owner=_names, repo=_names)
    def test_property_git_forms_collapse(owner, repo):
        base = f"github.com/{owner.lower()}/{repo.lower()}"
        forms = [
            f"https://github.com/{owner}/{repo}",
            f"https://github.com/{owner}/{repo}.git",
            f"https://github.com/{owner}/{repo}/",
            f"git@github.com:{owner}/{repo}.git",
            f"ssh://git@github.com/{owner}/{repo}.git",
        ]
        assert {N.normalize(f).canonical_key for f in forms} == {base}
except ImportError:  # hypothesis optional
    pass


# --- CLI -------------------------------------------------------------------

def test_cli_outputs_json():
    out = subprocess.run(
        [sys.executable, str(SCRIPTS / "normalize.py"), "https://github.com/anthropics/skills"],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(out.stdout)
    assert data["canonical_key"] == "github.com/anthropics/skills"
    assert data["platform"] == "github"
    assert "git@github.com:anthropics/skills.git" in data["aliases"]


def test_cli_errors_nonzero_on_bad_input():
    out = subprocess.run(
        [sys.executable, str(SCRIPTS / "normalize.py"), ""],
        capture_output=True, text=True,
    )
    assert out.returncode != 0
