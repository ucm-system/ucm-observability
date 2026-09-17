#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UCM Observability 部署工具 (跨平台核心)

通过 SSH 将 Prometheus + Grafana 部署到远程服务器。
本文件为 Windows / macOS 客户端共用的核心代码, 由各平台入口文件加载:
  - windows_deploy/deploy_client.pyw
  - macos_deploy/deploy_client.py
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

# 平台入口目录 (由入口文件注入, 用于定位该平台的 requirements.txt)
PLATFORM_DIR = globals().get("PLATFORM_DIR", os.path.dirname(os.path.abspath(__file__)))

# 内置依赖清单, 在脚本模式下用于自动安装
REQUIREMENTS_FILE = os.path.join(PLATFORM_DIR, "requirements.txt")
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
        self.title("UCM Observability 部署工具  (Prometheus + Grafana)")
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
        frm = ttk.LabelFrame(main, text="SSH 连接", padding=8)
        frm.grid(row=0, column=0, sticky="ew")
        frm.columnconfigure(1, weight=1)

        self.var_host = tk.StringVar()
        self.var_port = tk.StringVar(value="22")
        self.var_user = tk.StringVar(value="root")
        self.var_password = tk.StringVar()

        ttk.Label(frm, text="主机:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.var_host, width=30).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Label(frm, text="端口:").grid(row=0, column=2, sticky="w")
        ttk.Entry(frm, textvariable=self.var_port, width=6).grid(row=0, column=3, sticky="w", **pad)

        ttk.Label(frm, text="用户名:").grid(row=1, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.var_user, width=30).grid(row=1, column=1, sticky="ew", **pad)
        ttk.Label(frm, text="密码:").grid(row=1, column=2, sticky="w")
        ttk.Entry(frm, textvariable=self.var_password, width=20, show="*").grid(row=1, column=3, sticky="w", **pad)

        btn_frm = ttk.Frame(frm)
        btn_frm.grid(row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))
        self.btn_connect = ttk.Button(btn_frm, text="连接测试", command=self.on_test_connect)
        self.btn_connect.pack(side="left")
        self.btn_disconnect = ttk.Button(btn_frm, text="断开", command=self.on_disconnect)
        self.btn_disconnect.pack(side="left", padx=(6, 0))

        # Container images & ports
        frm2 = ttk.LabelFrame(main, text="容器镜像与端口 (可修改)", padding=8)
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
        ttk.Label(frm2, text="Grafana 镜像:").grid(row=1, column=0, sticky="w")
        ttk.Entry(frm2, textvariable=self.var_img_graf).grid(row=1, column=1, sticky="ew", **pad)
        ttk.Label(frm2, text="端口:").grid(row=1, column=2, sticky="e")
        ttk.Entry(frm2, textvariable=self.var_port_graf, width=7).grid(row=1, column=3, sticky="w", **pad)

        # vLLM targets
        frm3 = ttk.LabelFrame(main, text="vLLM 服务地址 (每行一个, 格式 host:port)", padding=8)
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
        self.btn_deploy = ttk.Button(frm4, text="开始部署", command=self.on_deploy)
        self.btn_deploy.pack(side="left")
        self.btn_uninstall = ttk.Button(frm4, text="卸载", command=self.on_uninstall)
        self.btn_uninstall.pack(side="left", padx=(6, 0))
        ttk.Button(frm4, text="清空日志", command=self.on_clear_log).pack(side="left", padx=(6, 0))

        # Log area
        frm5 = ttk.LabelFrame(main, text="日志", padding=4)
        frm5.grid(row=4, column=0, sticky="nsew", pady=(8, 0))
        frm5.columnconfigure(0, weight=1)
        frm5.rowconfigure(0, weight=1)

        self.txt_log = scrolledtext.ScrolledText(
            frm5, state="disabled", height=18, font=("Consolas", 9), wrap="word"
        )
        self.txt_log.grid(row=0, column=0, sticky="nsew")

        # Status bar
        self.var_status = tk.StringVar(value="未连接")
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
        prefix = {"info": "[信息]", "ok": "[成功]", "error": "[错误]", "warn": "[警告]"}
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
            self.log(f"执行: pip install -r {REQUIREMENTS_FILE}", "info")
        else:
            pkgs = missing or ["paramiko>=3.0.0", "PyYAML>=6.0"]
            cmd += pkgs
            self.log(f"执行: pip install {' '.join(pkgs)}", "info")
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
                self.log(f"pip install 返回码 {proc.returncode}", "error")
                return False
            self.log("pip install 完成", "ok")
            return True
        except subprocess.TimeoutExpired:
            self.log("pip install 超时 (300s)", "error")
        except Exception as e:
            self.log(f"pip install 异常: {e}", "error")
        return False

    def _ensure_dependencies(self):
        if getattr(sys, "frozen", False):
            self.log("依赖检查: Python 运行时已内置在可执行文件中", "ok")
            return
        missing = self._detect_missing_deps()
        if not missing:
            self.log("依赖检查通过: paramiko, PyYAML", "ok")
            return
        self.log(f"检测到缺少依赖: {', '.join(missing)}, 开始自动安装...", "warn")
        if self._pip_install(missing):
            self._reload_missing()
            still = self._detect_missing_deps()
            if still:
                self.log(f"安装完成但仍缺少依赖: {', '.join(still)}", "error")
            else:
                self.log("依赖安装成功", "ok")
        else:
            self.log("依赖安装失败, 请手动执行: pip install -r requirements.txt", "error")

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
            ttk.Button(bf, text="确定", command=on_ok).pack(side="left")
            ttk.Button(bf, text="取消", command=on_cancel).pack(side="right")
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
            ttk.Button(bf, text="选择", command=on_ok).pack(side="left")
            ttk.Button(bf, text="取消", command=on_cancel).pack(side="right")
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
        self.set_status("正在测试连接...")
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

    def on_clear_log(self):
        self.txt_log.config(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.config(state="disabled")

    def on_deploy(self):
        if self.deploying:
            return
        if paramiko is None or yaml is None:
            self.log("尝试自动安装依赖...", "warn")
            self._pip_install(None)
            self._reload_missing()
        if paramiko is None or yaml is None:
            messagebox.showerror(
                "缺少依赖",
                "自动安装失败，请手动执行:\npip install -r requirements.txt",
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
            messagebox.showerror("缺少依赖", "请先安装必要依赖")
            return
        if not self.remote.is_connected():
            messagebox.showerror("未连接", "请先连接到服务器")
            return
        if not messagebox.askyesno("确认卸载", "将执行 docker compose down 停止并移除容器，是否继续？"):
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
            self.log(f"[错误] {e}", "error")
            self.log(traceback.format_exc(), "error")
            self.notify("卸载失败", str(e))
        finally:
            self.deploying = False
            self.after(0, lambda: (self.btn_deploy.config(state="normal"), self.btn_uninstall.config(state="normal")))

    def _run_uninstall(self):
        self.log("==== 开始卸载 ====")
        code, out = self.remote.run("echo $HOME")
        home_dir = out.strip()
        remote_dir = home_dir + "/ucm-observability"
        self.log(f"卸载目录: {remote_dir}")
        qdir = shlex.quote(remote_dir)
        code, out = self.remote.run(f"cd {qdir} && {self.remote.compose_cmd} down", timeout=180)
        if out.strip():
            self.log(out.strip())
        if code != 0:
            self.log("警告: docker compose down 返回非零状态", "warn")
        else:
            self.log("卸载完成: 容器已停止并移除", "ok")
            self.notify("卸载完成", "docker compose down 执行成功，容器已移除")

    def _deploy_worker(self):
        try:
            self._run_deploy()
        except Exception as e:
            self.log(f"[错误] {e}", "error")
            self.log(traceback.format_exc(), "error")
            self.notify("部署失败", str(e))
        finally:
            self.deploying = False
            self.after(0, lambda: self.btn_deploy.config(state="normal"))

    def _connect_from_ui(self):
        host = self.var_host.get().strip()
        port = int(self.var_port.get().strip() or "22")
        user = self.var_user.get().strip()
        password = self.var_password.get()
        if not host or not user:
            raise RuntimeError("请填写主机地址和用户名")
        self.remote.connect(host, port, user, password)

    # ---- Core deploy flow -------------------------------------------------

    def _run_deploy(self):
        host = self.var_host.get().strip()
        port = int(self.var_port.get().strip() or "22")
        user = self.var_user.get().strip()
        password = self.var_password.get()
        if not host or not user:
            raise RuntimeError("请填写主机地址和用户名")

        prometheus_port = self._require_port(self.var_port_prom, "Prometheus 端口")
        grafana_port = self._require_port(self.var_port_graf, "Grafana 端口")
        self.log(f"配置端口: Prometheus={prometheus_port}, Grafana={grafana_port}", "info")

        self.log("==== 开始部署 ====")

        # 1. SSH connect
        if not self.remote.is_connected():
            self.log(f"正在连接 {user}@{host}:{port} ...")
            self.remote.connect(host, port, user, password)
            self.log("SSH 连接成功", "ok")
            self.set_status(f"已连接 {user}@{host}")

        # 2. Check docker
        code, out = self.remote.run("docker --version")
        if code != 0:
            raise RuntimeError("远程服务器未安装 docker")
        self.log(f"docker: {out.strip().splitlines()[-1]}")

        # 3. Check compose
        code, out = self.remote.run("docker compose version")
        if code == 0:
            self.remote.compose_cmd = "docker compose"
            self.log("docker compose (v2) 已安装")
        else:
            code, out = self.remote.run("docker-compose --version")
            if code != 0:
                raise RuntimeError("远程服务器未安装 docker compose")
            self.remote.compose_cmd = "docker-compose"
            self.log("docker-compose (v1) 已安装")

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
                self.log(f"镜像存在: {image}  (服务: {svc})", "ok")
            else:
                self.log(f"镜像不存在: {image}  (服务: {svc})", "warn")
                missing.append((svc, image))
        if missing:
            self.log("尝试在服务器上搜索相近镜像 ...", "info")
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
                    self.var_img_prom.set(choice) if "prom" in svc.lower() else self.var_img_graf.set(choice)
                    self.log(f"服务 [{svc}] 使用镜像: {choice}", "ok")
                    continue
            self.log(
                f"镜像下载地址提示: "
                f"https://modelscope.cn/datasets/keriko/UCM_tools/tree/master/metrics/Grafana , "
                f"https://modelscope.cn/datasets/keriko/UCM_tools/tree/master/metrics/Prometheus",
                "info",
            )
            new = self.ask_text(
                "镜像不存在",
                f"服务 [{svc}] 需要的镜像 '{image}' 在服务器上不存在。\n"
                f"可从以下地址下载并导入镜像:\n"
                f"  - https://modelscope.cn/datasets/keriko/UCM_tools/tree/master/metrics/Grafana\n"
                f"  - https://modelscope.cn/datasets/keriko/UCM_tools/tree/master/metrics/Prometheus\n\n"
                f"请输入实际存在的镜像名称 (留空则尝试 docker pull):",
                initial=image,
            )
            if new:
                image_map[svc] = new.strip()
            else:
                image_map[svc] = image

        # 5. Parse vLLM targets
        targets = self._parse_targets()
        if not targets:
            raise RuntimeError("请至少填写一个 vLLM 服务地址")
        self.log(f"vLLM 采集目标: {', '.join(targets)}", "ok")

        # 6. Check port availability
        self._check_ports()

        # 7. Stage files
        staging = tempfile.mkdtemp(prefix="ucm_obs_")
        try:
            self._stage_files(staging, image_map, targets)
            self.log("文件编排完成", "ok")

            # 8. Upload
            code, out = self.remote.run("echo $HOME")
            home_dir = out.strip()
            remote_dir = home_dir + "/ucm-observability"
            self.log(f"上传文件到 {remote_dir} ...")
            self.remote.upload_dir(staging, remote_dir, log=self.log)
            self.log("文件上传完成", "ok")

            # 9. docker compose up
            qdir = shlex.quote(remote_dir)
            code, out = self.remote.run(f"cd {qdir} && {self.remote.compose_cmd} up -d", timeout=600)
            if out.strip():
                self.log(out.strip())
            if code != 0:
                logs = self.remote.run(f"cd {qdir} && {self.remote.compose_cmd} logs --tail=200", timeout=120)[1]
                self.log("===== docker compose 失败日志 =====", "error")
                self.log(logs, "error")
                raise RuntimeError("docker compose up 失败, 详见日志")

            # 10. Verify
            code, out = self.remote.run(f"cd {qdir} && {self.remote.compose_cmd} ps", timeout=60)
            self.log(out.strip())
            if not self._verify_containers(out):
                logs = self.remote.run(f"cd {qdir} && {self.remote.compose_cmd} logs --tail=200", timeout=120)[1]
                self.log("===== 容器未正常启动 =====", "error")
                self.log(logs, "error")
                raise RuntimeError("容器未能正常启动")

            # 11. Done
            url = f"http://{host}:{grafana_port}"
            self.log("", "ok")
            self.log("==============================================", "ok")
            self.log("部署成功!", "ok")
            self.log(f"Grafana 访问地址: {url}", "ok")
            self.log("默认账号密码: admin / admin", "info")
            self.log(f"Prometheus: http://{host}:{prometheus_port}", "ok")
            self.log("==============================================", "ok")
            self.notify("部署成功", f"Grafana: {url}\n账号密码: admin / admin")
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
                    f"vLLM 地址格式不正确: {line}  (应为 host:port)"
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
            raise RuntimeError(f"{label} 不是有效的数字")
        if not 1 <= v <= 65535:
            raise RuntimeError(f"{label} 超出有效范围 (1-65535)")
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
            self.log(f"端口检查通过: {', '.join(map(str, ports))} 均空闲", "ok")
            return
        busy = sorted(set(busy))
        code, out2 = self.remote.run("docker ps --format '{{.Ports}}'")
        own = self._parse_docker_ports(out2)
        remaining = [p for p in busy if p not in own]
        if remaining:
            self.log(f"端口被占用: {', '.join(map(str, remaining))}", "warn")
            ok = self.ask_yesno("端口被占用",
                                f"以下端口已被占用: {', '.join(map(str, remaining))}\n\n"
                                f"是否仍然继续部署?")
            if not ok:
                raise RuntimeError("用户取消部署: 端口被占用")
        else:
            self.log(f"端口 {', '.join(map(str, busy))} 已由当前容器使用, 可继续", "info")

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
