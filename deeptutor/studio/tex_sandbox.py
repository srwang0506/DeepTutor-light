"""OS isolation for TeX; unsupported hosts retain source editing and export."""

import json
from pathlib import Path
import shutil
import sys


def narrow_root(value):
    path = Path(value).resolve()
    home = Path.home().resolve()
    if path == Path("/") or path == home or home.is_relative_to(path):
        raise ValueError("Cannot isolate TeX with a filesystem or home-directory allowlist.")
    return path


def distribution_roots(compiler):
    """Recognize distro packages and TeX Live trees; never infer by parent depth."""
    resolved = Path(compiler).resolve()
    for parent in resolved.parents:
        if (parent / "texmf-dist").is_dir():
            return [narrow_root(parent)]
    if resolved.is_relative_to("/usr/bin") or resolved.is_relative_to("/bin"):
        return [
            Path(p)
            for p in (
                "/usr/share/texlive",
                "/usr/share/texmf",
                "/usr/share/texmf-dist",
                "/etc/texmf",
                "/var/lib/texmf",
            )
            if Path(p).exists()
        ]
    raise ValueError(
        "Unrecognized TeX distribution. Configure an isolated TeX installation before compiling."
    )


def command(root, compiler, engine, main_file, tex_root):
    root = narrow_root(root)
    roots = tex_root if isinstance(tex_root, (list, tuple)) else [tex_root]
    roots = [narrow_root(p) for p in roots]
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
            *map(str, roots),
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
        for name in dict.fromkeys(
            [
                "/usr/bin",
                "/usr/lib",
                "/usr/lib64",
                "/bin",
                "/lib",
                "/lib64",
                "/usr/share/perl",
                "/usr/share/perl5",
                "/usr/share/fonts",
                "/usr/share/fontconfig",
                "/etc/fonts",
                "/etc/ld.so.cache",
                *map(str, roots),
            ]
        ):
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
