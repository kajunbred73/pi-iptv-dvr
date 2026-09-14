import os
import subprocess
import tkinter as tk
from tkinter import messagebox, scrolledtext

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _git(args, timeout=60):
    return subprocess.run(
        ["git", *args],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def _push_repo(message):
    """Commit and push whatever changed on this machine."""
    notes = []
    result = {"ok": True, "pushed": False, "committed": False}

    if _git(["status", "--porcelain"]).stdout.strip():
        _git(["add", "."])
        commit = _git(["commit", "-m", message])
        result["committed"] = commit.returncode == 0
        notes.append(commit.stdout.strip() or commit.stderr.strip())

    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    _git(["fetch"])
    behind = _git(["rev-list", "--count", "HEAD..@{{u}}"]).stdout.strip()
    ahead = _git(["rev-list", "--count", "@{{u}}..HEAD"]).stdout.strip()

    if behind.isdigit() and int(behind) > 0:
        rebase = _git(["pull", "--rebase"])
        notes.append(rebase.stdout.strip() or rebase.stderr.strip())
        if rebase.returncode != 0:
            _git(["rebase", "--abort"])
            result["ok"] = False
            notes.append(
                f"{behind} commit(s) on the remote conflict with the local "
                f"changes, so nothing was pushed. Resolve the conflict, then push again."
            )
            result["output"] = "\n\n".join(filter(None, notes))
            return result

    if not (ahead.isdigit() and int(ahead) > 0) and not result["committed"]:
        notes.append(f"Nothing to push — {branch} matches the remote.")
        result["output"] = "\n\n".join(filter(None, notes)) or "Done."
        return result

    push = _git(["push"])
    notes.append(push.stdout.strip() or push.stderr.strip())
    result["ok"] = push.returncode == 0
    result["pushed"] = result["ok"]
    result["output"] = "\n\n".join(filter(None, notes)) or "Done."
    return result


def _pull_repo():
    """Run git pull and report what actually landed."""
    notes = []
    dirty = _git(["status", "--porcelain"]).stdout.strip()
    if dirty:
        notes.append(
            "Uncommitted local changes are present — Push them first "
            "if the pull reports a conflict:\n" + dirty
        )

    before = _git(["rev-parse", "HEAD"]).stdout.strip()
    pull = _git(["pull"])
    after = _git(["rev-parse", "HEAD"]).stdout.strip()

    output = "\n".join(filter(None, [pull.stdout.strip(), pull.stderr.strip()]))
    result = {"ok": pull.returncode == 0, "changed_files": []}

    if result["ok"] and before and after and before != after:
        changed = _git(["diff", "--name-only", f"{before}..{after}"])
        files = [f for f in changed.stdout.split("\n") if f.strip()]
        result["changed_files"] = files
        commits = _git(["log", "--oneline", f"{before}..{after}"]).stdout.strip()
        if commits:
            notes.append(f"New commits:\n{commits}")
        notes.append(f"{len(files)} file(s) changed:\n" + "\n".join(files))
    elif result["ok"]:
        notes.append("Already up to date — nothing changed.")

    result["output"] = "\n\n".join(filter(None, [output] + notes)) or "Done."
    return result


def do_push():
    message = msg_entry.get().strip() or "Auto-push by sync GUI"
    result = _push_repo(message)
    output_box.delete("1.0", tk.END)
    output_box.insert(tk.END, result.get("output", ""))
    if not result["ok"]:
        messagebox.showerror("Push failed", result.get("output", "Unknown error"))


def do_pull():
    result = _pull_repo()
    output_box.delete("1.0", tk.END)
    output_box.insert(tk.END, result.get("output", ""))
    if not result["ok"]:
        messagebox.showerror("Pull failed", result.get("output", "Unknown error"))


root = tk.Tk()
root.title("Git Sync")
root.geometry("600x450")
root.configure(padx=15, pady=15)

tk.Label(root, text="Git Sync", font=("Segoe UI", 16, "bold")).pack(anchor="w")
tk.Label(
    root,
    text=f"Repo: {REPO_DIR}",
    font=("Segoe UI", 9),
    fg="#555",
).pack(anchor="w", pady=(0, 10))

tk.Label(root, text="Commit message (for Push):", anchor="w").pack(anchor="w")
msg_entry = tk.Entry(root, font=("Consolas", 10))
msg_entry.insert(0, "Auto-push by sync GUI")
msg_entry.pack(fill="x", pady=5)

btn_frame = tk.Frame(root)
btn_frame.pack(fill="x", pady=10)

tk.Button(btn_frame, text="Push", command=do_push, width=15, bg="#2563eb", fg="white", font=("Segoe UI", 10, "bold")).pack(side="left", padx=(0, 10))
tk.Button(btn_frame, text="Pull", command=do_pull, width=15, bg="#16a34a", fg="white", font=("Segoe UI", 10, "bold")).pack(side="left")

tk.Label(root, text="Output:", anchor="w").pack(anchor="w")
output_box = scrolledtext.ScrolledText(root, wrap=tk.WORD, font=("Consolas", 10), height=15)
output_box.pack(fill="both", expand=True)

root.mainloop()
