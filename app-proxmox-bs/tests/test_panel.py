"""The sidebar page: PBS passed through Home Assistant's ingress, against a stand-in PBS."""

import asyncio

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src import panel

SEEN = web.AppKey("seen", dict)
BASE = "/api/hassio_ingress/tok"
INGRESS = {"X-Ingress-Path": BASE}

PBS_PAGE = """<!DOCTYPE html><html><head><title>PBS</title>
<link rel="stylesheet" href="/widgettoolkit/css/ext6-pmx.css" />
<script type='text/javascript' src='/js/proxmox-backup-gui.js'></script>
<a href="//example.org/x">x</a></head><body></body></html>"""


def test_page_links_go_below_the_ingress_path_and_the_shim_comes_first():
    html = panel.rewrite_page(PBS_PAGE, BASE + "/pbs", "/*shim*/")
    assert 'href="/api/hassio_ingress/tok/pbs/widgettoolkit/css/ext6-pmx.css"' in html
    assert "src='/api/hassio_ingress/tok/pbs/js/proxmox-backup-gui.js'" in html
    assert 'href="//example.org/x"' in html
    head = html.index("<head>") + len("<head>")
    assert html[head:].startswith(
        '<meta charset="utf-8"><script>window.PBSAPP_BASE = "/api/hassio_ingress/tok/pbs";\n/*shim*/</script>'
    )


def test_login_cookie_is_kept_on_the_ingress_path_and_given_back_under_its_own_name():
    cookie = panel.rewrite_set_cookie(
        "__Host-PBSAuthCookie=; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Secure; Path=/", "/b/pbs"
    )
    assert cookie == "PBSAppAuth=; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Path=/b/pbs/"
    # The readable cookie PBS's page sets itself keeps its name
    assert panel.rewrite_set_cookie("PBSLangCookie=en; Path=/", "/b/pbs") == "PBSLangCookie=en; Path=/b/pbs/"
    assert panel.request_cookies(
        "PBSLangCookie=en; PBSAuthCookie=PBS:root@pam:1::ticketinfo; PBSAppAuth=PBS:root@pam:1"
    ) == (
        "PBSLangCookie=en; PBSAuthCookie=PBS:root@pam:1::ticketinfo; PBSAppAuth=PBS:root@pam:1; __Host-PBSAuthCookie=PBS:root@pam:1"
    )
    assert panel.request_cookies("other=1") == "other=1"


def test_redirects_to_root_paths_stay_below_the_ingress_path():
    assert panel.location("/?console=shell", "/b/pbs") == "/b/pbs/?console=shell"
    assert panel.location("/b/pbs/x", "/b/pbs") == "/b/pbs/x"
    assert panel.location("https://example.org/", "/b/pbs") == "https://example.org/"


async def fake_pbs() -> web.Application:
    app = web.Application()
    seen = {}

    async def index(request):
        seen["cookie"] = request.headers.get("Cookie")
        return web.Response(text=PBS_PAGE, content_type="text/html")

    async def gui(request):
        return web.Response(
            text="if (window.location.port.toString() !== '8007') { host += port; }", content_type="text/javascript"
        )

    async def ticket(request):
        data = await request.post()
        response = web.json_response({"data": {"ticket": "PBS:" + data["username"]}})
        response.headers.add("Set-Cookie", "__Host-PBSAuthCookie=; Secure; Path=/")
        return response

    async def termproxy(request):
        seen["termproxy"] = request.headers.get("Content-Type")
        return web.json_response({"data": {}})

    async def domains(request):
        seen["domains"] = request.headers.get("Content-Type")
        return web.json_response({"data": []})

    async def socket(request):
        ws = web.WebSocketResponse(protocols=["binary"])
        await ws.prepare(request)
        async for msg in ws:
            await ws.send_bytes(b"echo:" + msg.data)
        return ws

    app.router.add_get("/", index)
    app.router.add_get("/js/proxmox-backup-gui.js", gui)
    app.router.add_post("/api2/json/access/ticket", ticket)
    app.router.add_post("/api2/json/nodes/localhost/termproxy", termproxy)
    app.router.add_get("/api2/json/access/domains", domains)
    app.router.add_get("/api2/json/nodes/localhost/vncwebsocket", socket)
    app[SEEN] = seen
    return app


def run(check):
    async def main():
        pbs = TestServer(await fake_pbs())
        await pbs.start_server()
        sidebar = panel.Panel(pbs_url=str(pbs.make_url("")).rstrip("/"))
        client = TestClient(TestServer(sidebar.app))
        await client.start_server()
        try:
            await check(client, pbs.app[SEEN])
        finally:
            await client.close()
            await pbs.close()

    asyncio.run(main())


def test_pbs_page_through_the_sidebar():
    async def check(client, seen):
        r = await client.get("/pbs/", headers={**INGRESS, "Cookie": "PBSAppAuth=t1"})
        html = await r.text()
        assert r.status == 200
        assert "window.PBSAPP_BASE" in html and f"{BASE}/pbs/js/proxmox-backup-gui.js" in html
        assert seen["cookie"] == "PBSAppAuth=t1; __Host-PBSAuthCookie=t1"
        r = await client.get("/pbs", headers=INGRESS, allow_redirects=False)
        assert r.status == 302 and r.headers["Location"] == f"{BASE}/pbs/"

    run(check)


def test_connection_information_leaves_out_home_assistants_port():
    async def check(client, seen):
        text = await (await client.get("/pbs/js/proxmox-backup-gui.js", headers=INGRESS)).text()
        assert "if (false)" in text

    run(check)


def test_login_and_its_cookie():
    async def check(client, seen):
        r = await client.post("/pbs/api2/json/access/ticket", data={"username": "root@pam"}, headers=INGRESS)
        assert (await r.json())["data"]["ticket"] == "PBS:root@pam"
        assert r.headers["Set-Cookie"] == f"PBSAppAuth=; Path={BASE}/pbs/"

    run(check)


def test_requests_without_a_body_never_reach_pbs_as_octet_stream():
    async def check(client, seen):
        octet = {**INGRESS, "Content-Type": "application/octet-stream"}
        assert (await client.get("/pbs/api2/json/access/domains", headers=octet)).status == 200
        assert seen["domains"] is None
        form = {**INGRESS, "Content-Type": "application/x-www-form-urlencoded"}
        assert (await client.post("/pbs/api2/json/nodes/localhost/termproxy", headers=form)).status == 200
        assert seen["termproxy"] == "application/x-www-form-urlencoded"
        assert (await client.post("/pbs/api2/json/nodes/localhost/termproxy", headers=octet)).status == 200
        assert seen["termproxy"] == "application/x-www-form-urlencoded"

    run(check)


def test_console_websocket_is_passed_both_ways():
    async def check(client, seen):
        ws = await client.ws_connect("/pbs/api2/json/nodes/localhost/vncwebsocket?port=5900", protocols=["binary"])
        assert ws.protocol == "binary"
        await ws.send_bytes(b"ls")
        msg = await ws.receive(timeout=5)
        assert msg.type == aiohttp.WSMsgType.BINARY and msg.data == b"echo:ls"
        await ws.close()

    run(check)


def test_page_and_status():
    async def check(client, seen):
        page = await (await client.get("/")).text()
        assert "__VERSION__" not in page and 'href="static/app.css' in page and 'src="/' not in page
        assert (await client.get("/static/shim.js")).status == 200
        status = await (await client.get("/api/status")).json()
        assert status["pbs"] is False  # no PBS on port 8007 here

    run(check)


def test_only_home_assistants_ingress_under_home_assistant(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "x")

    async def check(client, seen):
        assert (await client.get("/pbs/", headers=INGRESS)).status == 403

    run(check)
