# macOS 部署软件编译使用指南

## 打包成独立 .app

### 方式一：运行编译脚本 (推荐)

```bash
bash build.sh
```

自动安装 PyInstaller 并完成编译，输出到 `dist/UCMObsDeploy.app`，可直接双击运行。

### 方式二：手动执行命令

在**项目根目录** (ucm-observability/) 依次执行：

```bash
# 1. 安装 PyInstaller
pip3 install pyinstaller

# 2. 编译 (注意 --add-data 冒号分隔，macOS/Linux 使用冒号而非分号)
python3 -m PyInstaller --noconfirm --onefile --windowed --name UCMObsDeploy \
    --add-data "prometheus_grafana/prometheus.yml:." \
    --add-data "prometheus_grafana/docker-compose.yaml:." \
    --add-data "prometheus_grafana/grafana-datasource.yml:." \
    --add-data "prometheus_grafana/grafana-dashboard-provider.yml:." \
    --add-data "prometheus_grafana/dashboards:dashboards" \
    "prometheus_grafana/macos_deploy/deploy_client.py"
```

生成的 `.app` 位于 `dist/UCMObsDeploy.app`，双击即可运行。

> **注意**: macOS 首次运行会提示 "无法验证开发者"，需前往 **系统设置 → 隐私与安全性** 点击 "仍要打开"。

## 软件使用操作步骤

1. 填写远程服务器 SSH 连接信息 (主机、端口、用户名、密码)
2. 点击 "连接测试" 确认连接正常
3. 确认容器镜像名称是否正确 (自动从 `docker-compose.yaml` 读取)
4. 填写 vLLM 服务地址 (每行一个, `host:port` 格式)
5. 点击 "开始部署"
6. 部署成功后会自动显示 Grafana 访问地址

## Python 脚本直接运行

不打包 .app，直接使用 Python 运行图形界面：

```bash
# 1. 安装依赖
pip3 install -r requirements.txt

# 2. 运行
python3 deploy_client.py
```

macOS 上 tkinter 默认随系统 Python 或 Homebrew Python 安装，如提示 `No module named tkinter`，请执行 `brew install python-tk`。

## X11 / 远程终端提示

若通过 SSH 远程登录 macOS，tkinter 图形界面无法显示。请确保在本地 macOS 桌面环境的终端中运行。