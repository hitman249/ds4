#!/usr/bin/env python3
"""Assemble redistributable S4+D2 source and embed its newly linked code object."""
import argparse
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

p = argparse.ArgumentParser()
p.add_argument("--hipcc", required=True)
p.add_argument("--arch", required=True)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
blob = b""


def _inst_pref_size(match):
    """Evaluate instprefsize(<code bytes>) for gfx1151.

    LLVM main evaluates this MCExpr inside the assembler: the gfx1151
    instruction cache line is 128 bytes and the INST_PREF_SIZE kernel
    descriptor field is 6 bits wide, so the value is min(ceil(bytes/128), 63).
    ROCm 7.2's clang (22.0.0git) predates the expression support and rejects
    the directive, so the build substitutes the equivalent literal and keeps
    the emitted code object byte-identical on every toolchain.
    """
    return str(min((int(match.group(1)) + 127) // 128, 63))


if a.arch == "gfx1151":
    hipcc = Path(shutil.which(a.hipcc) or a.hipcc).resolve(strict=True)
    roots = [hipcc.parent, hipcc.parent.parent / "llvm/bin", hipcc.parent.parent / "lib/llvm/bin"]
    clang = next((r / "clang" for r in roots if (r / "clang").is_file()), None)
    if clang is None:
        raise SystemExit("HIP installation must provide clang and ld.lld; specify HIPCC explicitly")
    linker = clang.parent / "ld.lld"
    if not linker.is_file():
        raise SystemExit("ld.lld is missing beside HIP clang")
    source = Path(__file__).resolve().parent.parent / "rocm/halo/output_b/s4_d2.s"
    with tempfile.TemporaryDirectory(prefix="ds4-halo-build-") as tmp:
        obj = Path(tmp) / "output_b.o"
        module = Path(tmp) / "output_b.hsaco"
        source_text, substitutions = re.subn(r"instprefsize\(\s*(\d+)\s*\)",
                                             _inst_pref_size, source.read_text())
        if substitutions:
            source = Path(tmp) / "s4_d2.s"
            source.write_text(source_text, encoding="utf-8")
        subprocess.run([str(clang), "--target=amdgcn-amd-amdhsa", "-mcpu=gfx1151",
                        "-x", "assembler", "-c", str(source), "-o", str(obj)], check=True)
        subprocess.run([str(linker), "-shared", str(obj), "-o", str(module)], check=True)
        blob = module.read_bytes()
a.output.parent.mkdir(parents=True, exist_ok=True)
lines = ["// Generated from s4_d2.s by the current HIP toolchain; do not edit.",
         "alignas(16) static const unsigned char halo_output_b_code[] = {"]
lines += ["    " + ",".join(str(x) for x in blob[i:i+24]) + "," for i in range(0,len(blob),24)]
if not blob:
    lines.append("    0,")
lines += ["};", f"static const size_t halo_output_b_code_size = {len(blob)};", ""]
a.output.write_text("\n".join(lines), encoding="utf-8")
