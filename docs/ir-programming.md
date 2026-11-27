# IR button programming research (parked)

Status: research only, no IR writes performed on any remote. Parked 2026-11-27;
resume after Chromecast voice work.

## Shared programming service: `d343bfc0-5a21-4f05-bc7d-af01f617b664`

Inventoried on both the G10/ONN (ABBEY) and the Chromecast Remote. Public
implementations: `Genius1237/g10-ir` (Linux/bleak) and `bytestore/g10-ir-win`
(Windows/bleak); both program codes over this service.

| Characteristic | Role | Chromecast handle | G10 |
|---|---|---|---|
| `d343bfc1-…` | programming session START (0x01) / STOP (0x00) | 0x2d | ✓ |
| `d343bfc2-…` | KEY: 2-byte Android keycode (0x18=VOL+, 0x19=VOL−, 0x1a=POWER, 0xa4=VOLUME_MUTE, 0xb2=TV_INPUT) | 0x2f | ✓ |
| `d343bfc3-…` | VALUE: raw IR code | 0x31 | ✓ |
| `d343bfc4-…` | unknown (status/ack?) | 0x33 | — |
| `d343bfc5-…` | notify, unknown (IR learning?) | 0x35 | — |

Sequence: write `bfc1=01`, then per key `(bfc2=key, bfc3=code)`, then `bfc1=00`.

## IR code format (from g10-ir captures)

Timing-pairs format, NOT the codes published online: capture starts e.g.
`02 21 01 7c 00 20` followed by 16-bit big-endian (mark, space) pairs. The
g10-ir author notes the remote rejects database-style codes and that the
captured values differ in scale (e.g. `000c` vs `000a`) — the encoding is
unresolved. `Genius1237` derived working codes by sniffing an onn Android TV
box programming the remote (Wireshark HCI snoop). AOSP Telink reference
`vendor/827x_ble_remote/app_ir.c` is the authoritative parser to decode the
format.

## Notes for an HA feature later

- Would fit as a device action in `bluetooth_tv_remotes`: program a key with
  an IR code via D-Bus GATT (same mechanism as ATVV voice, no extra perms).
- Code library would need conversion from common formats (Pronto/IRDB/
  Broadlink) to this timing-pairs format; format spec from `app_ir.c`.
- Amazon remote uses a different protocol (service `fe151500…`, chunked
  writes — see upstream `Pro-me3us/fire_remote_ir_programmer` research).
- READMEs claim the remote must be in pairing mode during programming (Back+
  Home, or Power+Back on G10); possibly only needed for discovery, unverified.
- Risk: writing a bad code is recoverable (reprogram the key); brick risk low.
- References: `bytestore/g10-ir-win` links FCC docs for the Ohsung C009 remote
  (`fcc.report/FCC-ID/OZ5C009/5122343.pdf`) and the AOSP Telink firmware.
