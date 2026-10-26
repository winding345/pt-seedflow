# pt-seedflow

PT 站点刷流服务：按磁盘配额自动选种、加种、淘汰，支持多服务实例，带一个极简 Web 面板。

代码中不含任何密钥，全部通过配置文件或其指向的文件提供，可安全公开。

## 工作方式

```text
每 5 分钟一个 tick：
  1. 删除轮   上传量不足的种子 → 删除任务并清文件
  2. 刷新轮   每 12 个 tick（1 小时）执行一次 search，刷新候选池
  3. 加种轮   按剩余空间与配额，挑分最高的候选加入 qB
```

顺序固定为「先删后补」，空间账本每一次 tick 都从 qB 重新读取，避免账本漂移。

## 空间计算

**纯逻辑计算，不读磁盘。**

```text
已用 = Σ size（该服务分类下所有任务，不论进度）
可用 = disk_gb - 已用
```

一个 40GB 的种子即使只落了 30GB，也按 40GB 计入；剩余的 10GB 迟早要落盘，提前占位才不会超发。

## 排序打分

```text
score = 0.50 × 新鲜度      # 按发布时间衰减，半衰期 6 小时
      + 0.35 × 人数比      # leechers / (seeders + 1)，饱和映射
      + 0.15 × 热度        # log10(leechers)，防止"比例好看但没人下"
```

死种（无人做种也无人下载）直接得 0。

## 配额保护

站点额度是硬约束，服务按 `上限 × safety`（默认 0.9）执行，留出余量给手动操作。

```text
m-team: search 1000 次/24h，下载种子 1000 个/天
```

- `search` 每小时一次，基本不会触顶
- `genDlToken`（换下载链接）才吃下载配额，**每次加种前都会校验，超了就停止加种**
- 配额耗尽时：不再加种，但**继续删除**废种，零点后自动恢复

## 删除策略

一个任务满足以下全部条件才会被删：

1. 已过豁免期（`exempt_minutes`）
2. 已过最短保留期（`min_keep_minutes`）
3. 不是「正在下载且进度在推进」的状态
4. 最近 `window_minutes` 内上传量低于 `delete_threshold_mb`

## 快速开始

### 本地运行

```bash
uv sync
uv run python -m app.main
```

首次启动会在 `~/.pt-seedflow/config.yaml` 生成示例配置并退出；编辑后重新启动即可。
配置不合法（缺字段、密钥文件不存在等）时同样拒绝启动。

### Docker 部署

两个容器：应用 + MySQL。

```bash
cp .env.example .env      # 改掉里面的密码
docker compose up -d --build
```

| 服务 | 说明 |
|---|---|
| `mysql` | MySQL 8.0，端口 `3366:3306`，局域网可直连，数据在 `./mysql-data` |
| `app` | pt-seedflow，`network_mode: host`，监听 8600 |

应用使用 host 网络，是为了让 qBittorrent 的「本机免认证」继续生效
（bridge 网络下容器来源 IP 会变，被 qB 拒绝 403）。
因为 host 网络，容器通过 `127.0.0.1:3366` 连 MySQL。

挂载：

| 宿主机 | 容器 | 用途 |
|---|---|---|
| `./data` | `/root/.pt-seedflow` | 启动用的 `config.yaml` |
| `./mysql-data` | `/var/lib/mysql` | MySQL 数据目录 |
| `~/.rss` | `/root/.rss:ro` | 站点 API Key（只读） |

MySQL 与应用解耦，其它服务也可以复用这个实例（库名 `ptsf`，端口 3366）。

### 配置存放在 MySQL

启动时的优先级：

| 情况 | 行为 |
|---|---|
| 第一次启动（库里没有服务配置） | 用 `data/config.yaml` 初始化数据库 |
| 之后启动 | **以数据库为准**，配置文件只用于首次初始化 |
| 带 `--cfg_force` 或 `PT_CFG_FORCE=1` | 用 `data/config.yaml` **覆盖**数据库 |

`bash force-config.sh` 就是「覆盖」那条路径。

运行中每 5 分钟（`PT_CFG_RELOAD_SECONDS` 可调）从数据库重读一次服务配置，
改完立即生效，不用重启：

```bash
mysql -h <nas-ip> -P 3366 -u ptsf -p ptsf \
  -e "update services set config=JSON_SET(config,'\$.disk_gb',150) where name='auto-mteam-1'"
```

同样的改动也能在网页上做：服务卡片 →「编辑配置」→ 直接改 JSON → 保存。

### 连接数据库

| 项目 | 值 |
|---|---|
| 端口 | `3366` |
| 库名 | `ptsf` |
| 账号 | `ptsf`（密码见 `.env` 的 `MYSQL_PASSWORD`） |
| 网络 | 局域网可达；无公网 IP |

### 测试

```bash
uv run pytest
```

测试全部离线运行，样本来自真实响应的裁剪版。

## 配置

见 [`config/services.example.yaml`](config/services.example.yaml)。要点：

| 参数 | 说明 |
|---|---|
| `disk_gb` | 该服务的逻辑磁盘配额 |
| `discounts` | 只刷哪些促销，如 `FREE`、`_2X_FREE`；留空表示所有非 `NORMAL` |
| `categories` | 只刷哪些分类；留空表示不限 |
| `size_mb` | 种子体积范围，单位 MB |
| `delete_threshold_mb` | 窗口内上传量低于该值则删除 |
| `exempt_minutes` | 新种豁免期 |
| `min_keep_minutes` | 最短保留时间，避免"下完就跑" |
| `max_torrents` | 并发任务上限 |
| `add_per_tick` | 每轮最多新增几个 |
| `save_path` | 传给 qB 的保存路径；留空则用 qB 默认 |
| `category` | qB 分类名，也是服务隔离边界 |
| `dry_run` | 只输出"想加/想删"，不真正执行 |

`dry_run` 默认 `true`，建议先观察几轮再关闭。

## 目录结构

```text
app/
  config.py        配置模型与校验
  bootstrap.py     首次启动生成配置、校验失败拒绝启动
  store.py         SQLite 实现（单测用）：配额、采样、历史
  db.py            MySQL 实现与建表（生产用）
  confrepo.py      配置读写 MySQL（含 --cfg_force 覆盖逻辑）
  scoring.py       候选打分
  lifecycle.py     加种/删除决策（纯函数）
  sites/           站点适配层
    base.py        通用接口（SiteAdapter）与 HTTP 底座
    mteam/         m-team 实现：client（API）/ parser（解析）
  qb.py            qBittorrent 客户端
  scheduler.py     调度与状态快照
  web.py           Web 面板
  main.py          入口
tests/             离线单测
```


## 在 NAS 上同步代码

NAS 通常只读不写，且部分网络环境直连 GitHub 不稳定，因此可以长期把 origin 指向加速镜像：

```bash
cd ~/workspace/pt-seedflow
git remote set-url origin https://ghproxy.net/https://github.com/winding345/pt-seedflow.git
git pull
```

也可以保留 GitHub 为 origin，改用 `sync.sh`（会先试 origin，失败自动回退到镜像）：

```bash
bash sync.sh      # 只同步代码
bash deploy.sh    # 只构建启动，不碰 git
```

`deploy.sh` 刻意不包含任何网络拉取动作，避免网络故障时整条部署流程卡死。



### 接入 pttime（NexusPHP 站点）

用 **RSS** 接入，不需要 API key、不需要 cookie、不消耗任何配额：

```yaml
sites:
  pttime:
    type: nexusphp
    base_url: https://pttime.org
    # 站点「RSS」页面里复制的地址，已含 passkey
    rss_url: https://pttime.org/getrss.php?passkey=你的passkey&showrows=100
    discount_tags:
      2X免费: _2X_FREE
      免费: FREE
      2X: _2X

services:
  - name: auto-pttime-1
    site: pttime
    category: auto-pttime-1
    dry_run: true
```

工作方式：

1. 拉 RSS，解析 `<item>`：`title` → 名称，`enclosure@url` → 下载直链，`enclosure@length` → 体积
2. 按 `discount_tags` 在标题/描述里匹配促销关键词
3. 加种时**直接用 RSS 里的直链**，不再调用任何站点接口

局限：

- RSS 只返回站点最新的 N 条（`showrows` 控制），不是按条件搜索
- 体积与促销依赖 RSS 里的文本，各站格式不同，需要用 `discount_tags` 调
- 想看实际字段，可以在浏览器打开你的 `rss_url` 看一条 `<item>` 的内容

## 新增站点（自定义适配器）

调度、选种、配额、删除这些逻辑与站点无关，新增站点只需要实现一个适配器：

```python
# app/sites/pttime/__init__.py
from .. import register
from ..base import SiteAdapter

@register("pttime")          # 配置里写 site: pttime 就会用到它
class PTTimesite(SiteAdapter):
    def search(self, page=1, page_size=100):
        ...                  # 返回 List[Candidate]

    def genlink(self, tid):
        ...                  # 返回可直接交给 qB 的下载链接
```

要点：

- `search` 负责把站点数据归一化成 `Candidate`（名称、体积、促销、促销截止时间、做种/下载数、发布时间）
- `genlink` 负责把种子 id 换成下载链接；NexusPHP 类站点通常用 passkey 拼固定 URL，不需要 API
- 促销没有明确结束时间时，`discount_end` 必须是 `None`，删除逻辑会把它当作"长期有效"

配置里加一段即可启用：

```yaml
sites:
  pttime:
    base_url: https://example.pttime.org
    api_key_file: ~/.pt-seedflow/pttime.passkey
services:
  - name: auto-pttime-1
    site: pttime
    dry_run: true
```

## 已知边界

- 只处理自己 `category` 下的任务，不会触碰其他任务
- qB 的 `add` 只返回 `Ok.`，加种后需从 `torrents/info` 反查 hash 做跟踪
- `dlv2` 链接有时效，换取后必须立即使用
- 目前仅适配 m-team；新增站点实现 `sites/` 下的适配器即可

## 路线

- [x] 配置与校验、状态库、打分、加删决策
- [x] m-team 适配、qB 客户端、dry-run 调度、Web 面板
- [ ] 实盘验证与参数调优
- [ ] 多站点适配（pttime）
