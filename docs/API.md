# HTTP API

默认服务端口8787；公网应置于可信HTTPS代理后，不直接公开明文端口。

除`GET /healthz`以外均需`Authorization: Bearer <自己的Token>`。Token至少24字符，不能使用示例值。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | /healthz | 返回service/version/ready；未就绪为503 |
| POST | /mfi/reset | JSON体`{}`；重新打开USB并完整自检，清除签名缓存 |
| GET | /mfi/certificate | type、protocolMajor、Base64证书与certificateSha256 |
| POST | /mfi/sign | JSON体包含Base64的challenge及requestId，返回Base64 signature |

签名请求示意（不是可直接使用的真实认证挑战）：

```json
{"challenge":"<Base64编码的实际挑战>","requestId":"<请求标识>"}
```

challenge解码后要求1～128字节；requestId具体格式以server.py校验为准。同一ID不能用于不同挑战；缓存有容量与有效期限制，不是无限持久幂等保证，也不保证所有并发竞态只执行一次硬件签名。

没有令牌/错误令牌返回401，参数错误通常400，硬件异常通常503。不要向Issue公开真实挑战、签名、Token或证书正文。

本服务依赖真实芯片，不提供认证材料。客户端仍需自行实现完整iAP2/CarPlay会话。
