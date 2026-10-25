# 来源与第三方软件

本仓库由GitHub维护者zy0816整理发布，服务代码来自ZCarPlay关联开发中的独立nas-mfi-service模块，当前版本1.0.2。与xcertplay系列客户端相关，不代表上游作者、Apple或硬件厂商背书。本仓库许可证见LICENSE；原始保留声明不因整理发布而取消。

运行/构建会使用下列第三方组件，它们各自的许可、版权和分发条款仍适用：

- Python与官方python容器基础镜像：https://github.com/docker-library/python
- PyUSB：https://github.com/pyusb/pyusb
- cryptography：https://github.com/pyca/cryptography
- cffi：https://cffi.readthedocs.io/
- pycparser：https://github.com/eliben/pycparser
- Debian、libusb、libffi、OpenSSH、autossh及构建期GCC等：通过Debian包管理器安装，其声明通常位于构建/运行环境的/usr/share/doc对应目录。

依赖未被当成仓库自有原创代码；本仓库不包含真实认证材料、SSH私钥、客户配置或预构建镜像。若分发自己构建的镜像，应同时履行相关软件的许可义务。
