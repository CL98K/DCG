# 设计说明：DCG Schema Builder

> 目标：把“从一份原始 JSON 到一份能跑通的 DCG schema”这件事，从手写 JSON 变成一个拖拽动作。
> 约束：本地、离线、零第三方依赖、与 DCG 仓库同源。

## 1. 分层与数据流

```
浏览器（web/）
   │  fetch /api/infer   {text}
   │  fetch /api/run     {text, schema}
   ▼
tools/app.py            HTTP 路由 / 静态文件 / 请求体与错误边界
   ▼
tools/dcg_bridge.py     推断（infer） · 执行（run）
   ├── json.loads       原始数据
   ├── dcg.validate     schema 合法性与报错文本
   └── dcg.generate    源码 → exec → run(data)
```

* 浏览器只认识三样东西：`infer` 的树（`tree` + `fields`）、`run` 的结果（`out`、`code`、`manifest`）、
  错误（`stage` + `error`）。HTTP 契约冻结在 [`../API.md`](../API.md)，前后端都以它为准。
* `app.py` 不碰 DCG；`dcg_bridge.py` 不碰 HTTP。两侧都可单测：桥用 pytest，服务端用 `selftest.py`。

## 2. 为什么是“推断 + 拖拽”而不是“表单”

手写 DCG schema 的认知负担在**路径**（`$.data.TASK_TYPE.0` 这种）。用户其实已经有一份真实数据，
路径完全可以由数据本身推出来，所以：

1. 推断阶段把样本变成一棵带 `path` 的树；
2. 用户只决定**哪些叶子成为输出列**、列叫什么、什么类型；
3. 路径由拖拽动作带入，人不再手敲。

这也解释了为什么叶子才可拖：DCG 的 `value` 是一个取值路径，指向“整个子树”没有意义；对象容器保留
在树里只是为了让用户看清嵌套关系（需要子字段时用 `expr`）。

## 3. 推断算法（infer）

### 3.1 形态判定

| 样本顶层 | mode | 行数据 |
| --- | --- | --- |
| 数组 | `rows` | 数组本身 |
| 对象，且恰有一个数组值 | `wrapper` | 该数组，`source = $.键` |
| 对象，无数组值 | `single` | 该对象（1 行） |

`wrapper` 对应 DCG 的 `input.source`：生成的 `run` 首行会自己 `_d = _d['orders']` 解包，
所以运行期传原始整份数据即可，不需要前端先拆。

### 3.2 路径与树

* 行根是 `$`；对象键与数组下标都接在点号后：`$.0`、`$.a`、`$.a.b`、`$.a.0.b`。
* 数组元素在 `tree` 里用 `[i]` 作 label；超过 3 个元素只展示前 3 个（`count` 给真实长度）。
* 键名含 `.` 时无法表达（DCG 用点号分段），跳过并 warning。

### 3.3 类型格（跨行合并）

```
bool ─────────────► bool
int ──────────────► int64
int + float ──────► float64
float ────────────► float64
str ──────────────► str
标量数组 ─────────► list
其余混合/含 null ─► any
```

顺序很重要：Python 里 `bool` 是 `int` 的子类，必须先判 bool。`null` 不单独成类型，只降低置信度
（合并进 `any`），DCG 运行期会按 `any` 原样透传。

### 3.4 叶子策略

* 标量叶子 → 字段，`type` 为标量类型，`value` 为路径。
* 标量数组叶子 → 字段，`type = list`（DCG 会把整个数组作为一列输出）。
* 对象/混合数组 → 只进树，不进 `fields`，附 warning。

`suggest`（建议输出名）在桥里生成一次：非标识符字符换 `_`、数字开头加 `col_`、重名追加 `_2`；
前端只把它当默认值，用户可改。

## 4. schema 组装（前端）

```js
{
  name: <顶栏输入，默认 infer.schema_name>,
  input: { source: <仅 wrapper 且非空时> },
  output: { shape: <list|dict|index|rows>, trace: false },
  out: { type: "object", fields: {
    <输出名>: <取值模式> ? { type: <类型>, value: "$..." }
                          : { expr: "`<用户表达式>`" }
  }}
}
```

`shape` 直接用 DCG 的四个值（`dcg/schema.py` 的 `SHAPES`）：`list` 行列表、`dict` 列式、
`index` 行号→行、`rows` 行式对象。**没有 `object`**——第一版 UI 曾按“list/object”写，联调时
发现枚举不符，按 DCG 修正（不做别名映射，避免契约里出现第二套词汇）。

表达式模式由前端加反引号（若用户自己写了反引号则不重复），这是 DCG 的 expr 语法要求。

## 5. 安全与边界

| 边界 | 处理 |
| --- | --- |
| 监听地址 | 默认 `127.0.0.1`；不对外网开放（工具定位是本地辅助） |
| 请求体 | 上限 32 MB，超出 400 |
| 静态文件 | 只允许 `web/` 下、文件名不含 `/`、`\`、以 `.` 开头；其余 404 |
| 异常 | 业务错误一律 HTTP 200 + `{ok:false,stage,error}`；`app.py` 有兜底，绝不给浏览器 HTML 500 |
| 表达式 | `expr` 在**本地** Python 里执行 —— 只对自己数据使用；README 明确提示 |
| 结果序列化 | NaN/Inf/不可序列化对象转字符串并 warning，避免 JSON 编码失败 |
| 依赖 | 标准库 + 原生前端；无 npm、无 CDN，断网可用 |

## 6. 文件职责

| 文件 | 职责 | 谁维护 |
| --- | --- | --- |
| `app.py` | 路由、静态文件、请求体与错误边界、启动参数 | Lead |
| `dcg_bridge.py` | 推断与执行；DCG 的唯一接触点 | bridge-core |
| `web/` | 单页 UI：树、画布、DnD、结果 tab | web-ui |
| `selftest.py` | 起临时端口打全部接口，断言契约 | Lead |
| `tests/test_bridge.py` | 桥的单元测试（形态/类型/错误/截断） | bridge-core |
| `API.md` | 冻结契约，改动需三方同步 | Lead |

## 7. 可预见的扩展（未实现）

* 「导出 schema / 用 `dcg.build` 落盘」——桥已具备全部信息，加一个 `/api/build` 即可。
* `cython=True` 与 `trace` 开关——`generate`/`build` 都支持，UI 加两个 checkbox。
* 多 schema 草稿（localStorage）与最近文件。
* 大样本采样而不是全量推断（当前 `row_count` 只用于展示，推断走全部行）。
* 结构化输入来源（CSV / JSONL）——需新增解析层，契约里加 `format` 字段。
