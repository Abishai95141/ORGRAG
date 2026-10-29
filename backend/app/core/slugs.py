import re
import unicodedata

RESERVED = {"api", "admin", "new", "settings", "login", "signup", "invite", "onboarding", "o"}
_SLUG_OK = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?$")


def slugify(value: str, max_len: int = 48) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-")
    slug = slug[:max_len].strip("-")
    return slug or "org"


def is_valid_slug(slug: str) -> bool:
    return bool(_SLUG_OK.match(slug)) and slug not in RESERVED


def next_available(base: str, taken: set[str]) -> str:
    candidate = base if base not in RESERVED else f"{base}-org"
    n = 2
    while candidate in taken:
        candidate = f"{base[:44]}-{n}"
        n += 1
    return candidate
