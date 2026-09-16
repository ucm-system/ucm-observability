# windows 部署软件编译使用指南

## 打包成独立 exe

### 方式一：运行编译脚本 (推荐)

```bat
build.bat
```

双击即可自动安装 PyInstaller、探测 conda 的 tcl/tk DLL 并完成编译，输出到项目根目录 `dist/UCMObsDeploy.exe`。

### 方式二：手动执行命令

在**项目根目录** (ucm-observability/) 依次执行：

```bash
# 1. 安装 PyInstaller
pip install pyinstaller

# 2. 编译 (注意各 --add-data / --add-binary 参数)
python -m PyInstaller --noconfirm --onefile --windowed --name UCMObsDeploy ^
    --add-data "prometheus_grafana/prometheus.yml;." ^
    --add-data "prometheus_grafana/docker-compose.yaml;." ^
    --add-data "prometheus_grafana/grafana-datasource.yml;." ^
    --add-data "prometheus_grafana/grafana-dashboard-provider.yml;." ^
    --add-data "prometheus_grafana/dashboards;dashboards" ^
    --add-binary "%CONDA_PREFIX%\Library\bin\tcl86t.dll;." ^
    --add-binary "%CONDA_PREFIX%\Library\bin\tk86t.dll;." ^
    --add-binary "%CONDA_PREFIX%\Library\bin\libcrypto-3-x64.dll;." ^
    --add-binary "%CONDA_PREFIX%\Library\bin\libssl-3-x64.dll;." ^
    --add-binary "%CONDA_PREFIX%\Library\bin\liblzma.dll;." ^
    --add-binary "%CONDA_PREFIX%\Library\bin\libbz2.dll;." ^
    "prometheus_grafana/windows_deploy/deploy_client.pyw"
```

> `%CONDA_PREFIX%` 需替换为实际 conda 环境路径，例如 `C:\ProgramData\anaconda3\envs\ucm`。若使用标准 Python 安装 (非 conda)，tcl/tk DLL 通常位于 `sys.prefix\DLLs` 下且 PyInstaller 能自动解析，可省略 `--add-binary` 中的 tcl/tk 两项。

生成的 exe 位于 `dist/UCMObsDeploy.exe`, 可直接双击运行。

## 软件使用操作步骤

1. 填写远程服务器 SSH 连接信息 (主机、端口、用户名、密码)
2. 点击 "连接测试" 确认连接正常
3. 确认容器镜像名称是否正确 (自动从 `docker-compose.yaml` 读取)
4. 填写 vLLM 服务地址 (每行一个, `host:port` 格式)
5. 点击 "开始部署"
6. 部署成功后会自动显示 Grafana 访问地址

## pyw 图形界面
不打包 exe，直接使用 pyw 图形界面，参考以下步骤。
### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 运行

双击 `deploy_client.pyw` 即可打开图形界面 (Python 需正确关联 `.pyw` 文件)。

或者命令行运行: `pythonw deploy_client.pyw`
