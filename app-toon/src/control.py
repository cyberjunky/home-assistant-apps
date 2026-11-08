"""Changing the thermostat: the only commands this app can send that change anything on the Toon.

They go to the Toon's own web server over the SSH session, because these are the commands known to
work on real Toons (the same ones the toon_climate integration sends). Everything is a fixed command
with checked numbers, so nothing a client sends ends up in a request as text.
The message bus stays read-only: see READ_ONLY_ACTIONS in boxtalk.py.
"""

import json
import math

MIN_SETPOINT = 6.0
MAX_SETPOINT = 30.0
STEP = 0.5
# changeSchemeState: what the program does
SCHEME_OFF = 0
SCHEME_ON = 1
SCHEME_TEMPORARY = 2
# activeState values of the four presets, and the metrics with their temperatures
PRESETS = {0: "preset_comfort", 1: "preset_home", 2: "preset_sleep", 3: "preset_away"}
HOLIDAY = 4
HOME = 1


class CommandRefused(Exception):
    """The Toon didn't accept a command."""


def setpoint_command(temperature: float) -> str:
    return f"/happ_thermstat?action=setSetpoint&Setpoint={round(temperature * 100)}"


def preset_command(state: int) -> str:
    return f"/happ_thermstat?action=changeSchemeState&state={SCHEME_TEMPORARY}&temperatureState={state}"


def plan(body: dict, values: dict) -> tuple[str, list[str]]:
    """What a request asks for: a description for the log, and the commands. Raises ValueError."""
    if not isinstance(body, dict) or len(body) != 1:
        raise ValueError("Send one change at a time")
    ((name, value),) = body.items()
    if name == "setpoint":
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            raise ValueError("The setpoint must be a number")
        if not MIN_SETPOINT <= value <= MAX_SETPOINT:
            raise ValueError(f"The setpoint must be between {MIN_SETPOINT:g} and {MAX_SETPOINT:g} °C")
        temperature = round(value / STEP) * STEP
        commands = [setpoint_command(temperature)]
        # A setpoint leaves the Toon in manual mode, also at a preset's temperature. Then that preset
        # is meant, unless two share the temperature.
        matches = [state for state, metric in PRESETS.items() if values.get(metric) == temperature]
        if len(matches) == 1:
            commands.append(preset_command(matches[0]))
        return f"setpoint {temperature:g} °C", commands
    if name == "preset":
        if isinstance(value, bool) or value not in PRESETS:
            raise ValueError("Unknown preset")
        return f"preset {PRESETS[value].removeprefix('preset_')}", [preset_command(value)]
    if name == "program":
        if not isinstance(value, bool):
            raise ValueError("The program is on or off")
        command = f"/happ_thermstat?action=changeSchemeState&state={SCHEME_ON if value else SCHEME_OFF}"
        # Leaving holiday mode needs a preset to fall back to
        if not value and values.get("active_state") == HOLIDAY:
            command += f"&temperatureState={HOME}"
        return f"program {'on' if value else 'off'}", [command]
    raise ValueError("Unknown change")


def check_answer(raw: bytes) -> None:
    """Whether the Toon's web server accepted a command, from its whole HTTP answer."""
    head, _, body = raw.partition(b"\r\n\r\n")
    status = head.split(b"\r\n", 1)[0].split()
    if len(status) < 2 or status[1] != b"200":
        raise CommandRefused(f"the Toon answered {b' '.join(status[1:]).decode('ascii', 'replace') or 'nothing'}")
    try:
        answer = json.loads(body)
    except ValueError as e:
        raise CommandRefused("the Toon gave an answer that isn't JSON") from e
    if not isinstance(answer, dict) or answer.get("success") is False:
        raise CommandRefused("the Toon refused the change")
