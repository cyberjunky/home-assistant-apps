"""Which of Ruff's checks Marshal uses, and the finding each one becomes.

Only checks that say something about how the code behaves: no style.
"""

# Fails when the line runs: undefined names, formats that do not fit, statements where they cannot be
BROKEN = """
    F821 F822 F823 F501 F502 F503 F504 F505 F506 F507 F508 F509 F521 F522 F523 F524 F525
    F701 F702 F704 F706 F707
    PLE0100 PLE0101 PLE0115 PLE0116 PLE0117 PLE0118 PLE0302 PLE0303 PLE0304 PLE0305 PLE0307 PLE0308
    PLE0309 PLE0604 PLE0605 PLE0643 PLE0704 PLE1142 PLE1205 PLE1206 PLE1300 PLE1307 PLE1310 PLE1507
    PLE1519 PLE1520 PLE1700
"""
# Often a mistake
BUG_PRONE = """
    B006 B012 B015 B016 B018 B020 B021 B023 B025 B026 B029 B030 B031 B032 B033 B035 B039
    F402 F403 F601 F602 F631 F632 F811
"""

RULE_FOR: dict[str, str] = {
    **dict.fromkeys(BROKEN.split(), "quality.broken"),
    **dict.fromkeys(BUG_PRONE.split(), "quality.bug_prone"),
    **dict.fromkeys(["E722", "S110", "S112"], "quality.swallows_errors"),
    "C901": "quality.complex",  # quality.very_complex above 50
    **dict.fromkeys(["F401", "F841"], "quality.unused"),
    # Security lint (Ruff's flake8-bandit rules), for what Marshal's own analyzer does not look at
    "S608": "code.sql_strings",
    "S202": "code.unsafe_extract",
    **dict.fromkeys(["S303", "S304", "S305", "S324"], "code.weak_crypto"),
    **dict.fromkeys(["S313", "S314", "S315", "S316", "S317", "S318", "S319"], "code.xml_unsafe"),
    "S108": "code.temp_path",
    "S103": "code.loose_permissions",
    **dict.fromkeys(["S502", "S503", "S504"], "network.weak_tls"),
    "S507": "network.no_host_key_check",
    **dict.fromkeys(["S312", "S321", "S401", "S402", "S508", "S509"], "network.cleartext_protocol"),
    "S113": "performance.no_timeout",
}
