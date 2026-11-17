"""OS secret store without extra packages. Values never go on a command line, into a log or to stdout.

  macOS    login keychain: `security -i` reads the add command (value hex-encoded) from stdin;
           `security find-generic-password -w` returns it on a pipe.
  Windows  DPAPI (current user): PowerShell ConvertFrom-SecureString reads the value from stdin and
           writes an encrypted blob to %APPDATA%\\alibaba-seller-cli\\secrets\\<service>@<account>.dpapi.
           Only the same Windows user on the same machine can decrypt it.
  other    not supported (StoreError).

CLI (for setup steps, value from stdin only):
  python3 secret_store.py set <service> <account>     # reads one line from stdin
  python3 secret_store.py has <service> <account>     # prints yes/no
"""
import os
import re
import subprocess
import sys


class StoreError(Exception):
    code = 1


class KeychainTimeout(StoreError):
    """`security` gave no answer in time: keychain locked, or no GUI session to show the approval dialog."""
    code = 69


# argv prefix of the macOS keychain tool (tests swap in a fake that never answers)
SECURITY_CMD = ["security"]


def keychain_timeout():
    return float(os.environ.get("ALIBABA_SELLER_KEYCHAIN_TIMEOUT") or 15)


def security(args, stdin=None, fallback=None):
    """Run `security <args>` with a hard timeout (child killed). -> CompletedProcess.
    Raises KeychainTimeout with the Korean how-to instead of hanging."""
    t = keychain_timeout()
    try:
        return subprocess.run(SECURITY_CMD + list(args), input=stdin, capture_output=True, text=True, timeout=t)
    except subprocess.TimeoutExpired:
        raise KeychainTimeout(
            "키체인이 %g초 동안 응답하지 않음(잠겨 있거나 GUI 세션이 아님). 맥 화면에 승인 창이 떠 있는지 보거나, "
            "GUI 터미널에서 security unlock-keychain ~/Library/Keychains/login.keychain-db 후 다시 실행. "
            "대기 시간 = ALIBABA_SELLER_KEYCHAIN_TIMEOUT(초)%s" % (t, (". 대신: " + fallback) if fallback else ""))


def _check(name):
    if not re.fullmatch(r"[A-Za-z0-9@._:+-]{1,128}", name or ""):
        raise StoreError("bad secret name (letters, digits, @._:+- only)")


def _win_path(service, account):
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(base, "alibaba-seller-cli", "secrets", "%s@%s.dpapi" % (service, account.replace(":", "_")))


_PS_SET = ("$v = [Console]::In.ReadToEnd().TrimEnd([char]13, [char]10); "
           "$s = ConvertTo-SecureString -String $v -AsPlainText -Force; "
           "[IO.File]::WriteAllText($env:ASC_SECRET_PATH, (ConvertFrom-SecureString -SecureString $s))")
_PS_GET = ("$s = ConvertTo-SecureString -String ([IO.File]::ReadAllText($env:ASC_SECRET_PATH)); "
           "$b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s); "
           "try { [Console]::Out.Write([Runtime.InteropServices.Marshal]::PtrToStringBSTR($b)) } "
           "finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }")


def _ps(script, path, stdin=None):
    env = dict(os.environ, ASC_SECRET_PATH=path)
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                          input=stdin, capture_output=True, text=True, env=env, timeout=60)


def set_secret(service, account, value):
    _check(service)
    _check(account)
    if not value:
        raise StoreError("empty value")
    if sys.platform == "darwin":
        cmd = "add-generic-password -U -s %s -a %s -X %s\n" % (service, account, value.encode().hex())
        r = security(["-i"], stdin=cmd)
        if r.returncode != 0 or "error" in (r.stdout + r.stderr).lower():
            raise StoreError("keychain write failed")
        return "keychain"
    if os.name == "nt":
        path = _win_path(service, account)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        r = _ps(_PS_SET, path, stdin=value + "\n")
        if r.returncode != 0:
            raise StoreError("DPAPI write failed")
        return "dpapi"
    raise StoreError("secret store not supported on %s (macOS keychain / Windows DPAPI only)" % sys.platform)


def get_secret(service, account):
    """-> value or None when missing."""
    _check(service)
    _check(account)
    if sys.platform == "darwin":
        r = security(["find-generic-password", "-s", service, "-a", account, "-w"])
        return r.stdout.rstrip("\n") if r.returncode == 0 and r.stdout.strip() else None
    if os.name == "nt":
        path = _win_path(service, account)
        if not os.path.isfile(path):
            return None
        r = _ps(_PS_GET, path)
        return r.stdout if r.returncode == 0 and r.stdout else None
    raise StoreError("secret store not supported on %s (macOS keychain / Windows DPAPI only)" % sys.platform)


def main(argv):
    if len(argv) != 3 or argv[0] not in ("set", "has"):
        print(__doc__.split("CLI")[1], file=sys.stderr)
        return 64
    op, service, account = argv
    try:
        if op == "set":
            where = set_secret(service, account, sys.stdin.readline().rstrip("\r\n"))
            print("saved (%s): %s / %s" % (where, service, account))
        else:
            print("yes" if get_secret(service, account) else "no")
    except StoreError as e:
        print("error: %s" % e, file=sys.stderr)
        return e.code
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
