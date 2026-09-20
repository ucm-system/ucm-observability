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
2. 点击 "连接测试" 确认连接正常；连接成功后会自动探测服务器上已部署的面板服务 (见下方 "管理已部署的服务")
3. 确认容器镜像名称是否正确 (自动从 `docker-compose.yaml` 读取，一般无需修改；可点击 "下载地址" 按钮跳转到镜像包下载页面)
4. 填写 vLLM 服务地址 (每行一个, `host:port` 格式)
5. 点击 "开始部署"
6. 部署成功后会自动在浏览器打开 Grafana 页面，并在日志中显示访问地址

## 离线服务器 (无法连外网) 导入镜像

若目标服务器无法执行 `docker pull`，在部署过程中提示镜像不存在时，选择 "上传本地镜像包 (.tar) 并导入"：

1. 先在有网环境下载镜像包 (可点击界面上的 "下载地址" 按钮跳转下载页)
2. 在弹窗中选择本地 `.tar` / `.tar.gz` 镜像包文件
3. 程序会通过 SFTP 将镜像包上传到服务器 `~/ucm-observability/images/` 并执行 `docker load` 导入
4. 若镜像包内的镜像名与所需名称不同，程序会自动执行 `docker tag` 补齐所需名称

## 管理已部署的服务

- SSH 连接成功后，程序会自动探测服务器上已有的 Grafana 面板服务 (含停止的容器) 并列表展示
- 选中某条记录后可：
  - "打开页面": 在浏览器中打开该面板的访问地址
  - "卸载选中": compose 部署的项目整体下线 (`docker compose down`)，非 compose 容器则直接 `docker rm -f` 移除
- 因此即使关闭程序后重新打开，只要重新连接服务器，即可再次发现并管理/卸载之前部署的服务

## pyw 图形界面
不打包 exe，直接使用 pyw 图形界面，参考以下步骤。
### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 运行

双击 `deploy_client.pyw` 即可打开图形界面 (Python 需正确关联 `.pyw` 文件)。

或者命令行运行: `pythonw deploy_client.pyw`
