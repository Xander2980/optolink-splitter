"""Multi-model local catalog; metadata never authorizes writes."""
import csv
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "profiles"


class Catalog:
    def __init__(self, root=ROOT):
        self.profiles = {}
        self.lists = {}
        self.errors = []
        for path in sorted(root.glob("*.json")):
            try:
                p = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(p, dict) or p.get("name") != path.stem:
                    raise ValueError("invalid name")
                ident = p["device_id"]
                if len(ident) != 4 or any(c not in "0123456789abcdef" for c in ident):
                    raise ValueError("invalid device ID")
                if not 0 <= p["version_min"] <= p["version_max"] <= 65535:
                    raise ValueError("invalid version range")
                for role, rule in p.get("roles", {}).items():
                    if not isinstance(rule["name"], str) or type(rule["address"]) is not int or type(rule["length"]) is not int:
                        raise ValueError("invalid role")
                    if not 0 <= rule["address"] <= 65535 or not 1 <= rule["length"] <= 255:
                        raise ValueError("invalid role address/length")
                    if not math.isfinite(float(rule["scale"])):
                        raise ValueError("invalid scale")
                if any(not k.isdecimal() or type(v) is not int or v not in (0, 1) for k,v in p.get("burner_states", {}).items()):
                    raise ValueError("invalid burner states")
                fw = p.get("firmware_datapoint")
                if fw is not None and (not isinstance(fw, list) or len(fw) != 2 or type(fw[0]) is not int or not 0 <= fw[0] <= 65535 or type(fw[1]) is not int or not 1 <= fw[1] <= 250):
                    raise ValueError("invalid firmware datapoint")
                self.profiles[path.stem] = p
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                self.errors.append(f"{path.name}: {exc}")
        for path in sorted(root.glob("*.tsv")):
            try:
                if path.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError("file exceeds 8 MiB")
                with path.open(encoding="utf-8-sig", errors="replace", newline="") as file:
                    reader = csv.reader(file, delimiter="\t", quoting=csv.QUOTE_NONE)
                    if next(reader, [])[:6] != ["Model", "Parameter", "Address", "Conversion", "ReadWrite", "ByteLength"]:
                        raise ValueError("unsupported TSV header")
                    for row in reader:
                        if len(row) < 6:
                            continue
                        model, name, address, conversion, access, length = row[:6]
                        try:
                            addr, size = int(address, 16), int(length)
                        except ValueError:
                            continue
                        if not model or not name or not 0 <= addr <= 65535 or not 1 <= size <= 255:
                            continue
                        group = self.lists.setdefault(model, [])
                        if len(group) >= 20000:
                            raise ValueError("model row limit exceeded")
                        unit = next((v.removeprefix("ecnUnit.") for v in row[6:] if v.startswith("ecnUnit.")), "")
                        group.append({"name":name,"address":f"0x{addr:04X}","conversion":conversion,
                                      "access":access,"length":size,"unit":unit,"file":path.name})
            except (OSError, ValueError, csv.Error) as exc:
                self.errors.append(f"{path.name}: {exc}")

    def match(self, ident):
        if not isinstance(ident, str) or len(ident) < 8:
            return None
        try:
            version = int(ident[4:8], 16)
        except ValueError:
            return None
        matches = [p for p in self.profiles.values() if p["device_id"] == ident[:4].lower()
                   and p["version_min"] <= version <= p["version_max"]]
        return matches[0] if len(matches) == 1 else None

    def describe(self):
        return {"lists":[{"name":n,"count":len(rows),"source":self.profiles.get(n,{}).get("source")}
                         for n,rows in sorted(self.lists.items())],"errors":self.errors}

    def resolve(self, profile, poll):
        roles = {}
        if not profile:
            return roles
        for role, rule in profile.get("roles", {}).items():
            name = rule["name"]
            info = poll.datapoint_metadata.get(name)
            if not info or info.get("addr") != rule["address"] or info.get("len") != rule["length"]:
                continue
            if info.get("bbfilter") != rule.get("filter") or bool(info.get("signed",False)) != bool(rule.get("signed",False)):
                continue
            try:
                if not math.isclose(float(info.get("scale_type")),float(rule["scale"]),rel_tol=1e-6):
                    continue
                if poll.cycle_groups[poll.items[info["list_index"]][0]] <= 0:
                    continue
            except (KeyError,TypeError,ValueError,IndexError):
                continue
            roles[role] = name
        return roles
