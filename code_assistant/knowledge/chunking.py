"""Adapted from Agent_MCP_RAG chunking: fence-aware, exact source line spans."""
from dataclasses import dataclass
import re

VERSION = "fenced-lines-v1"


@dataclass
class Piece:
    text: str
    start_line: int
    end_line: int
    heading: str


@dataclass
class Parent:
    piece: Piece
    children: list[Piece]


def split_parent_child(text, parent_size=2400, child_size=500, overlap=80):
    if not 0 <= overlap < child_size <= parent_size:
        raise ValueError("invalid_chunk_sizes")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines(keepends=True)
    sections, blocks, block, path = [], [], [], []
    fence, fence_len = "", 0

    def flush():
        nonlocal block
        if block:
            blocks.append(block)
            block = []

    for i, line in enumerate(lines, 1):
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            block.append((i, line))
            if marker and marker[1][0] == fence and len(marker[1]) >= fence_len and not marker[2].strip():
                fence = ""
                flush()
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)", line)
        if heading:
            flush()
            if blocks:
                sections.append((" / ".join(v for _, v in path), blocks))
                blocks = []
            depth = len(heading[1])
            path = [(d, v) for d, v in path if d < depth] + [(depth, heading[2].strip())]
        if marker:
            flush()
            fence, fence_len = marker[1][0], len(marker[1])
        block.append((i, line))
        if not fence and not line.strip():
            flush()
    if fence:
        raise ValueError("unclosed_code_fence")
    flush()
    if blocks:
        sections.append((" / ".join(v for _, v in path), blocks))

    def pack(units, size, shared=0):
        result, current = [], []
        for unit in units:
            length = sum(len(s) for _, s in unit)
            if length > parent_size:
                # Reject instead of silently corrupting a function or fenced example.
                raise ValueError("oversized_atomic_block")
            if current and sum(len(s) for u in current for _, s in u) + length > size:
                result.append(current)
                tail = []
                for prior in reversed(current):
                    if sum(len(s) for u in [prior] + tail for _, s in u) > shared:
                        break
                    tail.insert(0, prior)
                current = tail
            current.append(unit)
        if current:
            result.append(current)
        return result

    def piece(units, heading):
        flat = [line for unit in units for line in unit]
        return Piece("".join(s for _, s in flat), flat[0][0], flat[-1][0], heading)

    parents = []
    for heading, units in sections:
        for parent_units in pack(units, parent_size):
            parents.append(Parent(piece(parent_units, heading),
                [piece(child, heading) for child in pack(parent_units, child_size, overlap)]))
    if not parents:
        raise ValueError("empty_document")
    return parents
