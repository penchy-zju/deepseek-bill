# deepseek-bill

每天北京时间凌晨 1:00 自动抓取 DeepSeek 平台**前一天**的用量账单，把压缩包里的 `amount` CSV 解出来并按日期归档到本仓库。

---

## 一、工作方式

- Workflow 文件：[`.github/workflows/deepseek-cron.yml`](.github/workflows/deepseek-cron.yml)
- 触发时间：`cron: "0 17 * * *"`（GitHub Actions 的 cron 用 **UTC**；UTC 17:00 = 北京时间**次日** 01:00）
- 抓取区间：北京时间**前一天 00:00:00 → 当天 00:00:00**（正好 24 小时），接口参数 `tz=28800`（UTC+8）
- 数据归档：`data/amount-YYYY-MM-DD.csv`，其中 `YYYY-MM-DD` 是账单所属的那一天（即“前一天”）

例如北京时间 2026-09-22 01:00 触发时，抓取 2026-09-21 全天数据，写入 `data/amount-2026-09-21.csv`。

> 时间计算不依赖 `TZ=Asia/Shanghai`（该变量在部分环境不可靠），而是用纯 epoch 算术推导，并在写数据前做三重自检：区间必须正好 86400 秒、区间两端必须落在 UTC 16:00（= 北京时间 00:00）、归档日期标签必须与 `start` 对应的北京日期一致。任一不满足即报错退出，不会写错日期的数据。

---

## 二、一次性配置

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

## 三、手动触发测试

**仓库 → Actions → 左侧 `DeepSeek Daily Usage Export` → 右侧 `Run workflow` → 选择分支 → `Run workflow`**

运行结束后：

- 成功时，`data/` 下会出现对应日期的 CSV，并有一条由 `github-actions[bot]` 提交的 commit，提交信息形如 `data: amount 2026-09-21`
- 日志里会打印 `Target usage date`、`START`、`END` 以及解压出来的文件清单，可据此确认区间与文件名

**关于“空提交”**：如果目标日期的数据与仓库中已有文件完全一致（例如一天内重复手动触发），Workflow 会跳过提交并正常退出，不会产生空 commit。如果当天接口返回的 CSV 只有表头、没有数据行，同样不会提交空文件（日志中给出一条 warning）。

---

## 四、数据归档路径

```
data/
├── amount-2026-09-19.csv
├── amount-2026-09-20.csv
└── amount-2026-09-21.csv
```

- 文件内容即压缩包中 `amount` CSV 的原始内容，**仅去掉了 UTF-8 BOM**，未做其它改写
- 压缩包内的 `cost` 文件被忽略
- 文件名模糊匹配：`*amount*.csv` 或 `*usage*.csv`，并显式排除 `*cost*`。若匹配到 0 个或 2 个以上候选（平台改了打包结构），Workflow 会报错退出而不是猜

---

## 五、风险与维护提醒

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

## 六、本地自测（可选）

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
