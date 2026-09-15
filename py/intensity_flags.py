"""Shared argv parsers for intensity / region pipeline scripts."""


def parse_whole_flag(value) -> bool:
    """Parse -w / --whole argv value (never use bool(string) or eval)."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off", ""):
        return False
    raise ValueError(f"Invalid whole flag: {value!r}")
