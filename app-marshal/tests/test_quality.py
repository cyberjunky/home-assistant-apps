"""Code quality (Ruff), vulnerabilities all the way down, and the repository's own security advisories."""

import json

import pytest

from src import quality
from src.advisories import Advisories, covers
from src.analyzer import add_context, add_quality, analyze, finish
from src.analyzer.rules import RULES
from src.osv import fixed_after
from src.packages import Packages

MANIFEST = {
    "domain": "demo",
    "name": "Demo",
    "version": "1.0.0",
    "codeowners": ["@someone"],
    "issue_tracker": "https://github.com/someone/demo/issues",
    "iot_class": "cloud_polling",
    "config_flow": True,
    "requirements": [],
}
needs_ruff = pytest.mark.skipif(quality.program() is None, reason="Ruff is not installed")


def files_of(code: str, **manifest) -> dict:
    files = {"manifest.json": json.dumps({**MANIFEST, **manifest}).encode(), "api.py": code.encode()}
    return {path: (len(data), data) for path, data in files.items()}


def scan(code: str, lint: dict | None, **context) -> dict:
    files = files_of(code, **context.pop("manifest", {}))
    report = analyze(files)
    add_quality(report, files, lint)
    add_context(report, **context)
    return finish(report)


def rules(report: dict) -> set[str]:
    return {f["rule"] for f in report["findings"]}


def issue(code: str, line: int = 1, message: str = "x") -> dict:
    return {"code": code, "file": "api.py", "line": line, "message": message}


def test_every_lint_code_leads_to_a_rule():
    assert set(quality.RULE_FOR.values()) <= set(RULES)


@needs_ruff
def test_ruff_takes_the_whole_list_and_reads_a_file():
    code = "import hashlib\n\n\ndef f(a):\n    try:\n        return missing\n    except:\n        pass\n    hashlib.md5(a)\n"
    found = quality.lint(files_of(code))
    assert found["tool"].startswith("Ruff ")
    assert {"F821", "E722", "S324"} <= {i["code"] for i in found["issues"]}
    report = scan(code, found)
    assert {"quality.broken", "quality.swallows_errors", "code.weak_crypto"} <= rules(report)
    assert report["scores"]["quality"] == 84 and report["quality"]["issues"] == 3


@needs_ruff
def test_a_noqa_comment_does_not_hide_a_finding():
    found = quality.lint(files_of("x = missing  # noqa\n"))
    assert [i["code"] for i in found["issues"]] == ["F821"]


def test_quality_findings_quote_the_line():
    report = scan("a = 1\nb = missing\n", {"tool": "Ruff 1", "issues": [issue("F821", 2, "Undefined name `missing`")]})
    finding = next(f for f in report["findings"] if f["rule"] == "quality.broken")
    assert finding["where"][0]["snippet"] == "b = missing" and "F821" in finding["where"][0]["note"]


def test_complexity_has_two_steps():
    lint = {
        "tool": "Ruff 1",
        "issues": [issue("C901", 1, "`a` is too complex (30 > 25)"), issue("C901", 2, "`b` is too complex (61 > 25)")],
    }
    report = scan("x = 1\ny = 2\n", lint)
    assert {"quality.complex", "quality.very_complex"} <= rules(report)
    assert report["quality"]["most_complex"]["paths"] == 61


def test_a_few_unused_names_are_nothing():
    few = {"tool": "Ruff 1", "issues": [issue("F401", n) for n in range(1, 6)]}
    assert "quality.unused" not in rules(scan("x = 1\n" * 50, few))
    many = {"tool": "Ruff 1", "issues": [issue("F401", n) for n in range(1, 13)]}
    assert "quality.unused" in rules(scan("x = 1\n" * 50, many))


def test_type_hints_are_counted():
    bare = "".join(f"def f{n}(a):\n    return a\n" for n in range(12))
    report = scan(bare, None)
    assert "quality.untyped" in rules(report) and report["quality"]["typed"] == 0
    typed = "".join(f"def f{n}(a: int) -> int:\n    return a\n" for n in range(12))
    report = scan(typed, None)
    assert "quality.untyped" not in rules(report) and report["quality"]["typed"] == 100


def test_without_ruff_the_quality_is_not_measured():
    report = scan("x = 1\n", None)
    assert report["quality"]["tool"] is None and report["quality"]["issues"] is None


def test_poor_quality_alone_does_not_fail_an_integration():
    lint = {"tool": "Ruff 1", "issues": [issue("F821", n) for n in range(1, 9)] + [issue("B006", 9), issue("E722", 10)]}
    lint["issues"] += [issue("C901", 11, "`a` is too complex (80 > 25)")]
    report = scan("x = 1\n" * 20, lint)
    assert report["scores"]["quality"] < 70 and report["grade"] in ("A", "B")


def test_a_library_is_not_judged_on_quality():
    code = b"def f(a):\n    return a\n" * 12
    report = analyze({"lib/__init__.py": (len(code), code)}, package=True)
    add_quality(report, {}, None)
    assert "quality.untyped" not in rules(finish(report))


# -- vulnerabilities ---------------------------------------------------------------------------------
def test_vulnerabilities_by_severity_and_all_the_way_down():
    packages = {
        ("lib", "==1.0"): {"status": "scanned", "version": "1.0", "indirect": [{"name": "deep", "version": "2.0"}]}
    }
    vulnerabilities = {
        ("lib", "1.0"): [
            {"id": "CVE-1", "summary": "bad", "severity": "moderate", "fixed": "1.1"},
            {"id": "CVE-1", "summary": "the same one, from another list", "severity": "moderate", "fixed": "1.1"},
        ],
        ("deep", "2.0"): [{"id": "CVE-2", "summary": "worse", "severity": "", "fixed": ""}],
    }
    report = scan(
        "x = 1\n", None, manifest={"requirements": ["lib==1.0"]}, packages=packages, vulnerabilities=vulnerabilities
    )
    found = {f["rule"]: f for f in report["findings"]}
    assert found["code.vulnerable_dependency_medium"]["count"] == 1
    assert found["code.vulnerable_dependency_medium"]["where"][0]["note"] == "lib 1.0: CVE-1, fixed in 1.1"
    # Not rated counts as high; it names the requirement it comes in through
    assert found["code.vulnerable_dependency"]["where"][0]["note"] == "deep 2.0 (through lib): CVE-2"
    requirement = report["requirements"][0]
    assert (
        len(requirement["vulnerabilities"]) == 1 and requirement["indirect"][0]["vulnerabilities"][0]["id"] == "CVE-2"
    )


def test_withdrawn_and_brand_new_versions():
    packages = {
        ("lib", "==1.0"): {"status": "scanned", "version": "1.0", "yanked": True, "published": 1000 - 3 * 86400}
    }
    report = scan("x = 1\n", None, manifest={"requirements": ["lib==1.0"]}, packages=packages, now=1000)
    assert {"code.dependency_yanked", "code.dependency_new"} <= rules(report)
    packages[("lib", "==1.0")].update(yanked=False, published=1000 - 30 * 86400)
    report = scan("x = 1\n", None, manifest={"requirements": ["lib==1.0"]}, packages=packages, now=1000)
    assert not {"code.dependency_yanked", "code.dependency_new"} & rules(report)


def test_fixed_after():
    assert fixed_after(["1.26.19", "2.2.2"], "1.26.5") == "1.26.19"
    assert fixed_after(["1.26.19", "2.2.2"], "2.0.0") == "2.2.2"
    assert (
        fixed_after(["1.0"], "2.0") == "" and fixed_after(["junk"], "1.0") == "" and fixed_after(["1.0"], "junk") == ""
    )


def test_advisory_ranges():
    assert covers("< 1.4.2", "1.4.1") and covers(">= 1.0, < 1.2", "v1.1.0")
    assert covers("< 1.4.2", "1.4.2") is False
    assert covers("all versions", "1.0") is None and covers("< 2", "main") is None


def test_an_advisory_for_the_installed_version_is_a_finding(tmp_path):
    advisories = Advisories(tmp_path)
    advisories.known["someone/demo"] = {
        "at": 1,
        "items": [
            {
                "id": "CVE-9",
                "summary": "Path traversal",
                "severity": "high",
                "ranges": [{"range": "< 1.2", "fixed": "1.2"}],
            },
            {"id": "CVE-8", "summary": "Old", "severity": "high", "ranges": [{"range": "< 0.5", "fixed": "0.5"}]},
            {"id": "CVE-7", "summary": "Unclear", "severity": "high", "ranges": [{"range": "some", "fixed": ""}]},
        ],
    }
    covering = advisories.covering("Someone/Demo", "1.0.0")
    assert [a["id"] for a in covering] == ["CVE-9"]
    report = scan("x = 1\n", None, advisories=covering)
    finding = next(f for f in report["findings"] if f["rule"] == "code.advisory")
    assert finding["where"][0] == {"file": "CVE-9", "line": 0, "note": "Path traversal (fixed in 1.2)", "snippet": ""}
    assert advisories.covering("someone/other", "1.0.0") == [] and advisories.covering(None, "1.0.0") == []


async def test_requirements_are_followed_all_the_way_down(tmp_path):
    packages = Packages(tmp_path)
    packages.core = {"aiohttp"}
    pypi = {
        "top/1.0/json": {
            "info": {
                "requires_dist": [
                    "mid>=1,<3",
                    "aiohttp",
                    "winonly; sys_platform == 'win32'",
                    "extra1; extra == 'x'",
                    "not a requirement !!",
                ]
            }
        },
        "mid/json": {
            "info": {"version": "3.0", "requires_dist": []},
            "releases": {"2.5": [{}], "3.0": [{}], "2.9rc1": [{}]},
        },
        "mid/2.5/json": {"info": {"requires_dist": ["deep[fast]==1.0", "top"]}},
        "deep/json": {
            "info": {"version": "1.0", "requires_dist": ["speed; extra == 'fast'", "slow; extra == 'other'"]},
            "releases": {"1.0": [{}]},
        },
        "speed/json": {"info": {"version": "0.1", "requires_dist": None}, "releases": {"0.1": [{}]}},
    }
    asked = []

    async def answer(session, url):
        asked.append(url.split("/pypi/")[1])
        return pypi[asked[-1]]

    packages._json = answer
    found = await packages._indirect(None, "Top", "1.0")
    # The newest that fits; what Home Assistant ships, other systems and extras nobody asked for are left out
    assert found == [
        {"name": "deep", "version": "1.0"},
        {"name": "mid", "version": "2.5"},
        {"name": "speed", "version": "0.1"},
    ]
    assert "aiohttp/json" not in asked and "winonly/json" not in asked and "slow/json" not in asked
