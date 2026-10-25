# Remote MFi Docker

将CH341＋MFi Authentication 2.0C模块接在Linux NAS上，为兼容客户端提供证书读取与挑战签名。只做认证，不转发音乐/画面，不提供芯片、证书或私钥，不绕过认证。

## 1. 下载完整部署包

从[Releases](https://github.com/zy0816/remote-mfi-docker/releases/latest)下载`remote-mfi-docker-v1.0.2.zip`，在NAS持久存储目录完整解压。

**不能只复制Compose内容。**项目目录需要直接包含：

```text
compose.yaml           普通部署，x86-64/ARM64
compose.armv7.yaml     仅32位ARMv7额外使用
compose.relay.yaml     仅服务器中转额外使用
Dockerfile
Dockerfile.armv7
requirements.txt
docker-entrypoint.sh
mfi_service/
docs/remote.md         HTTPS及SSH中转配置
```

仓库另保留必要的许可证、测试和CI；部署包不含测试、宣传图或内部记录。**这是源码构建包，不是Docker Hub预构建镜像。**

## 2. 插入模块，填写令牌

模块应插在Docker所在的Linux主机上，USB识别为`1a86:5512`，不要与其他MFi实例争用同一芯片。

**v1.0.2不需要`.env`文件。**打开`compose.yaml`，将下面的`CHANGE_ME`换成自己的随机令牌：

```yaml
MFI_AUTH_TOKEN: "CHANGE_ME"
```

至少24字符，可以在NAS终端执行`openssl rand -hex 32`生成，再直接粘贴结果。不要加`$`或`${…}`。填写后的compose含真实令牌，不要提交到GitHub、公开截图或发给别人。未替换时服务明确拒绝启动，不会使用默认密码。

## 3. 飞牛Docker部署（x86-64）

1. 文件管理器解压完整部署包，找到**直接有Dockerfile的那一层**。
2. 修改`compose.yaml`里的令牌。
3. Docker → Compose创建项目，项目路径选择此目录，配置文件选择`compose.yaml`。
4. 构建并启动，查看日志里的芯片自检结果，再按第5步验证。

不同飞牛版本的界面名称可能不同，关键是完整源码目录，而不是只粘贴YAML。`pull_policy: build`要求本地构建，不会先去拉不存在的`remote-mfi:1.0.2`远程镜像。

**x86飞牛不要选择ARMv7配置，不要加`--platform linux/arm/v7`。**其他Linux NAS也可在终端使用下方命令。Portainer仅粘贴Stack也不会获得Dockerfile，需先让构建上下文能访问源码。

## 4. 命令行部署

如果已取得维护者构建好的x86离线镜像，改用[离线导入步骤](docs/offline.md)，不在NAS本地构建。维护者可在Actions手动导出；Release源码ZIP与容器镜像不是同一种文件。

进入含`compose.yaml`的目录；需要管理员权限的环境在命令前加`sudo`。

### x86-64／ARM64

```sh
docker compose config --quiet
docker compose build
# 失败就停在这里，不继续启动
docker compose run --rm --entrypoint python mfi -m mfi_service --check
docker compose up -d
```

默认只装运行库、下载预编译Python包，**不下载gcc，不编译cffi，不用Rust**。缺少匹配wheel时直接报错，不偷偷在NAS上编译。

### 32位ARMv7（例如部分海纳思）

```sh
docker compose -f compose.yaml -f compose.armv7.yaml config --quiet
docker compose -f compose.yaml -f compose.armv7.yaml build
docker compose -f compose.yaml -f compose.armv7.yaml run --rm --entrypoint python mfi -m mfi_service --check
docker compose -f compose.yaml -f compose.armv7.yaml up -d
```

只有ARMv7使用多阶段C编译工具链，最终运行镜像不带gcc；慢网络首次构建可能较久。

## 5. 确认运行

普通部署执行：

```sh
docker compose ps
docker compose logs --tail=80 mfi
curl --fail --show-error http://127.0.0.1:8787/healthz
```

预期容器`healthy`，NAS本机返回：

```json
{"service":"nas-mfi-service","version":"1.0.2","ready":true}
```

完整自检需证书读取、真实签名及验签全部通过；仅识别USB或读到`0x05`不够。`--check`会操作芯片，勿与其他服务并发。

`ready:true`是保存的成功自检状态，不是实时USB在线检测；`healthy`不检查公网、隧道或CarPlay会话。

## 6. 怎么从电脑和车机访问

默认只发布**NAS宿主本机**的`127.0.0.1:8787`，直接打开`http://NAS内网IP:8787`不会通。

- **同一可信局域网临时测试**：将compose.yaml端口左侧的`127.0.0.1`改为NAS实际内网IP，重新部署后使用`http://NAS内网IP:8787`。不要修改容器内监听地址，也不要填写公网IP或映射8787到公网。HTTP令牌有被同网段攻击者截获的风险，正式远程使用走HTTPS。
- **车上远程使用**：按[HTTPS直连／SSH中转](docs/remote.md)配置入口。公网IP配匹配的HTTPS证书也可用，不强制买域名；无家庭公网时需要自己的公网中转服务器。

客户端选“远程服务”，填HTTPS基础地址、compose.yaml中的令牌。地址末尾不加`/mfi`或`/healthz`，令牌不加`Bearer `。

## 常见错误

| 提示 | 处理 |
| --- | --- |
| `Dockerfile: no such file` | 上传完整部署包，项目路径选到有Dockerfile的一层 |
| 某个令牌名称`variable is not set` | 误写成`$…`或`${…}`，按第2步直接填值 |
| 最少24字符错误 | 未替换CHANGE_ME或令牌过短 |
| 基础镜像下载超时 | NAS的Docker镜像下载通道有问题，不是芯片错误 |
| 下载软件包慢 | 首次需要基础镜像/运行库；x86不应看到安装gcc |
| `No matching distribution` | 确认架构和网络；ARMv7使用专用配置 |
| 找不到USB/权限拒绝 | 模块是否接在Docker宿主机、模式、权限 |
| 公网502 | HTTPS代理上游/SSH隧道不通 |
| 401 | 令牌不一致或缺失 |

升级v1.0.1时，先保存原令牌，再填入新compose.yaml；旧`.env`不会自动覆盖这里的字面值。不要同时运行两套实例。v1.0.2只优化部署，不改芯片协议。

停止项目：`docker compose down`。ARMv7/relay运维始终用部署时相同的`-f`参数。不要清空Docker全局数据。

## 验证和安全范围

- ARMv7原部署已完成7项回归、真实芯片908字节证书/128字节签名验签、本机服务和鉴权验证。新x86快速构建由CI单独验证；不把历史结果当成所有平台实测。
- 公网、SSH中转、车机/iPhone完整会话和其他NAS仍需验证。服务正常不代表蓝牙/热点/AirPlay正常。
- 不使用privileged，但USB映射与189:*规则能访问映射范围内其他USB设备；不是单芯片隔离。桌面Docker虚拟机、rootless环境未验证。
- 不公开真实Token、SSH私钥或客户数据；公网使用HTTPS并配置限流，不开放NAS后台、Docker API、8787/18787公网访问。
- 源码按[GNU GPLv3](LICENSE)分发，见[第三方说明](THIRD_PARTY_NOTICES.md)。与xcertplay系列客户端相关，不代表上游、Apple或硬件厂商官方背书。
