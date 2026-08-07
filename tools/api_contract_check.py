#!/usr/bin/env python3
"""Deploydan oldin backend API kontraktini frontendlarga solishtiradi.

Ishlatish:
    python3 tools/api_contract_check.py              # tekshirish
    python3 tools/api_contract_check.py --update     # baseline'ni yangilash (deploydan keyin)

Nima qiladi:
  1. weel-backend-v2 dan yangi OpenAPI sxemasini eksport qiladi.
  2. tools/api-baseline/main.json (oxirgi deploy qilingan kontrakt) bilan solishtiradi.
  3. Buzuvchi (breaking) o'zgarishlarni topadi: o'chirilgan endpoint/method,
     yangi majburiy parametr, o'chirilgan javob maydoni, o'chirilgan enum qiymati.
  4. Har bir frontend kodida qaysi endpointlar ishlatilishini topib, buzilgan
     endpointni ishlatadigan frontendni nomma-nom ko'rsatadi.

Buzuvchi o'zgarish topilsa exit code 1 qaytaradi (CI da to'xtatish uchun).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# Skript backend repo ichida yashaydi; frontendlar backend bilan yonma-yon turadi.
BACKEND = Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent
BASELINE = BACKEND / "tools" / "api-baseline" / "main.json"

# Har bir frontend: papka, qidiriladigan fayl turlari, API yo'l prefiksi.
# prefix — frontend kodidagi yo'lga sxemadagi yo'lni moslashtirish uchun
# qo'shiladigan qism (masalan mobil ilova '/tasks/' deb yozadi, sxemada esa
# '/b2b/workspace/tasks/').
FRONTENDS = [
    {"name": "dashboard_weel_uz", "dir": "dashboard_weel_uz/src", "ext": ("*.ts", "*.tsx"), "prefixes": ["", "/b2b/workspace"]},
    {"name": "weel-admin", "dir": "weel-admin/src", "ext": ("*.ts", "*.tsx"), "prefixes": [""]},
    {"name": "weel-b2b", "dir": "weel-b2b/src", "ext": ("*.ts", "*.tsx"), "prefixes": [""]},
    {"name": "weel-b2b-mobile", "dir": "weel-b2b-mobile/lib", "ext": ("*.dart",), "prefixes": ["/b2b/workspace", ""]},
]

METHODS = ("get", "post", "put", "patch", "delete")
# Kod ichidagi string literallar: "/foo/", '/foo/', `/foo/${id}/`
STRING_LITERAL_RE = re.compile(r"""['"`](/[A-Za-z0-9_/{}$.\-]*)['"`]""")


def norm_path(path: str) -> str:
    """Parametr nomlarini bir xillashtiradi: /tasks/{guid}/ -> /tasks/{}/"""
    path = re.sub(r"\$\{[^}]+\}", "{}", path)  # JS `${id}`
    path = re.sub(r"\$\w+", "{}", path)  # Dart `$id`
    path = re.sub(r"\{[^}]*\}", "{}", path)  # OpenAPI {guid}
    return path.rstrip("/") + "/"


def export_fresh(out: Path) -> dict:
    python = BACKEND / "venv" / "bin" / "python"
    if not python.exists():
        python = Path(sys.executable)
    proc = subprocess.run(
        [str(python), "manage.py", "export_openapi_schema", "--output", str(out),
         "--variant", "main", "--base-path", "/api"],
        cwd=BACKEND, capture_output=True, text=True,
    )
    if proc.returncode != 0 or not out.exists():
        sys.exit(f"Sxemani eksport qilib bo'lmadi:\n{proc.stdout}\n{proc.stderr}")
    return json.loads(out.read_text())


def resolve(schema: dict, node: dict, seen: frozenset = frozenset()) -> dict:
    """$ref larni ochadi (rekursiv sxemalarda to'xtaydi)."""
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if ref in seen:
            return {}
        seen = seen | {ref}
        target = schema
        for part in ref.lstrip("#/").split("/"):
            target = target.get(part, {}) if isinstance(target, dict) else {}
        node = target
    return node if isinstance(node, dict) else {}


def response_fields(schema: dict, op: dict) -> dict[str, str]:
    """Muvaffaqiyatli javobning maydonlari -> turi."""
    for code in ("200", "201"):
        resp = op.get("responses", {}).get(code)
        if not resp:
            continue
        body = resolve(schema, resp.get("schema", {}))
        if body.get("type") == "array":
            body = resolve(schema, body.get("items", {}))
        # DRF paginatsiyasi: haqiqiy maydonlar `results` ichida
        props = body.get("properties", {})
        if "results" in props:
            body = resolve(schema, props["results"])
            if body.get("type") == "array":
                body = resolve(schema, body.get("items", {}))
            props = body.get("properties", {})
        out = {}
        for name, prop in props.items():
            prop = resolve(schema, prop)
            out[name] = prop.get("type", "object")
        return out
    return {}


def required_params(schema: dict, op: dict) -> dict[str, str]:
    """Majburiy so'rov parametrlari (query/path/body maydonlari) -> joyi."""
    out = {}
    for param in op.get("parameters", []):
        param = resolve(schema, param)
        loc = param.get("in")
        if loc == "body":
            body = resolve(schema, param.get("schema", {}))
            for name in body.get("required", []):
                out[name] = "body"
        elif param.get("required"):
            out[param.get("name", "?")] = loc or "query"
    return out


def enum_values(schema: dict, op: dict) -> dict[str, set]:
    out = {}
    for code in ("200", "201"):
        resp = op.get("responses", {}).get(code)
        if not resp:
            continue
        body = resolve(schema, resp.get("schema", {}))
        for name, prop in body.get("properties", {}).items():
            prop = resolve(schema, prop)
            if prop.get("enum"):
                out[name] = set(map(str, prop["enum"]))
    return out


def operations(schema: dict) -> dict[tuple[str, str], dict]:
    out = {}
    for path, item in schema.get("paths", {}).items():
        for method, op in item.items():
            if method in METHODS and isinstance(op, dict):
                out[(path, method)] = op
    return out


def diff(old: dict, new: dict) -> list[dict]:
    """Baseline -> yangi sxema orasidagi buzuvchi o'zgarishlar."""
    old_ops, new_ops = operations(old), operations(new)
    findings: list[dict] = []

    for key in sorted(set(old_ops) - set(new_ops)):
        path, method = key
        findings.append({"path": path, "method": method, "kind": "endpoint-removed",
                         "detail": "endpoint butunlay o'chirilgan"})

    for key in sorted(set(old_ops) & set(new_ops)):
        path, method = key
        o, n = old_ops[key], new_ops[key]

        old_req, new_req = required_params(old, o), required_params(new, n)
        for name, loc in sorted(new_req.items()):
            if name not in old_req:
                findings.append({"path": path, "method": method, "kind": "new-required-param",
                                 "detail": f"yangi majburiy {loc} parametri: {name}"})

        old_fields, new_fields = response_fields(old, o), response_fields(new, n)
        for name in sorted(set(old_fields) - set(new_fields)):
            findings.append({"path": path, "method": method, "kind": "response-field-removed",
                             "detail": f"javobdan maydon yo'qoldi: {name}"})
        for name in sorted(set(old_fields) & set(new_fields)):
            if old_fields[name] != new_fields[name]:
                findings.append({"path": path, "method": method, "kind": "response-type-changed",
                                 "detail": f"{name}: {old_fields[name]} -> {new_fields[name]}"})

        old_enums, new_enums = enum_values(old, o), enum_values(new, n)
        for name, values in sorted(old_enums.items()):
            gone = values - new_enums.get(name, values)
            if gone:
                findings.append({"path": path, "method": method, "kind": "enum-value-removed",
                                 "detail": f"{name} dan qiymat o'chdi: {', '.join(sorted(gone))}"})
    return findings


def frontend_usage() -> dict[str, dict[str, list[str]]]:
    """Har bir frontend uchun: normallashtirilgan yo'l -> ishlatilgan fayllar."""
    usage: dict[str, dict[str, list[str]]] = {}
    for fe in FRONTENDS:
        src = ROOT / fe["dir"]
        found: dict[str, list[str]] = {}
        if not src.exists():
            usage[fe["name"]] = found
            continue
        for pattern in fe["ext"]:
            for file in src.rglob(pattern):
                try:
                    text = file.read_text(errors="ignore")
                except OSError:
                    continue
                for raw in STRING_LITERAL_RE.findall(text):
                    normalized = norm_path(raw)
                    for prefix in fe["prefixes"]:
                        key = norm_path(prefix + normalized) if prefix else normalized
                        found.setdefault(key, [])
                        rel = str(file.relative_to(ROOT))
                        if rel not in found[key]:
                            found[key].append(rel)
        usage[fe["name"]] = found
    return usage


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true",
                        help="baseline'ni hozirgi sxema bilan almashtiradi (deploydan keyin)")
    args = parser.parse_args()

    tmp = BASELINE.parent / ".fresh.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    fresh = export_fresh(tmp)

    if args.update or not BASELINE.exists():
        BASELINE.write_text(json.dumps(fresh, indent=1, sort_keys=True, ensure_ascii=False))
        tmp.unlink(missing_ok=True)
        action = "yangilandi" if args.update else "birinchi marta yaratildi"
        print(f"✅ Baseline {action}: {BASELINE.relative_to(BACKEND)} ({len(fresh.get('paths', {}))} ta yo'l)")
        return 0

    baseline = json.loads(BASELINE.read_text())
    findings = diff(baseline, fresh)
    tmp.unlink(missing_ok=True)

    added = len(set(operations(fresh)) - set(operations(baseline)))
    print(f"Baseline: {len(operations(baseline))} operatsiya | Hozirgi: {len(operations(fresh))} operatsiya "
          f"(+{added} yangi)\n")

    if not findings:
        print("✅ Buzuvchi o'zgarish yo'q — frontendlar uchun xavfsiz.")
        return 0

    usage = frontend_usage()
    print(f"⚠️  {len(findings)} ta buzuvchi o'zgarish topildi:\n")
    exit_code = 0
    for finding in findings:
        normalized = norm_path(finding["path"])
        affected = []
        for name, paths in usage.items():
            if normalized in paths:
                affected.append(f"{name} ({', '.join(paths[normalized][:3])})")
        marker = "🔴" if affected else "🟡"
        if affected:
            exit_code = 1
        print(f"{marker} {finding['method'].upper():6} {finding['path']}")
        print(f"     {finding['detail']}")
        print(f"     ta'sir: {'; '.join(affected) if affected else 'hech bir frontend ishlatmaydi'}\n")

    if exit_code:
        print("🔴 = frontend kodida ishlatiladi, deploydan oldin tuzatilishi kerak.")
    else:
        print("🟡 lar hech bir frontendda ishlatilmaydi — deploy xavfsiz.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
