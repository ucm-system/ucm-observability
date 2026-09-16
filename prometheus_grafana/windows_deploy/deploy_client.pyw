#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UCM Observability 部署工具 (Windows 客户端)

通过 SSH 将 Prometheus + Grafana 部署到远程服务器。
双击运行 (pyw 无控制台窗口), 若关联了 Python 可直接双击。
"""

import os
import re
import sys
import shlex
import shutil
import threading
import subprocess
import tempfile
import traceback
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

try:
    import yaml
except ImportError:
    yaml = None

try:
    import paramiko
except ImportError:
    paramiko = None

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 内置依赖清单, 在脚本模式下用于自动安装
REQUIREMENTS_FILE = os.path.join(SCRIPT_DIR, "requirements.txt")
REQUIREMENTS_TEXT = (
    "paramiko>=3.0.0\n"
    "PyYAML>=6.0\n"
)

# 运行 pip 时禁止弹出输入 / 降低版本检查噪音
PIP_BASE = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input"]

if getattr(sys, 'frozen', False):
    RESOURCE_DIR = sys._MEIPASS
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    RESOURCE_DIR = os.path.normpath(os.path.join(_BASE_DIR, os.pardir))

PROMETHEUS_YML = os.path.join(RESOURCE_DIR, "prometheus.yml")
COMPOSE_YML = os.path.join(RESOURCE_DIR, "docker-compose.yaml")
DATASOURCE_YML = os.path.join(RESOURCE_DIR, "grafana-datasource.yml")
PROVIDER_YML = os.path.join(RESOURCE_DIR, "grafana-dashboard-provider.yml")
DASHBOARDS_DIR = os.path.join(RESOURCE_DIR, "dashboards")

GRAFANA_PORT = 3000
PROMETHEUS_PORT = 9090


# ---------------------------------------------------------------------------
# Remote host helpers (paramiko)
# ---------------------------------------------------------------------------

class RemoteHost:
    def __init__(self):
        self.client = None
        self.compose_cmd = "docker compose"

    def is_connected(self):
        if self.client is None:
            return False
        t = self.client.get_transport()
        return t is not None and t.is_active()

    def connect(self, host, port, user, password):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=host, port=port, username=user, password=password,
            timeout=15, banner_timeout=15, auth_timeout=15,
            look_for_keys=False, allow_agent=False,
        )
        self.client = client

    def close(self):
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None

    def run(self, command, timeout=180):
        stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
        code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        return code, (out + err)

    def upload_dir(self, local_dir, remote_dir, log=None):
        sftp = self.client.open_sftp()
        try:
            self._sftp_mkdir_p(sftp, remote_dir)
            for name in sorted(os.listdir(local_dir)):
                lp = os.path.join(local_dir, name)
                rp = remote_dir.rstrip("/") + "/" + name
                if os.path.isdir(lp):
                    self._sftp_mkdir_p(sftp, rp)
                    for fn in sorted(os.listdir(lp)):
                        local_file = os.path.join(lp, fn)
                        remote_file = rp.rstrip("/") + "/" + fn
                        sftp.put(local_file, remote_file)
                        if log:
                            log(f"上传 {name}/{fn}")
                else:
                    sftp.put(lp, rp)
                    if log:
                        log(f"上传 {name}")
        finally:
            sftp.close()

    @staticmethod
    def _sftp_mkdir_p(sftp, path):
        parts = path.strip("/").split("/")
        cur = "/" if path.startswith("/") else ""
        for part in parts:
            cur = cur.rstrip("/") + "/" + part
            try:
                sftp.stat(cur)
            except FileNotFoundError:
                sftp.mkdir(cur)


# ---------------------------------------------------------------------------
# GUI Application
# ---------------------------------------------------------------------------

class DeployApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("UCM Observability \u90e8\u7f72\u5de5\u5177  (Prometheus + Grafana)")
        self.geometry("940x760")
        self.minsize(820, 640)

        self.remote = RemoteHost()
        self.deploying = False

        self._build_ui()
        self._load_images_from_compose()
        self.after(500, self._ensure_dependencies)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- UI building ------------------------------------------------------

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        main = ttk.Frame(self, padding=10)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)

        # SSH connection
        frm = ttk.LabelFrame(main, text="SSH \u8fde\u63a5", padding=8)
        frm.grid(row=0, column=0, sticky="ew")
        frm.columnconfigure(1, weight=1)

        self.var_host = tk.StringVar()
        self.var_port = tk.StringVar(value="22")
        self.var_user = tk.StringVar(value="root")
        self.var_password = tk.StringVar()

        ttk.Label(frm, text="\u4e3b\u673a:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.var_host, width=30).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Label(frm, text="\u7aef\u53e3:").grid(row=0, column=2, sticky="w")
        ttk.Entry(frm, textvariable=self.var_port, width=6).grid(row=0, column=3, sticky="w", **pad)

        ttk.Label(frm, text="\u7528\u6237\u540d:").grid(row=1, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.var_user, width=30).grid(row=1, column=1, sticky="ew", **pad)
        ttk.Label(frm, text="\u5bc6\u7801:").grid(row=1, column=2, sticky="w")
        ttk.Entry(frm, textvariable=self.var_password, width=20, show="*").grid(row=1, column=3, sticky="w", **pad)

        btn_frm = ttk.Frame(frm)
        btn_frm.grid(row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))
        self.btn_connect = ttk.Button(btn_frm, text="\u8fde\u63a5\u6d4b\u8bd5", command=self.on_test_connect)
        self.btn_connect.pack(side="left")
        self.btn_disconnect = ttk.Button(btn_frm, text="\u65ad\u5f00", command=self.on_disconnect)
        self.btn_disconnect.pack(side="left", padx=(6, 0))

        # Container images & ports
        frm2 = ttk.LabelFrame(main, text="\u5bb9\u5668\u955c\u50cf\u4e0e\u7aef\u53e3 (\u53ef\u4fee\u6539)", padding=8)
        frm2.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        frm2.columnconfigure(1, weight=1)

        self.var_img_prom = tk.StringVar()
        self.var_img_graf = tk.StringVar()
        self.var_port_prom = tk.StringVar(value=str(PROMETHEUS_PORT))
        self.var_port_graf = tk.StringVar(value=str(GRAFANA_PORT))
        ttk.Label(frm2, text="Prometheus \u955c\u50cf:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm2, textvariable=self.var_img_prom).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Label(frm2, text="\u7aef\u53e3:").grid(row=0, column=2, sticky="e")
        ttk.Entry(frm2, textvariable=self.var_port_prom, width=7).grid(row=0, column=3, sticky="w", **pad)
        ttk.Label(frm2, text="Grafana \u955c\u50cf:").grid(row=1, column=0, sticky="w")
        ttk.Entry(frm2, textvariable=self.var_img_graf).grid(row=1, column=1, sticky="ew", **pad)
        ttk.Label(frm2, text="\u7aef\u53e3:").grid(row=1, column=2, sticky="e")
        ttk.Entry(frm2, textvariable=self.var_port_graf, width=7).grid(row=1, column=3, sticky="w", **pad)

        # vLLM targets
        frm3 = ttk.LabelFrame(main, text="vLLM \u670d\u52a1\u5730\u5740 (\u6bcf\u884c\u4e00\u4e2a, \u683c\u5f0f host:port)", padding=8)
        frm3.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        frm3.columnconfigure(0, weight=1)
        frm3.rowconfigure(0, weight=1)

        self.txt_targets = tk.Text(frm3, height=4, font=("Consolas", 10))
        self.txt_targets.insert("1.0", "host.docker.internal:8000")
        sb = ttk.Scrollbar(frm3, command=self.txt_targets.yview)
        self.txt_targets.config(yscrollcommand=sb.set)
        self.txt_targets.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")

        # Action buttons
        frm4 = ttk.Frame(main)
        frm4.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.btn_deploy = ttk.Button(frm4, text="\u5f00\u59cb\u90e8\u7f72", command=self.on_deploy)
        self.btn_deploy.pack(side="left")
        self.btn_uninstall = ttk.Button(frm4, text="\u5378\u8f7d", command=self.on_uninstall)
        self.btn_uninstall.pack(side="left", padx=(6, 0))
        ttk.Button(frm4, text="\u6e05\u7a7a\u65e5\u5fd7", command=self.on_clear_log).pack(side="left", padx=(6, 0))

        # Log area
        frm5 = ttk.LabelFrame(main, text="\u65e5\u5fd7", padding=4)
        frm5.grid(row=4, column=0, sticky="nsew", pady=(8, 0))
        frm5.columnconfigure(0, weight=1)
        frm5.rowconfigure(0, weight=1)

        self.txt_log = scrolledtext.ScrolledText(
            frm5, state="disabled", height=18, font=("Consolas", 9), wrap="word"
        )
        self.txt_log.grid(row=0, column=0, sticky="nsew")

        # Status bar
        self.var_status = tk.StringVar(value="\u672a\u8fde\u63a5")
        st = ttk.Label(self, textvariable=self.var_status, relief="sunken", anchor="w")
        st.pack(fill="x", side="bottom")

        main.rowconfigure(4, weight=1)

    def _load_images_from_compose(self):
        try:
            with open(COMPOSE_YML, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            svcs = data.get("services") or {}
            if "prometheus" in svcs:
                self.var_img_prom.set(svcs["prometheus"].get("image", ""))
                hp = self._extract_host_port(svcs["prometheus"].get("ports", []))
                if hp:
                    self.var_port_prom.set(str(hp))
            if "grafana" in svcs:
                self.var_img_graf.set(svcs["grafana"].get("image", ""))
                hp = self._extract_host_port(svcs["grafana"].get("ports", []))
                if hp:
                    self.var_port_graf.set(str(hp))
        except Exception:
            pass

    @staticmethod
    def _extract_host_port(ports):
        for p in ports or []:
            if isinstance(p, dict):
                pub = p.get("published")
                if pub:
                    return int(pub)
            else:
                parts = str(p).split(":")
                if len(parts) == 2:
                    host = parts[0]
                elif len(parts) >= 3:
                    host = parts[1]
                else:
                    host = None
                if host:
                    try:
                        return int(host)
                    except ValueError:
                        pass
        return None

    # ---- Log / status helpers (thread-safe) -------------------------------

    def log(self, msg, level="info"):
        prefix = {"info": "[\u4fe1\u606f]", "ok": "[\u6210\u529f]", "error": "[\u9519\u8bef]", "warn": "[\u8b66\u544a]"}
        text = f"{prefix.get(level, '[?]')} {msg}"
        self.after(0, self._append_log, text)

    def _append_log(self, text):
        self.txt_log.config(state="normal")
        self.txt_log.insert("end", text + "\n")
        self.txt_log.see("end")
        self.txt_log.config(state="disabled")

    def set_status(self, text):
        self.after(0, lambda: self.var_status.set(text))

    def notify(self, title, message):
        self.after(0, lambda: messagebox.showinfo(title, message))

    # ---- Dependency auto-install ------------------------------------------

    def _detect_missing_deps(self):
        missing = []
        if yaml is None:
            missing.append("PyYAML")
        if paramiko is None:
            missing.append("paramiko")
        return missing

    def _reload_missing(self):
        global yaml, paramiko
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            yaml = None
        try:
            import paramiko
        except ImportError:
            paramiko = None

    def _pip_install(self, missing):
        cmd = list(PIP_BASE)
        if os.path.isfile(REQUIREMENTS_FILE):
            cmd += ["-r", REQUIREMENTS_FILE]
            self.log(f"\u6267\u884c: pip install -r {REQUIREMENTS_FILE}", "info")
        else:
            pkgs = missing or ["paramiko>=3.0.0", "PyYAML>=6.0"]
            cmd += pkgs
            self.log(f"\u6267\u884c: pip install {' '.join(pkgs)}", "info")
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            out = (proc.stdout or "").strip()
            err = (proc.stderr or "").strip()
            for text in [out, err]:
                for line in text.splitlines():
                    line = line.strip()
                    if line and "[notice]" not in line.lower():
                        self.log(line, "info")
            if proc.returncode != 0:
                self.log(f"pip install \u8fd4\u56de\u7801 {proc.returncode}", "error")
                return False
            self.log("pip install \u5b8c\u6210", "ok")
            return True
        except subprocess.TimeoutExpired:
            self.log("pip install \u8d85\u65f6 (300s)", "error")
        except Exception as e:
            self.log(f"pip install \u5f02\u5e38: {e}", "error")
        return False

    def _ensure_dependencies(self):
        if getattr(sys, "frozen", False):
            self.log("\u4f9d\u8d56\u68c0\u67e5: Python \u8fd0\u884c\u65f6\u5df2\u5185\u7f6e\u5728 exe \u4e2d", "ok")
            return
        missing = self._detect_missing_deps()
        if not missing:
            self.log("\u4f9d\u8d56\u68c0\u67e5\u901a\u8fc7: paramiko, PyYAML", "ok")
            return
        self.log(f"\u68c0\u6d4b\u5230\u7f3a\u5c11\u4f9d\u8d56: {', '.join(missing)}, \u5f00\u59cb\u81ea\u52a8\u5b89\u88c5...", "warn")
        if self._pip_install(missing):
            self._reload_missing()
            still = self._detect_missing_deps()
            if still:
                self.log(f"\u5b89\u88c5\u5b8c\u6210\u4f46\u4ecd\u6709\u4f9d\u8d56\u7f3a\u5931: {', '.join(still)}", "error")
            else:
                self.log("\u4f9d\u8d56\u5b89\u88c5\u6210\u529f", "ok")
        else:
            self.log("\u4f9d\u8d56\u5b89\u88c5\u5931\u8d25, \u8bf7\u624b\u52a8\u6267\u884c: pip install -r requirements.txt", "error")

    # ---- Modal dialogs from worker thread ---------------------------------

    def ask_text(self, title, prompt, initial=""):
        evt = threading.Event()
        result = {"value": None}

        def _show():
            dlg = tk.Toplevel(self)
            dlg.title(title)
            dlg.transient(self)
            dlg.grab_set()
            frm = ttk.Frame(dlg, padding=16)
            frm.pack(fill="both", expand=True)
            ttk.Label(frm, text=prompt, wraplength=420).pack(anchor="w")
            var = tk.StringVar(value=initial)
            ent = ttk.Entry(frm, textvariable=var, width=50)
            ent.pack(fill="x", pady=8)

            def on_ok():
                result["value"] = var.get()
                evt.set()
                dlg.destroy()

            def on_cancel():
                result["value"] = None
                evt.set()
                dlg.destroy()

            bf = ttk.Frame(frm)
            bf.pack(fill="x", pady=(4, 0))
            ttk.Button(bf, text="\u786e\u5b9a", command=on_ok).pack(side="left")
            ttk.Button(bf, text="\u53d6\u6d88", command=on_cancel).pack(side="right")
            ent.focus_set()
            dlg.protocol("WM_DELETE_WINDOW", on_cancel)
            dlg.geometry(f"+{self.winfo_rootx() + 120}+{self.winfo_rooty() + 160}")

        self.after(0, _show)
        evt.wait()
        return result["value"]

    def ask_yesno(self, title, message):
        evt = threading.Event()
        result = {"ok": False}

        def _show():
            result["ok"] = messagebox.askyesno(title, message)
            evt.set()

        self.after(0, _show)
        evt.wait()
        return result["ok"]

    def ask_choice(self, title, prompt, options):
        evt = threading.Event()
        result = {"value": None}

        def _show():
            dlg = tk.Toplevel(self)
            dlg.title(title)
            dlg.transient(self)
            dlg.grab_set()
            frm = ttk.Frame(dlg, padding=16)
            frm.pack(fill="both", expand=True)
            ttk.Label(frm, text=prompt, wraplength=460).pack(anchor="w")
            lb = tk.Listbox(frm, height=min(len(options), 10), width=60, exportselection=False)
            for opt in options:
                lb.insert("end", opt)
            if options:
                lb.selection_set(0)
            sb = ttk.Scrollbar(frm, command=lb.yview)
            lb.config(yscrollcommand=sb.set)
            row = ttk.Frame(frm)
            row.pack(fill="both", expand=True, pady=8)
            lb.pack(side="left", fill="both", expand=True)
            sb.pack(side="left", fill="y")

            def on_ok():
                sel = lb.curselection()
                result["value"] = options[sel[0]] if sel else None
                evt.set()
                dlg.destroy()

            def on_cancel():
                result["value"] = None
                evt.set()
                dlg.destroy()

            bf = ttk.Frame(frm)
            bf.pack(fill="x", pady=(4, 0))
            ttk.Button(bf, text="\u9009\u62e9", command=on_ok).pack(side="left")
            ttk.Button(bf, text="\u53d6\u6d88", command=on_cancel).pack(side="right")
            lb.focus_set()
            dlg.protocol("WM_DELETE_WINDOW", on_cancel)
            dlg.geometry(f"+{self.winfo_rootx() + 120}+{self.winfo_rooty() + 160}")

        self.after(0, _show)
        evt.wait()
        return result["value"]

    # ---- UI action handlers -----------------------------------------------

    def on_test_connect(self):
        if self.deploying:
            return
        self.set_status("\u6b63\u5728\u6d4b\u8bd5\u8fde\u63a5...")
        t = threading.Thread(target=self._test_worker, daemon=True)
        t.start()

    def _test_worker(self):
        try:
            self._connect_from_ui()
            self.log("SSH \u8fde\u63a5\u6210\u529f", "ok")
            code, out = self.remote.run("docker --version")
            if code == 0 and out.strip():
                ver = out.strip().splitlines()[0]
                self.log(f"\u8fdc\u7a0b docker: {ver}")
            else:
                self.log("\u8fdc\u7a0b\u672a\u68c0\u6d4b\u5230 docker", "warn")
            self.set_status(f"\u5df2\u8fde\u63a5 {self.var_user.get()}@{self.var_host.get()}")
        except Exception as e:
            self.log(f"\u8fde\u63a5\u5931\u8d25: {e}", "error")
            self.set_status("\u8fde\u63a5\u5931\u8d25")

    def on_disconnect(self):
        self.remote.close()
        self.var_status.set("\u5df2\u65ad\u5f00")
        self.log("\u5df2\u65ad\u5f00\u8fde\u63a5", "info")

    def on_clear_log(self):
        self.txt_log.config(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.config(state="disabled")

    def on_deploy(self):
        if self.deploying:
            return
        if paramiko is None or yaml is None:
            self.log("\u5c1d\u8bd5\u81ea\u52a8\u5b89\u88c5\u4f9d\u8d56...", "warn")
            self._pip_install(None)
            self._reload_missing()
        if paramiko is None or yaml is None:
            messagebox.showerror(
                "\u7f3a\u5c11\u4f9d\u8d56",
                "\u81ea\u52a8\u5b89\u88c5\u5931\u8d25\uff0c\u8bf7\u624b\u52a8\u6267\u884c:\npip install -r requirements.txt",
            )
            return
        self.deploying = True
        self.btn_deploy.config(state="disabled")
        t = threading.Thread(target=self._deploy_worker, daemon=True)
        t.start()

    def on_uninstall(self):
        if self.deploying:
            return
        if paramiko is None or yaml is None:
            messagebox.showerror("\u7f3a\u5c11\u4f9d\u8d56", "\u8bf7\u5148\u5b89\u88c5\u5fc5\u8981\u4f9d\u8d56")
            return
        if not self.remote.is_connected():
            messagebox.showerror("\u672a\u8fde\u63a5", "\u8bf7\u5148\u8fde\u63a5\u5230\u670d\u52a1\u5668")
            return
        if not messagebox.askyesno("\u786e\u8ba4\u5378\u8f7d", "\u5c06\u6267\u884c docker compose down \u505c\u6b62\u5e76\u79fb\u9664\u5bb9\u5668\uff0c\u662f\u5426\u7ee7\u7eed\uff1f"):
            return
        self.deploying = True
        self.btn_deploy.config(state="disabled")
        self.btn_uninstall.config(state="disabled")
        t = threading.Thread(target=self._uninstall_worker, daemon=True)
        t.start()

    def _uninstall_worker(self):
        try:
            self._run_uninstall()
        except Exception as e:
            self.log(f"[\u9519\u8bef] {e}", "error")
            self.log(traceback.format_exc(), "error")
            self.notify("\u5378\u8f7d\u5931\u8d25", str(e))
        finally:
            self.deploying = False
            self.after(0, lambda: (self.btn_deploy.config(state="normal"), self.btn_uninstall.config(state="normal")))

    def _run_uninstall(self):
        self.log("==== \u5f00\u59cb\u5378\u8f7d ====")
        code, out = self.remote.run("echo $HOME")
        home_dir = out.strip()
        remote_dir = home_dir + "/ucm-observability"
        self.log(f"\u5378\u8f7d\u76ee\u5f55: {remote_dir}")
        qdir = shlex.quote(remote_dir)
        code, out = self.remote.run(f"cd {qdir} && {self.remote.compose_cmd} down", timeout=180)
        if out.strip():
            self.log(out.strip())
        if code != 0:
            self.log("\u8b66\u544a: docker compose down \u8fd4\u56de\u975e\u96f6\u72b6\u6001", "warn")
        else:
            self.log("\u5378\u8f7d\u5b8c\u6210: \u5bb9\u5668\u5df2\u505c\u6b62\u5e76\u79fb\u9664", "ok")
            self.notify("\u5378\u8f7d\u5b8c\u6210", "docker compose down \u6267\u884c\u6210\u529f\uff0c\u5bb9\u5668\u5df2\u79fb\u9664")

    def _deploy_worker(self):
        try:
            self._run_deploy()
        except Exception as e:
            self.log(f"[\u9519\u8bef] {e}", "error")
            self.log(traceback.format_exc(), "error")
            self.notify("\u90e8\u7f72\u5931\u8d25", str(e))
        finally:
            self.deploying = False
            self.after(0, lambda: self.btn_deploy.config(state="normal"))

    def _connect_from_ui(self):
        host = self.var_host.get().strip()
        port = int(self.var_port.get().strip() or "22")
        user = self.var_user.get().strip()
        password = self.var_password.get()
        if not host or not user:
            raise RuntimeError("\u8bf7\u586b\u5199\u4e3b\u673a\u5730\u5740\u548c\u7528\u6237\u540d")
        self.remote.connect(host, port, user, password)

    # ---- Core deploy flow -------------------------------------------------

    def _run_deploy(self):
        host = self.var_host.get().strip()
        port = int(self.var_port.get().strip() or "22")
        user = self.var_user.get().strip()
        password = self.var_password.get()
        if not host or not user:
            raise RuntimeError("\u8bf7\u586b\u5199\u4e3b\u673a\u5730\u5740\u548c\u7528\u6237\u540d")

        prometheus_port = self._require_port(self.var_port_prom, "Prometheus \u7aef\u53e3")
        grafana_port = self._require_port(self.var_port_graf, "Grafana \u7aef\u53e3")
        self.log(f"\u914d\u7f6e\u7aef\u53e3: Prometheus={prometheus_port}, Grafana={grafana_port}", "info")

        self.log("==== \u5f00\u59cb\u90e8\u7f72 ====")

        # 1. SSH connect
        if not self.remote.is_connected():
            self.log(f"\u6b63\u5728\u8fde\u63a5 {user}@{host}:{port} ...")
            self.remote.connect(host, port, user, password)
            self.log("SSH \u8fde\u63a5\u6210\u529f", "ok")
            self.set_status(f"\u5df2\u8fde\u63a5 {user}@{host}")

        # 2. Check docker
        code, out = self.remote.run("docker --version")
        if code != 0:
            raise RuntimeError("\u8fdc\u7a0b\u670d\u52a1\u5668\u672a\u5b89\u88c5 docker")
        self.log(f"docker: {out.strip().splitlines()[-1]}")

        # 3. Check compose
        code, out = self.remote.run("docker compose version")
        if code == 0:
            self.remote.compose_cmd = "docker compose"
            self.log("docker compose (v2) \u5df2\u5b89\u88c5")
        else:
            code, out = self.remote.run("docker-compose --version")
            if code != 0:
                raise RuntimeError("\u8fdc\u7a0b\u670d\u52a1\u5668\u672a\u5b89\u88c5 docker compose")
            self.remote.compose_cmd = "docker-compose"
            self.log("docker-compose (v1) \u5df2\u5b89\u88c5")

        # 4. Check images
        image_map = self._collect_images()
        missing = []
        for svc, image in image_map.items():
            if not image:
                continue
            q = shlex.quote(image)
            cmd = f"docker images --filter reference={q} --format '{{{{.Repository}}}}:{{{{.Tag}}}}'"
            code, out = self.remote.run(cmd)
            if code == 0 and out.strip():
                self.log(f"\u955c\u50cf\u5b58\u5728: {image}  (\u670d\u52a1: {svc})", "ok")
            else:
                self.log(f"\u955c\u50cf\u4e0d\u5b58\u5728: {image}  (\u670d\u52a1: {svc})", "warn")
                missing.append((svc, image))
        if missing:
            self.log("\u5c1d\u8bd5\u5728\u670d\u52a1\u5668\u4e0a\u641c\u7d22\u76f8\u8fd1\u955c\u50cf ...", "info")
        for svc, image in missing:
            keyword = "prometheus" if "prom" in svc.lower() else svc.lower()
            cmd2 = f"docker images --format '{{{{.Repository}}}}:{{{{.Tag}}}}' | grep -i {shlex.quote(keyword)}"
            code2, out2 = self.remote.run(cmd2)
            candidates = [l.strip() for l in out2.splitlines() if l.strip()]
            if candidates:
                self.log(f"\u641c\u7d22\u5230\u76f8\u8fd1\u955c\u50cf: {', '.join(candidates)}", "info")
                choice = self.ask_choice(
                    "\u9009\u62e9\u955c\u50cf",
                    f"\u670d\u52a1 [{svc}] \u7684\u955c\u50cf '{image}' \u4e0d\u5b58\u5728\uff0c\u4f46\u670d\u52a1\u5668\u4e0a\u627e\u5230\u4ee5\u4e0b\u76f8\u8fd1\u955c\u50cf\uff1a\n\n\u8bf7\u9009\u62e9\u8981\u4f7f\u7528\u7684\u955c\u50cf\uff1a",
                    candidates,
                )
                if choice:
                    image_map[svc] = choice
                    self.var_img_prom.set(choice) if "prom" in svc.lower() else self.var_img_graf.set(choice)
                    self.log(f"\u670d\u52a1 [{svc}] \u4f7f\u7528\u955c\u50cf: {choice}", "ok")
                    continue
            self.log(
                f"\u955c\u50cf\u4e0b\u8f7d\u5730\u5740\u63d0\u793a: "
                f"https://modelscope.cn/datasets/keriko/UCM_tools/tree/master/metrics/Grafana , "
                f"https://modelscope.cn/datasets/keriko/UCM_tools/tree/master/metrics/Prometheus",
                "info",
            )
            new = self.ask_text(
                "\u955c\u50cf\u4e0d\u5b58\u5728",
                f"\u670d\u52a1 [{svc}] \u9700\u8981\u7684\u955c\u50cf '{image}' \u5728\u670d\u52a1\u5668\u4e0a\u4e0d\u5b58\u5728\u3002\n"
                f"\u53ef\u4ece\u4ee5\u4e0b\u5730\u5740\u4e0b\u8f7d\u5e76\u5bfc\u5165\u955c\u50cf:\n"
                f"  - https://modelscope.cn/datasets/keriko/UCM_tools/tree/master/metrics/Grafana\n"
                f"  - https://modelscope.cn/datasets/keriko/UCM_tools/tree/master/metrics/Prometheus\n\n"
                f"\u8bf7\u8f93\u5165\u5b9e\u9645\u5b58\u5728\u7684\u955c\u50cf\u540d\u79f0 (\u7559\u7a7a\u5219\u5c1d\u8bd5 docker pull):",
                initial=image,
            )
            if new:
                image_map[svc] = new.strip()
            else:
                image_map[svc] = image

        # 5. Parse vLLM targets
        targets = self._parse_targets()
        if not targets:
            raise RuntimeError("\u8bf7\u81f3\u5c11\u586b\u5199\u4e00\u4e2a vLLM \u670d\u52a1\u5730\u5740")
        self.log(f"vLLM \u91c7\u96c6\u76ee\u6807: {', '.join(targets)}", "ok")

        # 6. Check port availability
        self._check_ports()

        # 7. Stage files
        staging = tempfile.mkdtemp(prefix="ucm_obs_")
        try:
            self._stage_files(staging, image_map, targets)
            self.log("\u6587\u4ef6\u7f16\u6392\u5b8c\u6210", "ok")

            # 8. Upload
            code, out = self.remote.run("echo $HOME")
            home_dir = out.strip()
            remote_dir = home_dir + "/ucm-observability"
            self.log(f"\u4e0a\u4f20\u6587\u4ef6\u5230 {remote_dir} ...")
            self.remote.upload_dir(staging, remote_dir, log=self.log)
            self.log("\u6587\u4ef6\u4e0a\u4f20\u5b8c\u6210", "ok")

            # 9. docker compose up
            qdir = shlex.quote(remote_dir)
            code, out = self.remote.run(f"cd {qdir} && {self.remote.compose_cmd} up -d", timeout=600)
            if out.strip():
                self.log(out.strip())
            if code != 0:
                logs = self.remote.run(f"cd {qdir} && {self.remote.compose_cmd} logs --tail=200", timeout=120)[1]
                self.log("===== docker compose \u5931\u8d25\u65e5\u5fd7 =====", "error")
                self.log(logs, "error")
                raise RuntimeError("docker compose up \u5931\u8d25, \u8be6\u89c1\u65e5\u5fd7")

            # 10. Verify
            code, out = self.remote.run(f"cd {qdir} && {self.remote.compose_cmd} ps", timeout=60)
            self.log(out.strip())
            if not self._verify_containers(out):
                logs = self.remote.run(f"cd {qdir} && {self.remote.compose_cmd} logs --tail=200", timeout=120)[1]
                self.log("===== \u5bb9\u5668\u672a\u6b63\u5e38\u542f\u52a8 =====", "error")
                self.log(logs, "error")
                raise RuntimeError("\u5bb9\u5668\u672a\u80fd\u6b63\u5e38\u542f\u52a8")

            # 11. Done
            url = f"http://{host}:{grafana_port}"
            self.log("", "ok")
            self.log("==============================================", "ok")
            self.log("\u90e8\u7f72\u6210\u529f!", "ok")
            self.log(f"Grafana \u8bbf\u95ee\u5730\u5740: {url}", "ok")
            self.log("\u9ed8\u8ba4\u8d26\u53f7\u5bc6\u7801: admin / admin", "info")
            self.log(f"Prometheus: http://{host}:{prometheus_port}", "ok")
            self.log("==============================================", "ok")
            self.notify("\u90e8\u7f72\u6210\u529f", f"Grafana: {url}\n\u8d26\u53f7\u5bc6\u7801: admin / admin")
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    # ---- Helper methods ---------------------------------------------------

    def _collect_images(self):
        with open(COMPOSE_YML, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        svcs = data.get("services") or {}
        m = {}
        if "prometheus" in svcs:
            m["prometheus"] = self.var_img_prom.get().strip()
        if "grafana" in svcs:
            m["grafana"] = self.var_img_graf.get().strip()
        return m

    def _parse_targets(self):
        raw = self.txt_targets.get("1.0", "end").strip()
        if not raw:
            return []
        lines = raw.splitlines()
        targets = []
        for line in lines:
            s = line.strip()
            if not s:
                continue
            s = re.sub(r"^https?://", "", s)
            s = s.split("/")[0].strip()
            if not re.match(r"^[\w.-]+:\d+$", s):
                raise RuntimeError(
                    f"vLLM \u5730\u5740\u683c\u5f0f\u4e0d\u6b63\u786e: {line}  (\u5e94\u4e3a host:port)"
                )
            targets.append(s)
        return targets

    def _collect_host_ports(self):
        ports = []
        p = self._get_port(self.var_port_prom, PROMETHEUS_PORT)
        if p:
            ports.append(p)
        p = self._get_port(self.var_port_graf, GRAFANA_PORT)
        if p:
            ports.append(p)
        return ports

    @staticmethod
    def _get_port(var, default):
        try:
            v = int(var.get().strip())
            if 1 <= v <= 65535:
                return v
        except (ValueError, AttributeError):
            pass
        return default

    @staticmethod
    def _require_port(var, label):
        try:
            v = int(var.get().strip())
        except (ValueError, AttributeError):
            raise RuntimeError(f"{label} \u4e0d\u662f\u6709\u6548\u7684\u6570\u5b57")
        if not 1 <= v <= 65535:
            raise RuntimeError(f"{label} \u8d85\u51fa\u6709\u6548\u8303\u56f4 (1-65535)")
        return v

    def _parse_listening_ports(self, text):
        ports = set()
        for line in text.splitlines():
            if "LISTEN" in line:
                m = re.search(r":(\d+)\s", line)
                if m:
                    ports.add(int(m.group(1)))
        return ports

    def _parse_docker_ports(self, text):
        ports = set()
        for seg in text.replace(",", " ").split():
            seg = seg.strip()
            m = re.match(r".*?:(\d+)->", seg)
            if m:
                ports.add(int(m.group(1)))
        return ports

    def _check_ports(self):
        ports = self._collect_host_ports()
        if not ports:
            return
        code, out = self.remote.run("ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null")
        listening = self._parse_listening_ports(out)
        busy = [p for p in ports if p in listening]
        if not busy:
            self.log(f"\u7aef\u53e3\u68c0\u67e5\u901a\u8fc7: {', '.join(map(str, ports))} \u5747\u7a7a\u95f2", "ok")
            return
        busy = sorted(set(busy))
        code, out2 = self.remote.run("docker ps --format '{{.Ports}}'")
        own = self._parse_docker_ports(out2)
        remaining = [p for p in busy if p not in own]
        if remaining:
            self.log(f"\u7aef\u53e3\u88ab\u5360\u7528: {', '.join(map(str, remaining))}", "warn")
            ok = self.ask_yesno("\u7aef\u53e3\u88ab\u5360\u7528",
                                f"\u4ee5\u4e0b\u7aef\u53e3\u5df2\u88ab\u5360\u7528: {', '.join(map(str, remaining))}\n\n"
                                f"\u662f\u5426\u4ecd\u7136\u7ee7\u7eed\u90e8\u7f72?")
            if not ok:
                raise RuntimeError("\u7528\u6237\u53d6\u6d88\u90e8\u7f72: \u7aef\u53e3\u88ab\u5360\u7528")
        else:
            self.log(f"\u7aef\u53e3 {', '.join(map(str, busy))} \u5df2\u7531\u5f53\u524d\u5bb9\u5668\u4f7f\u7528, \u53ef\u7ee7\u7eed", "info")

    def _stage_files(self, staging, image_map, targets):
        os.makedirs(os.path.join(staging, "dashboards"), exist_ok=True)

        # prometheus.yml: one job per vLLM target (job_name: vllm, vllm-2, ...)
        with open(PROMETHEUS_YML, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        data.pop("scrape_configs", None)
        scrape_configs = []
        for i, target in enumerate(targets, start=1):
            job_name = "vllm" if i == 1 else f"vllm-{i}"
            scrape_configs.append({
                "job_name": job_name,
                "static_configs": [{"targets": [target]}],
            })
        data["scrape_configs"] = scrape_configs
        with open(os.path.join(staging, "prometheus.yml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False)

        # docker-compose.yaml with corrected images and ports
        prom_port = self._get_port(self.var_port_prom, PROMETHEUS_PORT)
        graf_port = self._get_port(self.var_port_graf, GRAFANA_PORT)
        with open(COMPOSE_YML, encoding="utf-8") as f:
            cdata = yaml.safe_load(f) or {}
        svcs = cdata.get("services") or {}
        for svc, img in image_map.items():
            if img and svc in svcs:
                svcs[svc]["image"] = img
        if "prometheus" in svcs:
            svcs["prometheus"]["ports"] = [f"{prom_port}:9090"]
        if "grafana" in svcs:
            svcs["grafana"]["ports"] = [f"{graf_port}:3000"]
        with open(os.path.join(staging, "docker-compose.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(cdata, f, sort_keys=False)

        # Supporting files
        shutil.copy(DATASOURCE_YML, os.path.join(staging, "grafana-datasource.yml"))
        shutil.copy(PROVIDER_YML, os.path.join(staging, "grafana-dashboard-provider.yml"))

        # Dashboard JSONs
        if os.path.isdir(DASHBOARDS_DIR):
            for fn in os.listdir(DASHBOARDS_DIR):
                if fn.endswith(".json"):
                    shutil.copy(os.path.join(DASHBOARDS_DIR, fn),
                                os.path.join(staging, "dashboards", fn))

    def _verify_containers(self, ps_output):
        lines = [l.strip() for l in ps_output.splitlines() if l.strip()]
        if len(lines) < 2:
            return False
        statuses = []
        for l in lines[1:]:
            m = re.search(r"(Up\s|running|healthy)", l)
            if m:
                statuses.append(m.group(1))
            else:
                statuses.append("down")
        return all(s in ("Up ", "running", "healthy") for s in statuses)

    def _on_close(self):
        self.remote.close()
        self.destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = DeployApp()
    app.mainloop()