import difflib
import os
import subprocess
import tempfile
import shutil
import json
import re

MEMORY_FILE = "memory.json"

class PatchEngine:
    """
    PatchEngine safely applies unified diff patches to files.
    It validates, applies, and can roll back changes if needed.
    """
    def __init__(self, backup_dir=".andie_patches"):
        self.backup_dir = backup_dir
        os.makedirs(backup_dir, exist_ok=True)

    def backup(self, file_path):
        base = os.path.basename(file_path)
        backup_path = os.path.join(self.backup_dir, base)
        with open(file_path, "r") as fsrc, open(backup_path, "w") as fdst:
            fdst.write(fsrc.read())
        return backup_path

    def restore(self, file_path):
        base = os.path.basename(file_path)
        backup_path = os.path.join(self.backup_dir, base)
        if os.path.exists(backup_path):
            with open(backup_path, "r") as fsrc, open(file_path, "w") as fdst:
                fdst.write(fsrc.read())
            return True
        return False

    def apply_patch(self, file_path, patch_text):
        """
        Apply a unified diff patch to file_path.
        Returns True if successful, False otherwise.
        """
        self.backup(file_path)
        with open(file_path, "r") as f:
            original = f.readlines()
        patched = list(difflib.restore(difflib.ndiff(original, patch_text.splitlines(keepends=True)), 2))
        with open(file_path, "w") as f:
            f.writelines(patched)
        return True

    def validate_patch(self, file_path, patch_text):
        """
        Validate that the patch applies cleanly and the file compiles (if Python).
        """
        try:
            self.apply_patch(file_path, patch_text)
            if file_path.endswith(".py"):
                import py_compile
                py_compile.compile(file_path, doraise=True)
            return True
        except Exception as e:
            self.restore(file_path)
