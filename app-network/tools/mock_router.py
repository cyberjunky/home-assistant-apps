"""A stand-in internet router for development and the tests: a UPnP Internet Gateway Device.

    python3 -m tools.mock_router              # a FRITZ!Box on 127.0.0.1:8126 (set the option router: 127.0.0.1:8126)
    python3 -m tools.mock_router --generic    # a plain UPnP router (miniupnpd): 32-bit counters, no rates

Traffic follows the time of day with bursts, so the charts have something to show. A plain router
also answers SSDP on the port given with --ssdp.
"""

import argparse
import asyncio
import math
import random
import time

from aiohttp import web

STARTED = time.time()
WAN_IP = "198.51.100.23"
# The stand-in's state, to change from a test
STATE = web.AppKey("state", dict)


class Traffic:
    """Counters that grow with a believable rate: busier in the evening, a download now and then."""

    def __init__(self, wide: bool):
        self.wide = wide
        self.rx = 7_400_000_000 if not wide else 143_000_000_000
        self.tx = 1_200_000_000 if not wide else 16_700_000_000
        self.at = time.time()
        self.down = self.up = 0.0

    def tick(self) -> None:
        now = time.time()
        dt = max(0.0, now - self.at)
        self.at = now
        hour = time.localtime(now).tm_hour + time.localtime(now).tm_min / 60
        busy = 0.35 + 0.65 * max(0.0, math.sin((hour - 6) / 24 * 2 * math.pi))
        self.down = busy * random.uniform(60_000, 900_000) * (8 if random.random() < 0.08 else 1)
        self.up = busy * random.uniform(15_000, 160_000)
        self.rx += int(self.down * dt)
        self.tx += int(self.up * dt)
        if not self.wide:
            self.rx %= 2**32
            self.tx %= 2**32


def description(generic: bool) -> str:
    name = "Mock miniupnpd router" if generic else "FRITZ!Box 7590"
    maker = "OpenWrt" if generic else "AVM"
    prefix = "/ctl" if generic else "/igdupnp/control"
    return f"""<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
<specVersion><major>1</major><minor>0</minor></specVersion>
<device>
<deviceType>urn:schemas-upnp-org:device:InternetGatewayDevice:1</deviceType>
<friendlyName>{name}</friendlyName><manufacturer>{maker}</manufacturer>
<modelName>{name}</modelName><modelNumber>{"23.05" if generic else "154.08.00"}</modelNumber>
<deviceList><device>
<deviceType>urn:schemas-upnp-org:device:WANDevice:1</deviceType>
<serviceList><service>
<serviceType>urn:schemas-upnp-org:service:WANCommonInterfaceConfig:1</serviceType>
<controlURL>{prefix}/CommonIfCfg</controlURL></service></serviceList>
<deviceList><device>
<deviceType>urn:schemas-upnp-org:device:WANConnectionDevice:1</deviceType>
<serviceList><service>
<serviceType>urn:schemas-upnp-org:service:{"WANPPPConnection" if generic else "WANIPConnection"}:1</serviceType>
<controlURL>{prefix}/WANConn</controlURL></service></serviceList>
</device></deviceList>
</device></deviceList>
</device>
</root>"""


def soap(action: str, service: str, values: dict) -> web.Response:
    args = "".join(f"<{k}>{v}</{k}>" for k, v in values.items())
    body = (
        '<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
        f'<u:{action}Response xmlns:u="{service}">{args}</u:{action}Response></s:Body></s:Envelope>'
    )
    return web.Response(text=body, content_type="text/xml")


def upnp_error() -> web.Response:
    body = (
        '<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
        "<s:Fault><faultcode>s:Client</faultcode><faultstring>UPnPError</faultstring><detail>"
        '<UPnPError xmlns="urn:schemas-upnp-org:control-1-0"><errorCode>401</errorCode>'
        "<errorDescription>Invalid Action</errorDescription></UPnPError></detail></s:Fault></s:Body></s:Envelope>"
    )
    return web.Response(status=500, text=body, content_type="text/xml")


# What programs on the network asked the FRITZ!Box to open (UPnP); the plain router tells nothing
MAPPINGS = [
    {
        "NewRemoteHost": "",
        "NewExternalPort": 3074,
        "NewProtocol": "UDP",
        "NewInternalPort": 3074,
        "NewInternalClient": "192.168.2.61",
        "NewEnabled": 1,
        "NewPortMappingDescription": "Xbox (3074) UDP",
        "NewLeaseDuration": 0,
    },
    {
        "NewRemoteHost": "",
        "NewExternalPort": 51413,
        "NewProtocol": "TCP",
        "NewInternalPort": 51413,
        "NewInternalClient": "192.168.2.144",
        "NewEnabled": 1,
        "NewPortMappingDescription": "Transmission at 51413",
        "NewLeaseDuration": 3600,
    },
]


def index_error() -> web.Response:
    body = (
        '<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
        "<s:Fault><faultcode>s:Client</faultcode><faultstring>UPnPError</faultstring><detail>"
        '<UPnPError xmlns="urn:schemas-upnp-org:control-1-0"><errorCode>713</errorCode>'
        "<errorDescription>SpecifiedArrayIndexInvalid</errorDescription></UPnPError></detail></s:Fault></s:Body></s:Envelope>"
    )
    return web.Response(status=500, text=body, content_type="text/xml")


def make_app(generic: bool = False, connected: bool = True) -> web.Application:
    traffic = Traffic(wide=not generic)
    state = {"connected": connected}
    app = web.Application()
    app[STATE] = state
    path = "/rootDesc.xml" if generic else "/igddesc.xml"

    async def desc(request):
        return web.Response(text=description(generic), content_type="text/xml")

    async def control(request):
        action_header = request.headers.get("SOAPAction", "").strip('"')
        service, _, action = action_header.partition("#")
        traffic.tick()
        uptime = int(time.time() - STARTED) + 3 * 86400 + 4321
        if action == "GetAddonInfos" and not generic:
            return soap(
                action,
                service,
                {
                    "NewByteSendRate": int(traffic.up),
                    "NewByteReceiveRate": int(traffic.down),
                    "NewTotalBytesSent": traffic.tx % 2**32,
                    "NewTotalBytesReceived": traffic.rx % 2**32,
                    "NewDNSServer1": "192.0.2.53",
                    "NewDNSServer2": "192.0.2.54",
                    "NewX_AVM_DE_TotalBytesSent64": traffic.tx,
                    "NewX_AVM_DE_TotalBytesReceived64": traffic.rx,
                },
            )
        if action == "GetTotalBytesReceived":
            return soap(action, service, {"NewTotalBytesReceived": traffic.rx})
        if action == "GetTotalBytesSent":
            return soap(action, service, {"NewTotalBytesSent": traffic.tx})
        if action == "GetCommonLinkProperties":
            return soap(
                action,
                service,
                {
                    "NewWANAccessType": "DSL" if generic else "Ethernet",
                    "NewLayer1UpstreamMaxBitRate": 40_000_000 if generic else 1_000_000_000,
                    "NewLayer1DownstreamMaxBitRate": 200_000_000 if generic else 1_000_000_000,
                    "NewPhysicalLinkStatus": "Up" if state["connected"] else "Down",
                },
            )
        if action == "GetStatusInfo":
            return soap(
                action,
                service,
                {
                    "NewConnectionStatus": "Connected" if state["connected"] else "Disconnected",
                    "NewLastConnectionError": "ERROR_NONE",
                    "NewUptime": uptime if state["connected"] else 0,
                },
            )
        if action == "GetExternalIPAddress":
            return soap(action, service, {"NewExternalIPAddress": WAN_IP if state["connected"] else ""})
        if action == "X_AVM_DE_GetExternalIPv6Address" and not generic:
            return soap(
                action,
                service,
                {"NewExternalIPv6Address": "2001:db8:1::1", "NewPrefixLength": 64, "NewValidLifetime": 0},
            )
        if action == "X_AVM_DE_GetIPv6Prefix" and not generic:
            return soap(action, service, {"NewIPv6Prefix": "2001:db8:42::", "NewPrefixLength": 56})
        if action == "GetGenericPortMappingEntry" and not generic:
            text = await request.text()
            index = int(text.split("<NewPortMappingIndex>")[1].split("<")[0])
            return soap(action, service, MAPPINGS[index]) if index < len(MAPPINGS) else index_error()
        return upnp_error()

    async def toggle(request):
        state["connected"] = request.match_info["state"] == "up"
        return web.json_response(state)

    app.router.add_get(path, desc)
    app.router.add_post("/igdupnp/control/{name}", control)
    app.router.add_post("/ctl/{name}", control)
    app.router.add_post("/mock/wan/{state:up|down}", toggle)
    return app


class SsdpResponder(asyncio.DatagramProtocol):
    """Answers an M-SEARCH with where the description is, as a plain router does."""

    def __init__(self, location: str):
        self.location = location
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        if data.startswith(b"M-SEARCH"):
            answer = (
                "HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=120\r\n"
                f"LOCATION: {self.location}\r\n"
                "ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n"
                "USN: uuid:mock::urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n\r\n"
            )
            self.transport.sendto(answer.encode(), addr)


async def serve(port: int, generic: bool, ssdp: int | None) -> None:
    runner = web.AppRunner(make_app(generic))
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    print(f"Mock {'UPnP router' if generic else 'FRITZ!Box'} on http://127.0.0.1:{port}")
    if ssdp:
        loop = asyncio.get_running_loop()
        await loop.create_datagram_endpoint(
            lambda: SsdpResponder(f"http://127.0.0.1:{port}/rootDesc.xml"), local_addr=("127.0.0.1", ssdp)
        )
        print(f"SSDP on udp 127.0.0.1:{ssdp}")
    await asyncio.Event().wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8126)
    parser.add_argument("--generic", action="store_true")
    parser.add_argument("--ssdp", type=int)
    a = parser.parse_args()
    asyncio.run(serve(a.port, a.generic, a.ssdp))
