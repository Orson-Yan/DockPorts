# DockPorts - 容器端口监控工具

> 本项目 Fork 自 [coracoo/DockPorts](https://github.com/coracoo/DockPorts)，感谢原作者的开源贡献。
> Docker Hub 镜像：`orsoman/dockports:latest`

一个现代化的 Docker 容器端口监控和可视化工具，帮助你轻松管理 NAS 或服务器上的端口使用情况。

## ✨ 功能特性

### 监控与展示
- 🐳 **Docker 集成**：通过 Docker API 实时监控容器端口映射，支持 bridge / host 网络模式
- 🖥️ **系统监控**：使用 psutil 监控主机端口使用情况（跨平台，无需 netstat）
- 📊 **可视化展示**：卡片式界面，清晰区分 Docker 容器端口与系统服务端口
- 🌙 **深色模式**：一键切换深色/浅色主题，偏好本地持久化
- 📱 **响应式设计**：支持桌面和移动设备
- 📦 **容器详情**：卡片展示镜像名，悬浮显示镜像/容器端口/连接数
- ⏸ **端口预留视图**：可选显示「已停止容器声明的端口」，规划时避免与停止容器撞端口；占用卡上标注冲突
- 🔧 **占用进程**：宿主机端口可显示占用进程名（需 `pid:host` + `privileged`，默认关闭，详见下文）
- 🔗 **连接数显示**：展示每个端口当前活跃（ESTABLISHED）连接数
- 🟢 **Docker 状态**：工具栏指示灯实时显示 Docker 连接状态
- ⏱️ **刷新时间**：显示上次数据刷新时间

### 交互与筛选
- 🔄 **自动刷新**：支持手动刷新，可设置 10s / 30s / 60s 自动刷新间隔（带倒计时）
- 🔌 **端口跳转**：点击端口号直接跳转 `http(s)://地址:端口`，支持内网/外网地址切换
- 🔐 **登录鉴权**：可选会话登录保护，用户名/密码在应用设置中配置
- 🎯 **智能排序**：支持按端口升/降序、服务名、来源多种排序方式
- 🔎 **搜索过滤**：按端口、进程、服务名、容器名、协议搜索
- 🔌 **协议过滤**：支持 TCP/UDP 协议过滤与统计切换
- 🐳 **按容器筛选**：侧边栏动态列出有端口的容器，一键只看某容器的端口
- 💡 **变更高亮**：刷新后自动高亮新出现的端口
- 👁️ **端口隐藏**：支持隐藏不需要显示的端口，提供「已隐藏」标签页查看
- 📋 **批量操作**：支持批量隐藏/取消隐藏端口范围
- 📥 **数据导出**：一键导出当前端口列表为 CSV（UTF-8，中文无乱码）

---

## 🚀 快速开始

### 方式一：Docker Compose（推荐）

创建 `docker-compose.yml`：

```yaml
version: '3.8'

services:
  dockports:
    image: orsoman/dockports:latest
    container_name: dockports
    network_mode: host
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./config:/app/config
    environment:
      - DOCKPORTS_PORT=7577  # 可修改此端口以避免冲突
    restart: unless-stopped
    # —— 可选：在「宿主机」端口卡片上显示占用进程名（如 mihomo / ollama / xrdp）——
    # 默认关闭。容器与宿主机的 PID 命名空间隔离，且需提权才能映射 socket->进程。
    # 解开下面两行即可启用。⚠ 安全提示：privileged 容器近似拥有宿主机 root 权限，
    # 攻击面明显增大，请仅在可信内网/自用环境开启。
    # pid: host
    # privileged: true
```

启动：

```bash
docker compose up -d
```

访问 `http://你的IP:7577`

### 方式二：Docker Run

```bash
docker run -d \
  --name dockports \
  --network host \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -v ./config:/app/config \
  -e DOCKPORTS_PORT=7577 \
  orsoman/dockports:latest
```

---

## ⚙️ 配置说明

### 关键挂载

| 主机路径 | 容器路径 | 说明 |
|----------|----------|------|
| `/var/run/docker.sock` | `/var/run/docker.sock` | Docker API 访问（只读） |
| `./config` | `/app/config` | 配置文件目录（持久化） |

> `network_mode: host` 为**必须项**，否则 psutil 无法读取主机端口。

### 环境变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `DOCKPORTS_PORT` | `7577` | Web 界面端口 |
| `DOCKPORTS_HOST` | `0.0.0.0` | Web 服务监听地址 |
| `DOCKPORTS_DEBUG` | `false` | 启用调试模式（`true` / `1` / `yes`） |

**配置优先级：** 命令行参数 > 环境变量 > 默认值

### 命令行参数

```bash
# 自定义端口
docker run ... orsoman/dockports:latest --port 8080

# 启用调试模式
docker run ... orsoman/dockports:latest --debug
```

### 可选：显示宿主机端口的占用进程名

默认情况下，容器与宿主机的 PID 命名空间隔离，无法把端口映射到宿主机进程，故「宿主机」端口卡片不显示进程名。如需显示（如 `mihomo` / `ollama` / `xrdp`），在 `docker-compose.yml` 中解开两行：

```yaml
    pid: host
    privileged: true
```

> ⚠️ **安全提示**：`privileged` 容器近似拥有宿主机 root 权限，攻击面明显增大。请仅在可信内网/自用环境开启。不开启时该功能自动降级（进程名留空），其余功能不受影响。

---

## 🏗️ 支持平台

| 架构 | 适用设备 |
|------|----------|
| `linux/amd64` | x86_64（Intel / AMD 处理器） |
| `linux/arm64` | ARM64（绿联 / 极空间 / 飞牛 等 NAS） |
| `linux/arm/v7` | ARMv7（树莓派 3 等） |

---

## 📋 系统要求

- Docker Engine 20.10+
- Docker Compose 2.0+
- Linux 系统（host 网络模式依赖 `/proc/net`）

---

## 🔧 技术架构

- **后端**：Python Flask + Docker SDK + psutil
- **前端**：原生 HTML / CSS / JavaScript（单文件，无构建步骤）
- **容器化**：Docker + Docker Compose，多平台 buildx 构建

---

## 🛠️ API 接口

### `GET /api/ports`

获取端口信息，支持查询参数：`protocol`（TCP/UDP）、`start_port`、`end_port`、`search`、`reserved`（`1` 时附带已停止容器声明的预留端口）

### `GET /api/refresh`

重连 Docker 客户端并刷新端口信息

### `GET/POST /api/settings`

获取或保存应用设置（内网/外网地址、鉴权开关、用户名密码）

### 隐藏端口管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/hidden-ports` | 获取隐藏端口列表 |
| `POST` | `/api/hidden-ports` | 隐藏单个端口 |
| `DELETE` | `/api/hidden-ports` | 取消隐藏单个端口 |
| `POST` | `/api/hidden-ports/batch` | 批量隐藏端口 |
| `DELETE` | `/api/hidden-ports/batch` | 批量取消隐藏端口 |

---

## 🔍 故障排除

**端口扫描为空**
→ 必须使用 `--network host`，psutil 才能读取宿主机 `/proc/net`

**无法连接 Docker**
→ 确认 `/var/run/docker.sock` 已正确挂载，且为只读（`:ro`）

**端口冲突**
→ 修改环境变量 `DOCKPORTS_PORT=8080` 改用其他端口

**忘记登录密码**
→ 编辑 `config/settings.json`，将 `auth.enabled` 改为 `false` 后重启容器

**查看运行日志**
```bash
docker logs -f dockports
```

---

## 📝 更新日志

### v0.3.3
- 🐳 「按容器筛选」与「端口分类」联动优化：选中某个容器时端口分类自动重置为「全部」，避免停在「系统/容器」分类上看不到该容器的端口（与反向联动对称）

### v0.3.2
- ⏸ 新增**端口预留视图**：可选显示已停止容器声明的端口（读 `HostConfig.PortBindings`），帮助规划时避开撞端口；若声明端口已被占用，则在占用卡上标注冲突来源
- 🔧 新增**宿主机端口占用进程名**显示（可选，需 `pid:host` + `privileged`，默认关闭并优雅降级）
- 🐛 修复 `:host` 配置端口在「系统/容器」分类下消失的问题（source 归一化为 docker/system）
- 🐛 修复某容器引用的镜像被删除/更新时，`container.image` 触发 404 中断整个端口检测、导致容器端口显示不全的问题（改用 `attrs` 读取镜像名）
- 🐛 修复切换端口分类时未重置「按容器筛选」、容器筛选列表列出无端口容器导致点击为空的问题
- 🐛 修复设置弹窗内容超出屏幕、底部按钮无法点击的问题
- 💄 重排端口卡片布局：来源角标移至左上角，进程/镜像合并为紧凑标签，整体更清爽
- 🧹 「应用设置」并入「设置」弹窗（标签页切换）；移除端口范围锁定功能
- 🔧 新增 `DOCKPORTS_CONFIG_DIR` 环境变量，便于本地开发覆盖配置目录
- 🔒 新增 `.dockerignore`，避免将 `settings.json` 等运行时文件打入镜像

### v0.3.1
- 🔧 修复 UDP 端口过滤逻辑：只显示绑定通配地址（`0.0.0.0` / `::`）的 UDP 服务端口，过滤掉 DNS 查询、NTP 同步等客户端临时端口，避免大量无关 UDP 端口显示为占用

### v0.3.0
- 🔐 新增可选登录鉴权（werkzeug 哈希，secret_key 持久化）
- 🔌 端口号一键跳转，支持内网/外网地址切换
- ⚡ 用 psutil 替换 netstat，去除系统依赖
- 🔄 自动刷新（10s / 30s / 60s）+ 倒计时
- 🌙 深色模式，偏好本地持久化
- 🟢 工具栏 Docker 连接状态指示灯 + 刷新时间
- 📦 卡片显示镜像名、连接数，悬浮展示详情
- 🐳 侧边栏按容器筛选
- 🎯 多维度排序 + CSV 导出
- 🔒 端口范围快速预设（知名 / 注册 / 动态）
- 💡 刷新后高亮新增端口

### v0.2.1
- 🔧 修复 `config.json` 读取与保存逻辑
- ⚙️ 配置格式改为 `"服务名:docker/host": "端口:tcp/udp"`

### v0.2.0
- 🌐 新增 ARM64 / ARMv7 多平台支持
- 🔧 端口范围锁定功能
- 🔌 TCP / UDP 协议过滤与统计

### v0.1.0
- ⚙️ 命令行参数支持（`--port`、`--host`、`--debug`）
- 👁️ 端口隐藏功能
- 🐳 Docker 容器端口监控
- 🖥️ 系统端口监控
- 📊 卡片式可视化界面

---

## 📄 许可证

MIT License
