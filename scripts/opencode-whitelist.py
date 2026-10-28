#!/usr/bin/env python3
"""Privacy allow-list for OpenCode: disables in opencode.json every model of the `opencode` provider outside WHITELIST.

OpenCode v2.0.29 has no per-model allow-list (`whitelist` is not supported, core/src/config/normalize.ts:53; policies only target
provider/tool/integration, schema/src/config/policy.ts:9). So the script enumerates the catalog (models.dev, which OpenCode uses,
plus the visible models and those already disabled) and sets `disabled: true` (honoured by core/src/plugin/provider/opencode.ts:324).
Other providers (e.g. a local one) are not touched. Any tool that launches `opencode serve` with this config inherits the list.

Usage: opencode-whitelist.py          writes the config (re-run whenever you re-check the provider's privacy terms)
       opencode-whitelist.py --check  exits non-zero if an `opencode/` model outside the list is still visible
"""
import json, os, subprocess, sys, urllib.request

CONFIG = os.path.expanduser("~/.config/opencode/opencode.json")
# Models OpenCode documents as zero-retention and not used for training (reported by OpenCode, not verified at the provider).
WHITELIST = {"deepseek-v4.1-flash-free", "glm-5.3-flash-free"}


def visible():
    out = subprocess.run(["opencode", "models", "--standalone"], stdin=subprocess.DEVNULL,
                         capture_output=True, text=True, timeout=120).stdout
    return {line.strip().split("/", 1)[1] for line in out.splitlines() if line.startswith("opencode/")}


if "--check" in sys.argv:
    extra = sorted(visible() - WHITELIST)
    print("visible outside the list:", extra or "none")
    sys.exit(1 if extra else 0)

req = urllib.request.Request("https://models.dev/api.json", headers={"User-Agent": "opencode-whitelist"})  # 403 without a UA
with urllib.request.urlopen(req, timeout=30) as resp:
    catalog = set(json.load(resp)["opencode"]["models"])
config = json.load(open(CONFIG))
models = config.setdefault("providers", {}).setdefault("opencode", {}).setdefault("models", {})
for model_id in sorted(catalog | visible() | set(models)):
    if model_id in WHITELIST:
        models.get(model_id, {}).pop("disabled", None)
    else:
        models.setdefault(model_id, {})["disabled"] = True
with open(CONFIG, "w") as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
    f.write("\n")
print(f"{sum(1 for m in models.values() if m.get('disabled'))} models disabled; allowed: {sorted(WHITELIST)}")
