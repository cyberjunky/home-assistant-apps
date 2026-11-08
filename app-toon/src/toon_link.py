"""The one connection to the Toon: a single SSH session carrying everything.

The bus (port 1337) only listens on the Toon itself, so SSH is needed anyway, and all data comes
from the bus or from a read-only command. The Toon's web server isn't used at all: more web requests
seem to bring its hangs closer. That leaves one connection, one set of credentials and one place
where a lost connection is noticed.

A Toon runs Dropbear 0.51, which only speaks old algorithms. They are enabled for this connection
only; there is no alternative as long as the Toon runs this software.
"""

import asyncio
import logging
import warnings
from pathlib import Path

import asyncssh
from cryptography.utils import CryptographyDeprecationWarning

from .config import DATA_DIR, Config

logger = logging.getLogger(__name__)

# The key exchange a Toon needs is deprecated in cryptography, which warns on every connection
warnings.filterwarnings("ignore", category=CryptographyDeprecationWarning)

LEGACY_ALGORITHMS = {
    "kex_algs": ["diffie-hellman-group14-sha1", "diffie-hellman-group1-sha1"],
    "encryption_algs": ["aes128-cbc", "3des-cbc"],
    "mac_algs": ["hmac-sha1"],
    "server_host_key_algs": ["ssh-rsa"],
    "compression_algs": ["none"],
}
BUS_PORT = 1337
# A dead link is noticed after about 80 seconds: 3 unanswered keepalives, 20 seconds apart, plus one interval
KEEPALIVE_INTERVAL = 20
KEEPALIVE_COUNT = 3
CONNECT_TIMEOUT = 20
COMMAND_TIMEOUT = 20
HOST_KEY_FILE = DATA_DIR / "toon_host_key"


class CredentialsRejected(Exception):
    """The Toon refused the username or password."""


class HostKeyChanged(Exception):
    """The Toon presented a different SSH key than the one remembered."""


class ToonSession:
    """An open SSH session to the Toon."""

    def __init__(self, connection: asyncssh.SSHClientConnection):
        self._connection = connection

    async def wait_closed(self) -> None:
        """Return when the session has ended, for whatever reason."""
        await self._connection.wait_closed()

    def close(self) -> None:
        self._connection.close()

    async def open_bus(self):
        """A stream pair connected to the message bus."""
        return await self._connection.open_connection("127.0.0.1", BUS_PORT, encoding=None)

    async def run(self, command: str) -> str:
        """The output of a fixed, read-only command on the Toon."""
        result = await asyncio.wait_for(self._connection.run(command, check=False), COMMAND_TIMEOUT)
        return str(result.stdout or "")


class ToonLink:
    """Opens SSH sessions to the Toon with the configured credentials."""

    def __init__(self, config: Config, host_key_file: Path = HOST_KEY_FILE):
        self.config = config
        self.host_key_file = host_key_file

    def _known_hosts(self):
        """Trust the remembered key only; None on the very first connection (trust on first use)."""
        if self.config.trust_new_host_key or not self.host_key_file.exists():
            return None
        key = self.host_key_file.read_text().strip()
        return asyncssh.import_known_hosts(f"* {key}\n")

    async def connect(self) -> ToonSession:
        """A new session. Raises OSError or asyncssh.Error when the Toon can't be reached."""
        try:
            connection = await asyncio.wait_for(
                asyncssh.connect(
                    self.config.ssh_host,
                    port=self.config.ssh_port,
                    username=self.config.ssh_username,
                    password=self.config.ssh_password,
                    client_keys=None,
                    agent_path=None,
                    known_hosts=self._known_hosts(),
                    keepalive_interval=KEEPALIVE_INTERVAL,
                    keepalive_count_max=KEEPALIVE_COUNT,
                    **LEGACY_ALGORITHMS,
                ),
                CONNECT_TIMEOUT,
            )
        except asyncssh.PermissionDenied as e:
            raise CredentialsRejected(str(e)) from e
        except asyncssh.HostKeyNotVerifiable as e:
            raise HostKeyChanged(str(e)) from e
        self._remember_host_key(connection)
        return ToonSession(connection)

    def _remember_host_key(self, connection: asyncssh.SSHClientConnection) -> None:
        key = connection.get_server_host_key()
        if key is None:
            return
        text = key.export_public_key("openssh").decode().strip()
        known = self.host_key_file.read_text().strip() if self.host_key_file.exists() else ""
        if text != known:
            self.host_key_file.write_text(text + "\n")
            logger.info(f"Remembered the Toon's SSH key, fingerprint {key.get_fingerprint()}")
