"""Optional read-before-write, verified readback, explicit profile restrictions."""
import time
import uuid
from advanced_config import finite


def parse_write(command):
    parts = command.split(";")
    kind = parts[0].strip().lower()
    if kind in ("write", "w"):
        addr, length, value = int(parts[1], 0), int(parts[2]), int(parts[3], 0)
        if not 1 <= length <= 250:
            raise ValueError("Invalid write length")
        data = value.to_bytes(length, "little", signed=value < 0)
    elif kind in ("writeraw", "wraw"):
        addr = int(parts[1], 0)
        data = bytes.fromhex(parts[2].removeprefix("0x"))
    else:
        return None
    if not 0 <= addr <= 65535 or not 1 <= len(data) <= 250:
        raise ValueError("Invalid write address or length")
    return addr, data


def guarded_write(command, source, diag, read, execute, pause):
    """Run on the existing serial worker. No write retries or rollback writes."""
    cfg = diag.config
    event = {"id": uuid.uuid4().hex, "time": time.time(), "source": source}
    def finish(status, result):
        event["status"] = status
        diag.audit.append(event.copy())
        return result
    def reject(reason):
        return finish(reason, (0xFB, bytearray(), None, "write rejected: " + reason))
    if cfg.read_only:
        return reject("read_only")
    try:
        parsed = parse_write(command)
    except (ValueError, IndexError, OverflowError):
        return reject("invalid_command")
    if parsed is None:
        if cfg.verified_writes or cfg.require_profile_for_writes or diag.profile is not None:
            return reject("opaque_command")
        if diag.last_write is not None and time.monotonic() - diag.last_write < cfg.write_min_interval:
            return reject("rate_limit")
        diag.last_write = time.monotonic()
        result = execute()
        return finish("unverified_legacy" if result[0] == 1 else "failed", result)
    addr, target = parsed
    event.update(address="0x%04x" % addr, requested=target.hex())
    profile = diag.profile
    if cfg.require_profile_for_writes or profile is not None:
        rule = (profile or {}).get("writable", {}).get("%04x" % addr)
        if not rule or rule["length"] != len(target):
            return reject("profile_denied")
        lo, hi = rule["raw_limits"]
        value = int.from_bytes(target, "little", signed=rule.get("signed", False))
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            return reject("profile_limit")
    if diag.last_write is not None and time.monotonic() - diag.last_write < cfg.write_min_interval:
        return reject("rate_limit")
    if not cfg.verified_writes:
        diag.last_write = time.monotonic()
        result = execute()
        return finish("accepted_unverified" if result[0] == 1 else "failed", result)
    code, response_addr, before = read(addr, len(target))
    if code == 0xAA:
        return finish("usb_lost", (code, before, None, "USB connection lost"))
    if code != 1 or response_addr != addr or len(before) != len(target):
        return reject("pre_read_failed")
    event["before"] = bytes(before).hex()
    if before == target:
        return finish("unchanged", (1, bytearray(before), int.from_bytes(target, "little"), "1;0x%04x;%s" % (addr, target.hex())))
    pause()
    diag.last_write = time.monotonic()
    result = execute()
    if result[0] != 1:
        return finish("write_failed", result)
    pause()
    code, response_addr, after = read(addr, len(target))
    event["after"] = bytes(after).hex()
    if code != 1 or response_addr != addr or after != target:
        return finish("verification_failed", (0xAA if code == 0xAA else 0xFC, bytearray(after), None, "write verification failed"))
    return finish("verified", result)
