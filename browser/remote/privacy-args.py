"""privacy.json's build args, as GN args (the pristine-Chromium base's replacement for
ungoogled-chromium's flags.gn; docs/BASE-PLAN.md).   privacy-args.py <privacy.json> <out.gn>"""
import json
import sys

src, out = sys.argv[1:3]
lines = []
for name, arg in json.load(open(src))["args"].items():
    v = arg["value"]
    gn = ("true" if v else "false") if isinstance(v, bool) else json.dumps(v)
    lines.append(f"{name} = {gn}  # {arg['why']}\n")
open(out, "w").write("".join(lines))
