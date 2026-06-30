#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DockPorts - 容器化NAS端口记录工具
主要功能：
1. 通过Docker API监控容器端口映射
2. 通过psutil监控主机端口使用情况
3. 可视化展示端口使用状态
"""

import docker
import json
import re
import psutil
import secrets
from flask import Flask, render_template, jsonify, request, session, redirect
from werkzeug.security import generate_password_hash, check_password_hash
from collections import defaultdict
import logging
from datetime import datetime, timedelta
import os
import socket
import time
from functools import wraps
import argparse

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s:%(name)s:%(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 配置文件路径（容器内默认 /app/config，本地开发可用 DOCKPORTS_CONFIG_DIR 覆盖）
CONFIG_DIR = os.environ.get('DOCKPORTS_CONFIG_DIR', '/app/config')
CONFIG_FILE = os.path.join(CONFIG_DIR, 'config.json')
HIDDEN_PORTS_FILE = os.path.join(CONFIG_DIR, 'hidden_ports.json')
SETTINGS_FILE = os.path.join(CONFIG_DIR, 'settings.json')

def init_config():
    """初始化配置文件"""
    import shutil
    
    # 确保配置目录存在
    os.makedirs(CONFIG_DIR, exist_ok=True)
    
    # 初始化主配置文件
    config_created = False
    if not os.path.exists(CONFIG_FILE):
        # 配置文件不存在时，从示例文件复制
        example_config_file = os.path.join(os.path.dirname(__file__), 'config.json.example')
        
        if os.path.exists(example_config_file):
            # 从示例文件复制配置
            shutil.copy2(example_config_file, CONFIG_FILE)
            print(f"配置文件已从示例文件复制: {CONFIG_FILE}")
        else:
            # 如果示例文件不存在，创建默认配置（向后兼容）
            default_config = {
                "远程登录:host": "22:tcp",
                "HTTP:host": "80:tcp",
                "HTTPS:host": "443:tcp",
                "MySQL数据库:host": "3306:tcp",
                "PostgreSQL数据库:host": "5432:tcp",
                "Redis缓存:host": "6379:tcp",
                "MongoDB数据库:host": "27017:tcp",
                "搜索分析:host": "9200:tcp",
                "DockPorts:docker": "7575:tcp"
            }
            
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, indent=2, ensure_ascii=False)
            
            print(f"配置文件已创建（默认配置）: {CONFIG_FILE}")
        config_created = True
    else:
        print(f"配置文件已存在: {CONFIG_FILE}")
    
    # 初始化隐藏端口配置文件
    if not os.path.exists(HIDDEN_PORTS_FILE):
        # 创建空的隐藏端口配置文件
        with open(HIDDEN_PORTS_FILE, 'w', encoding='utf-8') as f:
            json.dump([], f, indent=2, ensure_ascii=False)
        print(f"隐藏端口配置文件已创建: {HIDDEN_PORTS_FILE}")
    else:
        print(f"隐藏端口配置文件已存在: {HIDDEN_PORTS_FILE}")

def load_config():
    """加载配置文件，支持新格式：服务名:docker/host -> 端口:tcp/udp"""
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            raw_config = json.load(f)
        
        # 处理配置文件，支持新格式
        processed_config = {}
        for key, value in raw_config.items():
            if isinstance(value, str) and ':' in value:
                # 检查是否为新格式：服务名:docker/host -> 端口:tcp/udp
                if ':' in key and (key.endswith(':docker') or key.endswith(':host')):
                    # 新格式的键：服务名:docker/host
                    service_name = key.rsplit(':', 1)[0]  # 提取服务名
                    service_type = key.rsplit(':', 1)[1]  # 提取服务类型
                    
                    # 解析值：端口:协议
                    value_parts = value.split(':')
                    if len(value_parts) >= 2:
                        try:
                            port = int(value_parts[0])
                            protocol = value_parts[1].upper()
                            processed_config[service_name] = {
                                'port': port, 
                                'protocol': protocol,
                                'service_type': service_type
                            }
                        except ValueError:
                            processed_config[key] = value
                    else:
                        processed_config[key] = value
                else:
                    # 兼容旧格式："服务名": "端口:协议"
                    parts = value.split(':')
                    if len(parts) >= 2:
                        try:
                            port = int(parts[0])  # 第一部分是端口号
                            protocol = parts[1].upper() if parts[1].upper() in ['TCP', 'UDP'] else 'TCP'
                            processed_config[key] = {'port': port, 'protocol': protocol}
                        except ValueError:
                            processed_config[key] = value
                    else:
                        processed_config[key] = value
            elif isinstance(value, int):
                # 默认为TCP协议
                processed_config[key] = {'port': value, 'protocol': 'TCP'}
            else:
                processed_config[key] = value
        
        return processed_config
    except Exception as e:
        print(f"加载配置文件失败: {e}")
        # 返回默认配置
        return {
            "ssh": {'port': 22, 'protocol': 'TCP'},
            "http": {'port': 80, 'protocol': 'TCP'},
            "https": {'port': 443, 'protocol': 'TCP'},
            "mysql": {'port': 3306, 'protocol': 'TCP'},
            "postgresql": {'port': 5432, 'protocol': 'TCP'},
            "redis": {'port': 6379, 'protocol': 'TCP'},
            "mongodb": {'port': 27017, 'protocol': 'TCP'},
            "elasticsearch": {'port': 9200, 'protocol': 'TCP'},
            "app_settings": {
                "host": "0.0.0.0",
                "port": 7577,
                "debug": False
            }
        }

def save_config(config):
    """保存配置文件，使用新格式：服务名:docker/host -> 端口:tcp/udp"""
    try:
        # 处理配置文件，将协议信息转换为新的字符串格式
        raw_config = {}
        for key, value in config.items():
            if isinstance(value, dict) and 'port' in value and 'protocol' in value:
                port = value['port']
                protocol = value['protocol'].lower()
                # 确定服务类型（默认为host，如果有容器信息则为docker）
                service_type = value.get('service_type', 'host')
                # 新格式："服务名:docker/host":"端口:tcp/udp"
                new_key = f"{key}:{service_type}"
                raw_config[new_key] = f"{port}:{protocol}"
            else:
                raw_config[key] = value
        
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(raw_config, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"保存配置文件失败: {e}")
        return False

def load_hidden_ports():
    """加载隐藏端口配置"""
    try:
        if os.path.exists(HIDDEN_PORTS_FILE):
            with open(HIDDEN_PORTS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    except Exception as e:
        print(f"加载隐藏端口配置失败: {e}")
        return []

def save_hidden_ports(hidden_ports):
    """保存隐藏端口配置"""
    try:
        with open(HIDDEN_PORTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(hidden_ports, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"保存隐藏端口配置失败: {e}")
        return False

def default_settings():
    """生成默认应用设置（含随机 secret_key）"""
    return {
        'intranet_host': '',
        'external_host': '',
        'auth': {
            'enabled': False,
            'username': 'admin',
            'password_hash': ''
        },
        'secret_key': secrets.token_hex(32)
    }

def init_settings():
    """初始化应用设置文件，缺字段时补全"""
    os.makedirs(CONFIG_DIR, exist_ok=True)

    if not os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(default_settings(), f, indent=2, ensure_ascii=False)
        print(f"应用设置文件已创建: {SETTINGS_FILE}")
        return

    # 已存在则补全缺失字段（向后兼容）
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        data = {}

    base = default_settings()
    changed = False
    if 'intranet_host' not in data:
        data['intranet_host'] = base['intranet_host']
        changed = True
    if 'external_host' not in data:
        data['external_host'] = base['external_host']
        changed = True
    if not isinstance(data.get('auth'), dict):
        data['auth'] = base['auth']
        changed = True
    else:
        for k, v in base['auth'].items():
            if k not in data['auth']:
                data['auth'][k] = v
                changed = True
    if not data.get('secret_key'):
        data['secret_key'] = base['secret_key']
        changed = True

    if changed:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"应用设置文件已更新: {SETTINGS_FILE}")

def load_settings():
    """加载应用设置"""
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"加载应用设置失败: {e}")
        return default_settings()

def save_settings(new_settings):
    """保存应用设置"""
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(new_settings, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"保存应用设置失败: {e}")
        return False

# 内网 IP 检测缓存（IP 极少变动，避免每次请求都新建 socket）
_intranet_ip_cache = {'ip': None, 'ts': 0}
_INTRANET_IP_TTL = 60  # 秒

def detect_intranet_ip():
    """自动检测内网 IP（通过出站路由判断主网卡地址），结果缓存 60 秒"""
    now = time.time()
    if _intranet_ip_cache['ip'] and (now - _intranet_ip_cache['ts']) < _INTRANET_IP_TTL:
        return _intranet_ip_cache['ip']

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()

    _intranet_ip_cache['ip'] = ip
    _intranet_ip_cache['ts'] = now
    return ip

def get_effective_host():
    """获取用于端口跳转的有效内网地址（手动设置优先，否则自动检测）"""
    return (settings.get('intranet_host') or '').strip() or detect_intranet_ip()

# 初始化配置
init_config()
init_settings()
config = load_config()
settings = load_settings()

# 设置 Flask session 密钥（持久化于 settings.json，重启后 session 不失效）
app.secret_key = settings.get('secret_key') or secrets.token_hex(32)

class PortMonitor:
    """端口监控类"""
    
    def __init__(self):
        """初始化Docker客户端"""
        # 缓存相关属性
        self.container_cache = {}  # 容器信息缓存
        self.cache_timestamp = 0   # 缓存时间戳
        self.cache_ttl = 30        # 缓存生存时间（秒）

        # 已停止容器「声明端口」缓存（端口预留功能用）
        self.stopped_cache = {}
        self.stopped_cache_ts = 0

        self.reconnect()
        
        # 默认端口服务映射
        self.default_ports = {
            21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS", 67: "DHCP Server", 68: "DHCP Client",
            69: "TFTP", 80: "HTTP", 110: "POP3", 123: "NTP", 135: "RPC", 137: "NetBIOS Name", 138: "NetBIOS Datagram",
            139: "NetBIOS Session", 143: "IMAP", 161: "SNMP", 389: "LDAP", 443: "HTTPS", 445: "SMB", 465: "SMTPS",
            514: "Syslog", 587: "SMTP", 631: "IPP", 636: "LDAPS", 993: "IMAPS", 995: "POP3S", 1433: "SQL Server",
            1521: "Oracle", 3306: "MySQL", 3389: "RDP", 5432: "PostgreSQL", 5900: "VNC", 6379: "Redis",
            8080: "HTTP Proxy", 8443: "HTTPS Alt", 9200: "Elasticsearch", 27017: "MongoDB"
        }

    def reconnect(self):
        """（重新）建立 Docker 客户端连接，并清空容器缓存"""
        try:
            self.docker_client = docker.from_env()
            logger.info("Docker客户端连接成功")
        except Exception as e:
            logger.error(f"Docker客户端连接失败: {e}")
            self.docker_client = None
        # 重连后旧缓存失效
        self.container_cache = {}
        self.cache_timestamp = 0
        self.stopped_cache = {}
        self.stopped_cache_ts = 0

    def get_docker_ports(self):
        """获取Docker容器端口映射信息"""
        ports_info = []
        
        if not self.docker_client:
            logger.warning("Docker客户端未连接")
            return ports_info
        
        try:
            containers = self.docker_client.containers.list()
            logger.info(f"发现 {len(containers)} 个运行中的容器")
            
            for container in containers:
                # 单个容器异常不应中断整个循环（否则后续容器的端口全部丢失）
                try:
                    container_name = container.name
                    # 从 attrs 读取镜像名，避免访问 container.image 触发额外 API 调用：
                    # 若容器引用的镜像已被删除/更新，container.image.tags 会 404 并中断整个循环
                    image_name = container.attrs.get('Config', {}).get('Image', '')
                    ports = container.attrs.get('NetworkSettings', {}).get('Ports', {})

                    for container_port, host_bindings in ports.items():
                        if host_bindings:
                            for binding in host_bindings:
                                host_port = int(binding['HostPort'])
                                ports_info.append({
                                    'port': host_port,
                                    'container_name': container_name,
                                    'container_port': container_port,
                                    'image': image_name,
                                    'type': 'docker_mapped'
                                })
                                logger.debug(f"发现映射端口: {host_port} -> {container_name}:{container_port}")

                    # 检查host网络模式的容器
                    network_mode = container.attrs.get('HostConfig', {}).get('NetworkMode', '')
                    if network_mode == 'host':
                        ports_info.append({
                            'port': None,  # host模式下无法直接获取端口
                            'container_name': container_name,
                            'container_port': 'host模式',
                            'type': 'docker_host'
                        })
                        logger.debug(f"发现host模式容器: {container_name}")
                except Exception as e:
                    logger.warning(f"处理容器 {getattr(container, 'name', '?')} 端口信息失败，已跳过: {e}")

        except Exception as e:
            logger.error(f"获取Docker端口信息失败: {e}")
        
        return ports_info
    
    def get_host_ports(self):
        """获取主机端口使用情况（简化版本，仅检测端口占用）"""
        port_info = {}
        port_protocols = {}  # 用于跟踪每个端口的协议和IP版本
        
        # 获取host网络容器信息
        host_containers = self.get_host_network_containers_cached()
        
        try:
            # 使用 psutil 获取监听端口信息（跨平台，无需 netstat）
            all_conns = psutil.net_connections(kind='inet')

            # 统计每个端口的活跃连接数（仅 ESTABLISHED，不含监听/无状态 socket）
            port_connection_count = {}
            for conn in all_conns:
                if conn.laddr and conn.status == psutil.CONN_ESTABLISHED:
                    p = conn.laddr.port
                    port_connection_count[p] = port_connection_count.get(p, 0) + 1

            # pid -> 进程名 缓存（本次调用内复用，避免重复 psutil.Process 开销）
            proc_name_cache = {}

            def resolve_proc(pid):
                """根据 pid 解析进程名；容器内未共享 PID 命名空间/无权限时返回 None（优雅降级）"""
                if not pid:
                    return None
                if pid in proc_name_cache:
                    return proc_name_cache[pid]
                name = None
                try:
                    name = psutil.Process(pid).name()
                except Exception:
                    name = None
                proc_name_cache[pid] = name
                return name

            for conn in all_conns:
                if not conn.laddr:
                    continue
                if conn.type == socket.SOCK_STREAM:
                    # TCP: 仅 LISTEN 状态
                    if conn.status != psutil.CONN_LISTEN:
                        continue
                else:
                    # UDP: 仅绑定通配地址的 socket（服务端行为），过滤客户端临时端口
                    if conn.laddr.ip not in ('0.0.0.0', '::'):
                        continue

                port = conn.laddr.port
                protocol_type = 'TCP' if conn.type == socket.SOCK_STREAM else 'UDP'
                ip_version = 'IPv6' if conn.family == socket.AF_INET6 else 'IPv4'
                local_address = f"{conn.laddr.ip}:{port}"

                # 检查是否为host网络容器的端口
                container_name = None
                for container_info in host_containers.values():
                    if port in container_info['exposed_ports']:
                        container_name = container_info['name']
                        break

                # 跟踪端口的协议和IP版本
                if port not in port_protocols:
                    port_protocols[port] = {'protocols': set(), 'ip_versions': set()}

                port_protocols[port]['protocols'].add(protocol_type)
                port_protocols[port]['ip_versions'].add(ip_version)

                # 如果端口已存在，更新信息
                if port not in port_info:
                    port_info[port] = {
                        'port': port,
                        'protocol': protocol_type,
                        'ip_version': ip_version,
                        'address': local_address,
                        'service_name': self.get_service_name(port),
                        'container_name': container_name,
                        'connection_count': port_connection_count.get(port, 0),
                        'pid': conn.pid,
                        'process_name': resolve_proc(conn.pid)
                    }

                logger.debug(f"发现主机使用端口: {port} ({protocol_type}/{ip_version})")
            
            # 合并协议信息
            for port, info in port_info.items():
                protocols = port_protocols[port]['protocols']
                ip_versions = port_protocols[port]['ip_versions']
                
                # 合并协议，包含IP版本信息
                protocol_list = []
                for protocol in sorted(protocols):
                    if 'IPv4' in ip_versions and 'IPv6' in ip_versions:
                        # 同时支持IPv4和IPv6，显示TCP/UDP和TCP6/UDP6
                        protocol_list.extend([protocol, protocol + '6'])
                    elif 'IPv6' in ip_versions:
                        # 只支持IPv6
                        protocol_list.append(protocol + '6')
                    else:
                        # 只支持IPv4
                        protocol_list.append(protocol)
                
                # 去重并排序
                protocol_list = sorted(list(set(protocol_list)))
                info['protocol'] = ','.join(protocol_list)
                
                # 移除单独的ip_version字段，信息已包含在protocol中
                del info['ip_version']

        except Exception as e:
            logger.error(f"获取主机端口信息失败: {e}")

        return port_info
    
    def get_service_name(self, port):
        """根据端口号获取服务名称（仅使用配置文件映射）"""
        # 从配置文件获取端口映射，适配新的数据结构
        config_ports = {}
        for k, v in config.items():
            if isinstance(v, dict) and 'port' in v:
                config_ports[k] = v['port']
            elif isinstance(v, int):
                config_ports[k] = v
        
        # 创建端口到服务名的映射（反向映射）
        port_to_service = {v: k for k, v in config_ports.items()}
        
        # 使用配置文件中的端口映射
        if port in port_to_service:
            return port_to_service[port]
        
        # 使用默认端口映射
        if port in self.default_ports:
            return self.default_ports[port]
        
        # 如果都没有，返回未知
        return '未知服务'
    
    def get_host_network_containers_cached(self):
        """获取host网络容器信息（带缓存，增强版本）"""
        import time
        import re
        
        current_time = time.time()
        
        # 检查缓存是否有效
        if (current_time - self.cache_timestamp) < self.cache_ttl and self.container_cache:
            logger.debug("使用缓存的容器信息")
            return self.container_cache
        
        logger.debug("刷新容器信息缓存")
        self.container_cache = {}
        
        if not self.docker_client:
            return self.container_cache
        
        try:
            containers = self.docker_client.containers.list()
            for container in containers:
                # 检查容器的网络模式
                network_mode = container.attrs.get('HostConfig', {}).get('NetworkMode', '')
                if network_mode == 'host':
                    container_info = {
                        'name': container.name,
                        'id': container.id[:12],
                        # 用 attrs 读取镜像名，避免 container.image 触发 API 调用导致 404 中断循环
                        'image': container.attrs.get('Config', {}).get('Image', 'unknown'),
                        'exposed_ports': set(),
                        'potential_ports': set(),  # 从其他配置推断的可能端口
                        'healthcheck_ports': set(),  # 从健康检查推断的端口
                        'entrypoint_ports': set()   # 从入口点推断的端口
                    }
                    
                    # 1. 获取容器的ExposedPorts
                    try:
                        exposed_ports = container.attrs.get('Config', {}).get('ExposedPorts', {})
                        if exposed_ports:
                            for port_spec in exposed_ports.keys():
                                # 解析端口格式，如 "80/tcp", "53/udp"
                                if '/' in port_spec:
                                    port_num = int(port_spec.split('/')[0])
                                    container_info['exposed_ports'].add(port_num)
                                    logger.debug(f"容器 {container.name} 暴露端口: {port_num}")
                    except Exception as e:
                        logger.debug(f"获取容器 {container.name} ExposedPorts失败: {e}")
                    
                    # 2. 检查Healthcheck配置中的端口
                    try:
                        healthcheck = container.attrs.get('Config', {}).get('Healthcheck', {})
                        if healthcheck and 'Test' in healthcheck:
                            test_cmd = ' '.join(healthcheck['Test']) if isinstance(healthcheck['Test'], list) else str(healthcheck['Test'])
                            # 使用正则表达式查找端口号
                            port_matches = re.findall(r'(?:localhost|127\.0\.0\.1|0\.0\.0\.0):?(\d{1,5})', test_cmd)
                            for port_str in port_matches:
                                try:
                                    port_num = int(port_str)
                                    if 1 <= port_num <= 65535:
                                        container_info['healthcheck_ports'].add(port_num)
                                        container_info['potential_ports'].add(port_num)
                                        logger.debug(f"容器 {container.name} 健康检查端口: {port_num}")
                                except ValueError:
                                    continue
                    except Exception as e:
                        logger.debug(f"获取容器 {container.name} Healthcheck失败: {e}")
                    
                    # 3. 检查Entrypoint和Cmd中的端口
                    try:
                        # 检查Entrypoint
                        entrypoint = container.attrs.get('Config', {}).get('Entrypoint', [])
                        cmd = container.attrs.get('Config', {}).get('Cmd', [])
                        
                        # 合并entrypoint和cmd
                        full_command = []
                        if entrypoint:
                            full_command.extend(entrypoint if isinstance(entrypoint, list) else [entrypoint])
                        if cmd:
                            full_command.extend(cmd if isinstance(cmd, list) else [cmd])
                        
                        command_str = ' '.join(str(arg) for arg in full_command)
                        
                        # 查找常见的端口参数模式
                        port_patterns = [
                            r'--port[=\s]+(\d{1,5})',      # --port=8080 或 --port 8080
                            r'-p[=\s]+(\d{1,5})',          # -p=8080 或 -p 8080
                            r'--listen[=\s]+(\d{1,5})',    # --listen=8080
                            r'--bind[=\s]+[^:]*:(\d{1,5})', # --bind=0.0.0.0:8080
                            r':(\d{1,5})\b',               # 通用的 :端口 模式
                            r'PORT[=\s]+(\d{1,5})',        # PORT=8080
                            r'HTTP_PORT[=\s]+(\d{1,5})',   # HTTP_PORT=8080
                        ]
                        
                        for pattern in port_patterns:
                            matches = re.findall(pattern, command_str, re.IGNORECASE)
                            for port_str in matches:
                                try:
                                    port_num = int(port_str)
                                    if 1 <= port_num <= 65535:
                                        container_info['entrypoint_ports'].add(port_num)
                                        container_info['potential_ports'].add(port_num)
                                        logger.debug(f"容器 {container.name} 入口点端口: {port_num}")
                                except ValueError:
                                    continue
                                    
                    except Exception as e:
                        logger.debug(f"获取容器 {container.name} Entrypoint/Cmd失败: {e}")
                    
                    # 4. 检查环境变量中的端口
                    try:
                        env_vars = container.attrs.get('Config', {}).get('Env', [])
                        for env_var in env_vars:
                            if '=' in env_var:
                                key, value = env_var.split('=', 1)
                                # 查找端口相关的环境变量
                                if any(port_keyword in key.upper() for port_keyword in ['PORT', 'LISTEN', 'BIND']):
                                    try:
                                        # 尝试从环境变量值中提取端口号
                                        port_matches = re.findall(r'\b(\d{1,5})\b', value)
                                        for port_str in port_matches:
                                            port_num = int(port_str)
                                            if 1 <= port_num <= 65535:
                                                container_info['potential_ports'].add(port_num)
                                                logger.debug(f"容器 {container.name} 环境变量端口: {port_num} (来自 {key})")
                                    except (ValueError, AttributeError):
                                        continue
                    except Exception as e:
                        logger.debug(f"获取容器 {container.name} 环境变量失败: {e}")
                    
                    # 合并所有端口到exposed_ports中
                    container_info['exposed_ports'].update(container_info['potential_ports'])
                    
                    self.container_cache[container.name] = container_info
                    
        except Exception as e:
            logger.error(f"获取Docker容器信息失败: {e}")
        
        self.cache_timestamp = current_time
        return self.container_cache

    def get_stopped_container_ports(self):
        """获取「已停止容器」声明的端口映射（端口预留信息，带 30 秒缓存）

        返回 {host_port: {'protocol': 'TCP'/'UDP',
                          'containers': [{'name','container_port','image','status'}, ...]}}
        说明：已停止容器不占用端口，这里读取的是其 HostConfig.PortBindings 中
        声明的宿主端口（意图），用于「撞端口」规划。host 网络容器无 PortBindings，
        初版不处理。
        """
        current_time = time.time()
        if (current_time - self.stopped_cache_ts) < self.cache_ttl and self.stopped_cache:
            return self.stopped_cache

        result = {}
        if not self.docker_client:
            self.stopped_cache = result
            self.stopped_cache_ts = current_time
            return result

        try:
            # 取所有非运行容器（exited/created/dead 等）。paused 仍占用端口，按运行处理（不取）
            containers = self.docker_client.containers.list(
                all=True, filters={'status': ['exited', 'created', 'dead']}
            )
            for container in containers:
                try:
                    name = container.name
                    image = container.attrs.get('Config', {}).get('Image', '')
                    host_config = container.attrs.get('HostConfig', {}) or {}
                    port_bindings = host_config.get('PortBindings') or {}
                    # port_bindings 形如 {"80/tcp": [{"HostIp":"","HostPort":"8080"}], ...}
                    for container_port, bindings in port_bindings.items():
                        if not bindings:
                            continue
                        proto = 'UDP' if '/udp' in container_port.lower() else 'TCP'
                        for b in bindings:
                            hp = (b or {}).get('HostPort')
                            if not hp:
                                continue
                            try:
                                host_port = int(hp)
                            except (ValueError, TypeError):
                                continue
                            if not (1 <= host_port <= 65535):
                                continue
                            entry = result.setdefault(
                                host_port, {'protocol': proto, 'containers': []}
                            )
                            # 同端口多协议时合并标注
                            if proto not in entry['protocol']:
                                entry['protocol'] = ','.join(sorted(set(
                                    entry['protocol'].split(',') + [proto]
                                )))
                            entry['containers'].append({
                                'name': name,
                                'container_port': container_port,
                                'image': image,
                                'status': container.status
                            })
                except Exception as e:
                    logger.warning(f"处理已停止容器 {getattr(container, 'name', '?')} 失败，已跳过: {e}")
        except Exception as e:
            logger.error(f"获取已停止容器端口失败: {e}")

        self.stopped_cache = result
        self.stopped_cache_ts = current_time
        return result

    def _build_reserved_card(self, port, entry):
        """根据预留端口信息构建一张 reserved 卡片"""
        names = sorted(set(c['name'] for c in entry['containers']))
        first = entry['containers'][0] if entry['containers'] else {}
        # 服务名优先取配置文件映射
        svc = None
        for sname, sconf in config.items():
            if isinstance(sconf, dict) and sconf.get('port') == port:
                svc = sname
                break
        if svc:
            service_name = svc
        elif names:
            service_name = names[0]
        else:
            service_name = '已停止容器'
        return {
            'type': 'reserved',
            'port': port,
            'protocol': entry.get('protocol', 'TCP'),
            'containers': names,
            'container': names[0] if names else None,
            'service_name': service_name,
            'image': first.get('image', ''),
            'container_port': first.get('container_port', ''),
            'status': first.get('status', '')
        }

    def get_port_analysis(self, start_port=1, end_port=65535, protocol_filter=None, include_reserved=False):
        """分析端口使用情况并生成可视化数据"""
        docker_ports = self.get_docker_ports()
        host_ports_info = self.get_host_ports()
        
        # 初始化端口卡片列表
        port_cards = []
        
        # 分别处理TCP和UDP端口
        tcp_ports = set()
        udp_ports = set()
        port_protocol_map = {}  # 端口到协议的映射
        
        # 处理主机端口信息，区分TCP和UDP，并应用端口范围过滤
        for port, info in host_ports_info.items():
            # 应用端口范围过滤
            if port < start_port or port > end_port:
                continue
                
            protocol = info.get('protocol', 'TCP')
            port_protocol_map[port] = protocol
            
            # 根据协议分类端口
            if 'TCP' in protocol.upper():
                tcp_ports.add(port)
            if 'UDP' in protocol.upper():
                udp_ports.add(port)
        
        # 处理Docker端口（通常是TCP），并应用端口范围过滤
        docker_port_map = {}
        for port_info in docker_ports:
            if port_info['port']:
                port = port_info['port']
                # 应用端口范围过滤
                if port < start_port or port > end_port:
                    continue
                    
                tcp_ports.add(port)  # Docker端口映射通常是TCP
                docker_port_map[port] = port_info
                if port not in port_protocol_map:
                    port_protocol_map[port] = 'TCP'
        
        # 根据协议过滤器选择端口
        if protocol_filter == 'TCP':
            filtered_ports = tcp_ports
            logger.info(f"TCP协议过滤: 发现 {len(tcp_ports)} 个TCP端口")
        elif protocol_filter == 'UDP':
            filtered_ports = udp_ports
            logger.info(f"UDP协议过滤: 发现 {len(udp_ports)} 个UDP端口")
        else:
            # 显示所有端口
            filtered_ports = tcp_ports.union(udp_ports)
            logger.info(f"总共发现 {len(filtered_ports)} 个已使用端口 (TCP: {len(tcp_ports)}, UDP: {len(udp_ports)})")
        
        sorted_ports = sorted(filtered_ports)
        
        # 预处理：收集所有端口信息
        port_data_list = []
        for port in sorted_ports:
            protocol = port_protocol_map.get(port, 'TCP')
            
            # 如果有协议过滤器，跳过不匹配的端口
            if protocol_filter and protocol_filter.upper() not in protocol.upper():
                continue
            
            # 检查配置文件中是否有该端口的service_type信息
            config_service_type = None
            config_service_name = None
            for service_name, service_config in config.items():
                if isinstance(service_config, dict) and service_config.get('port') == port:
                    config_service_type = service_config.get('service_type')
                    config_service_name = service_name
                    break
            
            if port in docker_port_map:
                # Docker容器端口
                docker_info = docker_port_map[port]
                # source 仅有两类展示分类：docker(容器) / system(系统/宿主机)。
                # 配置里的 service_type='host' 归到 system，否则按 docker。
                source = 'system' if config_service_type == 'host' else 'docker'
                card_data = {
                    'port': port,
                    'type': 'used',
                    'source': source,
                    'protocol': protocol,
                    'container': docker_info['container_name'],
                    'process': f"Docker: {docker_info['container_name']}",
                    'image': docker_info.get('image', ''),
                    'container_port': docker_info['container_port'],
                    'connection_count': host_ports_info.get(port, {}).get('connection_count', 0),
                    'service_name': config_service_name or docker_info['container_name']
                }
            else:
                # 系统服务端口
                host_info = host_ports_info.get(port, {})
                
                # 检查是否为host网络容器
                is_host_container = bool(host_info.get('container_name'))
                
                # 确定source（仅 docker/system 两类）：优先用配置 service_type，
                # host 归 system；其次 host 网络容器归 docker；否则 system
                if config_service_type == 'docker':
                    source = 'docker'
                elif config_service_type == 'host':
                    source = 'system'
                elif is_host_container:
                    source = 'docker'
                else:
                    source = 'system'
                
                card_data = {
                    'port': port,
                    'type': 'used',
                    'source': source,
                    'protocol': protocol,
                    'service_name': config_service_name or host_info.get('service_name', '未知服务'),
                    'container': host_info.get('container_name'),
                    'connection_count': host_info.get('connection_count', 0),
                    'is_host_network': is_host_container,
                    'process_name': host_info.get('process_name'),
                    'pid': host_info.get('pid')
                }
            port_data_list.append(card_data)
        
        # 处理连续的未知端口合并
        i = 0
        while i < len(port_data_list):
            current_port_data = port_data_list[i]
            
            # 检查是否为未知服务且可以开始合并
            if current_port_data['service_name'] == '未知服务':
                # 查找连续的未知端口
                consecutive_unknown = [current_port_data]
                j = i + 1
                
                while (j < len(port_data_list) and 
                       port_data_list[j]['service_name'] == '未知服务' and
                       port_data_list[j]['port'] == port_data_list[j-1]['port'] + 1):
                    consecutive_unknown.append(port_data_list[j])
                    j += 1
                
                # 如果有连续的未知端口（2个或以上），则合并
                if len(consecutive_unknown) >= 2:
                    range_start_port = consecutive_unknown[0]['port']
                    range_end_port = consecutive_unknown[-1]['port']
                    
                    # 创建合并的端口卡片
                    merged_card = {
                        'type': 'unknown_range',
                        'start_port': range_start_port,
                        'end_port': range_end_port,
                        'port_count': len(consecutive_unknown),
                        'source': consecutive_unknown[0]['source'],
                        'protocol': consecutive_unknown[0]['protocol'],
                        'service_name': '未知服务',
                        'container': consecutive_unknown[0].get('container'),
                        'is_host_network': consecutive_unknown[0].get('is_host_network', False)
                    }
                    port_cards.append(merged_card)
                    
                    # 跳过已处理的端口
                    i = j
                else:
                    # 单个未知端口，正常添加
                    port_cards.append(current_port_data)
                    i += 1
            else:
                # 非未知端口，正常添加
                port_cards.append(current_port_data)
                i += 1
            
            # 检查是否需要添加间隔卡片
            if i < len(port_data_list):
                # 获取当前卡片的最后一个端口
                last_card = port_cards[-1]
                if last_card['type'] == 'unknown_range':
                    current_last_port = last_card['end_port']
                else:
                    current_last_port = last_card.get('port')
                
                next_port = port_data_list[i]['port']
                gap = next_port - current_last_port - 1
                
                if gap > 0:
                    gap_card = {
                        'type': 'gap',
                        'start_port': current_last_port + 1,
                        'end_port': next_port - 1,
                        'available_count': gap
                    }
                    port_cards.append(gap_card)
        
        # 添加最后一个端口到65535的间隙
        if port_cards:
            # 获取最后一个卡片的最后端口
            last_card = port_cards[-1]
            
            if last_card['type'] == 'gap':
                # 如果最后一个是gap卡片，检查是否到达65535
                if last_card['end_port'] < end_port:
                    # 更新最后一个gap卡片到65535
                    last_card['end_port'] = end_port
                    last_card['available_count'] = last_card['end_port'] - last_card['start_port'] + 1
            else:
                if last_card['type'] == 'unknown_range':
                    last_port = last_card['end_port']
                else:
                    last_port = last_card.get('port', 0)
                
                if last_port < end_port:
                    final_gap = end_port - last_port
                    if final_gap > 0:
                        gap_card = {
                            'type': 'gap',
                            'start_port': last_port + 1,
                            'end_port': end_port,
                            'available_count': final_gap
                        }
                        port_cards.append(gap_card)
        else:
            # 如果没有任何端口卡片，创建一个从1到65535的完整gap
            gap_card = {
                'type': 'gap',
                'start_port': start_port,
                'end_port': end_port,
                'available_count': end_port - start_port + 1
            }
            port_cards.append(gap_card)
        
        # 统计Docker容器总数：直接从 Docker API 取所有运行中容器，不依赖端口检测结果
        all_running_names = []
        if self.docker_client:
            try:
                all_running_names = sorted(c.name for c in self.docker_client.containers.list())
            except Exception:
                pass

        # 计算可用端口数量（基于指定的端口范围）
        total_ports_in_range = end_port - start_port + 1
        if protocol_filter:
            # 如果有协议过滤器，可用端口数量是范围内总端口数减去该协议的已使用端口数
            available_ports = total_ports_in_range - len(filtered_ports)
        else:
            # 显示所有协议时，可用端口数量是范围内总端口数减去所有已使用端口数
            all_used_ports = tcp_ports.union(udp_ports)
            available_ports = total_ports_in_range - len(all_used_ports)
        
        # 过滤隐藏的端口
        hidden_ports = load_hidden_ports()
        if hidden_ports:
            filtered_port_cards = []
            for card in port_cards:
                should_hide = False
                
                if card['type'] == 'used':
                    # 检查单个端口是否被隐藏
                    if card['port'] in hidden_ports:
                        should_hide = True
                elif card['type'] == 'unknown_range':
                    # 检查端口范围是否有任何端口被隐藏
                    for port in range(card['start_port'], card['end_port'] + 1):
                        if port in hidden_ports:
                            should_hide = True
                            break
                
                if not should_hide:
                    filtered_port_cards.append(card)

            port_cards = filtered_port_cards

        # ===== 端口预留（已停止容器声明的端口）=====
        reserved_count = 0
        if include_reserved:
            reserved_map = self.get_stopped_container_ports()
            all_used_ports = tcp_ports.union(udp_ports)

            free_reserved = {}   # port -> entry，落在空闲区间、需单独成卡的预留端口
            for port, entry in reserved_map.items():
                # 端口范围过滤
                if port < start_port or port > end_port:
                    continue
                # 协议过滤
                if protocol_filter and protocol_filter.upper() not in entry['protocol'].upper():
                    continue
                # 隐藏过滤
                if hidden_ports and port in hidden_ports:
                    continue

                names = sorted(set(c['name'] for c in entry['containers']))
                if port in all_used_ports:
                    # 冲突：该端口此刻已被占用 -> 在对应 used 卡上加注记，不单独成卡
                    for card in port_cards:
                        if card.get('type') == 'used' and card.get('port') == port:
                            card['reserved_by'] = names
                            break
                        if card.get('type') == 'unknown_range' and \
                           card.get('start_port', 0) <= port <= card.get('end_port', 0):
                            card.setdefault('reserved_by', [])
                            for n in names:
                                if n not in card['reserved_by']:
                                    card['reserved_by'].append(n)
                            break
                else:
                    free_reserved[port] = entry

            # 把空闲的预留端口从 gap 卡中「挖」出来，单独成 reserved 卡
            if free_reserved:
                new_cards = []
                for card in port_cards:
                    if card.get('type') != 'gap':
                        new_cards.append(card)
                        continue
                    g_start, g_end = card['start_port'], card['end_port']
                    rps = sorted(p for p in free_reserved if g_start <= p <= g_end)
                    if not rps:
                        new_cards.append(card)
                        continue
                    cursor = g_start
                    for rp in rps:
                        if rp > cursor:
                            new_cards.append({
                                'type': 'gap', 'start_port': cursor,
                                'end_port': rp - 1, 'available_count': rp - cursor
                            })
                        new_cards.append(self._build_reserved_card(rp, free_reserved[rp]))
                        reserved_count += 1
                        cursor = rp + 1
                    if cursor <= g_end:
                        new_cards.append({
                            'type': 'gap', 'start_port': cursor,
                            'end_port': g_end, 'available_count': g_end - cursor + 1
                        })
                port_cards = new_cards

        # 「按容器筛选」列表：只列出在当前展示结果中确实有端口卡片的容器，
        # 避免列出无可见端口的容器（如 buildkit）导致点击后结果为空。
        # 从最终 port_cards（已过隐藏过滤）收集，保证点任意容器都有结果。
        filter_container_names = sorted(set(
            (p.get('container') or p.get('container_name') or '')
            for p in port_cards
        ) - {''})

        # 容器总数：优先用 Docker API 的运行容器数（更准确，含无可见端口的容器）；
        # Docker 不可用时回退为有端口的容器数。
        docker_container_count = len(all_running_names) if all_running_names else len(filter_container_names)

        return {
            'port_cards': port_cards,
            'total_used': len(filtered_ports),
            'total_available': available_ports,
            'tcp_used': len(tcp_ports),
            'udp_used': len(udp_ports),
            'docker_containers': docker_container_count,
            'docker_container_names': filter_container_names,
            'hidden_ports': hidden_ports,
            'protocol_filter': protocol_filter,
            'reserved_count': reserved_count
        }

# 创建端口监控实例
port_monitor = PortMonitor()

@app.before_request
def require_login():
    """登录守卫：开启鉴权且未登录时，HTML 跳转登录页，API 返回 401"""
    if not settings.get('auth', {}).get('enabled'):
        return None
    # 放行登录页与静态资源
    if request.endpoint in ('login', 'static'):
        return None
    if session.get('logged_in'):
        return None
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'error': '未登录'}), 401
    return redirect('/login')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """登录页与登录处理"""
    # 未开启鉴权时无需登录，直接回首页
    if not settings.get('auth', {}).get('enabled'):
        return redirect('/')

    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        auth = settings.get('auth', {})
        if username == auth.get('username') and auth.get('password_hash') \
                and check_password_hash(auth['password_hash'], password):
            session['logged_in'] = True
            return redirect('/')
        return render_template('login.html', error='用户名或密码错误')

    if session.get('logged_in'):
        return redirect('/')
    return render_template('login.html', error=None)

@app.route('/logout')
def logout():
    """退出登录"""
    session.clear()
    return redirect('/login')

@app.route('/')
def index():
    """主页面"""
    return render_template('index.html')

@app.route('/api/ports')
def api_ports():
    """获取端口信息API"""
    try:
        # 获取协议过滤器参数
        protocol_filter = request.args.get('protocol', '').strip().upper()
        if protocol_filter not in ['TCP', 'UDP', '']:
            protocol_filter = None
        
        # 获取端口范围参数
        start_port = request.args.get('start_port', '1')
        end_port = request.args.get('end_port', '65535')
        
        # 验证端口范围参数
        try:
            start_port = int(start_port)
            end_port = int(end_port)
            
            # 确保端口范围有效
            if start_port < 1:
                start_port = 1
            if end_port > 65535:
                end_port = 65535
            if start_port > end_port:
                start_port, end_port = end_port, start_port
                
        except ValueError:
            # 如果参数无效，使用默认范围
            start_port = 1
            end_port = 65535
        
        # 是否包含「已停止容器声明的预留端口」
        include_reserved = request.args.get('reserved', '').strip().lower() in ('1', 'true', 'yes', 'on')

        port_data = port_monitor.get_port_analysis(start_port=start_port, end_port=end_port,
                                                   protocol_filter=protocol_filter,
                                                   include_reserved=include_reserved)

        # 提供内网/外网地址，供前端拼接端口跳转链接
        port_data['host_ip'] = get_effective_host()
        port_data['external_host'] = (settings.get('external_host') or '').strip()

        # 服务端时间与 Docker 连接状态，供前端显示
        port_data['server_time'] = datetime.now().strftime('%H:%M:%S')
        port_data['docker_connected'] = port_monitor.docker_client is not None

        # 处理搜索参数
        search = request.args.get('search', '').strip().lower()
        if search:
            # 保存原始的可用端口数（基于当前端口范围，搜索仅过滤展示，不改变实际可用数）
            original_total_available = port_data['total_available']

            filtered_cards = []
            for card in port_data['port_cards']:
                if card['type'] == 'used':
                    # 搜索端口号、进程名、服务名、容器名、协议
                    searchable_text = ' '.join([
                        str(card.get('port', '')),
                        card.get('process', '') or '',
                        card.get('process_name', '') or '',
                        card.get('service_name', '') or '',
                        card.get('container', '') or '',
                        card.get('protocol', '') or ''
                    ]).lower()
                    
                    if search in searchable_text:
                        filtered_cards.append(card)
                elif card['type'] == 'unknown_range':
                    # 搜索端口范围、服务名、容器名、协议
                    searchable_text = ' '.join([
                        f"{card.get('start_port', '')}-{card.get('end_port', '')}",
                        str(card.get('start_port', '')),
                        str(card.get('end_port', '')),
                        card.get('service_name', '') or '',
                        card.get('container', '') or '',
                        card.get('protocol', '') or ''
                    ]).lower()
                    
                    # 检查是否搜索范围内的单个端口号
                    is_match = search in searchable_text
                    if not is_match and search.isdigit():
                        search_port = int(search)
                        card_start_port = card.get('start_port', 0)
                        card_end_port = card.get('end_port', 0)
                        if card_start_port <= search_port <= card_end_port:
                            is_match = True
                    
                    if is_match:
                        filtered_cards.append(card)
                elif card['type'] == 'gap':
                    # 搜索可用端口范围
                    searchable_text = ' '.join([
                        f"{card.get('start_port', '')}-{card.get('end_port', '')}",
                        str(card.get('start_port', '')),
                        str(card.get('end_port', '')),
                        '可用', 'available', 'unused'
                    ]).lower()
                    
                    # 检查是否搜索范围内的单个端口号
                    is_match = search in searchable_text
                    if not is_match and search.isdigit():
                        search_port = int(search)
                        gap_start_port = card.get('start_port', 0)
                        gap_end_port = card.get('end_port', 0)
                        if gap_start_port <= search_port <= gap_end_port:
                            is_match = True
                    
                    if is_match:
                        filtered_cards.append(card)
                elif card['type'] == 'reserved':
                    # 搜索预留端口：端口号、服务名、容器名、协议
                    searchable_text = ' '.join([
                        str(card.get('port', '')),
                        card.get('service_name', '') or '',
                        ' '.join(card.get('containers', []) or []),
                        card.get('protocol', '') or '',
                        '预留', '已停止', 'reserved'
                    ]).lower()
                    if search in searchable_text:
                        filtered_cards.append(card)

            # 按端口排序
            filtered_cards = sorted(filtered_cards, key=lambda x: x.get('port', x.get('start_port', 0)))
            
            # 计算搜索结果中的已使用端口数
            filtered_used_count = len([card for card in filtered_cards if card['type'] in ['used', 'unknown_range']])
            
            # 更新统计信息
            port_data['port_cards'] = filtered_cards
            port_data['total_used'] = filtered_used_count
            # 搜索只过滤展示，可用端口数沿用过滤前基于端口范围的统计
            port_data['total_available'] = original_total_available
        
        return jsonify({
            'success': True,
            'data': port_data
        })
    except Exception as e:
        logger.error(f"API调用失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/config')
def api_get_config():
    """API接口：获取配置信息"""
    try:
        return jsonify(config)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/config/raw')
def api_get_raw_config():
    """API接口：获取原始配置文件内容（用于设置界面编辑）"""
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            raw_config = json.load(f)
        return jsonify(raw_config)
    except Exception as e:
        logger.error(f"获取原始配置失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/config', methods=['POST'])
def api_save_config():
    """API接口：保存配置信息"""
    # 重新加载配置
    global config
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': '无效的配置数据'}), 400
        
        # 检查是否是添加单个端口的请求
        if 'port' in data and 'service_name' in data:
            port = data['port']
            service_name = data['service_name'].strip()
            service_type = data.get('service_type', 'host')  # 默认为host
            
            if not service_name:
                return jsonify({'error': '服务名称不能为空'}), 400
            
            # 验证端口号
            if not isinstance(port, int) or port < 1 or port > 65535:
                return jsonify({'error': '端口号必须在1-65535之间'}), 400
            
            # 验证服务类型
            if service_type not in ['docker', 'host']:
                return jsonify({'error': '服务类型必须是docker或host'}), 400
            
            # 加载当前配置
            current_config = load_config()
            
            # 检查端口是否已存在，适配新的数据结构
            existing_service = None
            for service, config_value in current_config.items():
                existing_port = None
                if isinstance(config_value, dict) and 'port' in config_value:
                    existing_port = config_value['port']
                elif isinstance(config_value, int):
                    existing_port = config_value
                
                if existing_port == port:
                    existing_service = service
                    break
            
            if existing_service:
                # 更新现有端口的服务名称
                del current_config[existing_service]
                current_config[service_name] = {
                    'port': port, 
                    'protocol': 'TCP',
                    'service_type': service_type
                }
            else:
                # 添加新的端口配置
                current_config[service_name] = {
                    'port': port, 
                    'protocol': 'TCP',
                    'service_type': service_type
                }
            
            # 保存配置
            if save_config(current_config):
                config = load_config()
                return jsonify({
                    'success': True, 
                    'message': f'端口 {port} 的服务名称已设置为 "{service_name}"（{service_type}）'
                })
            else:
                return jsonify({'error': '配置保存失败'}), 500
        else:
            # 保存整个配置（原有功能）- 支持混合格式
            # 验证配置格式（支持混合格式）
            for name, value in data.items():
                if name == 'app_settings':
                    continue
                    
                port = None
                
                if isinstance(value, int):
                    # 纯数字格式
                    port = value
                elif isinstance(value, str):
                    # 字符串格式："端口号:协议" 或 "端口号"
                    parts = value.split(':')
                    if len(parts) >= 1:
                        try:
                            port = int(parts[0])
                        except ValueError:
                            return jsonify({'error': f'配置项 "{name}" 的端口号 "{parts[0]}" 无效'}), 400
                        
                        # 验证协议（如果存在）
                        if len(parts) > 1:
                            protocol = parts[1].lower()
                            if protocol not in ['tcp', 'udp']:
                                return jsonify({'error': f'配置项 "{name}" 的协议 "{parts[1]}" 无效，只支持TCP或UDP'}), 400
                    else:
                        return jsonify({'error': f'配置项 "{name}" 格式无效'}), 400
                elif isinstance(value, dict):
                    # 对象格式：{port: 端口号, protocol: 协议}
                    if 'port' not in value:
                        return jsonify({'error': f'配置项 "{name}" 缺少端口号'}), 400
                    port = value['port']
                    
                    # 验证协议（如果存在）
                    if 'protocol' in value:
                        protocol = str(value['protocol']).lower()
                        if protocol not in ['tcp', 'udp']:
                            return jsonify({'error': f'配置项 "{name}" 的协议 "{value["protocol"]}" 无效，只支持TCP或UDP'}), 400
                else:
                    return jsonify({'error': f'配置项 "{name}" 格式无效，支持格式：端口号、"端口号:协议" 或 {{port: 端口号, protocol: 协议}}'}), 400
                
                if not isinstance(port, int) or port < 1 or port > 65535:
                    return jsonify({'error': f'端口号 "{port}" 无效，必须是1-65535之间的整数'}), 400
            
            # 直接保存原始格式的配置到文件
            try:
                with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                
                # 更新全局配置（重新加载以确保一致性）
                config = load_config()
                
                logger.info("配置已更新")
                return jsonify({'success': True, 'message': '配置保存成功'})
            except Exception as e:
                logger.error(f"写入配置文件失败: {e}")
                return jsonify({'error': '配置保存失败'}), 500
                
    except Exception as e:
        logger.error(f"保存配置时出错: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/settings')
def api_get_settings():
    """API接口：获取应用设置（脱敏，不含密码哈希与密钥）"""
    try:
        auth = settings.get('auth', {})
        return jsonify({
            'success': True,
            'data': {
                'intranet_host': settings.get('intranet_host', ''),
                'external_host': settings.get('external_host', ''),
                'detected_ip': detect_intranet_ip(),
                'auth': {
                    'enabled': bool(auth.get('enabled')),
                    'username': auth.get('username', 'admin'),
                    'has_password': bool(auth.get('password_hash'))
                }
            }
        })
    except Exception as e:
        logger.error(f"获取应用设置失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings', methods=['POST'])
def api_save_settings():
    """API接口：保存应用设置（内网地址、鉴权开关、用户名、可选新密码）"""
    global settings
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': '无效的设置数据'}), 400

        new_settings = load_settings()

        # 内网地址
        if 'intranet_host' in data:
            new_settings['intranet_host'] = (data.get('intranet_host') or '').strip()

        # 外网地址
        if 'external_host' in data:
            new_settings['external_host'] = (data.get('external_host') or '').strip()

        # 鉴权相关
        auth = new_settings.setdefault('auth', {})
        if 'username' in data:
            username = (data.get('username') or '').strip()
            if username:
                auth['username'] = username

        # 新密码（留空不改）
        new_password = data.get('password')
        if new_password:
            auth['password_hash'] = generate_password_hash(new_password)

        # 启用开关：开启时必须已设置密码
        if 'enabled' in data:
            enabled = bool(data.get('enabled'))
            if enabled and not auth.get('password_hash'):
                return jsonify({'success': False, 'error': '启用登录前请先设置密码'}), 400
            auth['enabled'] = enabled

        if save_settings(new_settings):
            settings = load_settings()
            # 配置者本次会话视为已登录，避免刚开启鉴权就被锁在外面
            if settings.get('auth', {}).get('enabled'):
                session['logged_in'] = True
            return jsonify({'success': True, 'message': '应用设置已保存'})
        return jsonify({'success': False, 'error': '设置保存失败'}), 500

    except Exception as e:
        logger.error(f"保存应用设置失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/refresh')
def api_refresh():
    """刷新端口信息API"""
    try:
        # 重新连接Docker客户端并清空缓存
        port_monitor.reconnect()
        include_reserved = request.args.get('reserved', '').strip().lower() in ('1', 'true', 'yes', 'on')
        port_data = port_monitor.get_port_analysis(include_reserved=include_reserved)
        port_data['host_ip'] = get_effective_host()
        port_data['external_host'] = (settings.get('external_host') or '').strip()
        port_data['server_time'] = datetime.now().strftime('%H:%M:%S')
        port_data['docker_connected'] = port_monitor.docker_client is not None
        return jsonify({
            'success': True,
            'data': port_data,
            'message': '端口信息已刷新'
        })
    except Exception as e:
        logger.error(f"刷新失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/hidden-ports')
def api_get_hidden_ports():
    """获取隐藏端口列表API"""
    try:
        hidden_ports = load_hidden_ports()
        return jsonify({
            'success': True,
            'data': hidden_ports
        })
    except Exception as e:
        logger.error(f"获取隐藏端口失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/hidden-ports', methods=['POST'])
def api_hide_port():
    """隐藏端口API"""
    try:
        data = request.get_json()
        if not data or 'port' not in data:
            return jsonify({'error': '缺少端口参数'}), 400
        
        port = data['port']
        if not isinstance(port, int) or port < 1 or port > 65535:
            return jsonify({'error': '端口号必须在1-65535之间'}), 400
        
        hidden_ports = load_hidden_ports()
        if port not in hidden_ports:
            hidden_ports.append(port)
            hidden_ports.sort()
            
            if save_hidden_ports(hidden_ports):
                return jsonify({
                    'success': True,
                    'message': f'端口 {port} 已隐藏'
                })
            else:
                return jsonify({'error': '保存隐藏端口配置失败'}), 500
        else:
            return jsonify({
                'success': True,
                'message': f'端口 {port} 已经被隐藏'
            })
            
    except Exception as e:
        logger.error(f"隐藏端口失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/hidden-ports', methods=['DELETE'])
def api_unhide_port():
    """取消隐藏端口API"""
    try:
        data = request.get_json()
        if not data or 'port' not in data:
            return jsonify({'error': '缺少端口参数'}), 400
        
        port = data['port']
        if not isinstance(port, int) or port < 1 or port > 65535:
            return jsonify({'error': '端口号必须在1-65535之间'}), 400
        
        hidden_ports = load_hidden_ports()
        if port in hidden_ports:
            hidden_ports.remove(port)
            
            if save_hidden_ports(hidden_ports):
                return jsonify({
                    'success': True,
                    'message': f'端口 {port} 已取消隐藏'
                })
            else:
                return jsonify({'error': '保存隐藏端口配置失败'}), 500
        else:
            return jsonify({
                'success': True,
                'message': f'端口 {port} 未被隐藏'
            })
            
    except Exception as e:
        logger.error(f"取消隐藏端口失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/hidden-ports/batch', methods=['POST'])
def api_hide_ports_batch():
    """批量隐藏端口API"""
    try:
        data = request.get_json()
        if not data or 'ports' not in data:
            return jsonify({'error': '缺少端口列表参数'}), 400
        
        ports = data['ports']
        if not isinstance(ports, list):
            return jsonify({'error': '端口列表必须是数组'}), 400
        
        # 验证所有端口号
        for port in ports:
            if not isinstance(port, int) or port < 1 or port > 65535:
                return jsonify({'error': f'端口号 {port} 无效，必须在1-65535之间'}), 400
        
        hidden_ports = load_hidden_ports()
        new_hidden_count = 0
        
        for port in ports:
            if port not in hidden_ports:
                hidden_ports.append(port)
                new_hidden_count += 1
        
        hidden_ports.sort()
        
        if save_hidden_ports(hidden_ports):
            return jsonify({
                'success': True,
                'message': f'成功隐藏 {new_hidden_count} 个端口'
            })
        else:
            return jsonify({'error': '保存隐藏端口配置失败'}), 500
            
    except Exception as e:
        logger.error(f"批量隐藏端口失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/hidden-ports/batch', methods=['DELETE'])
def api_unhide_ports_batch():
    """批量取消隐藏端口API"""
    try:
        data = request.get_json()
        if not data or 'ports' not in data:
            return jsonify({'error': '缺少端口列表参数'}), 400
        
        ports = data['ports']
        if not isinstance(ports, list):
            return jsonify({'error': '端口列表必须是数组'}), 400
        
        # 验证所有端口号
        for port in ports:
            if not isinstance(port, int) or port < 1 or port > 65535:
                return jsonify({'error': f'端口号 {port} 无效，必须在1-65535之间'}), 400
        
        hidden_ports = load_hidden_ports()
        removed_count = 0
        
        for port in ports:
            if port in hidden_ports:
                hidden_ports.remove(port)
                removed_count += 1
        
        if save_hidden_ports(hidden_ports):
            return jsonify({
                'success': True,
                'message': f'成功取消隐藏 {removed_count} 个端口'
            })
        else:
            return jsonify({'error': '保存隐藏端口配置失败'}), 500
            
    except Exception as e:
        logger.error(f"批量取消隐藏端口失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

def parse_args():
    """解析命令行参数和环境变量"""
    # 从环境变量获取默认值
    default_port = int(os.environ.get('DOCKPORTS_PORT', 7577))
    default_host = os.environ.get('DOCKPORTS_HOST', '0.0.0.0')
    default_debug = os.environ.get('DOCKPORTS_DEBUG', '').lower() in ('true', '1', 'yes')
    
    parser = argparse.ArgumentParser(description='DockPorts - 容器端口监控工具')
    parser.add_argument('--port', '-p', type=int, default=default_port,
                        help=f'Web服务端口 (默认: {default_port}, 可通过环境变量DOCKPORTS_PORT设置)')
    parser.add_argument('--host', type=str, default=default_host,
                        help=f'Web服务监听地址 (默认: {default_host}, 可通过环境变量DOCKPORTS_HOST设置)')
    parser.add_argument('--debug', action='store_true', default=default_debug,
                        help='启用调试模式 (可通过环境变量DOCKPORTS_DEBUG=true设置)')
    return parser.parse_args()

if __name__ == '__main__':
    # 解析命令行参数
    args = parse_args()
    
    # 显示配置信息
    logger.info("=== DockPorts 启动配置 ===")
    logger.info(f"监听地址: {args.host}")
    logger.info(f"监听端口: {args.port}")
    logger.info(f"调试模式: {args.debug}")
    
    # 显示环境变量信息（用于调试）
    env_port = os.environ.get('DOCKPORTS_PORT')
    env_host = os.environ.get('DOCKPORTS_HOST')
    env_debug = os.environ.get('DOCKPORTS_DEBUG')
    
    if env_port or env_host or env_debug:
        logger.info("=== 环境变量配置 ===")
        if env_port:
            logger.info(f"DOCKPORTS_PORT: {env_port}")
        if env_host:
            logger.info(f"DOCKPORTS_HOST: {env_host}")
        if env_debug:
            logger.info(f"DOCKPORTS_DEBUG: {env_debug}")
    
    logger.info("=========================")
    
    # 验证端口范围
    if not (1 <= args.port <= 65535):
        logger.error(f"端口号 {args.port} 无效，必须在1-65535之间")
        exit(1)
    
    try:
        app.run(host=args.host, port=args.port, debug=args.debug)
    except OSError as e:
        if "Address already in use" in str(e):
            logger.error(f"端口 {args.port} 已被占用，请使用 --port 参数指定其他端口")
            logger.info("例如: python app.py --port 8080")
        else:
            logger.error(f"启动失败: {e}")
        exit(1)
    except KeyboardInterrupt:
        logger.info("应用已停止")
    except Exception as e:
        logger.error(f"应用运行时出错: {e}")
        exit(1)