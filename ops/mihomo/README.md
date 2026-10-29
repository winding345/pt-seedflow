# mihomo 部署（替代 v2rayA）

## 1. 建目录并放文件

```bash
mkdir -p /vol2/1000/Docker/mihomo
cd /vol2/1000/Docker/mihomo
# 把本目录下的 docker-compose.yml、config.yaml 复制过来
```

## 2. 填订阅地址（只改一行）

```bash
vi config.yaml
# 找到：  url: "把这里换成你的订阅地址"
# 换成：  url: "https://link123.52pokemon66.cc/api/v1/client/subscribe?token=你的token"
```

## 3. 在 fnOS 的 Docker 里启动

飞牛 Docker 应用 → Compose → 新建/导入 → 选 `/vol2/1000/Docker/mihomo/docker-compose.yml` → 启动。
或者命令行：

```bash
cd /vol2/1000/Docker/mihomo && docker compose up -d
```

## 4. 验证（启动后执行）

```bash
ss -ltn | grep 7890                                  # 应看到 127.0.0.1:7890
curl -s -x http://127.0.0.1:7890 https://api.ipify.org   # 代理出口 IP
curl -s https://api.ipify.org                            # 直连出口 IP（应不同）
curl -s -o /dev/null -w '%{http_code}\n' -x http://127.0.0.1:7890 https://api.m-team.cc/  # 期望非 000
```

## 5. 完成

把上面第 4 步的结果发我，我来：

- 确认 qB 流量没被代理（对比出口 IP）
- 在面板把 mteam 站点配成 `proxy: http://127.0.0.1:7890`
- 手动 search 验证候选恢复

## 说明

- **不开 tun / 透明代理**：qB 的 BT 流量不会经过 mihomo，PT 不会被判盒子
- **只监听 127.0.0.1**：局域网其它设备访问不到这个代理
- Web UI：`http://<nas>:9091`（metacubexd），可看节点延迟、手动切节点
- 订阅每小时自动更新，节点变了不用管
