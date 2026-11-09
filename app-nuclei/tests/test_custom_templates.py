import pytest

from src import custom_templates
from src.custom_templates import CustomTemplates, TemplateError


@pytest.fixture
def templates(tmp_path, monkeypatch):
    monkeypatch.setattr(custom_templates, "CUSTOM_DIR", tmp_path / "custom-templates")

    async def ok(path):
        return True, "ok"

    return CustomTemplates(validate=ok)


async def test_save_list_read_delete(templates):
    await templates.save("my-check", "id: my-check\ninfo:\n  name: x\n")
    assert [t["name"] for t in templates.list()] == ["my-check"]
    assert "my-check" in templates.read("my-check")
    assert templates.dir_for_scan() is not None
    templates.delete("my-check")
    assert templates.list() == []
    assert templates.dir_for_scan() is None


async def test_bad_names_and_empty_body_refused(templates):
    for bad in ("../escape", "has space", "UPPER", ""):
        with pytest.raises(TemplateError):
            await templates.save(bad, "body")
    with pytest.raises(TemplateError):
        await templates.save("ok", "   ")


async def test_invalid_template_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(custom_templates, "CUSTOM_DIR", tmp_path / "ct")

    async def fail(path):
        return False, "line 2: bad"

    t = CustomTemplates(validate=fail)
    with pytest.raises(TemplateError):
        await t.save("x", "nonsense")
    assert t.list() == []
