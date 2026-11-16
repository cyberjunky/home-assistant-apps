"""One number for how exposed the network is: the security score, 0 to 100, and its grade.

Every issue that is still there counts, acknowledged ones too: acknowledging says you know about it, not
that it is gone. A critical weighs as much as 25 low ones. The score falls quickly for the first serious
issues and slowly after that, so 1 critical is a clear drop and 20 of them is not "minus 400".
"""

WEIGHTS = {"critical": 25, "high": 10, "medium": 4, "low": 1, "info": 0}
# The points at which the score is halved
HALF_AT = 50
GRADES = ((90, "A"), (80, "B"), (65, "C"), (50, "D"), (0, "F"))


def points(counts: dict) -> int:
    return sum(WEIGHTS.get(sev, 0) * int(n or 0) for sev, n in counts.items())


def score(counts: dict) -> int:
    return round(100 * HALF_AT / (HALF_AT + points(counts)))


def grade(value: int) -> str:
    return next(letter for floor, letter in GRADES if value >= floor)
