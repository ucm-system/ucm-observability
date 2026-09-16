# UCM Prometheus + Grafana 部署工具

## 前置准备工作
- 远程服务器已安装 docker、docker compose，下载：
    - [`docker`](https://docs.docker.com/engine/install/)
    - [`docker compose`](https://docs.docker.com/compose/install/linux/#install-using-the-repository)
- 已启动 vllm 推理服务
- 部署 Prometheus + Grafana 服务需要的镜像 'Grafana/Prometheus:latest'，如果未找到，部署工具会尝试 `docker pull` 下载，也可以从以下地址下载并导入镜像至服务器:
  - [Grafana 镜像下载](https://modelscope.cn/datasets/keriko/UCM_tools/tree/master/metrics/Grafana)
  - [Prometheus 镜像下载](https://modelscope.cn/datasets/keriko/UCM_tools/tree/master/metrics/Prometheus)


## 一键部署（for Windows PC）

一键将 Prometheus + Grafana 监控栈部署到远程服务器。
<img width="622" height="522" alt="image" src="https://github.com/user-attachments/assets/2c7825d7-c4ce-4524-ba8c-c43b21aa12bb" />

### 安装
请到发布页面下载对应的安装包：[Release page](https://github.com/ucm-system/ucm-observability/releases)

### 一键部署 Grafana + Prometheus 可视化界面

在 windows pc 上打开 `dist/UCMObsDeploy.exe`，连接到远程服务器，并填写关键信息，一键部署
- 填写远程服务器 SSH 连接信息 (主机、端口、用户名、密码)
- 点击 "连接测试" 确认连接正常
- 确认容器镜像名称是否正确 (自动从 `docker-compose.yaml` 读取)
- 填写 vLLM 服务地址 (每行一个, `host:port` 格式)
- 点击 "开始部署"
- 部署成功后会自动显示 Grafana 访问地址，账号密码



### 访问
打开网页浏览器，输入 Grafana 访问地址（登录提示修改密码），运行推理测试后等待几分钟，即可查看指标



## 部署（for linux）

修改配置文件 `prometheus_grafana/prometheus.yml` 的 vllm 推理服务地址为实际部署的ip和端口号。

启动 Prometheus + Grafana 服务
```sh
cd prometheus_grafana
docker compose up
```

停止服务
```sh
cd prometheus_grafana
docker compose down
```
