"""Vendor LightRAG's entity/relation extraction pieces into app/third_party/lightrag.

LightRAG (MIT, https://github.com/HKUDS/LightRAG) is a heavy dependency (runtime
package installer, cloud SDKs, its own storage layer), but its extraction prompts and
its tolerant parser for model output are self-contained and battle-tested. This
script copies exactly those top-level definitions — plus every module-level name
they reference, transitively — verbatim from a pinned wheel, so the provenance of
every vendored line is mechanical and re-checkable.

    .venv/bin/python scripts/vendor_lightrag.py path/to/lightrag_hku-1.5.7-py3-none-any.whl
"""

import ast
import sys
import zipfile
from pathlib import Path

VERSION = "1.5.7"
MODULES = [
    "lightrag/utils.py",
    "lightrag/operate.py",
    "lightrag/constants.py",
    "lightrag/prompt.py",
]
# What Strata uses directly; everything these reference is pulled in automatically.
SEEDS = [
    "tolerant_load_json_dict",
    "normalize_entity_name",
    "sanitize_and_normalize_extracted_text",
    "sanitize_text_for_encoding",
    "_normalize_and_validate_entity_type",
    "_combine_descriptions_dedup",
    "repair_vlm_json_escape_damage_nested",
]
PROMPT_KEYS = [
    "entity_extraction_json_system_prompt",
    "entity_extraction_json_user_prompt",
    "entity_extraction_json_examples",
]
# Third-party modules the vendored code may import; anything else is an error.
ALLOWED_IMPORTS = {
    "re",
    "html",
    "json",
    "json_repair",
    "logging",
    "unicodedata",
    "typing",
    "collections",
}
OUT = Path(__file__).resolve().parent.parent / "app" / "third_party" / "lightrag"


def defined_names(node: ast.stmt) -> list[str]:
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return [node.name]
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def main(wheel: str) -> None:
    zf = zipfile.ZipFile(wheel)
    defs: dict[str, tuple[str, ast.stmt, str]] = {}
    imports: dict[str, str] = {}
    for module in MODULES:
        source = zf.read(module).decode()
        tree = ast.parse(source)
        for node in tree.body:
            for name in defined_names(node):
                defs.setdefault(name, (module, node, ast.get_source_segment(source, node)))
            if isinstance(node, ast.Import | ast.ImportFrom):
                mod = node.module if isinstance(node, ast.ImportFrom) else None
                for alias in node.names:
                    root = (mod or alias.name).split(".")[0]
                    if root != "lightrag":
                        line = ast.get_source_segment(source, node)
                        imports[alias.asname or alias.name] = line

    keep: dict[str, tuple[str, ast.stmt, str]] = {}
    needed_imports: set[str] = set()
    todo = list(SEEDS)
    while todo:
        name = todo.pop()
        if name in keep or name not in defs:
            continue
        keep[name] = defs[name]
        for sub in ast.walk(defs[name][1]):
            if isinstance(sub, ast.Name):
                if sub.id in defs and sub.id not in keep:
                    todo.append(sub.id)
                elif sub.id in imports:
                    needed_imports.add(sub.id)

    bad = {n for n in needed_imports if imports[n].split()[1].split(".")[0] not in ALLOWED_IMPORTS}
    bad -= {"logger"}
    if bad:
        raise SystemExit(f"Vendored code would import non-allowed modules: {sorted(bad)}")

    # Keep the original definition order within each module.
    order = {(m, n.lineno): name for name, (m, n, _) in keep.items()}
    body = "\n\n\n".join(
        keep[order[k]][2] for k in sorted(order, key=lambda k: (MODULES.index(k[0]), k[1]))
    )
    header_imports = sorted({imports[n] for n in needed_imports})

    prompt_src = zf.read("lightrag/prompt.py").decode()
    prompt_tree = ast.parse(prompt_src)
    prompts = []
    for node in prompt_tree.body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Subscript)
            and isinstance(node.targets[0].slice, ast.Constant)
            and node.targets[0].slice.value in PROMPT_KEYS
        ):
            prompts.append(ast.get_source_segment(prompt_src, node))

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "LICENSE").write_bytes(zf.read(f"lightrag_hku-{VERSION}.dist-info/licenses/LICENSE"))
    banner = (
        f'"""Vendored verbatim from LightRAG {VERSION} (MIT, see LICENSE in this folder) by\n'
        f"scripts/vendor_lightrag.py. Do not edit by hand; re-run the script instead.\n"
        f'Sources: {", ".join(MODULES)}."""\n\n'
        "# ruff: noqa\n# fmt: off\n"
    )
    (OUT / "_parsing.py").write_text(banner + "\n".join(header_imports) + "\n\n\n" + body + "\n")
    (OUT / "_prompts.py").write_text(
        banner + "\nPROMPTS: dict = {}\n\n" + "\n\n".join(prompts) + "\n"
    )
    print(f"Vendored {len(keep)} definitions and {len(prompts)} prompts into {OUT}")
    for name, (module, node, _) in sorted(keep.items(), key=lambda kv: kv[1][0]):
        print(f"  {module}:{node.lineno} {name}")


if __name__ == "__main__":
    main(sys.argv[1])
