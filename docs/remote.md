# 远程访问：HTTPS或SSH中转

先按README在NAS完成Docker构建与芯片自检。默认只发布NAS本机127.0.0.1:8787，本页不会自动修改路由、防火墙或NAS后台。

## 一、有公网：HTTPS代理到NAS

可使用域名，也可使用公网IP。证书必须包含实际访问的域名/IP并被客户端信任，不能用域名证书直接访问IP。

反向代理运行在NAS宿主机时，Nginx示例（替换server_name和真实证书路径）：

```nginx
server {
    listen 443 ssl;
    server_name mfi.example.com;
    ssl_certificate /你的实际证书路径/fullchain.pem;
    ssl_certificate_key /你的实际证书路径/privkey.pem;
    client_max_body_size 8k;
    proxy_connect_timeout 5s;
    proxy_read_timeout 15s;
    proxy_send_timeout 15s;

    location /mfi/ {
        proxy_pass http://127.0.0.1:8787;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
    }
    location = /healthz {
        proxy_pass http://127.0.0.1:8787;
        access_log off;
    }
    location / { return 404; }
}
```

不要在proxy_pass末尾额外添加`/`；保留请求的/mfi路径。修改独立站点配置，`nginx -t`通过后再重载，不覆盖NAS已有站点。反向代理若在另一个容器里，127.0.0.1属于代理容器自己，需要另配受控Docker网络。

路由器或云防火墙只开放必要HTTPS端口，不映射8787，不公开NAS后台或Docker API。公网IPv6要求客户端也支持IPv6；家庭只有内网地址/运营商NAT时选下节中转。

### 无域名的IP证书

Let’s Encrypt提供短期IP证书。按[官方Certbot IP证书指南](https://letsencrypt.org/2026/03/11/shorter-certs-certbot)申请并配置自动续期和续期后代理重载。约6天有效，不能申请后不管。

下面是已有HTTP80站点与webroot的Certbot5.4+示例，不是NAS通用安装命令；先确认公网80可达、目录由站点正确提供：

```sh
sudo certbot certonly --preferred-profile shortlived \
  --webroot --webroot-path /你的实际ACME目录 \
  --ip-address 你的实际公网IP
```

用Certbot实际输出的fullchain/privkey路径配置Nginx。不要使用staging测试证书作为正式证书，不用curl -k或关闭校验。续期需保留验证通道、检查定时任务，并配置deploy-hook重载代理。已有NAS代理不支持IP证书时，应按其支持的证书导入方式或选域名/其他HTTPS网关，不能把系统安装命令盲目套到NAS。

## 二、无公网：NAS主动建立SSH反向隧道

需要自己管理的公网服务器。链路：

```text
客户端HTTPS → 公网服务器Nginx → 服务器127.0.0.1:18787
                                     ↓ SSH反向隧道
                         NAS容器127.0.0.1:8787 → USB芯片
```

### 1. 准备专用密钥和受限账号

NAS项目目录生成专用密钥，不覆盖已有密钥、不用管理员私钥：

```sh
mkdir -p secrets
chmod 700 secrets
ssh-keygen -t ed25519 -f secrets/relay_ssh_key
```

无人值守需要非交互可用密钥；专用无口令密钥要依靠文件权限、服务器账号限制保护。将`.pub`公钥交给服务器管理员，不公开没有.pub后缀的私钥。

公网服务器专用账号的authorized_keys公钥限制示例：

```text
restrict,port-forwarding,permitlisten="127.0.0.1:18787" ssh-ed25519 这里换成NAS隧道公钥
```

管理员应在sshd_config按实际账号配置仅反向转发、禁止交互会话等限制，例如：

```text
Match User mfi-tunnel
    PasswordAuthentication no
    AllowTcpForwarding remote
    AllowStreamLocalForwarding no
    PermitListen 127.0.0.1:18787
    GatewayPorts no
    AllowAgentForwarding no
    X11Forwarding no
    PermitTTY no
    MaxSessions 0
Match all
```

先运行sshd配置校验，通过后再按发行版重载，并保留管理员连接，避免锁出。不要对管理账号直接套这些限制。详见[OpenSSH文档](https://man.openbsd.org/sshd_config)。

将核对过指纹的服务器SSH主机公钥保存到`secrets/relay_known_hosts`；非22端口需正确的`[主机]:端口`条目。ssh-keyscan只取得候选公钥，不能代替可信渠道核验。

```sh
chmod 600 secrets/relay_ssh_key secrets/relay_known_hosts
```

### 2. 填写中转地址并部署

修改`compose.relay.yaml`里的RELAY_SSH_HOST、PORT、USER、REMOTE_PORT，值换成自己的。令牌仍填在主`compose.yaml`，不需要.env。

```sh
docker compose -f compose.yaml -f compose.relay.yaml config --quiet
docker compose -f compose.yaml -f compose.relay.yaml build
docker compose -f compose.yaml -f compose.relay.yaml run --rm --entrypoint python mfi -m mfi_service --check
docker compose -f compose.yaml -f compose.relay.yaml up -d
```

ARMv7需同时加第三个`-f compose.armv7.yaml`，并让所有运维命令使用同一组文件。私钥文件必须真实存在；模板不会把不存在的密钥路径默默建成目录。

### 3. 公网服务器配置HTTPS

使用上节Nginx模板，**两处**proxy_pass都改为`http://127.0.0.1:18787`。不要开放18787到公网。NAS需能主动访问服务器SSH端口，家庭不需要公网IP或入站映射。

## 三、验证与客户端填写

从外网访问`https://实际域名或IP/healthz`，应返回JSON且证书可信。先确认NAS本机接口，再检查中转机18787和公网HTTPS，不要跳层排查。

客户端选远程认证，填HTTPS基础地址和compose.yaml里的令牌。不要在地址末尾加/mfi或/healthz，令牌不加Bearer。此处客户端UI输入不加前缀；原始HTTP请求的Authorization仍需Bearer协议。

`ready:true`不代表SSH隧道、客户端兼容或完整iPhone会话正常。没有令牌的证书请求应返回401；公网502先查上游/隧道，证书报错先查地址匹配、时间和续期，不关闭验证。

本地healthy不监测后台隧道；隧道断线有重连等待时间。一个芯片操作串行，避免无界请求；公网按负载配置限流和监测。不把真实令牌、私钥或客户日志提交到Issue。
