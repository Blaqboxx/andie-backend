import subprocess
import os

class GitAgent:
    def __init__(self, repo_path="."):
        self.repo_path = repo_path

    def run_cmd(self, cmd):
        result = subprocess.run(cmd, cwd=self.repo_path, capture_output=True, text=True)
        return result

    def is_repo(self):
        result = self.run_cmd(["git", "status"])
        return result.returncode == 0

    def init_repo(self):
        print("[GitAgent] Initializing git repo...")
        self.run_cmd(["git", "init"])

    def remote_exists(self):
        result = self.run_cmd(["git", "remote", "-v"])
        return bool(result.stdout.strip())

    def ensure_main_branch(self):
        print("[GitAgent] Ensuring main branch...")
        self.run_cmd(["git", "branch", "-M", "main"])

    def has_changes(self):
        result = self.run_cmd(["git", "status", "--porcelain"])
        return bool(result.stdout.strip())

    def commit_changes(self, message="auto: update"):
        print("[GitAgent] Committing changes...")
        self.run_cmd(["git", "add", "."])
        self.run_cmd(["git", "commit", "-m", message])

    def push(self):
        print("[GitAgent] Pushing to remote...")
        self.run_cmd(["git", "push", "-u", "origin", "main"])

    def run(self):
        if not self.is_repo():
            self.init_repo()
        if not self.remote_exists():
            print("[GitAgent] No remote set. Please add a remote manually.")
            return
        self.ensure_main_branch()
        if self.has_changes():
            self.commit_changes()
        self.push()
