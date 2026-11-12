"""Inspect HID report sizes/pages. Input is a raw binary report map, not HID packets."""
import argparse
from pathlib import Path


def inspect(data):
    state = {"page": 0, "id": 0, "size": 0, "count": 0}
    stack, reports = [], []
    offset = collections = 0
    while offset < len(data):
        prefix = data[offset]
        offset += 1
        if prefix == 0xFE:
            if offset + 2 > len(data):
                raise ValueError("Truncated HID long-item header")
            size = data[offset]
            offset += 2
        else:
            size = (0, 1, 2, 4)[prefix & 3]
        if offset + size > len(data):
            raise ValueError("Truncated HID item")
        value = int.from_bytes(data[offset:offset + size], "little")
        offset += size
        if prefix == 0xFE:
            continue
        kind, tag = (prefix >> 2) & 3, prefix >> 4
        if kind == 1:
            if tag in (0, 7, 8, 9):
                state[{0: "page", 7: "size", 8: "id", 9: "count"}[tag]] = value
            elif tag == 10:
                stack.append(state.copy())
            elif tag == 11:
                if not stack:
                    raise ValueError("HID global stack underflow")
                state = stack.pop()
        elif kind == 0:
            if tag == 10:
                collections += 1
            elif tag == 12:
                collections -= 1
                if collections < 0:
                    raise ValueError("HID collection underflow")
            elif tag in (8, 9, 11):
                reports.append({**state, "type": {8: "input", 9: "output", 11: "feature"}[tag],
                                "bits": state["size"] * state["count"]})
    if collections or stack:
        raise ValueError("Unclosed HID collection/global stack")
    return reports


def self_check():
    # Complete 149-byte map read from the user's remote over 2A4B; contains no identifiers.
    data = bytes.fromhex("""
        05010906a1010507850195037508150025ff190029ff8100c0
        050c0901a1018502950275101500269c0219002a9c028100c0
        06ff000900a10185f095507508150025ff810085f19503750809008102
        85f2950175080900910285f3950a750809009102
        85ef95037508150025ff190029ff09018100c0
        050c0901a101850305010906a102050609201500266400750895018102c0c0
    """)
    assert len(data) == 149
    reports = inspect(data)
    assert len(reports) == 8
    assert [(r["id"], r["bits"]) for r in reports if r["type"] == "output"] == [(0xF2, 8), (0xF3, 80)]
    assert not any(r["page"] == 8 for r in reports)
    assert inspect(bytes.fromhex("0508750195019102"))[0]["page"] == 8
    assert inspect(bytes.fromhex("0508a405ffb4750195019102"))[0]["page"] == 8
    assert inspect(bytes.fromhex("fe0200aabb")) == []
    for broken in [data[:-1], b"\x06\x01", b"\xb4", b"\xc0", b"\xfe", b"\xfe\x03\x00\x01"]:
        try:
            inspect(broken)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Accepted malformed HID map: {broken.hex()}")
    print("HID map checks passed: captured descriptor, vendor outputs, LED page and malformed items")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", nargs="?", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_check()
    elif args.file:
        for report in inspect(args.file.read_bytes()):
            print(f'ID=0x{report["id"]:02X} {report["type"]:7} page=0x{report["page"]:04X} bits={report["bits"]}')
    else:
        parser.error("Supply a binary report map or --self-test")
