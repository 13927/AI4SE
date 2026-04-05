from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from tree_sitter import Node
from tree_sitter_languages import get_parser


@dataclass(frozen=True)
class SymbolRecord:
    symbol_id: str
    kind: str
    language: str
    file: str
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    name: str | None = None
    qualname: str | None = None
    signature: str | None = None

    def to_json(self, *, module: str | None = None) -> dict[str, Any]:
        obj: dict[str, Any] = {
            "symbol_id": self.symbol_id,
            "kind": self.kind,
            "language": self.language,
            "file": self.file,
            "range": {
                "start_line": self.start_line,
                "start_col": self.start_col,
                "end_line": self.end_line,
                "end_col": self.end_col,
            },
        }
        if self.name:
            obj["name"] = self.name
        if self.qualname:
            obj["qualname"] = self.qualname
        if self.signature:
            obj["signature"] = self.signature
        if module:
            obj["module"] = module
        return obj


def _sid(*parts: str) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(p.encode("utf-8", errors="ignore"))
        h.update(b"\0")
    return h.hexdigest()


def _node_range_1based(n: Node) -> tuple[int, int, int, int]:
    # tree-sitter points are 0-based; we store 1-based for human friendliness
    (sl, sc) = n.start_point
    (el, ec) = n.end_point
    return sl + 1, sc + 1, el + 1, ec + 1


def _text_of(src: bytes, n: Node) -> str:
    return src[n.start_byte : n.end_byte].decode("utf-8", errors="ignore")


def _child_by_field(n: Node, field: str) -> Node | None:
    return n.child_by_field_name(field)


def _walk(root: Node) -> Iterable[Node]:
    stack = [root]
    while stack:
        n = stack.pop()
        yield n
        for i in range(n.child_count - 1, -1, -1):
            c = n.child(i)
            if c is not None:
                stack.append(c)


def extract_java_symbols(*, repo_root: Path, file_path: Path, rel_file: str) -> list[SymbolRecord]:
    src = file_path.read_bytes()
    parser = get_parser("java")
    tree = parser.parse(src)
    root = tree.root_node

    records: list[SymbolRecord] = []

    pkg: str | None = None
    # quick package extraction (tree-sitter has package_declaration)
    for n in _walk(root):
        if n.type == "package_declaration":
            ident = _child_by_field(n, "name")
            if ident is not None:
                pkg = _text_of(src, ident).strip()
            break

    def _qual(name: str) -> str:
        return f"{pkg}.{name}" if pkg else name

    for n in _walk(root):
        t = n.type
        if t in ("class_declaration", "interface_declaration", "enum_declaration"):
            name_node = _child_by_field(n, "name")
            if name_node is None:
                continue
            name = _text_of(src, name_node).strip()
            kind = "class" if t == "class_declaration" else ("interface" if t == "interface_declaration" else "enum")
            sl, sc, el, ec = _node_range_1based(n)
            qn = _qual(name)
            records.append(
                SymbolRecord(
                    symbol_id=f"java:{kind}:{_sid(rel_file, qn)}",
                    kind=kind,
                    language="java",
                    file=rel_file,
                    start_line=sl,
                    start_col=sc,
                    end_line=el,
                    end_col=ec,
                    name=name,
                    qualname=qn,
                )
            )
        elif t == "method_declaration":
            name_node = _child_by_field(n, "name")
            if name_node is None:
                continue
            name = _text_of(src, name_node).strip()
            sl, sc, el, ec = _node_range_1based(n)
            records.append(
                SymbolRecord(
                    symbol_id=f"java:method:{_sid(rel_file, name, str(sl), str(sc))}",
                    kind="method",
                    language="java",
                    file=rel_file,
                    start_line=sl,
                    start_col=sc,
                    end_line=el,
                    end_col=ec,
                    name=name,
                )
            )
        elif t == "constructor_declaration":
            name_node = _child_by_field(n, "name")
            name = _text_of(src, name_node).strip() if name_node is not None else None
            sl, sc, el, ec = _node_range_1based(n)
            records.append(
                SymbolRecord(
                    symbol_id=f"java:ctor:{_sid(rel_file, name or '', str(sl), str(sc))}",
                    kind="constructor",
                    language="java",
                    file=rel_file,
                    start_line=sl,
                    start_col=sc,
                    end_line=el,
                    end_col=ec,
                    name=name,
                )
            )
        # fields are intentionally omitted in v1; globals don't exist in Java the same way

    return records


def extract_cpp_symbols(*, repo_root: Path, file_path: Path, rel_file: str) -> list[SymbolRecord]:
    src = file_path.read_bytes()
    parser = get_parser("cpp")
    tree = parser.parse(src)
    root = tree.root_node

    records: list[SymbolRecord] = []

    for n in _walk(root):
        t = n.type

        # Free functions / methods (cpp grammar uses function_definition)
        if t == "function_definition":
            decl = _child_by_field(n, "declarator")
            name = None
            if decl is not None:
                # try to find identifier inside declarator
                for c in _walk(decl):
                    if c.type in ("identifier", "field_identifier"):
                        name = _text_of(src, c).strip()
                        break
            sl, sc, el, ec = _node_range_1based(n)
            records.append(
                SymbolRecord(
                    symbol_id=f"cpp:function:{_sid(rel_file, name or '', str(sl), str(sc))}",
                    kind="function",
                    language="cpp",
                    file=rel_file,
                    start_line=sl,
                    start_col=sc,
                    end_line=el,
                    end_col=ec,
                    name=name,
                )
            )

        # Type declarations
        elif t in ("class_specifier", "struct_specifier", "enum_specifier", "namespace_definition"):
            name_node = _child_by_field(n, "name")
            name = _text_of(src, name_node).strip() if name_node is not None else None
            kind = (
                "class"
                if t in ("class_specifier", "struct_specifier")
                else ("enum" if t == "enum_specifier" else "namespace")
            )
            sl, sc, el, ec = _node_range_1based(n)
            records.append(
                SymbolRecord(
                    symbol_id=f"cpp:{kind}:{_sid(rel_file, name or '', str(sl), str(sc))}",
                    kind=kind,
                    language="cpp",
                    file=rel_file,
                    start_line=sl,
                    start_col=sc,
                    end_line=el,
                    end_col=ec,
                    name=name,
                )
            )

        # Global variables / constants: heuristically take declarations that are direct children of translation_unit
        elif t == "declaration" and n.parent is not None and n.parent.type == "translation_unit":
            # skip forward declarations like "class X;" or "namespace N;"
            # keep simple variable declarations; we look for init_declarator / identifier tokens.
            var_name = None
            for c in _walk(n):
                if c.type in ("identifier",):
                    var_name = _text_of(src, c).strip()
                    break
            if not var_name:
                continue
            sl, sc, el, ec = _node_range_1based(n)
            records.append(
                SymbolRecord(
                    symbol_id=f"cpp:global:{_sid(rel_file, var_name, str(sl), str(sc))}",
                    kind="global_var",
                    language="cpp",
                    file=rel_file,
                    start_line=sl,
                    start_col=sc,
                    end_line=el,
                    end_col=ec,
                    name=var_name,
                )
            )

    return records

