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
import webbrowser
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog

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

# 镜像包下载地址 (离线环境可先下载镜像包, 再通过本工具上传导入)
IMAGE_DOWNLOAD_URLS = {
    "prometheus": "https://modelscope.cn/datasets/keriko/UCM_tools/tree/master/metrics/Prometheus",
    "grafana": "https://modelscope.cn/datasets/keriko/UCM_tools/tree/master/metrics/Grafana",
}


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

    def upload_file(self, local_path, remote_path, log=None):
        sftp = self.client.open_sftp()
        try:
            rdir = remote_path.rsplit("/", 1)[0]
            if rdir:
                self._sftp_mkdir_p(sftp, rdir)
            sftp.put(local_path, remote_path)
            if log:
                log(f"上传 {os.path.basename(local_path)} -> {remote_path}")
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
        self.title("UCM Observability 部署工具  (Prometheus + Grafana)")
        self.geometry("940x820")
        self.minsize(860, 660)

        self.remote = RemoteHost()
        self.deploying = False
        self.deployments = {}

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
        frm2 = ttk.LabelFrame(main, text="容器镜像与端口", padding=8)
        frm2.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        frm2.columnconfigure(1, weight=1)

        self.var_img_prom = tk.StringVar()
        self.var_img_graf = tk.StringVar()
        self.var_port_prom = tk.StringVar(value=str(PROMETHEUS_PORT))
        self.var_port_graf = tk.StringVar(value=str(GRAFANA_PORT))
        ttk.Label(frm2, text="Prometheus 镜像:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm2, textvariable=self.var_img_prom).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Label(frm2, text="端口:").grid(row=0, column=2, sticky="e")
        ttk.Entry(frm2, textvariable=self.var_port_prom, width=7).grid(row=0, column=3, sticky="w", **pad)
        ttk.Button(frm2, text="下载地址",
                   command=lambda: self._open_url(IMAGE_DOWNLOAD_URLS["prometheus"])
                   ).grid(row=0, column=4, sticky="w")
        ttk.Label(frm2, text="Grafana 镜像:").grid(row=1, column=0, sticky="w")
        ttk.Entry(frm2, textvariable=self.var_img_graf).grid(row=1, column=1, sticky="ew", **pad)
        ttk.Label(frm2, text="端口:").grid(row=1, column=2, sticky="e")
        ttk.Entry(frm2, textvariable=self.var_port_graf, width=7).grid(row=1, column=3, sticky="w", **pad)
        ttk.Button(frm2, text="下载地址",
                   command=lambda: self._open_url(IMAGE_DOWNLOAD_URLS["grafana"])
                   ).grid(row=1, column=4, sticky="w")
        ttk.Label(
            frm2,
            text="镜像名称自动读取自 docker-compose.yaml，一般无需修改；仅当服务器上镜像名称不同或需指定版本时才修改。",
            foreground="#777",
        ).grid(row=2, column=0, columnspan=5, sticky="w", pady=(4, 0))

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

        # Existing deployments on the server
        frm_dep = ttk.LabelFrame(main, text="服务器上已部署的面板服务 (连接后自动探测)", padding=8)
        frm_dep.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        frm_dep.columnconfigure(0, weight=1)

        cols = ("container", "status", "port", "url")
        self.tree_dep = ttk.Treeview(frm_dep, columns=cols, show="headings", height=3, selectmode="browse")
        self.tree_dep.heading("container", text="容器")
        self.tree_dep.heading("status", text="状态")
        self.tree_dep.heading("port", text="端口")
        self.tree_dep.heading("url", text="访问地址")
        self.tree_dep.column("container", width=150, anchor="w")
        self.tree_dep.column("status", width=120, anchor="w")
        self.tree_dep.column("port", width=60, anchor="center")
        self.tree_dep.column("url", width=230, anchor="w")
        self.tree_dep.grid(row=0, column=0, sticky="ew")

        dep_btn = ttk.Frame(frm_dep)
        dep_btn.grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Button(dep_btn, text="重新探测", command=self.on_detect_deployments).pack(side="left")
        ttk.Button(dep_btn, text="打开页面", command=self.on_open_selected_deployment).pack(side="left", padx=(6, 0))
        ttk.Button(dep_btn, text="卸载选中", command=self.on_uninstall_selected_deployment).pack(side="left", padx=(6, 0))

        # Action buttons
        frm4 = ttk.Frame(main)
        frm4.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        self.btn_deploy = ttk.Button(frm4, text="\u5f00\u59cb\u90e8\u7f72", command=self.on_deploy)
        self.btn_deploy.pack(side="left")
        self.btn_uninstall = ttk.Button(frm4, text="\u5378\u8f7d", command=self.on_uninstall)
        self.btn_uninstall.pack(side="left", padx=(6, 0))
        ttk.Button(frm4, text="\u6e05\u7a7a\u65e5\u5fd7", command=self.on_clear_log).pack(side="left", padx=(6, 0))

        # Log area
        frm5 = ttk.LabelFrame(main, text="日志", padding=4)
        frm5.grid(row=5, column=0, sticky="nsew", pady=(8, 0))
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

        main.rowconfigure(5, weight=1)

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

    def _open_url(self, url):
        try:
            webbrowser.open(url)
            self.log(f"已打开: {url}", "info")
        except Exception as e:
            self.log(f"无法打开浏览器: {e}", "error")

    @staticmethod
    def _normalize_image(image):
        image = (image or "").strip()
        if not image:
            return image
        tail = image.rsplit("/", 1)[-1]
        if ":" not in tail:
            image += ":latest"
        return image

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

    def ask_openfile(self, title, prompt, filetypes):
        evt = threading.Event()
        result = {"value": None}

        def _show():
            dlg = tk.Toplevel(self)
            dlg.title(title)
            dlg.transient(self)
            dlg.grab_set()
            frm = ttk.Frame(dlg, padding=16)
            frm.pack(fill="both", expand=True)
            ttk.Label(frm, text=prompt, wraplength=460, justify="left").pack(anchor="w")
            var = tk.StringVar()

            def on_pick():
                path = filedialog.askopenfilename(title=title, parent=dlg, filetypes=filetypes)
                if path:
                    var.set(path)

            row = ttk.Frame(frm)
            row.pack(fill="x", pady=8)
            ent = ttk.Entry(row, textvariable=var, width=52)
            ent.pack(side="left", fill="x", expand=True)
            ttk.Button(row, text="浏览...", command=on_pick).pack(side="left", padx=(6, 0))

            def on_ok():
                result["value"] = var.get().strip() or None
                evt.set()
                dlg.destroy()

            def on_cancel():
                result["value"] = None
                evt.set()
                dlg.destroy()

            bf = ttk.Frame(frm)
            bf.pack(fill="x", pady=(4, 0))
            ttk.Button(bf, text="确定", command=on_ok).pack(side="left")
            ttk.Button(bf, text="取消", command=on_cancel).pack(side="right")
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
            self.log("SSH 连接成功", "ok")
            code, out = self.remote.run("docker --version")
            if code == 0 and out.strip():
                ver = out.strip().splitlines()[0]
                self.log(f"远程 docker: {ver}")
                if self._ensure_compose_cmd():
                    self.log(f"compose 命令: {self.remote.compose_cmd}")
                else:
                    self.log("未检测到 docker compose，部署/卸载功能不可用", "warn")
                self._detect_and_refresh()
            else:
                self.log("远程未检测到 docker", "warn")
            self.set_status(f"已连接 {self.var_user.get()}@{self.var_host.get()}")
        except Exception as e:
            self.log(f"连接失败: {e}", "error")
            self.set_status("连接失败")

    def on_disconnect(self):
        self.remote.close()
        self.var_status.set("已断开")
        self.log("已断开连接", "info")
        self._refresh_dep_tree([])

    def on_clear_log(self):
        self.txt_log.config(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.config(state="disabled")

    # ---- Existing deployments detection / management ----------------------

    def on_detect_deployments(self):
        if self.deploying:
            return
        if not self.remote.is_connected():
            self.log("未连接服务器，无法探测已部署服务", "warn")
            return
        threading.Thread(target=self._detect_and_refresh, daemon=True).start()

    def _detect_and_refresh(self):
        try:
            deps = self._detect_existing_deployments()
            self.after(0, self._refresh_dep_tree, deps)
            if deps:
                self.log(f"探测到 {len(deps)} 个已部署的面板服务，可在列表中选择打开或卸载", "ok")
            else:
                self.log("未探测到已部署的面板服务", "info")
        except Exception as e:
            self.log(f"探测已部署服务失败: {e}", "warn")

    def _detect_existing_deployments(self):
        fmt = '{{.Names}}|{{.Status}}|{{.Ports}}|{{.Label "com.docker.compose.project.working_dir"}}'
        cmd = "docker ps -a --format " + shlex.quote(fmt)
        code, out = self.remote.run(cmd, timeout=60)
        if code != 0:
            self.log("docker ps 执行失败，无法探测已部署服务", "warn")
            return []
        host = self.var_host.get().strip()
        deps = []
        for line in out.splitlines():
            parts = line.split("|")
            if len(parts) < 4:
                continue
            name, status, ports, workdir = (p.strip() for p in parts[:4])
            if "grafana" not in name.lower():
                continue
            if workdir in ("<no value>", "<nil>"):
                workdir = ""
            port = None
            m = re.search(r":(\d+)->", ports)
            if m:
                port = m.group(1)
            deps.append({
                "name": name,
                "status": status,
                "port": port or "-",
                "url": f"http://{host}:{port}" if port else "",
                "workdir": workdir,
            })
        return deps

    def _refresh_dep_tree(self, deployments):
        self.deployments = {}
        self.tree_dep.delete(*self.tree_dep.get_children())
        for d in deployments:
            iid = self.tree_dep.insert("", "end", values=(d["name"], d["status"], d["port"], d["url"] or "-"))
            self.deployments[iid] = d

    def on_open_selected_deployment(self):
        sel = self.tree_dep.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在列表中选择一个服务")
            return
        d = self.deployments.get(sel[0])
        if not d:
            return
        if not d["url"]:
            messagebox.showwarning("无法打开", "该容器未发布端口或已停止，无法生成访问地址")
            return
        self._open_url(d["url"])

    def on_uninstall_selected_deployment(self):
        if self.deploying:
            return
        sel = self.tree_dep.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在列表中选择一个服务")
            return
        d = self.deployments.get(sel[0])
        if not d:
            return
        target = d["workdir"] or d["name"]
        if not messagebox.askyesno(
            "确认卸载",
            f"将停止并移除: {target}\n(compose 部署会移除整个项目，含关联的 Prometheus 容器)\n\n是否继续？",
        ):
            return
        self.deploying = True
        self.btn_deploy.config(state="disabled")
        self.btn_uninstall.config(state="disabled")
        threading.Thread(target=self._uninstall_selected_worker, args=(d,), daemon=True).start()

    def _uninstall_selected_worker(self, d):
        try:
            if d.get("workdir"):
                qdir = shlex.quote(d["workdir"])
                code, out = self.remote.run(f"cd {qdir} && {self.remote.compose_cmd} down", timeout=300)
                if out.strip():
                    self.log(out.strip())
                if code == 0:
                    self.log(f"已卸载 compose 项目: {d['workdir']}", "ok")
                else:
                    self.log("docker compose down 失败，改为直接移除容器 ...", "warn")
                    code, out = self.remote.run(f"docker rm -f {shlex.quote(d['name'])}", timeout=120)
                    if out.strip():
                        self.log(out.strip())
                    if code != 0:
                        raise RuntimeError(f"移除容器 {d['name']} 失败")
                    self.log(f"已移除容器 {d['name']}", "ok")
            else:
                code, out = self.remote.run(f"docker rm -f {shlex.quote(d['name'])}", timeout=120)
                if out.strip():
                    self.log(out.strip())
                if code != 0:
                    raise RuntimeError(f"移除容器 {d['name']} 失败")
                self.log(f"已移除容器 {d['name']}", "ok")
            self.notify("卸载完成", f"{d['name']} 已停止并移除")
        except Exception as e:
            self.log(f"卸载失败: {e}", "error")
            self.notify("卸载失败", str(e))
        finally:
            self.deploying = False
            self.after(0, lambda: (self.btn_deploy.config(state="normal"), self.btn_uninstall.config(state="normal")))
            self._detect_and_refresh()

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
            self._detect_and_refresh()

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
        if not self._ensure_compose_cmd():
            raise RuntimeError("远程服务器未安装 docker compose")
        self.log(f"{self.remote.compose_cmd} 已安装")

        # 4. Check images
        image_map = self._collect_images()
        missing = []
        for svc, image in image_map.items():
            if not image:
                continue
            q = shlex.quote(self._normalize_image(image))
            cmd = f"docker images --filter reference={q} --format '{{{{.Repository}}}}:{{{{.Tag}}}}'"
            code, out = self.remote.run(cmd)
            if code == 0 and out.strip():
                self.log(f"镜像存在: {image}  (服务: {svc})", "ok")
            else:
                self.log(f"镜像不存在: {image}  (服务: {svc})", "warn")
                missing.append((svc, image))
        if missing:
            self.log("部分镜像不存在，尝试在服务器上搜索相近镜像 ...", "info")
        for svc, image in missing:
            keyword = "prometheus" if "prom" in svc.lower() else svc.lower()
            cmd2 = f"docker images --format '{{{{.Repository}}}}:{{{{.Tag}}}}' | grep -i {shlex.quote(keyword)}"
            code2, out2 = self.remote.run(cmd2)
            candidates = [l.strip() for l in out2.splitlines() if l.strip()]
            if candidates:
                self.log(f"搜索到相近镜像: {', '.join(candidates)}", "info")
                choice = self.ask_choice(
                    "选择镜像",
                    f"服务 [{svc}] 的镜像 '{image}' 不存在，但服务器上找到以下相近镜像：\n\n请选择要使用的镜像：",
                    candidates,
                )
                if choice:
                    image_map[svc] = choice
                    if "prom" in svc.lower():
                        self.var_img_prom.set(choice)
                    else:
                        self.var_img_graf.set(choice)
                    self.log(f"服务 [{svc}] 使用镜像: {choice}", "ok")
                    continue
            opt_pull = "尝试 docker pull (服务器需可连外网)"
            opt_tar = "上传本地镜像包 (.tar) 并导入"
            opt_cancel = "取消部署"
            action = self.ask_choice(
                "镜像不存在",
                f"服务 [{svc}] 需要的镜像 '{image}' 在服务器上不存在。\n\n"
                f"镜像包下载地址:\n"
                f"  - Grafana: {IMAGE_DOWNLOAD_URLS['grafana']}\n"
                f"  - Prometheus: {IMAGE_DOWNLOAD_URLS['prometheus']}\n\n"
                f"若服务器无法连外网 (docker pull 会失败)，请选择 '{opt_tar}'，\n"
                f"程序将通过 SFTP 上传镜像包并执行 docker load 导入。",
                [opt_pull, opt_tar, opt_cancel],
            )
            if action == opt_pull:
                new = self.ask_text(
                    "镜像名称",
                    f"要拉取的镜像名称 (留空使用 {image}):",
                    initial=image,
                )
                if new and new.strip():
                    image_map[svc] = new.strip()
                    if "prom" in svc.lower():
                        self.var_img_prom.set(new.strip())
                    else:
                        self.var_img_graf.set(new.strip())
            elif action == opt_tar:
                if not self._load_image_from_tar(svc, self._normalize_image(image)):
                    raise RuntimeError(f"服务 [{svc}] 镜像导入失败，部署中止")
            else:
                raise RuntimeError("用户取消部署")

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
            self.log("部署成功!", "ok")
            self.log(f"Grafana 访问地址: {url}", "ok")
            self.log("默认账号密码: admin / admin", "info")
            self.log(f"Prometheus: http://{host}:{prometheus_port}", "ok")
            self.log("==============================================", "ok")
            self.log("正在打开 Grafana 页面 ...", "info")
            self.after(0, self._open_url, url)
            self.notify("部署成功", f"Grafana: {url}\n账号密码: admin / admin")
            self._detect_and_refresh()
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

    def _ensure_compose_cmd(self):
        code, _ = self.remote.run("docker compose version", timeout=30)
        if code == 0:
            self.remote.compose_cmd = "docker compose"
            return True
        code, _ = self.remote.run("docker-compose --version", timeout=30)
        if code == 0:
            self.remote.compose_cmd = "docker-compose"
            return True
        return False

    def _load_image_from_tar(self, svc, required_image):
        tar_path = self.ask_openfile(
            "选择镜像包",
            f"请选择服务 [{svc}] 需要的镜像包文件 (.tar / .tar.gz):\n"
            f"可从以下地址下载:\n  - {IMAGE_DOWNLOAD_URLS.get(svc, IMAGE_DOWNLOAD_URLS['grafana'])}",
            [("镜像包 (*.tar;*.tar.gz;*.tgz)", "*.tar;*.tar.gz;*.tgz"), ("所有文件 (*.*)", "*.*")],
        )
        if not tar_path:
            self.log("未选择镜像包", "warn")
            return False

        _, before = self.remote.run("docker images --format '{{.Repository}}:{{.Tag}}'")
        before_set = {l.strip() for l in before.splitlines() if l.strip()}

        code, out = self.remote.run("echo $HOME")
        home_dir = out.strip()
        fname = os.path.basename(tar_path).replace('"', "").replace("'", "")
        remote_path = f"{home_dir}/ucm-observability/images/{fname}"
        size_mb = os.path.getsize(tar_path) / (1024 * 1024)
        self.log(f"上传镜像包 {fname} ({size_mb:.1f} MB) 到服务器，文件较大时请耐心等待 ...", "info")
        self.remote.upload_file(tar_path, remote_path, log=self.log)
        self.log("镜像包上传完成", "ok")

        qpath = shlex.quote(remote_path)
        if remote_path.endswith((".tar.gz", ".tgz")):
            load_cmd = f"gunzip -c {qpath} | docker load"
        else:
            load_cmd = f"docker load -i {qpath}"
        self.log(f"执行: {load_cmd}")
        code, out = self.remote.run(load_cmd, timeout=3600)
        if out.strip():
            self.log(out.strip())
        if code != 0:
            self.log("docker load 执行失败", "error")
            return False

        _, after = self.remote.run("docker images --format '{{.Repository}}:{{.Tag}}'")
        after_set = {l.strip() for l in after.splitlines() if l.strip()}
        if required_image in after_set:
            self.log(f"镜像导入成功: {required_image}", "ok")
            return True

        new_images = sorted(after_set - before_set)
        target = None
        if len(new_images) == 1:
            target = new_images[0]
            self.log(f"导入的镜像为 {target}", "info")
        elif new_images:
            self.log(f"镜像包导入了多个镜像: {', '.join(new_images)}", "info")
            target = self.ask_choice(
                "选择镜像",
                f"镜像包导入了多个镜像，请选择用于服务 [{svc}] 的镜像\n(将被标记为 {required_image}):",
                new_images,
            )
        if target:
            code, out = self.remote.run(
                f"docker tag {shlex.quote(target)} {shlex.quote(required_image)}", timeout=60
            )
            if code == 0:
                self.log(f"已将 {target} 标记为 {required_image}", "ok")
                return True
            self.log(f"docker tag 失败: {out.strip()}", "error")
            return False
        self.log(f"导入后未发现所需镜像 {required_image}", "error")
        return False

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