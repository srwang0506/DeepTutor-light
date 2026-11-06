"""OS isolation for TeX; unsupported hosts retain source editing and export."""

import json
from pathlib import Path
import shutil
import sys


def command(root, compiler, engine, main_file, tex_root):
    compile_args = [
        compiler,
        "-norc",
        engine,
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-synctex=1",
        "-no-shell-escape",
        main_file,
    ]
    if sys.platform == "darwin" and shutil.which("sandbox-exec"):
        allowed = [
            "/System",
            "/usr",
            "/bin",
            "/sbin",
            "/Library/Apple",
            str(tex_root),
            str(root),
            "/dev/null",
            "/dev/urandom",
        ]
        profile = '(version 1)(deny default)(allow process*)(allow sysctl-read)(allow mach-lookup)(allow file-read-metadata)(allow file-read* (literal "/"))'
        profile += "".join(
            "(allow file-read* (subpath " + json.dumps(path) + "))" for path in allowed
        )
        profile += "(allow file-write* (subpath " + json.dumps(str(root)) + "))"
        return [shutil.which("sandbox-exec"), "-p", profile, *compile_args]
    if sys.platform.startswith("linux") and shutil.which("bwrap"):
        args = [shutil.which("bwrap"), "--unshare-all", "--die-with-parent", "--new-session"]
        for name in ["/usr", "/bin", "/lib", "/lib64", str(tex_root)]:
            if Path(name).exists():
                args += ["--ro-bind", name, name]
        args += [
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--bind",
            str(root),
            str(root),
            "--chdir",
            str(root),
            "--",
        ]
        return [*args, *compile_args]
    raise ValueError(
        "Isolated TeX compilation requires macOS sandbox-exec or Linux bubblewrap. Source editing and internal archives remain available on this host."
    )
