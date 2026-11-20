"""Requirements from PyPI: which file is read, which version, and what a package's findings do to an integration."""

import io
import json
import tarfile
import time
import zipfile

import pytest

from src.analyzer import add_context, analyze, finish, parse_requirement
from src.packages import PackageError, Packages, choose, latest, read_sdist, read_wheel, scan_package

HOST = "https://files.pythonhosted.org/packages/"


def file(name: str, **more) -> dict:
    return {"filename": name, "url": HOST + name, **more}


def test_choose_prefers_what_can_be_read():
    pure, source, built = file("p-1.0-py3-none-any.whl"), file("p-1.0.tar.gz"), file("p-1.0-cp314-manylinux_x86_64.whl")
    assert choose([built, source, pure]) == (pure, False)
    assert choose([built, source]) == (source, True)  # the source is read; the install is compiled
    assert choose([file("p-1.0-cp314-win_amd64.whl"), built]) == (built, True)
    assert choose([file("p-1.0.zip"), source])[0] == source
    # A withdrawn file is passed over; a version withdrawn as a whole is still what gets installed
    assert choose([{**pure, "yanked": True}, source]) == (source, False)
    assert choose([{**pure, "yanked": True}]) == ({**pure, "yanked": True}, False)
    with pytest.raises(PackageError):
        choose([{"filename": "p-1.0-py3-none-any.whl", "url": "https://elsewhere.example/p.whl"}])


def test_latest_picks_like_pip():
    releases = {
        "1.0": [file("a")],
        "1.5": [file("b")],
        "2.0": [file("c")],
        "2.1rc1": [file("d")],
        "3.0": [],
        "junk": [file("e")],
    }
    assert latest(releases, "") == "2.0"
    assert latest(releases, ">=1.0,<2") == "1.5"
    assert latest({**releases, "2.0": [file("c", yanked=True)]}, "") == "1.5"
    with pytest.raises(PackageError):
        latest(releases, ">=9")
    with pytest.raises(PackageError):
        latest(releases, "not a spec")


def wheel(members: dict[str, bytes]) -> io.BytesIO:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, data in members.items():
            bundle.writestr(name, data)
    buffer.seek(0)
    return buffer


def test_read_wheel_and_sdist():
    files = read_wheel(
        wheel(
            {
                "lib/api.py": b"x = 1\n",
                "lib/tests/test_api.py": b"eval(x)",
                "lib-1.0.dist-info/METADATA": b"m",
                "lib/_c.so": b"\x7fELF" + b"\0" * 40,
            }
        )
    )
    assert set(files) == {"lib/api.py", "lib/_c.so"} and files["lib/_c.so"][1] == b"\x7fELF" + b"\0" * 12
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in {"lib-1.0/lib/api.py": b"y = 2\n", "lib-1.0/docs/conf.py": b"exec(z)"}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    buffer.seek(0)
    assert read_sdist(buffer) == {"lib/api.py": (6, b"y = 2\n")}
    with pytest.raises(PackageError):
        read_wheel(io.BytesIO(b"not a zip"))


def package(code: str, name: str = "vendorlib") -> dict:
    return scan_package(
        {"vendorlib/api.py": (len(code), code.encode()), "vendorlib/_speed.so": (9, b"\x7fELF")}, name, "1.2.0", False
    )


def test_a_package_is_judged_as_a_library():
    report = package(
        "import time\nBASE = 'https://api.vendor.example.io'\nasync def f():\n    time.sleep(1)\n    eval(x)\n"
    )
    found = {f["rule"] for f in report["findings"]}
    # No manifest, no blocking-call and no binary findings: those are for integrations
    assert found == {"code.eval", "code.compiled", "network.external_hosts"}
    assert report["key"] == "package:vendorlib==1.2.0" and report["scores"]["performance"] == 100


def integration(requirements: list[str], packages: dict, reports: dict) -> dict:
    manifest = {
        "domain": "demo",
        "version": "1",
        "codeowners": ["@a"],
        "issue_tracker": "x",
        "iot_class": "cloud_polling",
        "config_flow": True,
        "requirements": requirements,
    }
    data = json.dumps(manifest).encode()
    report = analyze({"manifest.json": (len(data), data)})
    add_context(report, packages=packages, package_reports=reports)
    return finish(report)


def test_a_package_counts_against_its_integration():
    bad = package(
        "import base64\nURL = 'https://collect.vendor.example.io/v1'\nexec(base64.b64decode(BLOB))\neval(x)\n"
    )
    report = integration(
        ["vendorlib>=1.0", "gone==9.9", "odd==1.0", "aiohttp==3.9"],
        {
            ("vendorlib", ">=1.0"): {"status": "scanned", "version": "1.2.0", "key": "vendorlib==1.2.0"},
            ("gone", "==9.9"): {"status": "missing"},
            ("odd", "==1.0"): {"status": "error", "reason": "larger than 40 MB"},
            ("aiohttp", "==3.9"): {"status": "core", "version": "3.9"},
        },
        {"vendorlib==1.2.0": bad},
    )
    found = {f["rule"]: f for f in report["findings"]}
    assert {
        "code.dependency_critical",
        "code.dependency_findings",
        "code.dependency_missing",
        "code.dependency_unread",
    } <= set(found)
    assert "vendorlib 1.2.0: Runs code that is hidden" in found["code.dependency_critical"]["where"][0]["note"]
    by_name = {r["name"]: r for r in report["requirements"]}
    assert by_name["vendorlib"]["resolved"] == "1.2.0" and by_name["vendorlib"]["grade"] == "F"
    assert by_name["vendorlib"]["package"] == "package:vendorlib==1.2.0"
    assert by_name["aiohttp"]["status"] == "core" and "package" not in by_name["aiohttp"]
    assert [(h["host"], h["via"]) for h in report["hosts"]] == [("collect.vendor.example.io", "vendorlib")]
    # A critical finding in a library is serious for the integration, but it is not the integration's own
    assert report["counts"]["critical"] == 0 and report["counts"]["high"] == 1


def test_a_clean_package_costs_nothing():
    clean = package("def f():\n    return 1\n")
    report = integration(
        ["vendorlib==1.2.0"],
        {("vendorlib", "==1.2.0"): {"status": "scanned", "version": "1.2.0", "key": "k"}},
        {"k": clean},
    )
    assert report["score"] == 100 and report["requirements"][0]["grade"] == "A"


async def test_core_packages_and_bad_names_are_not_fetched(tmp_path):
    packages = Packages(tmp_path)
    packages.core, packages._core_at = {"aiohttp", "py-yaml"}, time.time()
    requirements = [
        parse_requirement("aiohttp==3.9"),
        parse_requirement("Py_YAML>=6"),
        {**parse_requirement("x==1"), "name": "../x"},
    ]
    found = await packages.scan(None, [*requirements, parse_requirement("git+https://github.com/a/b.git")])
    assert [p["status"] for p in found.values()] == ["core", "core", "error"]


def test_reports_are_kept_between_starts(tmp_path):
    packages = Packages(tmp_path)
    packages.reports["vendorlib==1.2.0"] = package("x = 1\n")
    packages._save()
    assert "vendorlib==1.2.0" in Packages(tmp_path).reports
