"""
Diagnose + fix write access to config.yaml on the company laptop.

Runnable via the relay `python` whitelist. Reports the file's attributes,
clears the read-only flag if set, and tests whether a write handle can be
obtained (i.e. whether something else holds a lock). Does NOT modify the
file's contents and contains no secrets.
"""

import os
import stat

P = r"C:\Users\tmoghanan\Documents\AI\expressops-auto\config\config.yaml"


def main():
    print(f"path: {P}")
    print(f"exists: {os.path.exists(P)}")
    if not os.path.exists(P):
        return

    st = os.stat(P)
    readonly = not (st.st_mode & stat.S_IWRITE)
    print(f"mode: {oct(st.st_mode)}  read-only-attr: {readonly}  "
          f"os.access W_OK: {os.access(P, os.W_OK)}")

    try:
        os.chmod(P, stat.S_IWRITE | stat.S_IREAD)
        print("chmod: read-only flag cleared (or already clear)")
    except Exception as exc:
        print(f"chmod ERROR: {exc!r}")

    # Test for a write lock without changing content (append mode, write nothing).
    try:
        with open(P, "a", encoding="utf-8"):
            pass
        print("write-open: OK -- file is writable and not locked. "
              "You can now re-run the Add-Content paste.")
    except Exception as exc:
        print(f"write-open FAILED: {exc!r} -- file is locked by a process "
              "(close any editor with config.yaml open) or it's an ACL/ownership issue.")


if __name__ == "__main__":
    main()
