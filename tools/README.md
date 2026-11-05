# DCG Schema Builder

本地小工具：把**原始 JSON** 贴进来，自动推断结构树，然后从树里**拖拽**字段到画布上组成
[DCG](https://github.com/CL98K/DCG) schema，最后一键编译并跑数据、看结果。

- 纯本地：只监听 `127.0.0.1`，数据不出机器。
- 零依赖：后端只用 Python 标准库，前端是原生 HTML/CSS/JS（离线可用，不需要 npm / CDN）。
- 与 DCG 同仓库：直接 `import dcg`，用的就是当前工作树里的那个版本。

```
tools/
├── app.py              # HTTP 服务（/api/health|demo|infer|run + 静态文件）
├── dcg_bridge.py       # 推断 / 编译 / 运行的桥（用 dcg.validate + dcg.generate）
├── selftest.py         # 无浏览器自检：起服务、打接口、断言契约
├── run.cmd             # Windows 双击启动
├── API.md              # HTTP 契约（v1，冻结）
├── README.md           # 本文件
├── docs/DESIGN.md      # 设计说明与实现备忘
├── tests/test_bridge.py
└── web/{index.html,style.css,app.js}
```

## 快速开始

在仓库根目录（含 `dcg/` 与 `tools/` 的那一层）：

```bat
python tools\app.py
```

或双击 `tools\run.cmd`。浏览器会自动打开 <http://127.0.0.1:8765/>。

常用参数：

```bat
python tools\app.py --port 8790        :: 换端口
python tools\app.py --no-browser       :: 不自动开浏览器
python tools\app.py --host 127.0.0.1   :: 仅本机（默认）
```

要求：Python 3.8+（本仓库同样支持 3.8–3.15）；不需要安装任何第三方包。
DCG 的加速二进制（`dcg/_accel/`）是可选项——缺失时自动回退纯 Python，不影响本工具。

## 用法

1. **贴数据**：左侧 textarea 粘贴一段 JSON（数组、或 `{"orders": [...]}` 这种包一层的对象）。
2. **推断结构**：点「推断结构」。工具会判断数据形态：
   - `rows`：顶层就是数组，每个元素是一行；
   - `wrapper`：顶层对象的唯一数组值，运行时会按 `$.<key>` 自动解包；
   - `single`：顶层对象没有数组，当作单行。
3. **拖拽建 schema**：把结构树里的叶子拖进右侧画布；每张卡片可以改名、改类型、改成 Python
   表达式（自动包一层反引号），也可以拖动重排、删除。
4. **预览 schema**：检查生成的 JSON（就是 `dcg.validate` 吃的那种 schema）。
5. **运行测试**：把原始数据喂给生成的 `run()`，结果是表格；另有两个 tab 看生成的源码与错误。
   超过 200 行时只显示前 200 行并标注截断。

### 一个小例子

输入：

```json
{"orders": [{"sku": "A-1", "qty": 2, "price": 3.5}, {"sku": "B-2", "qty": 1, "price": 10.0}]}
```

拖入 `sku`、`qty`、`price` 三个叶子，schema 名填 `order`，输出形态 `list`，运行后得到：

```text
sku   qty  price
A-1   2    3.5
B-2   1    10.0
```

### 输出形态（`output.shape`）

| 值 | 产物 | 例 |
| --- | --- | --- |
| `list`（默认） | 行列表 | `[["A-1", 2], ["B-2", 1]]` |
| `dict` | 列式 | `{"sku": ["A-1", "B-2"], "qty": [2, 1]}` |
| `index` | 行号 → 行 | `{0: ["A-1", 2], 1: ["B-2", 1]}` |
| `rows` | 行式对象 | `[{"sku": "A-1", "qty": 2}, ...]` |

四个值就是 DCG 自己的枚举（`dcg/schema.py` 的 `SHAPES`），工具不做别名映射。

## 自检（无浏览器）

```bat
python tools\selftest.py            :: 起临时端口，打全部接口并断言字段
python -X utf8 -m pytest tools\tests -q
```

`selftest.py` 覆盖：静态页 200、`/api/health` 版本号、`/api/demo`、`/api/infer`（rows / wrapper /
single / 非法 JSON）、`/api/run`（成功路径与一条 schema 校验失败路径）。

## HTTP 契约

见 [`API.md`](API.md)。三句话版本：

- `GET /api/health` → `{"ok":true,"dcg_version":"2.2.2",...}`
- `POST /api/infer  {"text": "..."}` → 结构树 + 扁平叶子 + warnings
- `POST /api/run    {"text":"...","schema":{...}}` → 结果行、生成源码、耗时、manifest

业务错误一律 HTTP 200 + `{"ok":false,"stage":...,"error":...}`，前端按 `stage` 显示。

## 已知限制

- 只支持 JSON 文本输入；超大文件（>32 MB）会被服务端拒绝。
- 结构树里**对象容器**不直接进画布（DCG 路径无法表达“整个子树”），需要时用 Python 表达式取
  子字段，例如 `` `$.addr.city` ``。
- 键名里含 `.` 的字段无法用点号路径表达，推断时会跳过并给 warning。
- 结果为 NaN/Inf 或非 JSON 类型时会被转成字符串显示，并在 warnings 里说明，避免 JSON 编码失败。
- 画布上的表达式在**运行期**于本地 Python 里执行；只建议用于自己的数据，不要粘贴不可信表达式。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| `ModuleNotFoundError: dcg` | 必须从仓库根运行（`tools/` 与 `dcg/` 同级），不要在别处单独拷 `tools/`。 |
| 端口被占用 | 换端口：`python tools\app.py --port 8790`。 |
| 浏览器没自动打开 | 手动访问终端打印的 URL，或加 `--no-browser` 后自己开。 |
| 页面上「dcg 版本未知」 | 后端 `/api/health` 失败；看终端里的 `[http]` 日志。 |
| 想放到仓库外（如桌面 `tools\`） | 整个目录平移即可，但运行时仍需把 DCG 仓库根加进 `sys.path`（`app.py` 只假设 `tools/` 是 `dcg/` 的兄弟）。 |
