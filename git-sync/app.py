import os
import re
import subprocess
import sys

from flask import Flask, jsonify, render_template, request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__, template_folder="templates")


def _run(*args, check=False):
    """Run a git command in the repo root and return (ok, stdout, stderr)."""
    cmd = ["git", "-C", REPO_ROOT] + list(args)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=check,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except FileNotFoundError:
        return False, "", "git command not found. Make sure Git is installed and on PATH."
    return result.returncode == 0, result.stdout, result.stderr


def _status():
    ok, out, err = _run("status", "--porcelain", "-b")
    if not ok:
        return {"ok": False, "error": err or out}

    branch_line = out.splitlines()[0] if out.splitlines() else "## No commits yet"
    ahead_behind = ""
    m = re.search(r"\[ahead\s+(\d+)(?:,\s*behind\s+(\d+))?\]", branch_line)
    if m:
        ahead = m.group(1)
        behind = m.group(2)
        parts = []
        if ahead:
            parts.append(f"ahead {ahead}")
        if behind:
            parts.append(f"behind {behind}")
        ahead_behind = f" ({', '.join(parts)})" if parts else ""

    branch = branch_line.split("...")[0].replace("## ", "") if branch_line.startswith("## ") else branch_line
    files = []
    for line in out.splitlines()[1:]:
        if len(line) >= 3:
            files.append({"status": line[:2].strip(), "path": line[3:].strip()})

    ok, log, _ = _run("log", "--oneline", "-n", "5")
    commits = [l for l in log.splitlines() if l] if ok else []

    _, remote_out, _ = _run("remote", "-v")
    remotes = [l for l in remote_out.splitlines() if l]

    return {
        "ok": True,
        "branch": branch + ahead_behind,
        "files": files,
        "commits": commits,
        "remotes": remotes,
    }


@app.route("/")
def index():
    status = _status()
    return render_template("index.html", status=status)


@app.route("/api/status")
def api_status():
    return jsonify(_status())


@app.route("/api/commit", methods=["POST"])
def commit():
    message = request.json.get("message", "").strip()
    if not message:
        return jsonify({"ok": False, "error": "Commit message is required."})
    ok, _, err = _run("add", ".")
    if not ok:
        return jsonify({"ok": False, "error": err})
    ok, out, err = _run("commit", "-m", message)
    return jsonify({"ok": ok, "output": out + err})


@app.route("/api/push", methods=["POST"])
def push():
    _, remote, _ = _run("remote", "get-url", "origin")
    if not remote.strip():
        return jsonify({"ok": False, "error": "No origin remote set. Add one under Settings."})
    ok, out, err = _run("push", "origin", _current_branch())
    return jsonify({"ok": ok, "output": out + err})


@app.route("/api/pull", methods=["POST"])
def pull():
    ok, out, err = _run("pull", "origin", _current_branch())
    return jsonify({"ok": ok, "output": out + err})


@app.route("/api/remote", methods=["POST"])
def set_remote():
    url = request.json.get("url", "").strip()
    if not url:
        return jsonify({"ok": False, "error": "Remote URL is required."})
    _, _, _ = _run("remote", "remove", "origin")
    ok, out, err = _run("remote", "add", "origin", url)
    return jsonify({"ok": ok, "output": out + err})


@app.route("/api/config", methods=["POST"])
def set_config():
    name = request.json.get("name", "").strip()
    email = request.json.get("email", "").strip()
    if not name or not email:
        return jsonify({"ok": False, "error": "Name and email are required."})
    ok1, out1, err1 = _run("config", "user.name", name)
    ok2, out2, err2 = _run("config", "user.email", email)
    return jsonify({"ok": ok1 and ok2, "output": out1 + out2 + err1 + err2})


def _current_branch():
    ok, out, _ = _run("rev-parse", "--abbrev-ref", "HEAD")
    return out.strip() if ok and out.strip() else "main"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=True)
