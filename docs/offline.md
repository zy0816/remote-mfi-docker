# x86-64离线镜像导入

适用于已经取得维护者导出包的x86-64飞牛/NAS，不适用于32位ARM海纳思。导出任务只在GitHub构建镜像，不连接或修改NAS。

1. 将`remote-mfi-v1.0.2-linux-amd64.tar.gz`上传到NAS，在本地镜像界面导入；若界面不接受该压缩格式，可在NAS终端执行：

```sh
docker load -i remote-mfi-v1.0.2-linux-amd64.tar.gz
docker image inspect remote-mfi:1.0.2 --format '{{.Os}}/{{.Architecture}}'
```

第二条应显示linux/amd64。不使用docker import；镜像包不是源码ZIP，不要解压到Compose项目目录当作Dockerfile。

2. 导入后，使用包里的`compose.yaml`（即仓库的compose.prebuilt.yaml），将CHANGE_ME换成自己的至少24字符随机令牌。
3. 只创建这一个Compose项目。它没有build，且pull_policy为never，不会再下载Debian或Python依赖。不与原来正在构建的项目同时运行并争用同一芯片。
4. 默认仍只发布NAS本机127.0.0.1:8787；检查容器日志、自检和healthz。局域网/HTTPS访问按主README，不因离线导入自动开放公网。

校验文件SHA256SUMS用于检查传输完整性。镜像源码和构建方式在本仓库，导出包source.txt记录具体提交；离线镜像不包含用户令牌/私钥/客户配置。

维护者：在GitHub Actions手动运行Export x86 image，成功后下载remote-mfi-amd64-offline工件。工件有保留期，下载受GitHub登录/权限限制，不等于已发布Docker Hub/GHCR镜像。正式对外分发镜像时仍需遵守本项目和第三方依赖的许可证。
