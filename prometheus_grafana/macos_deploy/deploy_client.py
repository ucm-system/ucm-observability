import importlib.util
import os
import sys
import shlex
import shutil
import threading
import subprocess
import tempfile
import traceback
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import re

try:
    import yaml
except ImportError:
    yaml = None

try:
    import paramiko
except ImportError:
    paramiko = None

# 平台入口目录: 用于定位本平台的 requirements.txt
PLATFORM_DIR = os.path.dirname(os.path.abspath(__file__))

# Locate the canonical source: in source tree or frozen bundle
if getattr(sys, 'frozen', False):
    src = os.path.join(sys._MEIPASS, 'deploy_client_core.py')
else:
    src = os.path.normpath(
        os.path.join(PLATFORM_DIR, os.pardir, 'src', 'deploy_client_core.py')
    )

# Import the shared core module cleanly via importlib
spec = importlib.util.spec_from_file_location("deploy_client_core", src)
mod = importlib.util.module_from_spec(spec)
mod.PLATFORM_DIR = PLATFORM_DIR
sys.modules["deploy_client_core"] = mod
spec.loader.exec_module(mod)

app = mod.DeployApp()
app.mainloop()