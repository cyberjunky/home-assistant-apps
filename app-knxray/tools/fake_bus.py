"""A virtual KNX installation on the local network, for development: the devices of tools/installation.py
talking KNXnet/IP routing on a multicast port of its own (13671, so nothing reaches a real KNX network).

It sends what the house would send, answers group reads, answers a search for interfaces as a KNX IP
router, and its devices answer when asked directly: their mask, serial number, maker, order number,
programming mode and whether their application is loaded. Some are dead or misconfigured on purpose.
"""

import asyncio
import contextlib
import logging
import socket
import struct
import zlib
from typing import Any

from xknx import XKNX
from xknx.cemi import CEMIHandler
from xknx.dpt import DPTArray, DPTBinary
from xknx.io import ConnectionConfig, ConnectionType
from xknx.knxip import (
    HPAI,
    ConnectionStateRequest,
    ConnectionStateResponse,
    ConnectRequest,
    ConnectResponse,
    ConnectResponseData,
    DIBDeviceInformation,
    DIBServiceFamily,
    DIBSuppSVCFamilies,
    DisconnectRequest,
    DisconnectResponse,
    ErrorCode,
    KNXIPFrame,
    SearchRequest,
    SearchRequestExtended,
    SearchResponse,
    SearchResponseExtended,
    TunnellingRequest,
)
from xknx.knxip.knxip_enum import TunnellingLayer
from xknx.telegram import GroupAddress, IndividualAddress, Telegram, apci, tpci

from src.busmon import checksum
from tools import installation as house

logger = logging.getLogger(__name__)

MULTICAST_GROUP = "224.0.23.12"
MULTICAST_PORT = 13671
BUSMONITOR_PORT = 13672  # the virtual interface is found at port 3671, which nothing answers: enter this one by hand
ROUTER_ADDRESS = "1.0.1"


class _DeviceHandler(CEMIHandler):
    """Hands every telegram addressed to one of the virtual devices to the bus, not only those for the
    router itself."""

    bus: "FakeBus"

    def telegram_received(self, telegram: Telegram) -> None:
        if isinstance(telegram.tpci, tpci.TDataGroup):
            self.bus.group_telegram(telegram)
        else:
            self.bus.management_telegram(telegram)


class FakeBus:
    def __init__(self) -> None:
        self.simulation = house.Simulation()
        self.devices = {**house.DEVICES, **house.EXTRA_DEVICES}
        self.xknx = XKNX(
            connection_config=ConnectionConfig(
                connection_type=ConnectionType.ROUTING, individual_address=ROUTER_ADDRESS, multicast_port=MULTICAST_PORT
            ),
            multicast_port=MULTICAST_PORT,
        )
        handler = _DeviceHandler(self.xknx)
        handler.bus = self
        self.xknx.cemi_handler = handler
        # (device, who asked) -> the next sequence number the device sends with
        self.sequences: dict[tuple[str, str], int] = {}
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        await self.xknx.start()
        self._spawn(self._run())
        self._spawn(self._search_responder())
        await self._busmonitor_server()
        logger.info("Virtual KNX bus on %s:%s", MULTICAST_GROUP, MULTICAST_PORT)

    def _spawn(self, job: Any) -> None:
        task = asyncio.create_task(job)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _send(self, telegram: Telegram) -> None:
        with contextlib.suppress(Exception):
            await self.xknx.cemi_handler.send_telegram(telegram)

    async def _group(self, source: str, address: str, kind: str, payload: Any) -> None:
        if kind == "read":
            body: Any = apci.GroupValueRead()
        else:
            value = DPTBinary(payload) if isinstance(payload, int) else DPTArray(payload)
            body = apci.GroupValueWrite(value) if kind == "write" else apci.GroupValueResponse(value)
        await self._send(
            Telegram(destination_address=GroupAddress(address), source_address=IndividualAddress(source), payload=body)
        )
        self._on_the_wire(source, address, kind, payload)

    async def _run(self) -> None:
        await asyncio.sleep(2)
        while True:
            for source, address, kind, payload in self.simulation.step():
                if source == house.HA_ADDRESS and kind == "read":
                    continue  # reads are answered when somebody really asks
                if kind == "response":
                    continue
                await self._group(source, address, kind, payload)
                await asyncio.sleep(0.03)
            await asyncio.sleep(0.4)

    # ---- group reads ----

    def group_telegram(self, telegram: Telegram) -> None:
        if isinstance(telegram.payload, apci.GroupValueRead) and str(telegram.source_address) not in self.devices:
            address = str(telegram.destination_address)

            async def answer() -> None:
                for source, payload in self.simulation.answer(address):
                    await asyncio.sleep(0.05)
                    await self._group(source, address, "response", payload)

            self._spawn(answer())

    # ---- devices asked directly ----

    def management_telegram(self, telegram: Telegram) -> None:
        asker = str(telegram.source_address)
        if isinstance(telegram.tpci, tpci.TDataBroadcast):
            if isinstance(telegram.payload, apci.IndividualAddressRead):
                for device in house.PROGMODE:
                    self._spawn(
                        self._send(
                            Telegram(
                                destination_address=GroupAddress(0),
                                source_address=IndividualAddress(device),
                                tpci=tpci.TDataBroadcast(),
                                payload=apci.IndividualAddressResponse(),
                            )
                        )
                    )
            elif isinstance(telegram.payload, apci.IndividualAddressSerialRead):
                # The device with that serial number says where it is
                for device in self.devices:
                    if device not in house.OFFLINE and self._property(device, 0, 11) == telegram.payload.serial:
                        self._spawn(
                            self._send(
                                Telegram(
                                    destination_address=GroupAddress(0),
                                    source_address=IndividualAddress(device),
                                    tpci=tpci.TDataBroadcast(),
                                    payload=apci.IndividualAddressSerialResponse(
                                        serial=telegram.payload.serial, address=IndividualAddress(device)
                                    ),
                                )
                            )
                        )
            return
        device = str(telegram.destination_address)
        if device not in self.devices or device in house.OFFLINE:
            return
        key = (device, asker)
        if isinstance(telegram.tpci, tpci.TConnect):
            self.sequences[key] = 0
        elif isinstance(telegram.tpci, tpci.TDisconnect):
            self.sequences.pop(key, None)
        elif isinstance(telegram.tpci, tpci.TDataConnected) and key in self.sequences:
            self._spawn(self._answer(device, asker, telegram))
        elif (
            isinstance(telegram.tpci, tpci.TDataIndividual)
            and isinstance(telegram.payload, apci.DeviceDescriptorRead)
            and device != "1.1.30"
        ):
            # Asked without a connection: every device but the old power supply answers
            answer = apci.DeviceDescriptorResponse(descriptor=0, value=self._mask(device))
            self._spawn(self._reply(device, asker, tpci.TDataIndividual(), answer))

    async def _reply(self, device: str, asker: str, control: Any, payload: Any = None) -> None:
        await self._send(
            Telegram(
                destination_address=IndividualAddress(asker),
                source_address=IndividualAddress(device),
                tpci=control,
                payload=payload,
            )
        )

    async def _answer(self, device: str, asker: str, telegram: Telegram) -> None:
        await self._reply(device, asker, tpci.TAck(telegram.tpci.sequence_number))
        request = telegram.payload
        response: Any = None
        if isinstance(request, apci.DeviceDescriptorRead):
            response = apci.DeviceDescriptorResponse(descriptor=0, value=self._mask(device))
        elif isinstance(request, apci.PropertyValueRead):
            data = self._property(device, request.object_index, request.property_id)
            count = 1 if data else 0
            if request.object_index == 1 and request.property_id == 23 and device != "1.1.30":
                # The address table: element 0 is how many entries it has
                table = self._table(device)
                if request.start_index == 0:
                    data, count = len(table).to_bytes(2, "big"), 1
                else:
                    chosen = table[request.start_index - 1 : request.start_index - 1 + request.count]
                    data, count = b"".join(v.to_bytes(2, "big") for v in chosen), len(chosen)
            response = apci.PropertyValueResponse(
                object_index=request.object_index,
                property_id=request.property_id,
                count=count,
                start_index=request.start_index,
                data=data,
            )
        elif isinstance(request, apci.PropertyDescriptionRead):
            # Four objects (device, address table, association table, application), a few properties each
            pids = {0: [1, 9, 11, 12, 14, 15, 30, 54, 56], 1: [1, 5, 23], 2: [1, 5, 7], 3: [1, 5, 13], 4: [1, 5, 7]}
            if device == ROUTER_ADDRESS:
                pids[5] = [1, 52, 53, 54, 55, 61, 63]
            listed = pids.get(request.object_index, []) if device != "1.1.30" else []
            found = listed[request.property_index] if request.property_index < len(listed) else 0
            response = apci.PropertyDescriptionResponse(
                object_index=request.object_index,
                property_id=found,
                property_index=request.property_index,
                type_=0x11,
                max_count=1,
                access=0x30,
            )
        elif isinstance(request, apci.MemoryRead) and device != "1.1.30":
            # The association table at 4400 and the group object table at 4800
            image = self._memory(device)
            data = bytes(image.get(request.address + i, 0) for i in range(request.count))
            response = apci.MemoryResponse(address=request.address, count=request.count, data=data)
        elif isinstance(request, apci.MemoryRead) and device == "1.1.30":
            # The old power supply keeps its (empty) address table in memory: one entry, its own address
            memory = {0x0116: bytes([1]), 0x0117: (1 << 12 | 1 << 8 | 30).to_bytes(2, "big")}
            data = b"".join(memory.get(request.address + i, b"\0")[:1] for i in range(request.count))
            if request.address == 0x0117:
                data = memory[0x0117][: request.count]
            response = apci.MemoryResponse(address=request.address, count=request.count, data=data)
        if response is None:
            return
        sequence = self.sequences.get((device, asker), 0)
        self.sequences[(device, asker)] = (sequence + 1) & 0x0F
        await self._reply(device, asker, tpci.TDataConnected(sequence), response)

    @staticmethod
    def _table(device: str) -> list[int]:
        """The group addresses a device holds: what the project links to it, except that the kitchen push
        button was programmed from an older project and also holds the garage switch."""
        held = {ga for dev, *_rest, links in house.OBJECTS if dev == device for ga in links}
        if device == "1.1.11":
            held.add("1/0/8")
        return sorted(GroupAddress(ga).raw for ga in held)

    def _memory(self, device: str) -> dict[int, int]:
        """The two tables a System B device keeps in memory: which object is linked to which address, and
        the flags of each object. The living room controller's setpoint object lost its write flag, and the
        kitchen push button's first rocker is also linked to the garage switch."""
        table = self._table(device)
        objects = [
            (number, flags, links) for dev, number, _t, _f, _m, _s, flags, links in house.OBJECTS if dev == device
        ]
        pairs = []
        for number, _flags, links in objects:
            extra = ["1/0/8"] if device == "1.1.11" and number == 0 else []
            for ga in [*links, *extra]:
                pairs.append((table.index(GroupAddress(ga).raw) + 1, number + 1))
        image: dict[int, int] = {}

        def put(where: int, data: bytes) -> None:
            image.update({where + i: byte for i, byte in enumerate(data)})

        put(0x4400, len(pairs).to_bytes(2, "big") + b"".join(bytes(pair) for pair in pairs))
        highest = max((number for number, _f, _l in objects), default=-1) + 1
        put(0x4800, highest.to_bytes(2, "big"))
        bits = {"communication": 0x04, "read": 0x08, "write": 0x10, "transmit": 0x40, "update": 0x80}
        for number, flags, links in objects:
            byte = sum(bit for name, bit in bits.items() if flags.get(name))
            if device == "1.1.20" and number == 1:
                byte &= ~0x10
            put(0x4800 + 2 + number * 2, bytes([byte if links else 0, 1]))
        return image

    @staticmethod
    def _mask(device: str) -> int:
        return 0x091A if device == ROUTER_ADDRESS else 0x0012 if device == "1.1.30" else 0x07B0

    def _property(self, device: str, index: int, pid: int) -> bytes:
        """What a device says about itself. An old BCU 1 (the power supply) knows no properties."""
        if device == "1.1.30":
            return b""
        maker = house.MAKER_IDS.get(self.devices[device][1], 250)
        if index == 0:
            if pid == 1:
                return (0).to_bytes(2, "big")
            if pid == 11:
                return maker.to_bytes(2, "big") + struct.pack(">I", zlib.crc32(device.encode()))
            if pid == 12:
                return maker.to_bytes(2, "big")
            if pid == 15:
                return (
                    (self.devices[device][3].replace(" ", "").replace("/", "").replace(".", "")[:10] or "UNKNOWN")
                    .encode()
                    .ljust(10, b"\0")
                )
            if pid == 9:
                return bytes([0x13])
            if pid == 54:
                return bytes([1 if device in house.PROGMODE else 0])
            if pid == 14:
                return bytes([0])
            if pid == 30:
                return (3 + zlib.crc32(device.encode()) % 9).to_bytes(2, "big")
            if pid == 56:
                return (55).to_bytes(2, "big")
        # Objects 1 to 3: the address table, the association table, the application
        if index in (2, 4) and pid == 7:
            return (0x4400 if index == 2 else 0x4800).to_bytes(4, "big")
        if index == 4:
            return {1: (9).to_bytes(2, "big"), 5: bytes([1])}.get(pid, b"")
        if index == 5 and device == ROUTER_ADDRESS:
            # The router object: everything is passed towards the line (a commissioning setting left on)
            return {
                1: (6).to_bytes(2, "big"),
                52: b"\x03",
                53: b"\x03",
                54: b"\x0f",
                55: b"\x05",
                61: b"\x06",
                63: b"\x00",
            }.get(pid, b"")
        if 1 <= index <= 3:
            if pid == 1:
                return index.to_bytes(2, "big")
            if pid == 5:
                return bytes([0 if device in house.UNLOADED and index == 3 else 1])
            if pid == 13 and index == 3:
                return maker.to_bytes(2, "big") + bytes([0x10, 0x4A, 0x21])
        return b""

    # ---- bus monitor mode ----

    async def _busmonitor_server(self) -> None:
        """A tunnel in bus monitor mode on a port of its own: whoever connects gets every frame of the
        virtual line with its acknowledgement."""
        bus = self

        class Server(asyncio.DatagramProtocol):
            def connection_made(self, transport: Any) -> None:
                bus._monitor_transport = transport

            def datagram_received(self, data: bytes, addr: tuple) -> None:
                try:
                    frame, _rest = KNXIPFrame.from_knx(data)
                except Exception:
                    return
                body, answer = frame.body, None
                if isinstance(body, ConnectRequest):
                    wanted = body.cri.knx_layer is TunnellingLayer.BUSMONITOR_LAYER
                    answer = ConnectResponse(
                        communication_channel=7,
                        status_code=ErrorCode.E_NO_ERROR if wanted else ErrorCode.E_TUNNELLING_LAYER,
                        data_endpoint=HPAI(),
                        crd=ConnectResponseData(individual_address=IndividualAddress("1.0.240")),
                    )
                    bus._monitor_client, bus._monitor_sequence = (addr if wanted else None), 0
                elif isinstance(body, ConnectionStateRequest):
                    answer = ConnectionStateResponse(communication_channel_id=7)
                elif isinstance(body, DisconnectRequest):
                    answer = DisconnectResponse(communication_channel_id=7)
                    bus._monitor_client = None
                if answer is not None:
                    bus._monitor_transport.sendto(KNXIPFrame.init_from_body(answer).to_knx(), addr)

        self._monitor_client: tuple | None = None
        self._monitor_sequence = 0
        await asyncio.get_running_loop().create_datagram_endpoint(Server, local_addr=("0.0.0.0", BUSMONITOR_PORT))

    def _indication(self, frame: bytes, status: int = 0) -> None:
        if self._monitor_client is None:
            return
        self._monitor_tick = (getattr(self, "_monitor_tick", 0) + 1) % 8
        cemi = bytes([0x2B, 3, 0x03, 0x01, status | self._monitor_tick]) + frame
        request = TunnellingRequest(communication_channel_id=7, sequence_counter=self._monitor_sequence, raw_cemi=cemi)
        self._monitor_sequence = (self._monitor_sequence + 1) & 0xFF
        self._monitor_transport.sendto(KNXIPFrame.init_from_body(request).to_knx(), self._monitor_client)

    def _on_the_wire(self, source: str, address: str, kind: str, payload: Any) -> None:
        """A group telegram as frames on the line: the telegram and its acknowledgement. Nobody listens to
        the stranger's address, so it is sent four times and never acknowledged; the garage actuator is
        sometimes too busy; now and then a frame arrives damaged."""
        if self._monitor_client is None:
            return
        code = {"read": 0x00, "response": 0x40, "write": 0x80}[kind]
        if payload is None:
            tpdu = bytes([0x00, code])
        elif isinstance(payload, int):
            tpdu = bytes([0x00, code | (payload & 0x3F)])
        else:
            tpdu = bytes([0x00, code]) + bytes(payload)
        body = (
            IndividualAddress(source).raw.to_bytes(2, "big")
            + GroupAddress(address).raw.to_bytes(2, "big")
            + bytes([0x80 | (6 << 4) | (len(tpdu) - 1)])
            + tpdu
        )

        def frame(repeated: bool) -> bytes:
            data = bytes([0x9C if repeated else 0xBC]) + body
            return data + bytes([checksum(data)])

        if address == "7/3/1":
            for attempt in range(4):
                self._indication(frame(attempt > 0))
            return
        damaged = self.simulation.random.random() < 0.004
        self._indication(frame(False), 0x40 if damaged else 0)
        if address == "1/0/8" and self.simulation.random.random() < 0.5:
            self._indication(bytes([0xC0]))
            self._indication(frame(True))
        self._indication(bytes([0xCC]))

    # ---- being found ----

    async def _search_responder(self) -> None:
        """Answer a search for KNX IP interfaces the way a router does."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        with contextlib.suppress(AttributeError, OSError):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.bind(("", MULTICAST_PORT))
        sock.setsockopt(
            socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(MULTICAST_GROUP) + socket.inet_aton("0.0.0.0")
        )
        sock.setblocking(False)
        loop = asyncio.get_running_loop()
        while True:
            data, _sender = await loop.sock_recvfrom(sock, 1024)
            try:
                frame, _rest = KNXIPFrame.from_knx(data)
            except Exception:
                continue
            body = frame.body
            if not isinstance(body, SearchRequest | SearchRequestExtended):
                continue
            target = (body.discovery_endpoint.ip_addr, body.discovery_endpoint.port)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect(target)
                own_ip = probe.getsockname()[0]
            info = DIBDeviceInformation()
            info.name = "KNX IP Router 751 (virtual)"
            info.individual_address = IndividualAddress(ROUTER_ADDRESS)
            info.serial_number = "00:c5:02:03:8a:11"
            info.mac_address = "00:24:6d:02:8a:11"
            families = DIBSuppSVCFamilies()
            families.families = [
                DIBSuppSVCFamilies.Family(DIBServiceFamily.CORE, 2),
                DIBSuppSVCFamilies.Family(DIBServiceFamily.DEVICE_MANAGEMENT, 2),
                DIBSuppSVCFamilies.Family(DIBServiceFamily.TUNNELING, 2),
                DIBSuppSVCFamilies.Family(DIBServiceFamily.ROUTING, 2),
            ]
            kind = SearchResponseExtended if isinstance(body, SearchRequestExtended) else SearchResponse
            response = kind(control_endpoint=HPAI(ip_addr=own_ip, port=3671))
            response.dibs = [info, families]
            with contextlib.suppress(OSError):
                sock.sendto(KNXIPFrame.init_from_body(response).to_knx(), target)
