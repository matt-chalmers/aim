"""Change one top-level block of a hand-written YAML file, touching only the lines whose
values change — or refuse, and say what to paste.

WHY NOT A ROUND-TRIP. `yaml.safe_dump` discards every comment and reorders keys, and a real
`harness.yaml` is mostly comments explaining why each value is what it is. A comment-keeping
YAML library would be a second runtime dependency installed everywhere the plugin is
(`harness/pyproject.toml`: "KEEP THIS SET SMALL"), and it still reflows layouts. So this is
the line-anchored technique `check_commands._stack_entry_span` / `_set_in_entry` use for one
command, generalised to a whole block: walk the block by indentation, edit only what
changed, and leave every other line — comments inside the block included — byte-for-byte.
A removed key or list item takes its own lines, comments on them included.

BEING WRONG COSTS A REFUSAL, NEVER A FILE. Every edit ends with a post-condition: re-parse
the result, require the target block to equal what was asked for and every other top-level
key to equal what was there. Anything the walker cannot anchor — an anchor or alias, a
changed block scalar, a flow collection spanning lines, duplicate keys, tab indentation —
raises `Unanchorable` with the block rendered for the owner to paste.

WHAT MOVES RATHER THAN CHANGES. A list keeps each surviving item's lines verbatim, comments
with them, in the new order; an item matched by identity (a stack's `name@root`, an area's
`path`) but changed is edited in place. Only a genuinely new item is rendered.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import yaml


class Unanchorable(Exception):
    """The edit cannot be made safely. `fragment` is the block, rendered, to paste by hand."""

    def __init__(self, why: str, fragment: str = ""):
        super().__init__(why)
        self.why = why
        self.fragment = fragment


_MISSING = object()
#: A mapping key at the start of a line: plain, or single/double quoted.
_KEY = re.compile(
    r"""^(?P<indent> *)(?P<key>"[^"]*"|'[^']*'|[^\s#'"\-][^:#]*?|-[^\s][^:#]*?):(?=\s|$)"""
)
_WIDE = 10**9


# --- line helpers -------------------------------------------------------------------------


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _blank(line: str) -> bool:
    return not line.strip()


def _comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def _content(line: str) -> bool:
    return not _blank(line) and not _comment(line)


def _eol(line: str) -> str:
    return "\n" if line.endswith("\n") else ""


def _split_comment(text: str) -> tuple[str, str]:
    """`value  # note` → (`value`, `  # note`).

    A quote is only a quote at the START of a scalar — `it's docs  # note` is a plain
    scalar containing an apostrophe, and treating that apostrophe as an opening quote lost
    the comment. Inside a double-quoted scalar `\\"` is escaped; inside a single-quoted one
    `''` is."""
    i = 0
    if text and text[0] in "'\"":
        q, i = text[0], 1
        while i < len(text):
            if q == '"' and text[i] == "\\":
                i += 2
                continue
            if text[i] == q:
                if q == "'" and text[i + 1 : i + 2] == "'":
                    i += 2
                    continue
                i += 1
                break
            i += 1
    while i < len(text):
        if text[i] == "#" and (i == 0 or text[i - 1] in " \t"):
            j = i
            while j > 0 and text[j - 1] in " \t":
                j -= 1
            return text[:j], text[j:]
        i += 1
    return text.rstrip(), text[len(text.rstrip()) :]


def _same(a: Any, b: Any) -> bool:
    """Equal AND the same type, all the way down. Python's `1 == True` and `1 == 1.0` would
    let a requested `true` stay written as `1` — and the post-condition, comparing the same
    way, would pass it."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def _key_line(line: str) -> tuple[str, str, str, str] | None:
    """A key line split as (head through the colon and its space, value text, comment, eol)."""
    m = _KEY.match(line)
    if not m:
        return None
    body = line[m.end() :].rstrip("\n")
    lead = body[: len(body) - len(body.lstrip(" "))]
    value, comment = _split_comment(body.lstrip(" "))
    if not value:
        # `key:      # note` with the value on the lines below: the spacing belongs to the
        # comment, so a value written onto this line lands before it, not glued to it.
        return line[: m.end()], value, (lead + comment) if comment else "", _eol(line)
    return line[: m.end()] + lead, value, comment, _eol(line)


def _parse_key(token: str) -> Any:
    return yaml.safe_load(f"{token}: 0").popitem()[0]


# --- rendering ----------------------------------------------------------------------------


def _inline(value: Any) -> str:
    """One value as a single-line YAML token: a scalar, `[]`, `{}`, or a flow collection."""
    return yaml.safe_dump(
        [value],
        default_flow_style=True,
        sort_keys=False,
        width=_WIDE,
        allow_unicode=True,
    ).strip()[1:-1]


def _block(value: Any, indent: int) -> list[str]:
    text = yaml.safe_dump(
        value,
        default_flow_style=False,
        sort_keys=False,
        width=_WIDE,
        allow_unicode=True,
    )
    return [(" " * indent + ln if ln else ln) + "\n" for ln in text.splitlines()]


def _is_collection(v: Any) -> bool:
    return isinstance(v, (dict, list))


def _fragment(top_key: str, value: Any) -> str:
    return (
        "".join(_block({top_key: value}, 0))
        if value is not _MISSING
        else f"# remove the `{top_key}:` block\n"
    )


# --- regions ------------------------------------------------------------------------------


def _children(lines: list[str], indent: int) -> list[tuple[Any, int, int]]:
    """The mapping's children at `indent`: (key, first line, end), where a child's region runs
    to its last content line or deeper comment — blank lines and comments at or left of
    `indent` after it belong to whatever follows."""
    starts = []
    for i, ln in enumerate(lines):
        if _content(ln) and _indent(ln) == indent:
            if not _KEY.match(ln):
                if ln.lstrip().startswith("- ") or ln.strip() == "-":
                    continue  # a sequence at the key's own indent, owned by the key above
                raise Unanchorable(f"cannot read the line {ln.strip()!r} as a key")
            starts.append(i)
        elif _content(ln) and _indent(ln) < indent:
            raise Unanchorable(f"unexpected outdent at {ln.strip()!r}")
    out = []
    keys_seen = set()
    for n, s in enumerate(starts):
        e = starts[n + 1] if n + 1 < len(starts) else len(lines)
        while e - 1 > s and (
            _blank(lines[e - 1])
            or (_comment(lines[e - 1]) and _indent(lines[e - 1]) <= indent)
        ):
            e -= 1
        key = _parse_key(_KEY.match(lines[s]).group("key"))
        if key in keys_seen:
            raise Unanchorable(f"the key {key!r} appears twice")
        keys_seen.add(key)
        out.append((key, s, e))
    return out


def _items(
    lines: list[str],
) -> tuple[list[tuple[list[str], list[str]]], list[str], int]:
    """A block sequence's items as (leading lines, item lines), the trailing lines, and the
    dash indent."""
    first = next((ln for ln in lines if _content(ln)), None)
    if first is None or not first.lstrip().startswith("-"):
        raise Unanchorable("expected a block sequence")
    dash = _indent(first)
    items: list[tuple[list[str], list[str]]] = []
    pending: list[str] = []
    current: list[str] | None = None
    for ln in lines:
        if _content(ln) and _indent(ln) == dash and ln.lstrip().startswith("-"):
            current = [ln]
            items.append((pending, current))
            pending = []
        elif current is not None and (
            _content(ln) and _indent(ln) > dash or _comment(ln) and _indent(ln) > dash
        ):
            current.extend(pending)
            pending = []
            current.append(ln)
        elif _content(ln) and _indent(ln) <= dash:
            raise Unanchorable(f"unexpected line in a sequence: {ln.strip()!r}")
        else:
            pending.append(ln)
    return items, pending, dash


# --- the walk -----------------------------------------------------------------------------


def _refuse_specials(region: list[str]) -> None:
    for ln in region:
        if ln[: len(ln) - len(ln.lstrip())].count("\t"):
            raise Unanchorable("tab indentation")
        value = _split_comment(ln.strip())[0]
        if re.search(r"(^|[:\-]\s+)[&*!]\S", value) or value.startswith(
            ("&", "*", "!")
        ):
            raise Unanchorable("an anchor, alias or tag")


def _edit_value(
    region: list[str], indent: int, old: Any, new: Any, identity: Callable | None
) -> list[str]:
    """`region` is one key's lines: the key line at `indent`, then its value's lines."""
    if _same(old, new):
        return region
    _refuse_specials(region)
    head, value, comment, eol = _key_line(region[0])
    rest = region[1:]
    if value.startswith(("|", ">")):
        raise Unanchorable("a block scalar whose value changed")
    if value:
        if value[0] in "[{" and value.count(value[0]) != value.count(
            "]" if value[0] == "[" else "}"
        ):
            raise Unanchorable("a flow collection spanning lines")
        if any(_content(ln) for ln in rest):
            raise Unanchorable("a scalar continued over several lines")
        if not _is_collection(new) or not new or value[0] in "[{":
            return [f"{head}{_inline(new)}{comment}{eol}", *rest]
        return [f"{head.rstrip()}{comment}{eol}", *_block(new, indent + 2), *rest]

    # A block value (or an empty one): edit in place when the shape is unchanged.
    body = rest
    if isinstance(old, dict) and isinstance(new, dict) and new:
        child = next(_indent(ln) for ln in body if _content(ln))
        return [region[0], *_edit_mapping(body, child, old, new, identity)]
    if isinstance(old, list) and isinstance(new, list) and new:
        return [region[0], *_edit_list(body, old, new, identity)]
    keep = [
        ln for ln in body if _comment(ln)
    ]  # the owner's notes inside a replaced value survive
    if not _is_collection(new) or not new:
        return [f"{head.rstrip()} {_inline(new)}{comment}{eol}", *keep]
    return [f"{head.rstrip()}{comment}{eol}", *keep, *_block(new, indent + 2)]


def _edit_mapping(
    lines: list[str], indent: int, old: dict, new: dict, identity: Callable | None
) -> list[str]:
    kids = _children(lines, indent)
    if {k for k, _, _ in kids} != set(old):
        raise Unanchorable("the block's keys could not all be located")
    out: list[str] = []
    pos = 0
    last_end = 0
    for key, s, e in kids:
        out.extend(lines[pos:s])
        if key in new:
            out.extend(_edit_value(lines[s:e], indent, old[key], new[key], None))
            last_end = len(out)
        pos = e
    tail = lines[pos:]
    added = [
        line for k, v in new.items() if k not in old for line in _block({k: v}, indent)
    ]
    return out[:last_end] + added + out[last_end:] + tail


def _edit_list(
    lines: list[str], old: list, new: list, identity: Callable | None
) -> list[str]:
    items, tail, dash = _items(lines)
    if len(items) != len(old):
        raise Unanchorable("the sequence's items could not all be located")
    used: set[int] = set()
    out: list[str] = []
    for value in new:
        i = next(
            (
                n
                for n, o in enumerate(old)
                if n not in used and _same(o, value)
            ),
            None,
        )
        if i is not None:
            used.add(i)
            out.extend(items[i][0] + items[i][1])
            continue
        if identity is not None:
            i = next(
                (
                    n
                    for n, o in enumerate(old)
                    if n not in used and identity(o) == identity(value)
                ),
                None,
            )
            if i is not None:
                used.add(i)
                out.extend(items[i][0] + _edit_item(items[i][1], dash, old[i], value))
                continue
        out.extend(_block([value], dash))
    return out + tail


def _edit_item(item: list[str], dash: int, old: Any, new: Any) -> list[str]:
    """One sequence item changed in place: a mapping item is walked as a mapping."""
    first = item[0]
    after = first[dash + 1 :]
    if after.lstrip().startswith(("{", "[")):
        # A flow item (`- {name: node-npm, root: web}`) is one line: rewrite that line.
        if any(_content(ln) for ln in item[1:]):
            raise Unanchorable("a flow collection spanning lines")
        _, comment = _split_comment(after.strip())
        return [f"{first[:dash]}- {_inline(new)}{comment}{_eol(first)}", *item[1:]]
    if (
        isinstance(old, dict)
        and isinstance(new, dict)
        and after.strip()
        and not after.lstrip().startswith("#")
    ):
        child = dash + 1 + (len(after) - len(after.lstrip(" ")))
        as_map = [first[:dash] + " " + first[dash + 1 :], *item[1:]]
        edited = _edit_mapping(as_map, child, old, new, None)
        for n, ln in enumerate(edited):
            if _content(ln) and _indent(ln) == child:
                edited[n] = ln[:dash] + "-" + ln[dash + 1 :]
                break
        return edited
    _, comment = _split_comment(first[dash + 1 :].rstrip("\n"))
    rendered = _block([new], dash)
    rendered[0] = rendered[0].rstrip("\n") + comment + "\n"
    return rendered


# --- the public surface -------------------------------------------------------------------


def _post_condition(before: dict, text: str, top_key: str, want: Any) -> str:
    try:
        after = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise Unanchorable(
            f"the edit produced invalid YAML: {exc}", _fragment(top_key, want)
        ) from exc
    got = after.get(top_key, _MISSING)
    if not _same(got, want) or not _same(
        {k: v for k, v in after.items() if k != top_key},
        {k: v for k, v in before.items() if k != top_key},
    ):
        raise Unanchorable(
            "the edit did not produce exactly the requested block",
            _fragment(top_key, want),
        )
    return text


def _load(text: str, top_key: str, value: Any) -> dict:
    try:
        before = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise Unanchorable(
            f"the file is not valid YAML: {exc}", _fragment(top_key, value)
        ) from exc
    if not isinstance(before, dict):
        raise Unanchorable("the file is not a mapping", _fragment(top_key, value))
    return before


def set_block(
    text: str, top_key: str, value: Any, *, identity: Callable[[Any], Any] | None = None
) -> str:
    """`text` with the top-level `top_key` set to `value`. A missing key is appended."""
    before = _load(text, top_key, value)
    if _same(before.get(top_key, _MISSING), value):
        return text
    lines = text.splitlines(keepends=True)
    try:
        kids = _children(lines, 0)
        span = next(((s, e) for k, s, e in kids if k == top_key), None)
        if span is None:
            sep = (
                ""
                if not text or text.endswith("\n\n")
                else ("\n" if text.endswith("\n") else "\n\n")
            )
            new = text + sep + "".join(_block({top_key: value}, 0))
        else:
            s, e = span
            edited = _edit_value(lines[s:e], 0, before[top_key], value, identity)
            new = "".join(lines[:s] + edited + lines[e:])
    except Unanchorable as exc:
        raise Unanchorable(exc.why, _fragment(top_key, value)) from None
    except (
        yaml.YAMLError,
        ValueError,
        TypeError,
        IndexError,
        StopIteration,
        AttributeError,
    ) as exc:
        # A shape the walker did not anticipate. Being wrong must cost a refusal, never a
        # crash mid-setup and never a file: the owner pastes the fragment.
        raise Unanchorable(
            f"an unexpected layout ({type(exc).__name__})", _fragment(top_key, value)
        ) from None
    return _post_condition(before, new, top_key, value)


def remove_block(text: str, top_key: str) -> str:
    """`text` without the top-level `top_key` — its own lines only; the comments above it,
    which describe the block a reader may want back, stay."""
    before = _load(text, top_key, _MISSING)
    if top_key not in before:
        return text
    lines = text.splitlines(keepends=True)
    try:
        s, e = next((s, e) for k, s, e in _children(lines, 0) if k == top_key)
    except (Unanchorable, StopIteration):
        raise Unanchorable(
            f"cannot locate `{top_key}:` to remove it", _fragment(top_key, _MISSING)
        ) from None
    return _post_condition(before, "".join(lines[:s] + lines[e:]), top_key, _MISSING)
