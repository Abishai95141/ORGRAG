"""Vendored verbatim from LightRAG 1.5.7 (MIT, see LICENSE in this folder) by
scripts/vendor_lightrag.py. Do not edit by hand; re-run the script instead.
Sources: lightrag/utils.py, lightrag/operate.py, lightrag/constants.py, lightrag/prompt.py."""

# ruff: noqa
# fmt: off
from typing import Any, Mapping, TypedDict
import html
import json
import json_repair
import logging
import re


_SURROGATE_PATTERN = re.compile(r"[\uD800-\uDFFF\uFFFE\uFFFF]")


_CONTROL_CHAR_PATTERN_ALL = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


logger = logging.getLogger("lightrag")


def sanitize_and_normalize_extracted_text(
    input_text: str, remove_inner_quotes=False
) -> str:
    """Santitize and normalize extracted text
    Args:
        input_text: text string to be processed
        is_name: whether the input text is a entity or relation name

    Returns:
        Santitized and normalized text string
    """
    safe_input_text = sanitize_text_for_encoding(input_text)
    if safe_input_text:
        normalized_text = normalize_extracted_info(
            safe_input_text, remove_inner_quotes=remove_inner_quotes
        )
        return normalized_text
    return ""


def normalize_entity_name(input_text: str) -> str:
    """Normalize an entity identifier using the extraction naming contract."""
    return sanitize_and_normalize_extracted_text(input_text, remove_inner_quotes=True)


def normalize_extracted_info(name: str, remove_inner_quotes=False) -> str:
    """Normalize entity/relation names and description with the following rules:
    - Clean HTML tags (paragraph and line break tags)
    - Convert Chinese symbols to English symbols
    - Remove spaces between Chinese characters
    - Remove spaces between Chinese characters and English letters/numbers
    - Preserve spaces within English text and numbers
    - Replace Chinese parentheses with English parentheses
    - Replace Chinese dash with English dash
    - Remove English quotation marks from the beginning and end of the text
    - Remove English quotation marks in and around chinese
    - Remove Chinese quotation marks
    - Filter out short numeric-only text (length < 3 and only digits/dots)
    - remove_inner_quotes = True
        remove Chinese quotes
        remove English quotes in and around chinese
        Convert non-breaking spaces to regular spaces
        Convert narrow non-breaking spaces after non-digits to regular spaces

    Args:
        name: Entity name to normalize
        is_entity: Whether this is an entity name (affects quote handling)

    Returns:
        Normalized entity name
    """
    # Clean HTML tags - remove paragraph and line break tags
    name = re.sub(r"</p\s*>|<p\s*>|<p/>", "", name, flags=re.IGNORECASE)
    name = re.sub(r"</br\s*>|<br\s*>|<br/>", "", name, flags=re.IGNORECASE)

    # Chinese full-width letters to half-width (A-Z, a-z)
    name = name.translate(
        str.maketrans(
            "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
        )
    )

    # Chinese full-width numbers to half-width
    name = name.translate(str.maketrans("０１２３４５６７８９", "0123456789"))

    # Chinese full-width symbols to half-width
    name = name.replace("－", "-")  # Chinese minus
    name = name.replace("＋", "+")  # Chinese plus
    name = name.replace("／", "/")  # Chinese slash
    name = name.replace("＊", "*")  # Chinese asterisk

    # Replace Chinese parentheses with English parentheses
    name = name.replace("（", "(").replace("）", ")")

    # Replace Chinese dash with English dash (additional patterns)
    name = name.replace("—", "-").replace("－", "-")

    # Chinese full-width space to regular space (after other replacements)
    name = name.replace("　", " ")

    # Use regex to remove spaces between Chinese characters
    # Regex explanation:
    # (?<=[\u4e00-\u9fa5]): Positive lookbehind for Chinese character
    # \s+: One or more whitespace characters
    # (?=[\u4e00-\u9fa5]): Positive lookahead for Chinese character
    name = re.sub(r"(?<=[\u4e00-\u9fa5])\s+(?=[\u4e00-\u9fa5])", "", name)

    # Remove spaces between Chinese and English/numbers/symbols
    name = re.sub(
        r"(?<=[\u4e00-\u9fa5])\s+(?=[a-zA-Z0-9\(\)\[\]@#$%!&\*\-=+_])", "", name
    )
    name = re.sub(
        r"(?<=[a-zA-Z0-9\(\)\[\]@#$%!&\*\-=+_])\s+(?=[\u4e00-\u9fa5])", "", name
    )

    # Remove outer quotes
    if len(name) >= 2:
        # Handle double quotes
        if name.startswith('"') and name.endswith('"'):
            inner_content = name[1:-1]
            if '"' not in inner_content:  # No double quotes inside
                name = inner_content

        # Handle single quotes
        if name.startswith("'") and name.endswith("'"):
            inner_content = name[1:-1]
            if "'" not in inner_content:  # No single quotes inside
                name = inner_content

        # Handle Chinese-style double quotes
        if name.startswith("“") and name.endswith("”"):
            inner_content = name[1:-1]
            if "“" not in inner_content and "”" not in inner_content:
                name = inner_content
        if name.startswith("‘") and name.endswith("’"):
            inner_content = name[1:-1]
            if "‘" not in inner_content and "’" not in inner_content:
                name = inner_content

        # Handle Chinese-style book title mark
        if name.startswith("《") and name.endswith("》"):
            inner_content = name[1:-1]
            if "《" not in inner_content and "》" not in inner_content:
                name = inner_content

    if remove_inner_quotes:
        # Remove Chinese quotes
        name = name.replace("“", "").replace("”", "").replace("‘", "").replace("’", "")
        # Remove English queotes in and around chinese
        name = re.sub(r"['\"]+(?=[\u4e00-\u9fa5])", "", name)
        name = re.sub(r"(?<=[\u4e00-\u9fa5])['\"]+", "", name)
        # Convert non-breaking space to regular space
        name = name.replace("\u00a0", " ")
        # Convert narrow non-breaking space to regular space when after non-digits
        name = re.sub(r"(?<=[^\d])\u202F", " ", name)

    # Remove spaces from the beginning and end of the text
    name = name.strip()

    # Filter out pure numeric content with length < 3
    if len(name) < 3 and re.match(r"^[0-9]+$", name):
        return ""

    def should_filter_by_dots(text):
        """
        Check if the string consists only of dots and digits, with at least one dot
        Filter cases include: 1.2.3, 12.3, .123, 123., 12.3., .1.23 etc.
        """
        return all(c.isdigit() or c == "." for c in text) and "." in text

    if len(name) < 6 and should_filter_by_dots(name):
        # Filter out mixed numeric and dot content with length < 6, requiring at least one dot
        return ""

    return name


def sanitize_text_for_encoding(text: str, replacement_char: str = "") -> str:
    """Sanitize text to ensure safe UTF-8 encoding by removing or replacing problematic characters.

    This function handles:
    - Surrogate characters (the main cause of encoding errors)
    - Other invalid Unicode sequences
    - Control characters that might cause issues
    - Unescape HTML escapes
    - Remove control characters
    - Whitespace trimming

    Args:
        text: Input text to sanitize
        replacement_char: Character to use for replacing invalid sequences

    Returns:
        Sanitized text that can be safely encoded as UTF-8
    """
    if not text:
        return text

    # First, strip whitespace
    text = text.strip()

    # Early return if text is empty after basic cleaning
    if not text:
        return text

    # 1. html.unescape first to catch entities that might become surrogates or control chars
    text = html.unescape(text)

    # 2. Use pre-compiled regex to clean surrogates and non-characters in one pass
    # This replaces the slow manual loop and initial .encode() check
    text = _SURROGATE_PATTERN.sub(replacement_char, text)

    # 3. Remove control characters but preserve common whitespace (\t, \n, \r)
    text = _CONTROL_CHAR_PATTERN_ALL.sub(replacement_char, text)

    return text.strip()


_FORMFEED_LATEX_PATTERN = re.compile(r"\x0c(?=[A-Za-z])")


_BACKSPACE_LATEX_PATTERN = re.compile(r"\x08(?=[A-Za-z])")


_WS_LATEX_SUSPECT_PATTERN = re.compile(
    r"\t(?=(?:au|heta|imes|ext|ilde|herefore|riangle)\b)"
    r"|\r(?=(?:ho|ight|angle|ceil)\b)"
    r"|\n(?=(?:abla|otin)\b)"
)


def repair_vlm_json_escape_damage(text: str, *, context: str = "") -> str:
    """Restore LaTeX backslashes destroyed by JSON escape decoding.

    Applied to string values parsed out of VLM/LLM JSON responses, where an
    un-doubled LaTeX command like ``"\\frac"`` arrives as ``\\x0c`` + ``rac``.
    Only the two zero-risk cases are repaired:

    - form feed + letter  -> ``\\f`` + letter (``\\frac``, ``\\forall``, ...)
    - backspace + letter  -> ``\\b`` + letter (``\\beta``, ``\\bar``, ...)

    Isolated control characters (not followed by a letter) are left alone for
    downstream sanitization to drop. Whitespace-class damage (``\\tau`` ->
    tab + ``au`` etc.) is ambiguous with legitimate whitespace and is only
    logged at WARNING level, never rewritten.

    Args:
        text: Parsed string value to repair.
        context: Optional label (e.g. ``"table/t1.description"``) included in
            the detection log line.
    """
    if not text:
        return text

    repaired = _FORMFEED_LATEX_PATTERN.sub(r"\\f", text)
    repaired = _BACKSPACE_LATEX_PATTERN.sub(r"\\b", repaired)
    if repaired != text:
        logger.warning(
            "Repaired LaTeX escape damage (\\f/\\b decoded by JSON parser)%s",
            f" in {context}" if context else "",
        )

    suspect = _WS_LATEX_SUSPECT_PATTERN.search(repaired)
    if suspect:
        snippet = repaired[max(0, suspect.start() - 30) : suspect.start() + 30]
        logger.warning(
            "Suspected whitespace-class LaTeX escape damage%s (not auto-repaired): %r",
            f" in {context}" if context else "",
            snippet,
        )

    return repaired


def repair_vlm_json_escape_damage_nested(obj: Any, *, context: str = "") -> Any:
    """Apply :func:`repair_vlm_json_escape_damage` to every string inside a
    parsed JSON structure (dicts / lists nested arbitrarily).

    Used on the output of ``json_repair.loads`` for LLM responses that may
    quote LaTeX — multimodal analysis objects and entity-extraction results
    (``{"entities": [{...}], "relationships": [{...}]}``). Non-string leaves
    are returned untouched.
    """
    if isinstance(obj, str):
        return repair_vlm_json_escape_damage(obj, context=context)
    if isinstance(obj, dict):
        return {
            key: repair_vlm_json_escape_damage_nested(
                value, context=f"{context}.{key}" if context else str(key)
            )
            for key, value in obj.items()
        }
    if isinstance(obj, list):
        return [
            repair_vlm_json_escape_damage_nested(item, context=context) for item in obj
        ]
    return obj


_CODE_FENCE_PATTERN = re.compile(
    r"^\s*```(?:json|JSON)?\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL
)


def strip_markdown_code_fence(text: str) -> str:
    """Strip a surrounding markdown code fence (```json ... ``` or ``` ... ```).

    Why: LLM training priors strongly associate "JSON output" with fenced code
    blocks, so providers routinely wrap responses despite explicit instructions
    to the contrary. Stripping here avoids relying on ``json_repair`` and the
    noisy warning it emits. The ``\\n?`` make the interior newlines optional so
    single-line fences (```` ```json {...}``` ````) are handled too.
    """

    match = _CODE_FENCE_PATTERN.match(text)
    return match.group(1) if match else text


def _first_structural_opener(text: str) -> tuple[str | None, int]:
    """Return the first ``[`` or ``{`` that sits outside a double-quoted string.

    Only ``"`` is treated as a string delimiter: prose apostrophes (``Here's``)
    are far too common to treat ``'`` as a quote, and leading prose that wraps a
    ``[`` inside single quotes is rare enough to accept as graceful degradation.
    The result drives the top-level array-vs-object decision — a leading ``[``
    means a top-level array (rejected by the caller), a leading ``{`` marks where
    object recovery starts.
    """
    quote_open = False
    escaped = False
    for index, char in enumerate(text):
        if quote_open:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quote_open = False
            continue
        if char == '"':
            quote_open = True
        elif char in "[{":
            return char, index
    return None, -1


def _first_balanced_object_slice(text: str) -> str:
    """Return the first brace-balanced ``{...}`` slice; ``text`` starts at ``{``.

    ``"`` always opens a string. ``'`` opens a string only in a value/key
    position — right after ``{``, ``[``, ``,`` or ``:`` (spaces skipped) — so a
    bare apostrophe inside an unquoted token (``O'Reilly``, ``it's``) is not
    mistaken for a string start. Without that guard the scan would run past the
    object's closing ``}`` and json_repair would fold trailing prose into the
    last value — a silent, schema-valid but corrupted result. Single quotes are
    tracked at all because weaker models emit single-quoted JSON strings that can
    legitimately contain stray braces. Falls back to the whole string when no
    matching close brace exists so an incomplete object stays repairable.
    """
    depth = 0
    quote: str | None = None
    escaped = False
    previous_non_space = ""
    for index, char in enumerate(text):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char == '"' or (char == "'" and previous_non_space in "{[,:"):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[: index + 1]
        if not char.isspace():
            previous_non_space = char
    return text


def tolerant_load_json_dict(text: str) -> dict[str, Any]:
    """Recover a single JSON object from noisy LLM/VLM text.

    Returns the first genuine JSON *object*, or ``{}`` when the text does not
    yield exactly one object. ``{}`` is the signal callers act on: multimodal
    analysis retries once, entity extraction falls back to delimiter parsing.

    Recovered — returns the object (the malformed shapes weak models emit):

    * a clean object, optionally inside a markdown ``json`` code fence, with or
      without interior newlines: ``{"a": 1}``
    * leading prose before the object — a label, a ``#`` or ``//`` comment
      lead-in, a URL, or an apostrophe: ``Here is the result: {"a":1}``,
      ``Result #1: {"a":1}``, ``Source: http://x//y#z {"a":1}``,
      ``Here's the note: {"a":1}``
    * trailing prose after the object, even when it contains braces — the bug
      this helper exists for, where a greedy ``{...}`` slice over-extends and
      drops everything: ``{"facts":[...]} trailing {brace}``
    * object-level slips ``json_repair`` fixes — trailing comma, single quotes,
      unquoted keys, truncation: ``{"a":1,}``, ``{'a':1}``, ``{a:1}``, ``{"a":1``
    * a genuine (possibly malformed) object followed by a bracket citation:
      ``{"a":1,} [1]``

    Rejected — returns ``{}`` so callers retry / fall back rather than accept a
    non-object:

    * any top-level array, so one element is never mistaken for the whole
      answer — including prose-prefixed, truncated, single-quoted, and commented
      arrays: ``[{"a":1},{"b":2}]``, ``Here is the result: [{...}]``,
      ``['note', {...}]``, ``[/* ] */ {...}]``
    * a top-level array reached past a broken array shell, an unclosed quote, or
      pseudo-object prose — never scavenged for an inner object: ``[}{"a":1}]``,
      ``"oops [}{"a":1}]``, ``{note} [{"a":1}]``
    * an object behind bracketed prose — indistinguishable from a real array
      without heuristics and not seen in practice: ``analysis: [draft] {"a":1}``

    Two edge rules worth knowing when extending this:

    * the top-level shape is decided by the first structural opener outside a
      double-quoted string; ``json_repair`` runs only on the first balanced
      ``{...}`` slice, never the whole string (it would scavenge a dict across
      structural boundaries and defeat array rejection).
    * a single quote is a string delimiter only in a value/key position, so a
      bare apostrophe in an unquoted token (``O'Reilly``, ``it's``) does not
      swallow the object's closing brace.

    Not handled here: LaTeX-escape damage in string values — callers apply
    ``repair_vlm_json_escape_damage_nested`` themselves, keeping that choke
    point out of this helper.
    """
    if not text:
        return {}
    candidate = strip_markdown_code_fence(text).strip()

    # Decide the top-level shape from the raw structure FIRST: the first opener
    # outside a double-quoted string. json_repair is never run over the whole
    # candidate — it scavenges a dict across structural boundaries (out of a
    # broken array shell '[}{...}]', or past pseudo-object prose '{note} [..]'),
    # which would bypass the top-level-array rejection contract.
    opener, index = _first_structural_opener(candidate)
    if opener != "{":
        # '[' is a top-level array. None means the structure is untrusted — e.g.
        # an unclosed double quote swallows the openers ('"oops [}{"a":1}]').
        # Reject either way so callers retry (multimodal) / fall back (entity).
        return {}
    suffix = candidate[index:]

    # 1) Strict decode of the first object from the opener. A clean object,
    #    optionally followed by trailing prose, is the answer — raw_decode stops
    #    at the end of the first value, so "{...} trailing {brace}" is handled.
    try:
        obj, _end = json.JSONDecoder().raw_decode(suffix)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    # 2) The opener did not start a clean object: a malformed leading object
    #    (trailing comma / single quotes / unquoted keys), possibly followed by
    #    trailing text such as a "[1]" citation, must still be recovered. Repair
    #    the first balanced slice and accept it only if it is itself an object.
    #    json_repair returns a *list* (not a dict) for pseudo-object prose like
    #    '{note}', so a real payload of the form '{note} [ {...} ]' falls through
    #    to {} here — a trailing top-level array is never scavenged for an
    #    element (repairing only the slice, never the whole candidate, is what
    #    keeps the array out of reach).
    slice_ = _first_balanced_object_slice(suffix)
    try:
        repaired = json_repair.loads(slice_)
        if isinstance(repaired, dict):
            return repaired
    except Exception:
        pass
    return {}


_RESERVED_ENTITY_TYPES = frozenset({"__proto__", "constructor", "prototype"})


def _normalize_and_validate_entity_type(entity_type: str, context: str) -> str | None:
    """Validate and normalize a sanitized entity_type extracted from an LLM.

    Shared by the text (delimited-record) and JSON extraction paths so both
    reject the same values. Expects ``entity_type`` to have already been passed
    through ``sanitize_and_normalize_extracted_text``.

    Returns the cleaned, space-stripped, lowercased type, or ``None`` when the
    entity must be dropped:
    - empty, or containing a structural delimiter character;
    - all-empty after a comma split;
    - a reserved JavaScript prototype member name (see ``_RESERVED_ENTITY_TYPES``).

    ``context`` is a short caller-supplied descriptor included in warning logs.
    """
    if not entity_type.strip() or any(
        char in entity_type for char in ["'", "(", ")", "<", ">", "|", "/", "\\"]
    ):
        logger.warning(f"Entity extraction error: invalid entity type in {context}")
        return None

    # Handle comma-separated entity types by finding the first non-empty token
    if "," in entity_type:
        original = entity_type
        tokens = [t.strip() for t in entity_type.split(",")]
        non_empty = [t for t in tokens if t]
        if not non_empty:
            logger.warning(
                f"Entity extraction error: all tokens empty after comma-split in {context}: '{original}'"
            )
            return None
        entity_type = non_empty[0]
        logger.warning(
            f"Entity type contains comma, taking first non-empty token in {context}: '{original}' -> '{entity_type}'"
        )

    # Remove spaces and convert to lowercase
    entity_type = entity_type.replace(" ", "").lower()

    if entity_type in _RESERVED_ENTITY_TYPES:
        logger.warning(
            f"Entity extraction error: reserved entity type '{entity_type}' rejected in {context}"
        )
        return None

    return entity_type


def _combine_descriptions_dedup(
    already_description: list[str], new_descriptions: list[str]
) -> tuple[list[str], int]:
    """Merge stored and newly-extracted descriptions, dropping exact duplicates.

    Stored fragments come first (preserving prior order), then new fragments not
    already present. Deduplicating across stored *and* new (not only within the
    new batch) prevents a re-extracted description from appending a duplicate
    fragment on every reprocess or resume (issue #3367); it also collapses any
    legacy duplicate fragments already stored. Returns the combined list and the
    count of surviving stored fragments, used for accurate merge accounting.

    Fragments are compared and stored *after* ``sanitize_text_for_encoding``:
    stored descriptions were already sanitized before being persisted (the
    single-description return and the join in ``_handle_entity_relation_summary``
    both sanitize), while a freshly re-extracted description is still raw.
    Comparing raw-vs-sanitized would miss the duplicate whenever sanitization
    changed the string (e.g. an XML-illegal control char was stripped), so
    "dirty" descriptions would keep accumulating across reprocess. Sanitizing
    here is idempotent, so the later sanitize calls are unaffected. Fragments
    that sanitize to empty are dropped (they carry no information and would only
    inflate the fragment count / emit ``<sep><sep>`` artifacts on join).

    The two-phase walk is deliberate: ``already_fragment`` is captured after the
    stored pass so it stays an accurate count of surviving stored fragments. Do
    NOT collapse both phases into a single ``dict.fromkeys`` over the
    concatenation — that loses the stored/new boundary the merge accounting log
    depends on.
    """
    combined: list[str] = []
    seen: set[str] = set()

    def _add_sanitized(descriptions: list[str]) -> None:
        for desc in descriptions:
            sanitized = sanitize_text_for_encoding(desc)
            if sanitized and sanitized not in seen:
                seen.add(sanitized)
                combined.append(sanitized)

    _add_sanitized(already_description)
    already_fragment = len(combined)
    _add_sanitized(new_descriptions)
    return combined, already_fragment
