# deepseek-bill

每天北京时间凌晨 1:00 自动抓取 DeepSeek 平台**前一天**的用量账单，把压缩包里的 `amount` CSV 解出来并按日期归档到本仓库，同时把结果渲染成一个静态用量看板发布到 GitHub Pages。

- 数据仓库路径：`data/amount-YYYY-MM-DD.csv`
- 用量看板：`index.html`（数据内嵌，发布到 `gh-pages` 分支）
- 看板地址：`https://penchy-zju.github.io/deepseek-bill/`

---

## 一、工作方式

- Workflow 文件：[`.github/workflows/deepseek-cron.yml`](.github/workflows/deepseek-cron.yml)
- 触发时间：`cron: "0 17 * * *"`（GitHub Actions 的 cron 用 **UTC**；UTC 17:00 = 北京时间**次日** 01:00）
- 抓取区间：北京时间**前一天 00:00:00 → 当天 00:00:00**（正好 24 小时），接口参数 `tz=28800`（UTC+8）
- 数据归档：`data/amount-YYYY-MM-DD.csv`，其中 `YYYY-MM-DD` 是账单所属的那一天（即“前一天”）

例如北京时间 2026-09-22 01:00 触发时，抓取 2026-09-21 全天数据，写入 `data/amount-2026-09-21.csv`。

每次运行依次做四件事：

1. 拉取前一天账单 → 写入 `data/amount-YYYY-MM-DD.csv`（内容不变则不提交）
2. 用 `scripts/build_site.py` 重新生成看板 `index.html` + `site-data.json`
3. 跑校验：`scripts/verify_site.js`（独立复算 + 在 Node 里执行页面脚本交叉比对）、`scripts/test_publish.py`（在临时仓库里演练发布逻辑）
4. 把站点文件发布到 `gh-pages` 分支（GitHub Pages 自动生效）

> 时间计算不依赖 `TZ=Asia/Shanghai`（该变量在部分环境不可靠），而是用纯 epoch 算术推导，并在写数据前做三重自检：区间必须正好 86400 秒、区间两端必须落在 UTC 16:00（= 北京时间 00:00）、归档日期标签必须与 `start` 对应的北京日期一致。任一不满足即报错退出，不会写错日期的数据。

---

## 二、用量看板

### 看板能看什么

页面顶部是当天总览，下面分「按模型明细」和「全部 API Key 对比」两张表：

| 指标 | 含义 |
| --- | --- |
| 调用次数 | 接口 `request_count` 求和 |
| 缓存命中 tokens | `input_cache_hit_tokens`，即命中上下文的输入量 |
| 缓存未命中 tokens | `input_cache_miss_tokens`，即未命中、按全价计费的输入量 |
| 输出 tokens | `output_tokens`，模型生成的量 |
| 各项费用 / 费用合计 | 见下方「费用口径」 |
| 输入缓存命中率 | `命中 ÷ (命中 + 未命中)`，反映上下文复用程度 |

**筛选**：顶部 `API Key` 下拉可以切换「全部」或某一个 Key，切换后总览卡片和明细表都会跟着变；`日期` 下拉可以回看历史某一天（默认选中最近一个已结束的日期）。

### 费用口径（重要）

费用**不是**硬编码价目表算出来的，而是逐行用接口返回的 `price × amount` 累加：

- 接口返回的每行都带 `price` 和 `amount`，`price` **已经包含平台的优惠时段折扣**——同一模型同一天会出现两档单价（例如 `deepseek-v4-pro` 输出全价 `0.000027`、优惠价 `0.0000135`），所以无需再手工打折
- 金额用 Python `Decimal` 累加，避免浮点误差（数据里单行费用小到 `1.4e-7`）
- 页面显示时自适应精度：小于 1 显示 6 位小数，小于 0.0001 显示 8 位并去掉末尾零，避免小费用被显示成 `¥0.0000`；金额一律用人民币符号 `¥`

### 数据口径与已知限制

- `api_key` 在接口返回里**已被平台掩码**（形如 `sk-63b33***c1e8`），所以看板用「名称 + 掩码」共同标识一个 Key；掩码相同即视为同一个 Key。原始明文 Key 不会被写入仓库，也不会出现在页面上
- 看板只包含**已归档的 `amount` 明细**：接口没返回的小时不会出现在数据里（例如某天只有 08:00–23:00 有数据，就只统计这些时段）
- 数据按**小时粒度**返回，看板展示的是当天所有小时聚合后的结果；要看逐小时明细请直接看 `data/amount-*.csv`
- 页面是**纯静态**的：数据以 JSON 形式内嵌在 `index.html` 里，不依赖任何后端；`site-data.json` 是对外暴露的同一份数据，方便自己写脚本消费

### 发布方式

站点发布到 `gh-pages` 分支（内容 = `index.html` + `site-data.json` + `data/*.csv` + `.nojekyll`）。选择这种方式的理由：站点文件与数据仓库隔离，`gh-pages` 每次都是干净重建，不会混入源码。

- **Pages 已启用**：Source = `Deploy from a branch`，分支 `gh-pages`，目录 `/ (root)`；页面地址 <https://penchy-zju.github.io/deepseek-bill/>
- 只有数据或页面内容真的变化时才会重新发布；没有变化时日志里 `Publish to gh-pages` 显示 `skipped`，属正常
- 仓库是 **public**，所以看板对所有人可访问；若不想公开，可把仓库改为 private（Pages 可用性取决于你的 GitHub 套餐）

---

## 三、一次性配置

### 1. 获取 `userToken`

1. 浏览器登录 <https://platform.deepseek.com>
2. 按 `F12` 打开开发者工具
3. 进入 **Application**（应用）→ 左侧 **Local Storage** → 选中 `https://platform.deepseek.com`
4. 找到 **`userToken`**，复制它的值（**只复制值本身**，不要带 `userToken=` 或引号）

### 2. 添加为仓库 Secret

**仓库 → Settings → Secrets and variables → Actions → New repository secret**

| Name | Secret |
| --- | --- |
| `DEEPSEEK_PLATFORM_TOKEN` | 上一步复制的 `userToken` 值 |

Workflow 只需要这一个 Secret，不要把它写进代码或提交到仓库。

### 3. 确认 Actions 有写权限

**仓库 → Settings → Actions → General → Workflow permissions** 选择 **Read and write permissions**。
（Workflow 里已声明 `permissions: contents: write`，但若仓库级设置被限制为只读，推送仍会失败。）

---

## 四、手动触发测试

**仓库 → Actions → 左侧 `DeepSeek Daily Usage Export` → 右侧 `Run workflow` → 选择分支 → `Run workflow`**

运行结束后：

- 成功时，`data/` 下会出现对应日期的 CSV，并有一条由 `github-actions[bot]` 提交的 commit，提交信息形如 `data: amount 2026-09-21`
- 日志里会打印 `Target usage date`、`START`、`END` 以及解压出来的文件清单，可据此确认区间与文件名
- 接着会生成看板并把站点发布到 `gh-pages` 分支；日志里 `Publish to gh-pages` 这一步显示 `success` 或 `skipped` 都属正常（无变化时会跳过）

**关于“空提交”**：如果目标日期的数据与仓库中已有文件完全一致（例如一天内重复手动触发），Workflow 会跳过数据提交与看板提交，`Publish to gh-pages` 直接显示 skipped，**不会产生任何空 commit**。如果当天接口返回的 CSV 只有表头、没有数据行，同样不会提交空文件（日志中给出一条 warning）。

> 为什么需要特别处理：这个导出接口每次返回的**行内容相同但顺序会变**，接口压缩包里的 CSV 又是 CRLF。如果原样入库，同一份数据每次运行都会呈现差异，从而每天产生一次“假提交”。因此抓取步骤会对数据行做稳定排序（按 `start_time, model, api_key, type`）并统一成 LF，使得同一份数据逐字节可复现。

---

## 五、数据归档路径

```
data/
├── amount-2026-09-19.csv
├── amount-2026-09-20.csv
└── amount-2026-09-21.csv
```

- 文件内容即压缩包中 `amount` CSV 的数据，处理方式为：**去掉 UTF-8 BOM、统一为 LF 行尾、按 `(start_time, model, api_key, type)` 稳定排序**，字段与数值原样保留，不做其它改写
- 压缩包内的 `cost` 文件被忽略
- 文件名模糊匹配：`*amount*.csv` 或 `*usage*.csv`，并显式排除 `*cost*`。若匹配到 0 个或 2 个以上候选（平台改了打包结构），Workflow 会报错退出而不是猜
- 若某天确实没有数据（接口只返回表头），不会生成文件，也不会留下空文件

---

## 六、风险与维护提醒

### ⚠️ `userToken` 是会话凭证，会过期

- 它是登录态的一部分，**不是长期 API Key**，随时可能失效（重新登录、过期、切换设备等都会导致失效）
- 失效后 Workflow 会以 `HTTP 401` / `HTTP 403` **明确报错退出**，日志里直接提示需要更新 Secret，**不会写入空文件或错误数据**
- 处理方式：重新按上文「获取 `userToken`」复制新值，回到 Secret 页面 **Update** 即可
- 建议把每日 Actions 失败通知打开，避免账单数据静默断档
- 安全提醒：该值等同于你的平台登录态，请只放在 Secret 中；一旦怀疑泄露，重新登录使其失效并更新 Secret

### ⚠️ 平台内部接口不是官方承诺的

`https://platform.deepseek.com/api/v0/usage/export` 是平台的**内部接口**，不属于公开 API，**可能随时改动或下线**（改路径、改参数名、改鉴权头、改打包格式都可能导致失败）。

若某天开始持续失败，请按浏览器里的真实请求校准本 Workflow：

1. 打开 <https://platform.deepseek.com> → `F12` → **Network**
2. 在用量/账单页面触发一次「导出」
3. 找到那个导出请求，检查：
   - **Request URL** 与查询参数（`start` / `end` / `tz` 是否仍是这些名字）
   - **Request Headers** 里鉴权用的具体头（当前实现用的是 `Authorization: Bearer <token>`；若平台改成 cookie 或别的头，需要同步修改 Workflow 的 `curl -H` 部分）
   - **Response** 是否仍是 zip、内部文件名是否仍能匹配 `*amount*.csv`
4. 按实际请求更新 `.github/workflows/deepseek-cron.yml` 中对应片段

### 其它注意点

- 建议用**公开或私有仓库均可**；若仓库是 public，数据文件也会公开，请注意账单数据是否敏感
- GitHub 定时任务在高峰期可能延迟数分钟到数十分钟，这不影响结果（脚本按“当前已结束的北京日”计算，而不是写死日期）
- 仓库 60 天无任何活动时，GitHub 会自动暂停定时 Workflow（本仓库每日有 commit，通常不会触发；首次配置后若长时间未跑，去 Actions 页面点一下 `Enable workflow`）

---

## 七、本地自测与开发（可选）

Workflow 的三个脚本块只依赖 `bash`、`curl`、`unzip`、`date`（GNU date）。下面这段与 Workflow 内的算法完全一致，可直接在 Linux/WSL/macOS 上验证时间区间：

```bash
BEIJING_OFFSET=28800
DAY=86400
NOW=$(date -u +%s)
ROUNDED_MIDNIGHT=$(( (NOW + BEIJING_OFFSET) - ((NOW + BEIJING_OFFSET) % DAY) ))

TARGET_DATE=$(date -u -d "@$(( ROUNDED_MIDNIGHT - DAY ))" +%F)   # 归档用的账单日期
END=$(( ROUNDED_MIDNIGHT - BEIJING_OFFSET ))                      # 北京时间 当天 00:00
START=$(( END - DAY ))                                            # 北京时间 前一天 00:00

echo "target=${TARGET_DATE} interval=$(( END - START ))s"
echo "START=$(date -u -d "@$START" '+%F %H:%M:%S') UTC = $(date -u -d "@$(( START + BEIJING_OFFSET ))" '+%F %H:%M:%S') UTC+8"
echo "END  =$(date -u -d "@$END" '+%F %H:%M:%S') UTC = $(date -u -d "@$(( END + BEIJING_OFFSET ))" '+%F %H:%M:%S') UTC+8"
```

预期：`interval=86400`，且 `START` / `END` 换算成 UTC+8 后都是 `00:00:00`。

直接测试接口（把 `<TOKEN>` 换成你的 `userToken`，`START`/`END` 用上面算出的值）：

```bash
curl -sS -D - -o usage.zip \
  "https://platform.deepseek.com/api/v0/usage/export?start=${START}&end=${END}&tz=28800" \
  -H "Authorization: Bearer <TOKEN>"
unzip -l usage.zip   # 确认内部文件名
```

### 看板相关脚本

```bash
# 校验 0：workflow 文件本身能否被解析（语法坏掉的 workflow 会被 GitHub 直接忽略）
python3 scripts/check_workflow.py

# 重新生成看板（只依赖 Python 标准库）
python3 scripts/build_site.py

# 校验 1：独立复算 CSV + 在 Node 里真实执行页面脚本，交叉比对每个 Key/模型的数字
node scripts/verify_site.js

# 校验 2：在临时仓库里演练 gh-pages 发布（含重复运行不应产生空提交、旧文件应被清理）
python3 scripts/test_publish.py
```

前三个（`check_workflow.py` + `verify_site.js` + `test_publish.py`）每次运行都会在 CI 里执行一遍，任何一项失败都会阻止发布。

另外两个**仅本地**使用的脚本（需要本机有 Chrome，且 `npm i ws` 提供 WebSocket）用于检查真实渲染效果，CI 里不跑：

```bash
# 用 CDP 检查页面在真实浏览器中的渲染、筛选交互与计算样式
node scripts/browser_check.js "/path/to/chrome" "$PWD/index.html"

# 检查掩码 Key 是否被浏览器当成电话号码自动着色
node scripts/check_phone.js "/path/to/chrome" "$PWD/index.html"
```

### 给这个仓库加新指标时

1. `scripts/build_site.py` 里 `METRICS` 字典把接口的 `type` 映射到内部字段；新指标先加到这里
2. 聚合结果通过 `to_jsonable()` 落到 `site-data.json`，再内嵌进 `index.html`
3. 页面 JS 里 `mergeModels()` / `metricsOf()` 负责跨 Key、跨模型合并（**不要**用 `Object.assign` 合并模型，同名模型会被覆盖而不是相加）
4. 改完必须让 `check_workflow.py`、`verify_site.js` 和 `test_publish.py` 都通过——后两个会独立复算，能抓出聚合写错
5. **不要**把构建时间戳之类的非确定性内容写进产物：那会让每次构建都产生差异，从而每次都提交一次假更新。产物里用的是输入 CSV 的 sha256 指纹（页面副标题的 `数据指纹`）
6. 改 `.github/workflows/*.yml` 后**一定要跑** `check_workflow.py`：YAML 一坏，GitHub 会静默忽略整个 workflow（连 `workflow_dispatch` 都会消失），很难排查

### 这个仓库踩过的坑（避免重复）

- **接口返回的行顺序不稳定**：不排序就会每天产生“假变更”，进而每天一次空提交 → 已在抓取步骤做稳定排序
- **接口 CSV 是 CRLF**：跨平台会出现差异 → 已统一为 LF
- **`GITHUB_ENV` 的变量在写入它的那个 step 内不可见**：后续 step 必须先取到本地变量再用
- **`actions/checkout` 默认只抓默认分支且是 shallow clone**：本地看不到 `origin/gh-pages`，且 shallow 仓库无法 push 到新远端 → 判断分支是否存在要用 `git ls-remote`
- **`git checkout -B` 不会重建索引**：切到 `gh-pages` 后如果索引里还留着 `master` 的树，`git add -A` 会把 `README`、`scripts/` 等“删除”一起提交上去 → 切分支后要 `git reset --hard`

