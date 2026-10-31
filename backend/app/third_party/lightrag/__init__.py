"""LightRAG extraction prompts and output parser, vendored by scripts/vendor_lightrag.py."""

from app.third_party.lightrag._parsing import (
    _combine_descriptions_dedup as combine_descriptions_dedup,
)
from app.third_party.lightrag._parsing import (
    _normalize_and_validate_entity_type as normalize_entity_type,
)
from app.third_party.lightrag._parsing import (
    normalize_entity_name,
    repair_vlm_json_escape_damage_nested,
    sanitize_and_normalize_extracted_text,
    tolerant_load_json_dict,
)
from app.third_party.lightrag._prompts import PROMPTS

__all__ = [
    "PROMPTS",
    "combine_descriptions_dedup",
    "normalize_entity_name",
    "normalize_entity_type",
    "repair_vlm_json_escape_damage_nested",
    "sanitize_and_normalize_extracted_text",
    "tolerant_load_json_dict",
]
