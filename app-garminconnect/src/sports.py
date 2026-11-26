"""Sports grouped into families, for colours, filters and the heat map."""

FAMILIES = (
    ("running", ("running", "run")),
    ("cycling", ("cycling", "biking", "ride", "bmx", "gravel", "mountain")),
    ("walking", ("walking", "hiking", "walk", "hike")),
    ("swimming", ("swim",)),
    ("strength", ("strength", "hiit", "fitness_equipment", "cardio", "yoga", "pilates", "training")),
)


def family(activity_type: str) -> str:
    key = (activity_type or "").lower()
    for name, words in FAMILIES:
        if any(word in key for word in words):
            return name
    return "other"
