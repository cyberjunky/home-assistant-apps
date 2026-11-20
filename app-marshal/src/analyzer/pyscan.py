"""Reads one Python file as a syntax tree and reports what it could do. Nothing is imported or run."""

import ast
import math
import re
from collections.abc import Callable

Hit = Callable[[str, int, str], None]  # rule, line, note

SECRET_NAME = re.compile(
    r"(^|_)(password|passwd|pwd|secret|token|api_?key|access_?key|private_?key|credentials?)$", re.I
)
NOT_A_SECRET_NAME = re.compile(
    r"^(conf|attr|data|key|field|default|step|error|schema|header|param|option)_|"
    r"(url|uri|file|path|name|type|prefix|label|regex|pattern|format|header|field|param|length|len|expir\w*|ttl)$",
    re.I,
)
SECRET_VALUE = re.compile(
    r"AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{40,}|xox[baprs]-[A-Za-z0-9-]{20,}|"
    r"AIza[0-9A-Za-z_-]{35}|sk-[A-Za-z0-9]{32,}|-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
)
URL = re.compile(r"\b(https?|wss?|ftp|mqtts?)://([^\s\"'<>\\)\]]*)", re.I)
BASE64_BLOB = re.compile(r"[A-Za-z0-9+/=\s]{2000,}")
PRIVATE_STORAGE = re.compile(
    r"\.storage/(auth|auth_provider|core\.config_entries|cloud|http\.auth|onboarding)\b|"
    r"^(auth_provider\.homeassistant|core\.config_entries)$"
)
HOST_SECRETS = re.compile(r"(^|/)\.ssh(/|$)|\bid_(rsa|ed25519|ecdsa)\b|/etc/(shadow|passwd)\b")
PIP_COMMAND = re.compile(r"^\s*(python3? -m )?pip3? install\b")

AUTH_METHODS = {
    "async_create_refresh_token", "async_create_access_token", "async_get_users", "async_get_owner",
    "async_create_user", "async_create_system_user", "async_get_refresh_token_by_token",
}  # fmt: skip
LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
DECODERS = {"b64decode", "b32decode", "b16decode", "a85decode", "b85decode", "decompress", "fromhex", "unhexlify"}
DESERIALIZE = {
    "pickle.load", "pickle.loads", "cPickle.load", "cPickle.loads", "dill.load", "dill.loads",
    "marshal.load", "marshal.loads", "shelve.open", "jsonpickle.decode",
}  # fmt: skip
SHELL = {
    "os.system", "os.popen", "subprocess.getoutput", "subprocess.getstatusoutput",
    "asyncio.create_subprocess_shell", "asyncio.subprocess.create_subprocess_shell",
}  # fmt: skip
SUBPROCESS = {
    "subprocess.run", "subprocess.call", "subprocess.check_call", "subprocess.check_output", "subprocess.Popen",
    "asyncio.create_subprocess_exec", "asyncio.subprocess.create_subprocess_exec",
}  # fmt: skip
RUNTIME_INSTALL = {
    "pip.main", "pip._internal.main", "pip._internal.cli.main.main",
    "homeassistant.util.package.install_package", "homeassistant.requirements.async_process_requirements",
}  # fmt: skip
LISTENERS = {
    "aiohttp.web.TCPSite", "aiohttp.web.run_app", "asyncio.start_server", "http.server.HTTPServer",
    "http.server.ThreadingHTTPServer", "socketserver.TCPServer", "socketserver.UDPServer", "websockets.serve",
}  # fmt: skip
OWN_SESSION = {"aiohttp.ClientSession", "httpx.AsyncClient", "httpx.Client"}
TELEMETRY_MODULES = {
    "sentry_sdk", "posthog", "mixpanel", "amplitude", "rollbar", "bugsnag", "datadog", "ddtrace", "newrelic",
    "honeybadger", "segment",
}  # fmt: skip
NATIVE_MODULES = {"ctypes", "cffi"}

# What must not be called directly from async code
BLOCKING = {
    "time.sleep": "performance.blocking_sleep",
    "urllib.request.urlopen": "performance.blocking_http",
    "urllib.request.urlretrieve": "performance.blocking_http",
    "os.system": "performance.blocking_subprocess",
    "socket.create_connection": "performance.blocking_socket",
    "socket.gethostbyname": "performance.blocking_socket",
    "socket.getaddrinfo": "performance.blocking_socket",
    "open": "performance.blocking_io",
    "os.listdir": "performance.blocking_io",
    "os.walk": "performance.blocking_io",
    "os.scandir": "performance.blocking_io",
    "glob.glob": "performance.blocking_io",
    "shutil.copy": "performance.blocking_io",
    "shutil.copyfile": "performance.blocking_io",
    "shutil.copytree": "performance.blocking_io",
    "shutil.rmtree": "performance.blocking_io",
}
BLOCKING.update(
    {f"requests.{m}": "performance.blocking_http" for m in ("get", "post", "put", "patch", "delete", "head", "request")}
)
BLOCKING.update({f"httpx.{m}": "performance.blocking_http" for m in ("get", "post", "put", "patch", "delete")})
BLOCKING.update(
    {f"subprocess.{m}": "performance.blocking_subprocess" for m in ("run", "call", "check_call", "check_output")}
)
BLOCKING_METHODS = {"read_text", "write_text", "read_bytes", "write_bytes"}


def entropy(text: str) -> float:
    counts = {c: text.count(c) for c in set(text)}
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def looks_like_secret(value: str) -> bool:
    return (
        20 <= len(value) <= 300
        and not any(c.isspace() for c in value)
        and any(c.isdigit() for c in value)
        and any(c.isalpha() for c in value)
        and "://" not in value
        and "{" not in value
        and entropy(value) >= 3.5
    )


class Facts:
    """What the files say together; the rules that need the whole integration read these."""

    def __init__(self) -> None:
        self.urls: list[tuple[str, str, str, int]] = []  # scheme, rest, file, line
        self.intervals: list[tuple[float, str, int]] = []  # seconds, file, line
        self.reads_all_states: tuple[str, int] | None = None
        self.diagnostics = False
        self.diagnostics_redacted = False
        self.lines = 0
        self.functions = 0
        self.typed = 0  # functions that say what they take or give back


class FileScan(ast.NodeVisitor):
    def __init__(self, path: str, hit: Hit, facts: Facts):
        self.path = path
        self.hit = hit
        self.facts = facts
        self.aliases: dict[str, str] = {}
        self.in_loop: list[bool] = [False]  # is the code we are in run by the event loop?
        self.defined: set[str] = set()

    # -- names --------------------------------------------------------------------------------------
    def name(self, node: ast.AST) -> str:
        """The dotted name of what is called or used, with imports followed: req.get -> requests.get."""
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return f"{self.name(node.value)}.{node.attr}"
        return "?"

    @staticmethod
    def ident(node: ast.AST) -> str:
        """The last name a value goes by: self.api_key -> api_key, data["token"] -> token."""
        if isinstance(node, ast.FormattedValue):
            node = node.value
        if (
            isinstance(node, ast.Call)
            and node.args
            and isinstance(node.func, ast.Name)
            and node.func.id in ("str", "repr")
        ):
            node = node.args[0]
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            return node.slice.value
        return ""

    # -- imports ------------------------------------------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top = alias.name.split(".")[0]
            self.aliases[alias.asname or top] = alias.name if alias.asname else top
            self._module(top, node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = "." * node.level + (node.module or "")
        for alias in node.names:
            self.aliases[alias.asname or alias.name] = f"{module}.{alias.name}".lstrip(".") if not node.level else "~"
        if not node.level and node.module:
            self._module(node.module.split(".")[0], node.lineno)

    def _module(self, top: str, line: int) -> None:
        if top in TELEMETRY_MODULES:
            self.hit("data.telemetry", line, top)
        elif top in NATIVE_MODULES:
            self.hit("code.native", line, top)

    # -- where code runs ----------------------------------------------------------------------------
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node, True)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # A @callback runs in the event loop, like async code
        self._function(node, any(self.name(d).endswith("callback") for d in node.decorator_list))

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, in_loop: bool) -> None:
        self.defined.add(node.name)
        self.facts.functions += 1
        arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        if node.returns is not None or any(a.annotation is not None for a in arguments):
            self.facts.typed += 1
        if node.name == "async_get_config_entry_diagnostics" or node.name == "async_get_device_diagnostics":
            self.facts.diagnostics = True
        self.in_loop.append(in_loop)
        self.generic_visit(node)
        self.in_loop.pop()

    def visit_Lambda(self, node: ast.Lambda) -> None:
        # A lambda is handed to something else to run, most often the executor
        self.in_loop.append(False)
        self.generic_visit(node)
        self.in_loop.pop()

    def visit_While(self, node: ast.While) -> None:
        if self.in_loop[-1] and isinstance(node.test, ast.Constant) and node.test.value is True:
            # A loop that awaits lets Home Assistant go on; one that can leave is not endless
            leaves = ast.Await | ast.AsyncFor | ast.AsyncWith | ast.Yield | ast.Break | ast.Return | ast.Raise
            if not any(isinstance(n, leaves) for n in ast.walk(node)):
                self.hit("performance.busy_loop", node.lineno, "")
        self.generic_visit(node)

    # -- calls --------------------------------------------------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:
        name = self.name(node.func)
        last = name.rsplit(".", 1)[-1]
        line = node.lineno
        keywords = {k.arg: k.value for k in node.keywords if k.arg}

        if name in ("eval", "exec") and name not in self.defined:
            hidden = any(
                isinstance(n, ast.Call) and self.name(n.func).rsplit(".", 1)[-1] in DECODERS
                for arg in node.args
                for n in ast.walk(arg)
            )
            self.hit("code.obfuscated_exec" if hidden else "code.eval", line, name)
        elif name in DESERIALIZE:
            self.hit("code.deserialize", line, name)
        elif name in ("yaml.unsafe_load", "yaml.full_load") or (name == "yaml.load" and not self._safe_loader(node)):
            self.hit("code.yaml_load", line, name)
        elif name in SHELL:
            self.hit("code.shell", line, name)
        elif name in SUBPROCESS or name.startswith(("os.exec", "os.spawn")):
            shell = keywords.get("shell")
            is_shell = isinstance(shell, ast.Constant) and shell.value is True
            self.hit("code.shell" if is_shell else "code.subprocess", line, name)
        elif name in RUNTIME_INSTALL:
            self.hit("code.runtime_install", line, name)
        elif name in ("importlib.import_module", "__import__"):
            if node.args and not isinstance(node.args[0], ast.Constant):
                self.hit("code.dynamic_import", line, name)
        elif name == "setattr" and node.args and self.name(node.args[0]).startswith("homeassistant."):
            self.hit("code.monkeypatch", line, self.name(node.args[0]))
        elif name in ("ctypes.CDLL", "ctypes.cdll.LoadLibrary"):
            self.hit("code.native", line, name)

        if last in AUTH_METHODS:
            self.hit("data.auth_tokens", line, last)
        elif name.endswith("config_entries.async_entries") and not node.args and not node.keywords:
            self.hit("data.all_config_entries", line, "")
        elif name.endswith((".states.async_all", ".states.all")) and not node.args:
            self.hit("data.all_states", line, "")
            self.facts.reads_all_states = self.facts.reads_all_states or (self.path, line)
        elif name in ("os.getenv", "os.environ.get"):
            self.hit("data.environ", line, "")
        elif last == "async_redact_data":
            self.facts.diagnostics_redacted = True
        elif (
            last in LOG_METHODS and isinstance(node.func, ast.Attribute) and "log" in self.name(node.func.value).lower()
        ):
            self._logged(node)

        for key in ("verify", "verify_ssl", "ssl"):
            value = keywords.get(key)
            if isinstance(value, ast.Constant) and value.value is False:
                self.hit("network.tls_off", line, f"{key}=False")
        if name == "ssl._create_unverified_context":
            self.hit("network.tls_off", line, name)
        elif name == "socket.socket":
            self.hit("network.raw_socket", line, "")
        elif name in LISTENERS:
            self.hit("network.listener", line, name)
        elif name in OWN_SESSION:
            self.hit("performance.own_session", line, name)

        if name.endswith(("bus.async_listen", "bus.listen")) and node.args:
            event = node.args[0]
            what = event.value if isinstance(event, ast.Constant) else self.name(event).rsplit(".", 1)[-1]
            if what in ("*", "state_changed", "MATCH_ALL", "EVENT_STATE_CHANGED"):
                self.hit("performance.all_events", line, str(what))
                self.facts.reads_all_states = self.facts.reads_all_states or (self.path, line)

        if last == "timedelta":
            self._interval(node)

        if self.in_loop[-1]:
            rule = BLOCKING.get(name)
            if name == "open" and "open" in self.defined:
                rule = None
            if rule is None and last in BLOCKING_METHODS and isinstance(node.func, ast.Attribute):
                rule = "performance.blocking_io"
            if rule:
                self.hit(rule, line, name if rule != "performance.blocking_io" else last)

        self.generic_visit(node)

    @staticmethod
    def _safe_loader(node: ast.Call) -> bool:
        loaders = [k.value for k in node.keywords if k.arg == "Loader"] + node.args[1:2]
        return any("Safe" in ast.dump(loader) for loader in loaders)

    def _logged(self, node: ast.Call) -> None:
        values: list[ast.AST] = []
        for arg in [*node.args, *(k.value for k in node.keywords)]:
            if isinstance(arg, ast.JoinedStr):
                values.extend(v for v in arg.values if isinstance(v, ast.FormattedValue))
            elif not isinstance(arg, ast.Constant):
                values.append(arg)
        for value in values:
            ident = self.ident(value)
            if ident and SECRET_NAME.search(ident) and not NOT_A_SECRET_NAME.search(ident):
                self.hit("data.log_secret", node.lineno, ident)
                return

    def _interval(self, node: ast.Call) -> None:
        """A timedelta that sets how often something runs: its seconds, when they are written out."""
        units = {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86400, "milliseconds": 0.001}
        seconds = 0.0
        if node.args or not node.keywords:
            return
        for keyword in node.keywords:
            value = keyword.value
            if (
                keyword.arg not in units
                or not isinstance(value, ast.Constant)
                or not isinstance(value.value, int | float)
            ):
                return
            seconds += value.value * units[keyword.arg]
        parent = getattr(node, "parent", None)
        context = ""
        if isinstance(parent, ast.keyword):
            context = parent.arg or ""
        elif isinstance(parent, ast.Assign | ast.AnnAssign):
            targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
            context = " ".join(self.ident(t) for t in targets)
        elif isinstance(parent, ast.Call):
            context = self.name(parent.func).rsplit(".", 1)[-1]
        if seconds > 0 and re.search(r"interval|scan|poll|refresh", context, re.I):
            self.facts.intervals.append((seconds, self.path, node.lineno))

    # -- assignments --------------------------------------------------------------------------------
    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._assigned(target, node.value, node.lineno)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self._assigned(node.target, node.value, node.lineno)
        self.generic_visit(node)

    def _assigned(self, target: ast.AST, value: ast.AST, line: int) -> None:
        ident = self.ident(target)
        constant = value.value if isinstance(value, ast.Constant) else None
        if isinstance(target, ast.Attribute):
            root = target
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and self.aliases.get(root.id, "").startswith("homeassistant"):
                self.hit("code.monkeypatch", line, self.name(target))
        if ident == "requires_auth" and constant is False:
            self.hit("network.unauth_view", line, "")
        elif ident == "cors_allowed" and constant is True:
            self.hit("network.cors", line, "")
        elif (ident == "check_hostname" and constant is False) or (
            ident == "verify_mode" and self.name(value).endswith("CERT_NONE")
        ):
            self.hit("network.tls_off", line, ident)
        elif (
            isinstance(constant, str)
            and SECRET_NAME.search(ident)
            and not NOT_A_SECRET_NAME.search(ident)
            and looks_like_secret(constant)
        ):
            self.hit("data.hardcoded_secret", line, ident)

    # -- text ---------------------------------------------------------------------------------------
    def visit_List(self, node: ast.List) -> None:
        words = {e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
        if {"pip", "install"} <= words:
            self.hit("code.runtime_install", node.lineno, "pip install")
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if self.name(node.value) == "os.environ":
            self.hit("data.environ", node.lineno, "")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        text = node.value
        if not isinstance(text, str) or len(text) < 4:
            return
        # A docstring says things, it does not do them
        if isinstance(getattr(node, "parent", None), ast.Expr):
            return
        line = node.lineno
        for match in URL.finditer(text):
            self.facts.urls.append((match.group(1).lower(), match.group(2), self.path, line))
        if "secrets.yaml" in text:
            self.hit("data.secrets_file", line, "")
        if PRIVATE_STORAGE.search(text):
            self.hit("data.private_storage", line, "")
        elif ".storage" in text:
            self.hit("data.storage_dir", line, "")
        if HOST_SECRETS.search(text):
            self.hit("data.host_secrets", line, "")
        if "home-assistant_v2.db" in text:
            self.hit("data.recorder_db", line, "")
        if text in ("SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
            self.hit("data.supervisor_token", line, text)
        if SECRET_VALUE.search(text):
            self.hit("data.hardcoded_secret", line, "")
        if PIP_COMMAND.search(text):
            self.hit("code.runtime_install", line, "pip install")
        if len(text) >= 2000 and BASE64_BLOB.fullmatch(text):
            self.hit("code.blob", line, f"{len(text) // 1024} kB")


def scan(path: str, source: str, hit: Hit, facts: Facts) -> bool:
    """Scan one file. False when it could not be parsed."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return False
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child.parent = parent  # type: ignore[attr-defined]
    facts.lines += source.count("\n") + 1
    try:
        FileScan(path, hit, facts).visit(tree)
    except RecursionError:
        return False
    return True
