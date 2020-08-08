# Application install guides

1. 配置 `/etc/kubernetes-ansible/globals.yml`, 开启需要开启的 `application` 选项

    ``` bash
    enable_<appication_name>: "yes"
    ```

    已支持的application name：
    - flannel
    - calico
    - ovn
    - metrics\_server
    - nfs\_provisioner
    - rbd\_provisioner [ceph](ceph-guide.md)
    - dashboard
    - prometheus
    - efk
    - ingress\_nginx
    - helm [helm3](helm3-guide.md)

2. 执行如下命令完成指定 `applications` 的安装.

    ``` bash
    # multinode
    kubernetes-ansible -i multinode apply

    # all-in-one
    kubernetes-ansible apply
    ```

3. 自行验证
