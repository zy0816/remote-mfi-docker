# 公网连接部署

先完成 [Docker 安装和本机自检](../README.md)，确认 `http://127.0.0.1:8787/healthz` 返回 `"ready":true`。以下示例使用 HTTP、TCP 8787，不要求购买域名或配置 HTTPS。

## 方式 A：Docker 主机有公网 IP（直接连接）

1. 默认 `compose.yaml` 已映射 `0.0.0.0:8787:8787`，无需修改端口。

2. 在项目目录执行：

```sh
docker compose up -d --no-build
docker compose ps
```

3. 在服务器的安全组/防火墙允许 **TCP 8787**；如果主机位于路由器后面，还需将路由器的外部 **TCP 8787** 转发到 NAS 内网 IP 的 TCP 8787（家庭网络必须有可入站的公网 IP）。
4. 从外网访问：

```text
http://公网IP:8787/healthz
```

应返回 `"ready":true`。客户端填写 `http://公网IP:8787` 和 `compose.yaml` 中的 `MFI_AUTH_TOKEN`。

## 方式 B：NAS 无公网 IP（通过 VPS 中转）

只需要一台可 SSH 登录的公网 Linux VPS。USB 芯片仍插在 NAS 上，VPS **不用安装 MFi Docker**。

**1. 从 NAS 建立 SSH 反向隧道**

确保 NAS 可通过 SSH 登录 VPS（建议使用专用 SSH 密钥），在 NAS 终端运行：

```sh
ssh -N -T \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=20 \
  -R 127.0.0.1:18787:127.0.0.1:8787 \
  用户名@公网服务器IP
```

这个终端需要保持运行；长期部署可以用 systemd 或 autossh 保活。隧道只把 VPS 本机的 18787 转到 NAS 的 8787，不要求 NAS 有公网 IP 或开放入站端口。

**2. 在 VPS 安装 Nginx 并添加 HTTP 转发**

Debian/Ubuntu：

```sh
sudo apt-get update
sudo apt-get install -y nginx
```

在 VPS 创建 `/etc/nginx/conf.d/mfi-http.conf`（如果发行版不加载 conf.d，请放到它实际加载的 Nginx 站点目录）：

```nginx
server {
    listen 8787;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:18787;
        proxy_http_version 1.1;
        proxy_read_timeout 30s;
    }
}
```

```sh
sudo nginx -t
sudo systemctl reload nginx
```

在 VPS 安全组/防火墙放行 **TCP 8787**。

**3. 外网测试并填写客户端**

```text
http://公网服务器IP:8787/healthz
```

看到 `"ready":true` 后，客户端填写 **`http://公网服务器IP:8787`**，令牌填写 NAS `compose.yaml` 中的值，不加 `Bearer ` 前缀。

### 排错

| 现象 | 先检查 |
| --- | --- |
| NAS 本机 `/healthz` 为 503 | USB 芯片、自检日志、容器 USB 权限 |
| VPS 访问 `http://127.0.0.1:18787/healthz` 失败 | NAS 上的 SSH 隧道有没有连上 |
| VPS 本机成功，外网访问超时 | VPS TCP 8787 安全组/防火墙 |
| 外网显示 502 | Nginx 到隧道 18787 不通 |
| 客户端 401 | `MFI_AUTH_TOKEN` 不匹配 |

仅在可信环境使用明文 HTTP；令牌随 HTTP 请求传输时不加密。需要加密时可自行在前面添加 HTTPS 代理，不影响后端配置。
