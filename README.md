# Remote MFi Docker

通过 USB CH341（`1a86:5512`）连接 MFi 模块，在 Linux / NAS 的 Docker 中提供认证服务。

## 1. 下载

下载并解压 [项目源码](https://github.com/zy0816/remote-mfi-docker/archive/refs/heads/main.zip)，进入包含 `Dockerfile`、`compose.yaml` 和 `mfi_service/` 的目录。**只复制 Compose 文本不能构建。**

## 2. 设置

将模块插到运行 Docker 的 Linux 主机，执行 `lsusb`，确认显示 `1a86:5512`。

编辑 `compose.yaml`，将 `MFI_AUTH_TOKEN: "CHANGE_ME"` 改为自己生成的至少 24 字符令牌：

```sh
openssl rand -hex 32
```

不需要创建 `.env`；不要在令牌前加 `$`，也不要让两个 MFi 服务同时占用同一模块。

## 3. 启动

**飞牛 NAS**：打开 Docker → Compose → 创建项目，项目目录选择上一步解压后的源码目录，选中 `compose.yaml` 后构建、启动。

**普通 Linux**：在上述源码目录运行：

```sh
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=50 mfi
```

x86-64 / ARM64 使用默认 Compose。32 位 ARMv7 使用：

```sh
docker compose -f compose.yaml -f compose.armv7.yaml up -d --build
```

首次构建需要下载镜像和 Python 依赖。已取得 x86 离线镜像的用户可按 [离线部署步骤](docs/offline.md) 跳过构建。

## 4. 检查是否成功

在运行 Docker 的主机执行：

```sh
curl -i http://127.0.0.1:8787/healthz
```

HTTP 200 且响应中包含 `"ready":true` 表示芯片初始化与自检成功。容器显示 `healthy` 后，再配置客户端连接。

## 5. 从其他设备连接

默认端口绑定为 `127.0.0.1:8787:8787`，只有 Docker 主机本地能访问。若需局域网访问，修改 `compose.yaml` 的 `ports` 左侧绑定地址为 NAS 实际局域网 IP，例如 `192.168.1.10:8787:8787`，重新运行 `docker compose up -d`。

公网服务器直连或 NAS 经公网服务器中转的具体操作见 [远程部署教程](docs/remote.md)。客户端填写服务**基础地址**和 Compose 中的令牌；地址后面不要加 `/mfi` 或 `/healthz`。

## 常见问题

| 现象 | 检查 |
| --- | --- |
| `Dockerfile: no such file` | 解压完整源码，使用含 Dockerfile 的目录 |
| `variable is not set` | 令牌误写成 `$` 或 `${…}` |
| 503 / `ready:false` | CH341 USB 连接、Docker USB 权限、芯片占用 |
| 401 | 客户端令牌是否与 Compose 一致 |
| 502 | 公网代理与 SSH 中转是否连接 |
| 外部访问超时 | 端口绑定、路由器端口转发、防火墙 |

停止：`docker compose down`。注意：HTTP 连接上的认证令牌并不加密。

许可证：[GPLv3](LICENSE) · [第三方说明](THIRD_PARTY_NOTICES.md)
