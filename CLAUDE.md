# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

DockPorts 是一个容器端口监控与可视化工具。后端为单文件 Flask 应用，前端为单文件 HTML（内嵌全部 CSS/JS）。运行时通过 Docker API 读取容器端口映射、通过 `netstat` 读取主机监听端口，合并后在卡片式界面展示。

## 常用命令

```bash
# 本地开发（默认端口 7577）
pip install -r requirements.txt
python app.py
python app.py --port 8080 --debug    # 自定义端口 + 调试模式
python app.py --help

# Docker（必须 host 网络 + 挂载 docker.sock）
docker-compose up -d
docker compose logs -f dockports

# 直接构建镜像
docker build -t dockports .
```

无测试套件、无 lint 配置。镜像发布由 GitHub Actions（`.github/workflows/docker-publish.yml`）在推送 `v*.*.*` tag 时触发，构建 `linux/amd64,arm64,arm/v7` 并推送到 ghcr.io 与阿里云。

## 架构要点

**单文件后端 `app.py`**：核心是 `PortMonitor` 类（全局单例 `port_monitor`）。Flask 路由都是它的薄封装。

- `get_docker_ports()`：读取容器 `NetworkSettings.Ports` 的显式端口映射。
- `get_host_ports()`：解析 `netstat -tuln` 输出，区分 TCP/UDP 与 IPv4/IPv6（`TCP6`/`UDP6` 后缀判定 IPv6）。
- `get_host_network_containers_cached()`：**host 网络容器没有端口映射**，因此从四处推断端口——`ExposedPorts`、`Healthcheck.Test`、`Entrypoint/Cmd`（多种 `--port`/`:port` 正则）、含 `PORT/LISTEN/BIND` 的环境变量。结果带 30 秒缓存（`cache_ttl`）。修改端口检测逻辑通常在这里。
- `get_port_analysis(start_port, end_port, protocol_filter)`：把上述数据合并为前端用的 `port_cards` 列表。卡片有四种 `type`：`used`（单端口）、`unknown_range`（≥2 个连续「未知服务」端口合并）、`gap`（未使用端口区间）、虚拟隐藏端口。最后按 `hidden_ports` 过滤。返回结构即 `/api/ports` 的响应。

**配置体系（两个文件，都在 `config/`，由容器卷挂载持久化）**：

- `config.json`：端口→服务名映射。**当前格式**键值均为字符串：`"服务名:host"` 或 `"服务名:docker"` → `"端口:tcp"` 或 `"端口:udp"`（例 `"远程登录:host": "22:tcp"`）。`load_config()` 会把它解析成内部 dict `{port, protocol, service_type}`；`save_config()` 反向序列化。代码同时**兼容旧格式**（`"服务名": 端口` 或 `"服务名": "端口:协议"`），改动解析逻辑时务必保持两种格式都能读。
- `hidden_ports.json`：被隐藏端口的整数数组。
- 二者首次运行由 `init_config()` 创建（`config.json` 优先从 `config.json.example` 复制）。

**全局 `config` 变量**：模块加载时 `load_config()` 一次，写配置的 API 成功后会 `global config; config = load_config()` 重新加载——新增写配置路径时记得同步刷新，否则界面读到旧值。

**前端 `templates/index.html`（约 2700 行，单文件）**：原生 JS，无构建步骤。通过 `/api/ports`（支持 `protocol`/`start_port`/`end_port`/`search` 查询参数）拉数据并渲染卡片；端口范围锁定、协议过滤、搜索都在前端配合后端参数完成。改交互逻辑直接编辑此文件内的 `<script>`。

## 关键约束

- **路径硬编码**：`CONFIG_DIR = '/app/config'` 是容器内绝对路径。本地 `python app.py` 运行时会尝试在 `/app/config` 读写（Windows 下需注意），这是容器优先的设计。
- **依赖 Linux 运行时**：`netstat`（net-tools）、Docker socket `/var/run/docker.sock`（只读挂载）、host 网络模式是正常工作的前提。
- **配置优先级**：命令行参数 > 环境变量（`DOCKPORTS_PORT`/`HOST`/`DEBUG`）> 默认值，逻辑在 `parse_args()`。

## 提交规范

提交信息使用中文，遵循现有风格（如 `feat:`、`v0.2.1: ...`）。版本通过 git tag（`vX.Y.Z`）发布并触发 CI。
